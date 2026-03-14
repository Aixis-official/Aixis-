"""Audit runner service — bridges the Web dashboard to the aixis_agent pipeline.

Runs the audit pipeline in a background thread (since Playwright is synchronous
and we don't want to block the FastAPI event loop). Progress is tracked in the
web app's async SQLAlchemy database so the dashboard can poll for updates.
"""

import asyncio
import logging
import threading
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

from ..config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# In-memory registry of running audits (lightweight, no Redis needed for MVP)
# ---------------------------------------------------------------------------

_running_audits: dict[str, dict[str, Any]] = {}
_login_events: dict[str, threading.Event] = {}
_abort_events: dict[str, threading.Event] = {}
_lock = threading.RLock()  # RLock allows re-entrant locking (e.g. abort_audit → _update_running)


def get_running_audit(session_id: str) -> dict[str, Any] | None:
    with _lock:
        return _running_audits.get(session_id)


def list_running_audits() -> list[dict[str, Any]]:
    with _lock:
        return list(_running_audits.values())


def _update_running(session_id: str, **kwargs: Any) -> None:
    with _lock:
        if session_id in _running_audits:
            _running_audits[session_id].update(kwargs)


def abort_audit(session_id: str) -> bool:
    """Signal an abort to a running audit. Stops API usage immediately."""
    with _lock:
        event = _abort_events.get(session_id)
        if event:
            event.set()
            _update_running(session_id, status="aborting", phase="aborting")
            return True
        # Also search by db_session_id
        for sid, info in _running_audits.items():
            if info.get("db_session_id") == session_id:
                ev = _abort_events.get(sid)
                if ev:
                    ev.set()
                    _update_running(sid, status="aborting", phase="aborting")
                    return True
    return False


def resume_after_login(session_id: str) -> bool:
    """Signal that manual login is complete for a waiting audit."""
    with _lock:
        event = _login_events.get(session_id)
        if event:
            event.set()
            return True
        # Also search by db_session_id
        for sid, info in _running_audits.items():
            if info.get("db_session_id") == session_id:
                ev = _login_events.get(sid)
                if ev:
                    ev.set()
                    return True
    return False


# ---------------------------------------------------------------------------
# Agent imports (lazy to avoid import errors when agent deps aren't installed)
# ---------------------------------------------------------------------------

def _get_base_dir() -> Path:
    """Get the project root directory (where config/ lives)."""
    return Path(__file__).resolve().parents[3]


