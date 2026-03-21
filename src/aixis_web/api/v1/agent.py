"""Remote agent API — allows a local agent to create sessions and upload results.

The local agent runs the browser on the user's machine (visible, with manual login)
and reports results back to the Railway-hosted platform via these endpoints.

Auth: API key with 'agent:write' scope (X-API-Key header).
"""
import json
import uuid
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..deps import get_api_key_user, get_db
from ...db.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Auth dependency: require API key with agent:write scope
# ---------------------------------------------------------------------------

async def require_agent_key(
    request: Request,
    user: User = Depends(get_api_key_user),
) -> User:
    """Require an API key with 'agent:write' scope."""
    scopes = getattr(request.state, "api_key_scopes", []) or []
    # Allow if scopes include agent:write, or if user is admin/analyst
    if "agent:write" in scopes or user.role in ("admin", "analyst", "auditor"):
        return user
    raise HTTPException(403, "API key requires 'agent:write' scope")


# ---------------------------------------------------------------------------
# Request/Response schemas
# ---------------------------------------------------------------------------

class AgentSessionCreate(BaseModel):
    tool_id: str
    profile_id: str = ""
    target_config_name: str = ""


class AgentSessionResponse(BaseModel):
    session_id: str
    session_code: str
    tool_id: str
    status: str


class TestCasePayload(BaseModel):
    id: str
    category: str
    prompt: str
    metadata: dict = Field(default_factory=dict)
    expected_behaviors: list = Field(default_factory=list)
    failure_indicators: list = Field(default_factory=list)
    tags: list = Field(default_factory=list)


class TestResultPayload(BaseModel):
    test_case_id: str
    category: str
    prompt_sent: str
    response_raw: str | None = None
    response_time_ms: float = 0
    error: str | None = None
    screenshot_path: str | None = None
    executed_at: str | None = None
    ai_steps_taken: int = 0
    ai_calls_used: int = 0
    ai_tokens_input: int = 0
    ai_tokens_output: int = 0


class AxisScorePayload(BaseModel):
    axis: str
    axis_name_jp: str = ""
    score: float = 0
    confidence: float = 0
    source: str = "auto"
    details: dict = Field(default_factory=dict)
    strengths: list = Field(default_factory=list)
    risks: list = Field(default_factory=list)


class AgentResultsUpload(BaseModel):
    total_planned: int = 0
    total_executed: int = 0
    was_aborted: bool = False
    test_cases: list[TestCasePayload] = Field(default_factory=list)
    test_results: list[TestResultPayload] = Field(default_factory=list)
    axis_scores: list[AxisScorePayload] = Field(default_factory=list)
    volume_metrics: dict = Field(default_factory=dict)
    reliability_data: dict | None = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/local-script")
async def download_local_agent_script():
    """Download the local agent script."""
    script_path = Path(__file__).resolve().parents[3] / "scripts" / "aixis_local_agent.py"
    if not script_path.exists():
        raise HTTPException(404, "ローカルエージェントスクリプトが見つかりません")
    return FileResponse(script_path, media_type="text/x-python", filename="aixis_local_agent.py")


@router.post("/sessions", response_model=AgentSessionResponse)
async def create_agent_session(
    body: AgentSessionCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_agent_key),
):
    """Create a new audit session for the remote agent."""
    session_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    # Generate session code (matches format used by audit_runner)
    import hashlib
    hash_input = f"{now}-{session_id}"
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
        raise HTTPException(404, f"Tool not found: {body.tool_id}")

    # Create session
    await db.execute(text("""
        INSERT INTO audit_sessions
        (id, session_code, tool_id, profile_id, status, initiated_by, created_at)
        VALUES (:id, :code, :tool_id, :profile_id, :status, :initiated_by, :now)
    """), {
        "id": session_id,
        "code": session_code,
        "tool_id": body.tool_id,
        "profile_id": body.profile_id or None,
        "status": "running",
        "initiated_by": user.id,
        "now": now,
    })
    await db.commit()

    logger.info("Agent session created: %s (tool=%s, user=%s)", session_code, body.tool_id, user.email)
    return AgentSessionResponse(
        session_id=session_id,
        session_code=session_code,
        tool_id=body.tool_id,
        status="running",
    )


