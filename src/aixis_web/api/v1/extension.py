"""Chrome extension API — manages audit sessions, observations, and scoring.

The Chrome extension records human tester interactions with AI tools and
uploads structured observation data for LLM-based scoring.

Auth: API key with 'agent:write' scope (X-API-Key header).
"""

import base64
import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..deps import get_db
from .agent import require_agent_key
from ...db.models.user import User
from ...schemas.extension import (
    ExtensionSessionCreate,
    ExtensionSessionResponse,
    ObservationResponse,
    ObservationUpload,
    SessionProgressResponse,
    TestCaseOut,
    ToolListItem,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# Screenshots storage base path
_SCREENSHOTS_DIR = Path(__file__).resolve().parents[2] / "static" / "screenshots" / "extension"


# ---------------------------------------------------------------------------
# POST /sessions — Create extension audit session
# ---------------------------------------------------------------------------

@router.post("/sessions", response_model=ExtensionSessionResponse)
async def create_extension_session(
    body: ExtensionSessionCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_agent_key),
):
    """Create a new audit session for the Chrome extension.

    In protocol mode, generates test cases from YAML patterns and returns them.
    In freeform mode, creates session without pre-generated test cases.
    """
    session_id = str(uuid.uuid4())
    now = datetime.utcnow()

    # Generate session code
    hash_input = f"{now.isoformat()}-{session_id}"
    short_hash = hashlib.sha256(hash_input.encode()).hexdigest()[:8].upper()
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    session_code = f"AX-{date_str}-{short_hash}"

    # Verify tool exists
    result = await db.execute(
        text("SELECT id, name FROM tools WHERE id = :tid"),
        {"tid": body.tool_id},
    )
    tool_row = result.fetchone()
    if not tool_row:
        raise HTTPException(404, f"ツールが見つかりません: {body.tool_id}")

    # Generate test cases in memory first (don't insert yet — FK requires session)
    test_cases_out: list[TestCaseOut] = []
    cases = []
    total_planned = 0

    if body.recording_mode == "protocol":
        try:
            from aixis_agent.patterns.generator import generate_all

            config_dir = Path(__file__).resolve().parents[4] / "config"
            patterns_dir = config_dir / "patterns"

            # Resolve categories from profile if not explicitly provided
            categories = body.categories
            if not categories and body.profile_id:
                try:
                    from aixis_agent.profiles.registry import (
                        get_categories_for_profile,
                        get_profile,
                    )
                    profiles_dir = config_dir / "profiles"
                    profile = get_profile(body.profile_id, profiles_dir)
                    if profile:
                        categories = get_categories_for_profile(profile)
                except Exception:
                    pass

            cases = generate_all(patterns_dir, categories)

            # Sort by priority for optimal coverage
            from aixis_agent.orchestrator.pipeline import sort_by_priority
            cases = sort_by_priority(cases)

            # Cap test cases for manual testing (human testers can't do 500+)
            max_cases = body.max_cases or 30
            if len(cases) > max_cases:
                cases = cases[:max_cases]

            total_planned = len(cases)

        except Exception as e:
            logger.warning("Test case generation failed: %s", e)

    # 1. Create session FIRST (test cases have FK to audit_sessions)
    await db.execute(text("""
        INSERT INTO audit_sessions
        (id, session_code, tool_id, profile_id, status, initiated_by,
         created_at, executor_type, total_planned, total_executed)
        VALUES (:id, :code, :tool_id, :profile_id, :status, :initiated_by,
                :now, :executor_type, :total_planned, 0)
    """), {
        "id": session_id,
        "code": session_code,
        "tool_id": body.tool_id,
        "profile_id": body.profile_id or "",
        "status": "running",
        "initiated_by": user.id,
        "now": now,
        "executor_type": "extension",
        "total_planned": total_planned,
    })

    # 2. Insert test cases AFTER session exists
    for case in cases:
        cat_val = case.category.value if hasattr(case.category, "value") else str(case.category)
        try:
            await db.execute(text("""
                INSERT INTO db_test_cases
                (id, session_id, category, prompt, metadata_json,
                 expected_behaviors, failure_indicators, tags)
                VALUES (:id, :session_id, :category, :prompt, :metadata,
                        :expected, :failures, :tags)
                ON CONFLICT (id) DO NOTHING
            """), {
                "id": case.id,
                "session_id": session_id,
                "category": cat_val,
                "prompt": case.prompt,
                "metadata": json.dumps(case.metadata, ensure_ascii=False),
                "expected": json.dumps(case.expected_behaviors, ensure_ascii=False),
                "failures": json.dumps(case.failure_indicators, ensure_ascii=False),
                "tags": json.dumps(case.tags, ensure_ascii=False),
            })
        except Exception as e:
            logger.warning("Failed to insert test case %s: %s", case.id, e)
            continue

        test_cases_out.append(TestCaseOut(
            id=case.id,
            category=cat_val,
            prompt=case.prompt,
            expected_behaviors=case.expected_behaviors,
            failure_indicators=case.failure_indicators,
            tags=case.tags,
            metadata=case.metadata,
        ))

    await db.commit()

    logger.info(
        "Extension session created: %s (tool=%s, mode=%s, cases=%d, user=%s)",
        session_code, body.tool_id, body.recording_mode, total_planned, user.email,
    )
    return ExtensionSessionResponse(
        session_id=session_id,
        session_code=session_code,
        tool_id=body.tool_id,
        status="running",
        recording_mode=body.recording_mode,
        test_cases=test_cases_out,
    )