def _run_pipeline_sync(
    session_id: str,
    db_session_id: str,
    target_config_path: Path,
    patterns_dir: Path,
    output_dir: Path,
    categories: list[str] | None,
    profile: dict | None,
    scoring_rules_path: Path | None,
    db_url: str,
) -> None:
    """Synchronous function that runs inside a background thread.

    This function:
    1. Runs the aixis_agent Pipeline (Playwright tests)
    2. Runs the ScoringEngine to produce scores
    3. Writes results back to the web app's async database
    """
    import json

    from aixis_agent.orchestrator.pipeline import Pipeline
    from aixis_agent.orchestrator.session import SessionStore as AgentSessionStore
    from aixis_agent.scoring.engine import ScoringEngine, load_scoring_rules
    from aixis_agent.reporting.builder import build_report

    _update_running(session_id, status="running", phase="test_generation")

    try:
        # -- Phase 1+2: Run the pipeline (generate + execute) ----------------
        pipeline = Pipeline(
            target_config_path=target_config_path,
            patterns_dir=patterns_dir,
            output_dir=output_dir,
            categories=categories,
            dry_run=False,
            max_concurrency=1,
            profile=profile,
        )

        # Create abort event for this session
        abort_event = threading.Event()
        with _lock:
            _abort_events[session_id] = abort_event

        # If target config has wait_for_manual_login, create an event and
        # register it so the /continue endpoint can signal it
        login_event = None
        if pipeline.target_config.wait_for_manual_login:
            login_event = threading.Event()
            with _lock:
                _login_events[session_id] = login_event
            _update_running(session_id, phase="waiting_login")
            try:
                _sync_status_to_web_db(db_url, db_session_id, "waiting_login")
            except Exception:
                logger.exception("Failed to update waiting_login status")

        _update_running(session_id, phase="executing" if not login_event else "waiting_login")

        # --- Progress callback: updates in-memory + DB incrementally ---
        def on_progress(completed: int, total: int, current_case: str, current_category: str):
            _update_running(
                session_id,
                phase="executing",
                completed=completed,
                total=total,
                current_case_id=current_case,
                current_category=current_category,
            )
            # Sync to web DB every 3 tests or on the last test
            if completed % 3 == 0 or completed == total:
                try:
                    _sync_progress_to_web_db(db_url, db_session_id, completed, total)
                except Exception:
                    logger.warning("Failed to sync progress to web DB")

        # Run the async pipeline in a new event loop (we're in a thread)
        loop = asyncio.new_event_loop()
        try:
            agent_session_id = loop.run_until_complete(
                pipeline.run(
                    session_id=session_id,
                    login_event=login_event,
                    abort_event=abort_event,
                    progress_callback=on_progress,
                )
            )
        finally:
            loop.close()

        was_aborted = abort_event.is_set()

        # Clean up events
        with _lock:
            _login_events.pop(session_id, None)
            _abort_events.pop(session_id, None)

        # -- Phase 3: Score results (even if aborted — partial results are valid)
        _update_running(session_id, phase="scoring")
        if was_aborted:
            logger.info("Audit %s was aborted — scoring partial results", session_id)

        agent_db_path = output_dir / f"{session_id}.db"
        agent_store = AgentSessionStore(agent_db_path)

        try:
            agent_session = agent_store.get_session(session_id)
            results = agent_store.get_results(session_id)
            cases = agent_store.get_test_cases(session_id)

            total_planned = agent_session.total_planned if agent_session else len(cases)
            total_executed = agent_session.total_executed if agent_session else len(results)

            # Score
            rules_config = {}
            if scoring_rules_path and scoring_rules_path.exists():
                rules_config = load_scoring_rules(scoring_rules_path)

            report = None
            axis_scores_data = []
            if results:
                engine = ScoringEngine(rules_config)
                report = engine.score_all(results, cases, pipeline.target_config.name)

                for axis_score in report.axis_scores:
                    axis_scores_data.append({
                        "axis": axis_score.axis.value,
                        "axis_name_jp": axis_score.axis_name_jp,
                        "score": axis_score.score,
                        "confidence": axis_score.confidence,
                        "source": axis_score.source.value if hasattr(axis_score.source, 'value') else str(axis_score.source),
                        "details": [d.model_dump() for d in axis_score.details] if axis_score.details else [],
                        "strengths": axis_score.strengths,
                        "risks": axis_score.risks,
                    })

        finally:
            agent_store.close()

        # -- Phase 4: Write back to web DB ------------------------------------
        _update_running(session_id, phase="saving")

        # Gather AI volume metrics from pipeline executor
        ai_volume = {}
        if hasattr(pipeline, 'target_config') and pipeline.target_config.executor_type == "ai_browser":
            ai_volume = {
                "executor_type": "ai_browser",
                "ai_total_steps": sum(
                    (r.metadata or {}).get("ai_steps_taken", 0) for r in results
                ),
                "ai_total_api_calls": sum(
                    (r.metadata or {}).get("ai_calls_used", 0) for r in results
                ),
                "ai_total_input_tokens": sum(
                    (r.metadata or {}).get("ai_tokens_input", 0) for r in results
                ),
                "ai_total_output_tokens": sum(
                    (r.metadata or {}).get("ai_tokens_output", 0) for r in results
                ),
                "ai_screenshots_captured": sum(
                    1 for r in results if r.screenshot_path
                ),
            }

        _sync_results_to_web_db(
            db_url=db_url,
            db_session_id=db_session_id,
            total_planned=total_planned,
            total_executed=total_executed,
            results=results,
            cases=cases,
            axis_scores_data=axis_scores_data,
            report=report,
            ai_volume=ai_volume,
            was_aborted=was_aborted,
        )

        final_status = "aborted" if was_aborted else "completed"
        _update_running(session_id, status=final_status, phase="done")
        logger.info("Audit %s %s", session_id, final_status)

        # Emit webhook & notification events (best-effort, don't fail audit)
        _tool_name = (get_running_audit(session_id) or {}).get("tool_name", "")
        try:
            _emit_audit_events_sync(
                db_url, db_session_id, session_id, _tool_name,
                event_type=f"audit.{final_status}",
                total_planned=total_planned,
                total_executed=total_executed,
            )
        except Exception:
            logger.warning("Failed to emit audit events for %s", session_id)

    except Exception as e:
        logger.exception("Audit %s failed: %s", session_id, e)
        _update_running(session_id, status="failed", error=str(e), phase="error")

        try:
            _sync_failure_to_web_db(db_url, db_session_id, str(e))
        except Exception:
            logger.exception("Failed to update web DB with failure for %s", session_id)

        # Emit failure webhook event (best-effort)
        _tool_name = (get_running_audit(session_id) or {}).get("tool_name", "")
        try:
            _emit_audit_events_sync(
                db_url, db_session_id, session_id, _tool_name,
                event_type="audit.failed",
                error=str(e),
            )
        except Exception:
            logger.warning("Failed to emit failure event for %s", session_id)

    finally:
        # Clean up after a delay so status can be polled
        def _cleanup():
            import time
            time.sleep(300)
            with _lock:
                _running_audits.pop(session_id, None)

        cleanup_thread = threading.Thread(target=_cleanup, daemon=True)
        cleanup_thread.start()