@router.post("/sessions/{session_id}/results")
async def upload_agent_results(
    session_id: str,
    body: AgentResultsUpload,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_agent_key),
):
    """Upload audit results from the remote agent."""
    # Verify session exists and belongs to user
    result = await db.execute(
        text("SELECT id, tool_id, status FROM audit_sessions WHERE id = :sid"),
        {"sid": session_id},
    )
    session_row = result.fetchone()
    if not session_row:
        raise HTTPException(404, f"Session not found: {session_id}")

    tool_id = session_row[1]
    now = datetime.now(timezone.utc).isoformat()

    # Determine status
    if body.was_aborted:
        new_status = "aborted"
    else:
        has_manual = any(s.confidence == 0 for s in body.axis_scores)
        new_status = "awaiting_manual" if has_manual else "completed"

    completeness = int(body.total_executed / body.total_planned * 100) if body.total_planned > 0 else 0
    vol = body.volume_metrics

    # Calculate cost
    ti = vol.get("ai_total_input_tokens", 0)
    to_ = vol.get("ai_total_output_tokens", 0)
    cost_cents = int((ti * 1.0 + to_ * 5.0) / 1_000_000 * 100)

    # Update session
    await db.execute(text("""
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
            completeness_ratio = :completeness,
            reliability_scores = :reliability
        WHERE id = :session_id
    """), {
        "status": new_status,
        "total_planned": body.total_planned,
        "total_executed": body.total_executed,
        "now": now,
        "session_id": session_id,
        "executor_type": vol.get("executor_type", "ai_browser"),
        "ai_total_steps": vol.get("ai_total_steps", 0),
        "ai_total_api_calls": vol.get("ai_total_api_calls", 0),
        "ai_total_input_tokens": ti,
        "ai_total_output_tokens": to_,
        "ai_estimated_cost": cost_cents,
        "ai_screenshots": vol.get("ai_screenshots_captured", 0),
        "completeness": completeness,
        "reliability": json.dumps(body.reliability_data, ensure_ascii=False) if body.reliability_data else None,
    })

    # Store test cases
    for case in body.test_cases:
        await db.execute(text("""
            INSERT INTO db_test_cases
            (id, session_id, category, prompt, metadata_json, expected_behaviors, failure_indicators, tags)
            VALUES (:id, :session_id, :category, :prompt, :metadata, :expected, :failures, :tags)
            ON CONFLICT (id) DO UPDATE SET
                category = EXCLUDED.category, prompt = EXCLUDED.prompt,
                metadata_json = EXCLUDED.metadata_json, expected_behaviors = EXCLUDED.expected_behaviors,
                failure_indicators = EXCLUDED.failure_indicators, tags = EXCLUDED.tags
        """), {
            "id": case.id,
            "session_id": session_id,
            "category": case.category,
            "prompt": case.prompt,
            "metadata": json.dumps(case.metadata, ensure_ascii=False),
            "expected": json.dumps(case.expected_behaviors, ensure_ascii=False),
            "failures": json.dumps(case.failure_indicators, ensure_ascii=False),
            "tags": json.dumps(case.tags, ensure_ascii=False),
        })

    # Store test results
    for r in body.test_results:
        await db.execute(text("""
            INSERT INTO db_test_results
            (session_id, test_case_id, category, prompt_sent, response_raw,
             response_time_ms, error, screenshot_path, executed_at, metadata_json,
             ai_steps_taken, ai_calls_used, ai_tokens_input, ai_tokens_output)
            VALUES (:session_id, :test_case_id, :category, :prompt, :response,
                    :time_ms, :error, :screenshot, :executed_at, :metadata,
                    :ai_steps, :ai_calls, :ai_tok_in, :ai_tok_out)
        """), {
            "session_id": session_id,
            "test_case_id": r.test_case_id,
            "category": r.category,
            "prompt": r.prompt_sent,
            "response": r.response_raw,
            "time_ms": int(r.response_time_ms),
            "error": r.error,
            "screenshot": r.screenshot_path,
            "executed_at": r.executed_at or now,
            "metadata": "{}",
            "ai_steps": r.ai_steps_taken,
            "ai_calls": r.ai_calls_used,
            "ai_tok_in": r.ai_tokens_input,
            "ai_tok_out": r.ai_tokens_output,
        })

    # Store axis scores
    for score in body.axis_scores:
        score_id = str(uuid.uuid4())
        await db.execute(text("""
            INSERT INTO axis_scores
            (id, session_id, tool_id, axis, axis_name_jp, score, confidence,
             source, details, strengths, risks, scored_at)
            VALUES (:id, :session_id, :tool_id, :axis, :axis_name_jp, :score,
                    :confidence, :source, :details, :strengths, :risks, :scored_at)
            ON CONFLICT (id) DO UPDATE SET
                score = EXCLUDED.score, confidence = EXCLUDED.confidence,
                source = EXCLUDED.source, details = EXCLUDED.details,
                strengths = EXCLUDED.strengths, risks = EXCLUDED.risks
        """), {
            "id": score_id,
            "session_id": session_id,
            "tool_id": tool_id,
            "axis": score.axis,
            "axis_name_jp": score.axis_name_jp,
            "score": score.score,
            "confidence": score.confidence,
            "source": score.source,
            "details": json.dumps(score.details, ensure_ascii=False, default=str),
            "strengths": json.dumps(score.strengths, ensure_ascii=False),
            "risks": json.dumps(score.risks, ensure_ascii=False),
            "scored_at": now,
        })

    await db.commit()

    logger.info("Agent results uploaded: session=%s, tests=%d, scores=%d",
                session_id, len(body.test_results), len(body.axis_scores))
    return {
        "status": "ok",
        "session_id": session_id,
        "final_status": new_status,
        "test_results_count": len(body.test_results),
        "axis_scores_count": len(body.axis_scores),
    }