# ---------------------------------------------------------------------------
# GET /sessions/{id}/test-cases — Get test cases for session
# ---------------------------------------------------------------------------

@router.get("/sessions/{session_id}/test-cases", response_model=list[TestCaseOut])
async def get_session_test_cases(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_agent_key),
):
    """Return test cases for a session."""
    result = await db.execute(
        text("""
            SELECT id, category, prompt, metadata_json,
                   expected_behaviors, failure_indicators, tags
            FROM db_test_cases
            WHERE session_id = :sid
            ORDER BY id
        """),
        {"sid": session_id},
    )
    rows = result.fetchall()
    if not rows:
        raise HTTPException(404, "テストケースが見つかりません")

    cases = []
    for row in rows:
        cases.append(TestCaseOut(
            id=row[0],
            category=row[1],
            prompt=row[2],
            metadata=json.loads(row[3]) if row[3] else {},
            expected_behaviors=json.loads(row[4]) if row[4] else [],
            failure_indicators=json.loads(row[5]) if row[5] else [],
            tags=json.loads(row[6]) if row[6] else [],
        ))
    return cases


# ---------------------------------------------------------------------------
# POST /sessions/{id}/observations — Upload observation
# ---------------------------------------------------------------------------

@router.post("/sessions/{session_id}/observations", response_model=ObservationResponse)
async def upload_observation(
    session_id: str,
    body: ObservationUpload,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_agent_key),
):
    """Upload a single observation (input/output pair) from the Chrome extension."""
    # Verify session exists
    result = await db.execute(
        text("SELECT id, tool_id, status, total_executed FROM audit_sessions WHERE id = :sid"),
        {"sid": session_id},
    )
    session_row = result.fetchone()
    if not session_row:
        raise HTTPException(404, f"セッションが見つかりません: {session_id}")

    if session_row[2] not in ("running", "pending"):
        raise HTTPException(400, f"セッションは現在 {session_row[2]} 状態です。観察データを追加できません。")

    now = datetime.utcnow()

    # Get actual observation count (including manual screenshots) for sequence numbering
    count_result = await db.execute(
        text("SELECT COUNT(*) FROM db_test_results WHERE session_id = :sid"),
        {"sid": session_id},
    )
    observation_count = count_result.scalar() or 0
    sequence_number = observation_count + 1

    # Handle screenshot
    screenshot_path = None
    if body.screenshot_base64:
        try:
            img_data = base64.b64decode(body.screenshot_base64)
            session_dir = _SCREENSHOTS_DIR / session_id
            session_dir.mkdir(parents=True, exist_ok=True)
            img_path = session_dir / f"{sequence_number:04d}.png"
            img_path.write_bytes(img_data)
            screenshot_path = f"/static/screenshots/extension/{session_id}/{sequence_number:04d}.png"
        except Exception as e:
            logger.warning("Screenshot save failed: %s", e)

    # Determine observation type: manual screenshot vs test observation
    is_manual_screenshot = (not body.test_case_id) and (
        body.metadata and (
            body.metadata.get("type") == "manual_screenshot"
            or body.metadata.get("capture_type") == "manual_screenshot"
        )
    )

    test_case_id = body.test_case_id
    category = "freeform"

    if is_manual_screenshot:
        # Manual screenshots: store as supplementary evidence, don't count as test
        test_case_id = f"manual-{session_id[:8]}-{sequence_number:04d}"
        category = "manual_screenshot"
        await db.execute(text("""
            INSERT INTO db_test_cases
            (id, session_id, category, prompt, metadata_json,
             expected_behaviors, failure_indicators, tags)
            VALUES (:id, :session_id, :category, :prompt, :metadata,
                    :expected, :failures, :tags)
            ON CONFLICT (id) DO NOTHING
        """), {
            "id": test_case_id,
            "session_id": session_id,
            "category": "manual_screenshot",
            "prompt": body.prompt_text or "手動スクリーンショット",
            "metadata": "{}",
            "expected": "[]",
            "failures": "[]",
            "tags": '["manual_screenshot"]',
        })
    elif test_case_id:
        # Protocol mode: look up the actual category from the test case
        tc_result = await db.execute(
            text("SELECT category FROM db_test_cases WHERE id = :tid AND session_id = :sid"),
            {"tid": test_case_id, "sid": session_id},
        )
        tc_row = tc_result.fetchone()
        if tc_row:
            category = tc_row[0]
        else:
            # test_case_id provided but not found in DB — store as-is with protocol category
            logger.warning("Test case %s not found in DB for session %s, creating inline", test_case_id, session_id)
            category = body.metadata.get("category", "protocol") if body.metadata else "protocol"
            await db.execute(text("""
                INSERT INTO db_test_cases
                (id, session_id, category, prompt, metadata_json,
                 expected_behaviors, failure_indicators, tags)
                VALUES (:id, :session_id, :category, :prompt, :metadata,
                        :expected, :failures, :tags)
                ON CONFLICT (id) DO NOTHING
            """), {
                "id": test_case_id,
                "session_id": session_id,
                "category": category,
                "prompt": body.prompt_text or "",
                "metadata": "{}",
                "expected": "[]",
                "failures": "[]",
                "tags": '["protocol"]',
            })
    else:
        # Freeform mode: create synthetic test case
        test_case_id = f"freeform-{session_id[:8]}-{sequence_number:04d}"
        await db.execute(text("""
            INSERT INTO db_test_cases
            (id, session_id, category, prompt, metadata_json,
             expected_behaviors, failure_indicators, tags)
            VALUES (:id, :session_id, :category, :prompt, :metadata,
                    :expected, :failures, :tags)
            ON CONFLICT (id) DO NOTHING
        """), {
            "id": test_case_id,
            "session_id": session_id,
            "category": "freeform",
            "prompt": body.prompt_text or "",
            "metadata": "{}",
            "expected": "[]",
            "failures": "[]",
            "tags": '["freeform"]',
        })

    # Store as DBTestResult
    await db.execute(text("""
        INSERT INTO db_test_results
        (session_id, test_case_id, category, prompt_sent, response_raw,
         response_time_ms, error, screenshot_path, page_url, executed_at, metadata_json)
        VALUES (:session_id, :test_case_id, :category, :prompt, :response,
                :time_ms, :error, :screenshot, :page_url, :executed_at, :metadata)
    """), {
        "session_id": session_id,
        "test_case_id": test_case_id,
        "category": category,
        "prompt": body.prompt_text,
        "response": body.response_text,
        "time_ms": body.response_time_ms,
        "error": None,
        "screenshot": screenshot_path,
        "page_url": body.page_url,
        "executed_at": now,
        "metadata": json.dumps(body.metadata, ensure_ascii=False) if body.metadata else "{}",
    })

    # Use sequence number as observation ID
    obs_id = sequence_number

    # Update session progress — only count actual tests, not manual screenshots
    if not is_manual_screenshot:
        await db.execute(text("""
            UPDATE audit_sessions
            SET total_executed = COALESCE(total_executed, 0) + 1,
                started_at = COALESCE(started_at, :now)
            WHERE id = :sid
        """), {
            "now": now,
            "sid": session_id,
        })
    else:
        # Still update started_at if needed
        await db.execute(text("""
            UPDATE audit_sessions
            SET started_at = COALESCE(started_at, :now)
            WHERE id = :sid
        """), {
            "now": now,
            "sid": session_id,
        })

    await db.commit()

    return ObservationResponse(
        observation_id=obs_id,
        sequence_number=sequence_number,
    )