def _emit_audit_events_sync(
    db_url: str,
    db_session_id: str,
    session_id: str,
    tool_name: str,
    event_type: str,
    total_planned: int = 0,
    total_executed: int = 0,
    error: str | None = None,
) -> None:
    """Emit webhook and notification events for audit lifecycle (sync context).

    Creates a temporary async event loop to call the async webhook/notification
    services, since this runs inside a background thread.
    """
    from datetime import datetime as _dt

    payload = {
        "event": event_type,
        "session_id": session_id,
        "db_session_id": db_session_id,
        "tool_name": tool_name,
        "total_planned": total_planned,
        "total_executed": total_executed,
        "timestamp": _dt.utcnow().isoformat(),
    }
    if error:
        payload["error"] = error

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(
            _emit_audit_events_async(db_url, db_session_id, payload, event_type, tool_name)
        )
    except Exception:
        logger.warning("Failed to emit async audit events for %s", session_id)
    finally:
        loop.close()


async def _emit_audit_events_async(
    db_url: str,
    db_session_id: str,
    payload: dict,
    event_type: str,
    tool_name: str,
) -> None:
    """Async helper to emit webhook and notification events."""
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession as _AsyncSession
    from sqlalchemy.orm import sessionmaker
    from .webhook_service import emit_event
    from .notification_service import dispatch_notification

    engine = create_async_engine(db_url, echo=False)
    async_session = sessionmaker(engine, class_=_AsyncSession, expire_on_commit=False)

    async with async_session() as db:
        # Emit webhook event
        try:
            await emit_event(event_type, payload, db)
            await db.commit()
        except Exception:
            logger.warning("Webhook emit failed for %s", event_type)
            await db.rollback()

        # Send in-app notification to audit initiator
        try:
            from sqlalchemy import select, text
            result = await db.execute(
                text("SELECT initiated_by FROM audit_sessions WHERE id = :sid"),
                {"sid": db_session_id},
            )
            row = result.fetchone()
            if row and row[0]:
                user_id = row[0]
                if "completed" in event_type:
                    title_jp = f"監査完了: {tool_name}"
                    title_en = f"Audit completed: {tool_name}"
                elif "failed" in event_type:
                    title_jp = f"監査失敗: {tool_name}"
                    title_en = f"Audit failed: {tool_name}"
                else:
                    title_jp = f"監査更新: {tool_name}"
                    title_en = f"Audit update: {tool_name}"

                await dispatch_notification(
                    db=db,
                    user_id=user_id,
                    event_type=event_type,
                    title=title_en,
                    title_jp=title_jp,
                    link=f"/platform/dashboard/audits/{db_session_id}",
                )
                await db.commit()
        except Exception:
            logger.warning("Notification dispatch failed for %s", event_type)
            await db.rollback()

    await engine.dispose()