# ---------------------------------------------------------------------------
# POST /sessions/{id}/complete — Mark session complete, trigger scoring
# ---------------------------------------------------------------------------

@router.post("/sessions/{session_id}/complete")
async def complete_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_agent_key),
):
    """Mark session as complete and trigger LLM scoring."""
    result = await db.execute(
        text("SELECT id, tool_id, status, total_planned, total_executed FROM audit_sessions WHERE id = :sid"),
        {"sid": session_id},
    )
    session_row = result.fetchone()
    if not session_row:
        raise HTTPException(404, f"セッションが見つかりません: {session_id}")

    if session_row[2] not in ("running", "pending"):
        raise HTTPException(400, f"セッションは既に {session_row[2]} 状態です")

    tool_id = session_row[1]
    total_planned = session_row[3] or 0
    total_executed = session_row[4] or 0
    completeness = int(total_executed / total_planned * 100) if total_planned > 0 else 100

    now = datetime.utcnow()

    # Update session to "scoring" status
    await db.execute(text("""
        UPDATE audit_sessions
        SET status = 'scoring',
            completed_at = :now,
            completeness_ratio = :completeness
        WHERE id = :sid
    """), {
        "now": now,
        "completeness": completeness,
        "sid": session_id,
    })
    await db.commit()

    # Trigger LLM scoring as asyncio task (same event loop, avoids cross-loop DB issues)
    import asyncio
    asyncio.ensure_future(_run_llm_scoring_background(session_id, tool_id))

    logger.info("Session %s marked complete, scoring started (observations=%d)", session_id, total_executed)
    return {
        "status": "scoring",
        "session_id": session_id,
        "total_executed": total_executed,
        "completeness_ratio": completeness,
        "message": "LLMスコアリングをバックグラウンドで開始しました",
    }