def _sync_results_to_web_db(
    db_url: str,
    db_session_id: str,
    total_planned: int,
    total_executed: int,
    results: list,
    cases: list,
    axis_scores_data: list[dict],
    report: Any,
    ai_volume: dict | None = None,
    was_aborted: bool = False,
) -> None:
    """Write agent results back to the web app's SQLAlchemy database (synchronously)."""
    import json
    from sqlalchemy import text
    from datetime import datetime

    sync_url = db_url.replace("sqlite+aiosqlite", "sqlite").replace("postgresql+asyncpg", "postgresql+psycopg2")
    engine = _get_sync_engine(db_url)

    with engine.begin() as conn:
        # Determine final status — respect abort status (don't overwrite)
        if was_aborted:
            new_status = "aborted"
        else:
            has_manual_axes = any(s.get("confidence", 1) == 0 for s in axis_scores_data)
            new_status = "awaiting_manual" if has_manual_axes else "completed"

        completeness = int((total_executed / total_planned * 100)) if total_planned > 0 else 0
        ai_vol = ai_volume or {}

        # Calculate estimated cost from token counts
        # Haiku 4.5 pricing: $1/M input tokens, $5/M output tokens
        total_input_tokens = ai_vol.get("ai_total_input_tokens", 0)
        total_output_tokens = ai_vol.get("ai_total_output_tokens", 0)
        estimated_cost_usd = (total_input_tokens * 1.0 + total_output_tokens * 5.0) / 1_000_000
        # Store as integer cents in DB
        estimated_cost_cents = int(estimated_cost_usd * 100)

        conn.execute(text("""
            UPDATE audit_sessions
            SET status = :status,
                total_planned = :total_planned,
                total_executed = :total_executed,
                started_at = COALESCE(started_at, :now),
                completed_at = :now,
                executor_type = :executor_type,
                ai_total_steps = :ai_total_steps,
                ai_total_api_calls = :ai_total_api_calls,
                ai_total_input_tokens = :ai_total_input_tokens,
                ai_total_output_tokens = :ai_total_output_tokens,
                ai_estimated_cost_usd = :ai_estimated_cost,
                ai_screenshots_captured = :ai_screenshots,
                completeness_ratio = :completeness
            WHERE id = :session_id
        """), {
            "status": new_status,
            "total_planned": total_planned,
            "total_executed": total_executed,
            "now": datetime.utcnow().isoformat(),
            "session_id": db_session_id,
            "executor_type": ai_vol.get("executor_type", "playwright"),
            "ai_total_steps": ai_vol.get("ai_total_steps", 0),
            "ai_total_api_calls": ai_vol.get("ai_total_api_calls", 0),
            "ai_total_input_tokens": ai_vol.get("ai_total_input_tokens", 0),
            "ai_total_output_tokens": ai_vol.get("ai_total_output_tokens", 0),
            "ai_estimated_cost": estimated_cost_cents,
            "ai_screenshots": ai_vol.get("ai_screenshots_captured", 0),
            "completeness": completeness,
        })

        # Detect DB dialect for portable upsert syntax
        _is_sqlite = "sqlite" in sync_url

        # Store test cases (upsert: portable across SQLite and PostgreSQL)
        for case in cases:
            params = {
                "id": case.id,
                "session_id": db_session_id,
                "category": case.category.value if hasattr(case.category, 'value') else str(case.category),
                "prompt": case.prompt,
                "metadata": json.dumps(case.metadata, ensure_ascii=False) if case.metadata else "{}",
                "expected": json.dumps(case.expected_behaviors, ensure_ascii=False) if case.expected_behaviors else "[]",
                "failures": json.dumps(case.failure_indicators, ensure_ascii=False) if case.failure_indicators else "[]",
                "tags": json.dumps(case.tags, ensure_ascii=False) if case.tags else "[]",
            }
            if _is_sqlite:
                conn.execute(text("""
                    INSERT OR REPLACE INTO db_test_cases
                    (id, session_id, category, prompt, metadata_json, expected_behaviors, failure_indicators, tags)
                    VALUES (:id, :session_id, :category, :prompt, :metadata, :expected, :failures, :tags)
                """), params)
            else:
                conn.execute(text("""
                    INSERT INTO db_test_cases
                    (id, session_id, category, prompt, metadata_json, expected_behaviors, failure_indicators, tags)
                    VALUES (:id, :session_id, :category, :prompt, :metadata, :expected, :failures, :tags)
                    ON CONFLICT (id) DO UPDATE SET
                        category = EXCLUDED.category,
                        prompt = EXCLUDED.prompt,
                        metadata_json = EXCLUDED.metadata_json,
                        expected_behaviors = EXCLUDED.expected_behaviors,
                        failure_indicators = EXCLUDED.failure_indicators,
                        tags = EXCLUDED.tags
                """), params)

        # Store test results
        for result in results:
            meta = result.metadata or {}
            conn.execute(text("""
                INSERT INTO db_test_results
                (session_id, test_case_id, category, prompt_sent, response_raw,
                 response_time_ms, error, screenshot_path, executed_at, metadata_json,
                 ai_steps_taken, ai_calls_used, ai_tokens_input, ai_tokens_output)
                VALUES (:session_id, :test_case_id, :category, :prompt, :response,
                        :time_ms, :error, :screenshot, :executed_at, :metadata,
                        :ai_steps, :ai_calls, :ai_tok_in, :ai_tok_out)
            """), {
                "session_id": db_session_id,
                "test_case_id": result.test_case_id,
                "category": result.category.value if hasattr(result.category, 'value') else str(result.category),
                "prompt": result.prompt_sent,
                "response": result.response_raw,
                "time_ms": int(result.response_time_ms),
                "error": result.error,
                "screenshot": result.screenshot_path,
                "executed_at": result.timestamp.isoformat() if result.timestamp else datetime.utcnow().isoformat(),
                "metadata": json.dumps(meta, ensure_ascii=False) if meta else "{}",
                "ai_steps": meta.get("ai_steps_taken", 0),
                "ai_calls": meta.get("ai_calls_used", 0),
                "ai_tok_in": meta.get("ai_tokens_input", 0),
                "ai_tok_out": meta.get("ai_tokens_output", 0),
            })

        # Store axis scores — fetch tool_id ONCE (not per-axis)
        import uuid
        row = conn.execute(text(
            "SELECT tool_id FROM audit_sessions WHERE id = :sid"
        ), {"sid": db_session_id}).fetchone()
        tool_id = row[0] if row else ""

        for score_data in axis_scores_data:
            params = {
                "id": str(uuid.uuid4()),
                "session_id": db_session_id,
                "tool_id": tool_id,
                "axis": score_data["axis"],
                "axis_name_jp": score_data["axis_name_jp"],
                "score": score_data["score"],
                "confidence": score_data["confidence"],
                "source": score_data["source"],
                "details": json.dumps(score_data["details"], ensure_ascii=False, default=str),
                "strengths": json.dumps(score_data["strengths"], ensure_ascii=False),
                "risks": json.dumps(score_data["risks"], ensure_ascii=False),
                "scored_at": datetime.utcnow().isoformat(),
            }
            if _is_sqlite:
                conn.execute(text("""
                    INSERT OR REPLACE INTO axis_scores
                    (id, session_id, tool_id, axis, axis_name_jp, score, confidence,
                     source, details, strengths, risks, scored_at)
                    VALUES (:id, :session_id, :tool_id, :axis, :axis_name_jp, :score,
                            :confidence, :source, :details, :strengths, :risks, :scored_at)
                """), params)
            else:
                conn.execute(text("""
                    INSERT INTO axis_scores
                    (id, session_id, tool_id, axis, axis_name_jp, score, confidence,
                     source, details, strengths, risks, scored_at)
                    VALUES (:id, :session_id, :tool_id, :axis, :axis_name_jp, :score,
                            :confidence, :source, :details, :strengths, :risks, :scored_at)
                    ON CONFLICT (id) DO UPDATE SET
                        score = EXCLUDED.score,
                        confidence = EXCLUDED.confidence,
                        source = EXCLUDED.source,
                        details = EXCLUDED.details,
                        strengths = EXCLUDED.strengths,
                        risks = EXCLUDED.risks,
                        scored_at = EXCLUDED.scored_at
                """), params)


def _get_sync_engine(db_url: str):
    """Get or create a cached sync engine to avoid repeated create_engine() calls."""
    from sqlalchemy import create_engine

    sync_url = db_url.replace("sqlite+aiosqlite", "sqlite").replace("postgresql+asyncpg", "postgresql+psycopg2")
    # Use module-level cache to reuse engine across calls within the same thread
    cache_key = "_cached_sync_engine"
    existing = getattr(_get_sync_engine, cache_key, None)
    if existing and existing[0] == sync_url:
        return existing[1]
    if existing:
        existing[1].dispose()
    engine = create_engine(sync_url, pool_pre_ping=True)
    setattr(_get_sync_engine, cache_key, (sync_url, engine))
    return engine


def _sync_progress_to_web_db(db_url: str, db_session_id: str, completed: int, total: int) -> None:
    """Incrementally update progress counts in the web DB during execution."""
    from sqlalchemy import text

    engine = _get_sync_engine(db_url)
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE audit_sessions
            SET total_executed = :executed,
                total_planned = :planned
            WHERE id = :session_id
        """), {
            "executed": completed,
            "planned": total,
            "session_id": db_session_id,
        })


def _sync_status_to_web_db(db_url: str, db_session_id: str, new_status: str) -> None:
    """Update the web DB audit session status."""
    from sqlalchemy import text

    engine = _get_sync_engine(db_url)
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE audit_sessions SET status = :status WHERE id = :session_id
        """), {"status": new_status, "session_id": db_session_id})