async def _run_llm_scoring_background(session_id: str, tool_id: str) -> None:
    """Run LLM scoring as a background asyncio task (same event loop as FastAPI)."""
    from ...db.base import async_session

    try:
        from ...services.audit_runner import register_job, update_job, cleanup_job
        register_job(session_id, phase="llm_scoring", tool_id=tool_id)
    except Exception:
        pass

    try:
        from ...services.llm_scorer import LLMScorer

        async with async_session() as db:
            scorer = LLMScorer()
            await scorer.score_session(session_id, tool_id, db)

            await db.execute(
                text("UPDATE audit_sessions SET status = 'completed' WHERE id = :sid"),
                {"sid": session_id},
            )
            await db.commit()

        logger.info("LLM scoring completed for session %s", session_id)

        try:
            from ...services.audit_runner import update_job, cleanup_job
            update_job(session_id, status="completed", phase="done")
            cleanup_job(session_id)
        except Exception:
            pass

    except Exception as e:
        logger.exception("LLM scoring failed for session %s: %s", session_id, e)

        # Update session status to failed
        try:
            async with async_session() as db:
                await db.execute(text("""
                    UPDATE audit_sessions
                    SET status = 'failed', error_message = :error
                    WHERE id = :sid
                """), {"error": str(e)[:2000], "sid": session_id})
                await db.commit()
        except Exception:
            logger.error("Failed to update session status for %s", session_id)

        try:
            from ...services.audit_runner import update_job, cleanup_job
            update_job(session_id, status="failed", error=str(e))
            cleanup_job(session_id)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# GET /sessions/{id}/progress — Get session progress
# ---------------------------------------------------------------------------

@router.get("/sessions/{session_id}/progress", response_model=SessionProgressResponse)
async def get_session_progress(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_agent_key),
):
    """Return current session progress."""
    result = await db.execute(
        text("""
            SELECT id, session_code, status, total_planned, total_executed,
                   completeness_ratio, executor_type
            FROM audit_sessions WHERE id = :sid
        """),
        {"sid": session_id},
    )
    row = result.fetchone()
    if not row:
        raise HTTPException(404, f"セッションが見つかりません: {session_id}")

    return SessionProgressResponse(
        session_id=row[0],
        session_code=row[1],
        status=row[2],
        total_planned=row[3] or 0,
        total_executed=row[4] or 0,
        completeness_ratio=row[5] or 0,
        recording_mode="protocol" if (row[3] or 0) > 0 else "freeform",
    )


# ---------------------------------------------------------------------------
# GET /tools — List available tools for extension UI
# ---------------------------------------------------------------------------

@router.get("/tools", response_model=list[ToolListItem])
async def list_tools_for_extension(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_agent_key),
):
    """Return tools available for auditing."""
    try:
        result = await db.execute(text("""
            SELECT t.id, t.name, t.name_jp, t.vendor,
                   COALESCE(tc.name_jp, '') as category_name_jp
            FROM tools t
            LEFT JOIN tool_categories tc ON t.category_id = tc.id
            WHERE t.is_active = true
            ORDER BY t.name_jp
        """))
        rows = result.fetchall()
    except Exception as e:
        logger.error("Failed to query tools with category join: %s", e)
        # Rollback the failed transaction before retrying
        await db.rollback()
        try:
            result = await db.execute(text(
                "SELECT id, name, name_jp, vendor, '' FROM tools WHERE is_active = true ORDER BY name_jp"
            ))
            rows = result.fetchall()
        except Exception as e2:
            logger.error("Fallback tool query also failed: %s", e2)
            await db.rollback()
            raise HTTPException(500, f"ツール一覧の取得に失敗しました: {e2}")

    return [
        ToolListItem(
            id=row[0],
            name=row[1] or "",
            name_jp=row[2] or "",
            vendor=row[3] or "",
            category_name_jp=row[4] or "",
        )
        for row in rows
    ]