def _sync_failure_to_web_db(db_url: str, db_session_id: str, error_msg: str) -> None:
    """Update the web DB to mark the audit as failed."""
    from sqlalchemy import text
    from datetime import datetime

    engine = _get_sync_engine(db_url)
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE audit_sessions
            SET status = 'failed',
                error_message = :error,
                completed_at = :now
            WHERE id = :session_id
        """), {
            "error": error_msg[:2000],
            "now": datetime.utcnow().isoformat(),
            "session_id": db_session_id,
        })


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tool_name_to_slug(name: str) -> str:
    """Convert a tool name (JP or EN) to a slug for config file lookup."""
    import re
    slug = name.strip().lower()
    slug = re.sub(r'[^a-z0-9_-]', '', slug)
    return slug or "unknown"


def _make_temp_config_with_manual_login(config_path: Path) -> Path:
    """Read a YAML config file and create a temp copy with wait_for_manual_login=true, headless=false."""
    import tempfile
    import yaml

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if isinstance(data, dict):
            data["wait_for_manual_login"] = True
            data["headless"] = False
            tmp = tempfile.NamedTemporaryFile(
                mode="w", suffix=".yaml", delete=False, encoding="utf-8"
            )
            yaml.dump(data, tmp, allow_unicode=True, default_flow_style=False)
            tmp.close()
            return Path(tmp.name)
    except Exception:
        pass
    return config_path


# ---------------------------------------------------------------------------
# Public API called by FastAPI endpoints
# ---------------------------------------------------------------------------

def start_audit(
    session_id: str,
    db_session_id: str,
    tool_name: str,
    target_config_yaml: str | None = None,
    target_config_name: str | None = None,
    profile_id: str | None = None,
    categories: list[str] | None = None,
) -> dict[str, Any]:
    """Start an audit in a background thread."""
    with _lock:
        if session_id in _running_audits:
            return {"error": "この監査セッションは既に実行中です", "session_id": session_id}

    base_dir = _get_base_dir()
    config_dir = base_dir / settings.config_dir
    output_dir = base_dir / settings.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    # ------- Pre-flight validation -------
    import yaml as _yaml
    _preflight_yaml = target_config_yaml
    if not _preflight_yaml and target_config_name:
        _pf_path = config_dir / "targets" / f"{target_config_name}.yaml"
        if _pf_path.exists():
            _preflight_yaml = _pf_path.read_text(encoding="utf-8")
    if not _preflight_yaml:
        _tool_slug = _tool_name_to_slug(tool_name)
        _pf_path = config_dir / "targets" / f"{_tool_slug}.yaml"
        if _pf_path.exists():
            _preflight_yaml = _pf_path.read_text(encoding="utf-8")

    if _preflight_yaml:
        try:
            _pf_data = _yaml.safe_load(_preflight_yaml)
            if isinstance(_pf_data, dict) and _pf_data.get("executor_type") == "ai_browser":
                import os as _os
                if not _os.environ.get("AIXIS_ANTHROPIC_API_KEY", ""):
                    return {
                        "error": "AIブラウザ実行にはAnthropicのAPIキーが必要です。"
                        "設定画面またはAIXIS_ANTHROPIC_API_KEY環境変数で設定してください。"
                    }
        except Exception:
            pass

    # Resolve target config
    if target_config_yaml:
        import tempfile
        import yaml
        try:
            config_data = yaml.safe_load(target_config_yaml)
            if isinstance(config_data, dict):
                config_data["wait_for_manual_login"] = True
                config_data["headless"] = False
                target_config_yaml = yaml.dump(config_data, allow_unicode=True, default_flow_style=False)
        except Exception:
            pass
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, encoding="utf-8"
        )
        tmp.write(target_config_yaml)
        tmp.close()
        target_config_path = Path(tmp.name)
    elif target_config_name:
        target_config_path = config_dir / "targets" / f"{target_config_name}.yaml"
        if not target_config_path.exists():
            return {"error": f"ターゲット設定ファイルが見つかりません: {target_config_name}"}
        target_config_path = _make_temp_config_with_manual_login(target_config_path)
    else:
        tool_slug = _tool_name_to_slug(tool_name)
        file_config_path = config_dir / "targets" / f"{tool_slug}.yaml"
        if file_config_path.exists():
            logger.info("Using file-based target config: %s", file_config_path)
            target_config_path = _make_temp_config_with_manual_login(file_config_path)
        else:
            return {"error": "ターゲット設定が指定されていません。ツール管理画面でターゲット設定を登録するか、config/targets/ にYAMLファイルを配置してください。"}

    patterns_dir = config_dir / "patterns"
    scoring_rules_path = config_dir / "scoring" / "scoring_rules.yaml"

    # Resolve profile
    profile = None
    if profile_id:
        from aixis_agent.profiles.registry import get_profile
        profiles_dir = config_dir / "profiles"
        profile = get_profile(profile_id, profiles_dir)
        if profile and not categories:
            from aixis_agent.profiles.registry import get_categories_for_profile
            categories = get_categories_for_profile(profile)

    # Register in-memory tracking
    with _lock:
        _running_audits[session_id] = {
            "session_id": session_id,
            "db_session_id": db_session_id,
            "tool_name": tool_name,
            "status": "starting",
            "phase": "init",
            "error": None,
            "started_at": datetime.utcnow().isoformat(),
            "completed": 0,
            "total": 0,
            "current_category": "",
        }

    # Start background thread
    thread = threading.Thread(
        target=_run_pipeline_sync,
        args=(
            session_id,
            db_session_id,
            target_config_path,
            patterns_dir,
            output_dir,
            categories,
            profile,
            scoring_rules_path,
            settings.database_url,
        ),
        daemon=True,
        name=f"audit-{session_id}",
    )
    thread.start()

    return {
        "session_id": session_id,
        "db_session_id": db_session_id,
        "status": "starting",
        "message": f"監査を開始しました: {tool_name}",
    }
