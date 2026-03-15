"""Audit session endpoints."""
import asyncio as _asyncio
import csv
import io
import json as _json
import logging
import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status

logger = logging.getLogger(__name__)
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response, StreamingResponse

from ...db.base import get_db
from ...db.models.audit import AuditSession, DBTestCase, DBTestResult
from ...db.models.score import AxisScoreRecord, ManualChecklistRecord
from ...db.models.tool import Tool, ToolTargetConfig
from ...db.models.user import User
from ...schemas.audit import (
    AuditCreate,
    AuditDetailResponse,
    AuditListResponse,
    AuditProgressResponse,
    AuditResponse,
    AuditStartRequest,
    AuditStartResponse,
    ManualScoreSubmit,
    VolumeMetrics,
)
from ..deps import require_analyst

router = APIRouter()


def _generate_session_code() -> str:
    """Generate a human-readable session code like AX-20260312-A1B2C3D4."""
    now = datetime.now(timezone.utc)
    short_id = uuid.uuid4().hex[:8].upper()
    return f"AX-{now.strftime('%Y%m%d')}-{short_id}"


@router.post("/", response_model=AuditResponse, status_code=status.HTTP_201_CREATED)
async def create_audit(
    body: AuditCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(require_analyst)],
):
    """Create a new audit session (analyst+ only)."""
    session = AuditSession(
        session_code=_generate_session_code(),
        tool_id=body.tool_id,
        profile_id=body.profile_id,
        status="pending",
        initiated_by=user.id,
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session


@router.post("/start", response_model=AuditStartResponse)
async def start_audit(
    body: AuditStartRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(require_analyst)],
):
    """Start a new audit: create session and launch the agent pipeline."""
    from ...services.audit_runner import start_audit as runner_start

    # Validate tool exists
    result = await db.execute(select(Tool).where(Tool.id == body.tool_id))
    tool = result.scalar_one_or_none()
    if not tool:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="ツールが見つかりません",
        )

    # Get target config YAML from DB (optional — runner will fallback to file-based)
    target_config_yaml = None
    target_config_name = body.target_config_name
    if not target_config_name:
        config_result = await db.execute(
            select(ToolTargetConfig)
            .where(ToolTargetConfig.tool_id == tool.id, ToolTargetConfig.is_active == True)
            .order_by(ToolTargetConfig.version.desc())
            .limit(1)
        )
        config = config_result.scalar_one_or_none()
        if config:
            target_config_yaml = config.config_yaml

    # No longer error if no config — runner will try config/targets/{slug}.yaml fallback

    # Create audit session in web DB
    session_code = _generate_session_code()
    agent_session_id = f"session-{uuid.uuid4().hex[:8]}"

    audit_session = AuditSession(
        session_code=session_code,
        tool_id=tool.id,
        profile_id=body.profile_id or tool.profile_id or "",
        status="running",
        initiated_by=user.id,
        started_at=datetime.now(timezone.utc),
    )
    db.add(audit_session)
    await db.commit()
    await db.refresh(audit_session)

    # Launch background pipeline (pass tool slug for file-based config fallback)
    result = runner_start(
        session_id=agent_session_id,
        db_session_id=audit_session.id,
        tool_name=tool.name_jp or tool.name,
        target_config_yaml=target_config_yaml,
        target_config_name=target_config_name or tool.slug,
        profile_id=body.profile_id or tool.profile_id or "",
        categories=body.categories,
    )

    if "error" in result:
        audit_session.status = "failed"
        audit_session.error_message = result["error"]
        await db.commit()
        logger.error("Audit start failed for %s: %s", tool.slug, result["error"])
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="監査の開始に失敗しました。管理者にお問い合わせください。",
        )

    return AuditStartResponse(
        session_id=agent_session_id,
        db_session_id=audit_session.id,
        status="running",
        message=f"監査を開始しました: {tool.name_jp or tool.name} ({session_code})",
    )


@router.get("/running", response_model=list[AuditProgressResponse])
async def list_running_audits(
    _user: Annotated[User, Depends(require_analyst)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """List all currently running audit sessions."""
    from ...services.audit_runner import list_running_audits as runner_list

    running = runner_list()
    responses = []

    for r in running:
        # Get DB info
        db_status = ""
        total_planned = 0
        total_executed = 0
        db_session_id = r.get("db_session_id", "")

        if db_session_id:
            result = await db.execute(
                select(AuditSession).where(AuditSession.id == db_session_id)
            )
            session = result.scalar_one_or_none()
            if session:
                db_status = session.status
                total_planned = session.total_planned
                total_executed = session.total_executed

        # Use in-memory progress (real-time) over DB values (batched)
        mem_completed = r.get("completed", 0)
        mem_total = r.get("total", 0)

        responses.append(AuditProgressResponse(
            session_id=r.get("session_id", ""),
            db_session_id=db_session_id,
            status=r.get("status", "unknown"),
            phase=r.get("phase", ""),
            tool_name=r.get("tool_name", ""),
            error=r.get("error"),
            started_at=r.get("started_at"),
            total_planned=total_planned,
            total_executed=total_executed,
            db_status=db_status,
            completed=mem_completed,
            total=mem_total,
            current_category=r.get("current_category", ""),
        ))

    return responses


@router.get("/progress/{session_id}", response_model=AuditProgressResponse)
async def get_audit_progress(
    session_id: str,
    _user: Annotated[User, Depends(require_analyst)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Get real-time progress of a running audit."""
    from ...services.audit_runner import get_running_audit

    running = get_running_audit(session_id)

    # Also check DB for the audit session
    db_session_id = running.get("db_session_id", "") if running else ""

    # Try to find by session_id in DB (check session_code or search)
    db_session = None
    if db_session_id:
        result = await db.execute(
            select(AuditSession).where(AuditSession.id == db_session_id)
        )
        db_session = result.scalar_one_or_none()

    if not running and not db_session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="監査セッションが見つかりません",
        )

    return AuditProgressResponse(
        session_id=session_id,
        db_session_id=db_session_id,
        status=running.get("status", db_session.status if db_session else "unknown") if running else (db_session.status if db_session else "unknown"),
        phase=running.get("phase", "") if running else "done",
        tool_name=running.get("tool_name", "") if running else "",
        error=running.get("error") if running else (db_session.error_message if db_session else None),
        started_at=running.get("started_at") if running else (db_session.started_at.isoformat() if db_session and db_session.started_at else None),
        total_planned=db_session.total_planned if db_session else 0,
        total_executed=db_session.total_executed if db_session else 0,
        db_status=db_session.status if db_session else "",
        completed=running.get("completed", 0) if running else (db_session.total_executed if db_session else 0),
        total=running.get("total", 0) if running else (db_session.total_planned if db_session else 0),
        current_category=running.get("current_category", "") if running else "",
    )


@router.get("/", response_model=AuditListResponse)
async def list_audits(
    db: Annotated[AsyncSession, Depends(get_db)],
    _user: Annotated[User, Depends(require_analyst)],
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    tool_id: str | None = None,
    audit_status: str | None = Query(None, alias="status"),
):
    """List audit sessions (analyst+ only)."""
    query = select(AuditSession)
    count_query = select(func.count()).select_from(AuditSession)

    if tool_id:
        query = query.where(AuditSession.tool_id == tool_id)
        count_query = count_query.where(AuditSession.tool_id == tool_id)
    if audit_status:
        query = query.where(AuditSession.status == audit_status)
        count_query = count_query.where(AuditSession.status == audit_status)

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    offset = (page - 1) * page_size
    query = query.order_by(AuditSession.created_at.desc()).offset(offset).limit(page_size)
    result = await db.execute(query)
    items = result.scalars().all()

    # Resolve tool names
    tool_ids = list({item.tool_id for item in items if item.tool_id})
    tool_names = {}
    if tool_ids:
        tools_result = await db.execute(
            select(Tool).where(Tool.id.in_(tool_ids))
        )
        for t in tools_result.scalars().all():
            tool_names[t.id] = t.name_jp or t.name

    response_items = []
    for item in items:
        data = AuditResponse.model_validate(item)
        data.tool_name = tool_names.get(item.tool_id)
        response_items.append(data)

    return AuditListResponse(items=response_items, total=total)


@router.get("/{session_id}", response_model=AuditDetailResponse)
async def get_audit(
    session_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    _user: Annotated[User, Depends(require_analyst)],
):
    """Get audit session detail with test results and scores."""
    result = await db.execute(
        select(AuditSession).where(AuditSession.id == session_id)
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="監査セッションが見つかりません",
        )

    # Get tool name
    tool_name = None
    tool_result = await db.execute(select(Tool).where(Tool.id == session.tool_id))
    tool = tool_result.scalar_one_or_none()
    if tool:
        tool_name = tool.name_jp or tool.name

    # Get test results
    results_query = select(DBTestResult).where(
        DBTestResult.session_id == session_id
    ).order_by(DBTestResult.executed_at.desc()).limit(100)
    results_result = await db.execute(results_query)
    test_results = []
    for r in results_result.scalars().all():
        test_results.append({
            "id": r.id,
            "test_case_id": r.test_case_id,
            "category": r.category,
            "prompt_sent": r.prompt_sent[:200] if r.prompt_sent else "",
            "response_raw": r.response_raw[:500] if r.response_raw else None,
            "response_time_ms": r.response_time_ms,
            "error": r.error,
            "executed_at": r.executed_at.isoformat() if r.executed_at else None,
            "ai_steps_taken": r.ai_steps_taken or 0,
            "ai_calls_used": r.ai_calls_used or 0,
        })

    # Get axis scores
    scores_query = select(AxisScoreRecord).where(
        AxisScoreRecord.session_id == session_id
    )
    scores_result = await db.execute(scores_query)
    axis_scores = []
    for s in scores_result.scalars().all():
        axis_scores.append({
            "axis": s.axis,
            "axis_name_jp": s.axis_name_jp,
            "score": s.score,
            "confidence": s.confidence,
            "source": s.source,
            "strengths": s.strengths or [],
            "risks": s.risks or [],
            "details": s.details or [],
        })

    # Build volume metrics
    volume_metrics = VolumeMetrics(
        executor_type=session.executor_type or "playwright",
        ai_total_steps=session.ai_total_steps or 0,
        ai_total_api_calls=session.ai_total_api_calls or 0,
        ai_total_input_tokens=session.ai_total_input_tokens or 0,
        ai_total_output_tokens=session.ai_total_output_tokens or 0,
        ai_estimated_cost_usd=(session.ai_estimated_cost_usd or 0) / 100.0,  # cents → dollars
        ai_screenshots_captured=session.ai_screenshots_captured or 0,
        completeness_ratio=session.completeness_ratio or 0,
    )

    return AuditDetailResponse(
        id=session.id,
        session_code=session.session_code,
        tool_id=session.tool_id,
        profile_id=session.profile_id,
        status=session.status,
        total_planned=session.total_planned,
        total_executed=session.total_executed,
        error_message=session.error_message,
        initiated_by=session.initiated_by,
        started_at=session.started_at,
        completed_at=session.completed_at,
        created_at=session.created_at,
        test_results=test_results,
        axis_scores=axis_scores,
        tool_name=tool_name,
        volume_metrics=volume_metrics,
    )


@router.post("/{session_id}/continue")
async def continue_audit(
    session_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    _user: Annotated[User, Depends(require_analyst)],
):
    """Signal that manual login is complete; resume automated testing."""
    from ...services.audit_runner import resume_after_login

    # Update DB status from waiting_login to running
    result = await db.execute(
        select(AuditSession).where(AuditSession.id == session_id)
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="監査セッションが見つかりません",
        )

    if session.status != "waiting_login":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"現在のステータス '{session.status}' ではこの操作を実行できません",
        )

    # Signal the background thread to resume
    resumed = resume_after_login(session_id)
    if not resumed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="実行中の監査が見つかりません。監査が既に終了している可能性があります。",
        )

    session.status = "running"
    await db.commit()

    return {"status": "resumed", "session_id": session_id, "message": "ログイン完了。自動監査を再開しました。"}


@router.post("/{session_id}/abort")
async def abort_audit(
    session_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    _user: Annotated[User, Depends(require_analyst)],
):
    """Abort a running audit. Stops API usage immediately."""
    from ...services.audit_runner import abort_audit as runner_abort

    result = await db.execute(
        select(AuditSession).where(AuditSession.id == session_id)
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="監査セッションが見つかりません",
        )

    if session.status not in ("running", "waiting_login"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"ステータス '{session.status}' の監査は中止できません",
        )

    aborted = runner_abort(session_id)
    if not aborted:
        # Process is already dead — just update the DB status directly
        session.status = "aborted"
        session.error_message = "プロセスが応答しないため強制中止しました"
        await db.commit()
        return {"status": "aborted", "session_id": session_id, "message": "プロセスが見つからないため、ステータスを中止に更新しました。"}

    session.status = "aborted"
    session.error_message = "ユーザーにより中止されました"
    await db.commit()

    return {"status": "aborted", "session_id": session_id, "message": "監査を中止しました。API使用を停止します。"}


@router.post("/{session_id}/retry", response_model=AuditStartResponse)
async def retry_audit(
    session_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(require_analyst)],
):
    """Retry a failed audit session with the same parameters."""
    from ...services.audit_runner import start_audit as runner_start

    # Find the original session
    result = await db.execute(
        select(AuditSession).where(AuditSession.id == session_id)
    )
    original = result.scalar_one_or_none()
    if not original:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="監査セッションが見つかりません",
        )

    if original.status not in ("failed", "aborted"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"ステータスが '{original.status}' のセッションは再試行できません。失敗または中止済みのセッションのみ再試行可能です。",
        )

    # Get the tool
    tool_result = await db.execute(select(Tool).where(Tool.id == original.tool_id))
    tool = tool_result.scalar_one_or_none()
    if not tool:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="対象ツールが見つかりません",
        )

    # Get target config (DB or fallback to file-based via slug)
    target_config_yaml = None
    config_result = await db.execute(
        select(ToolTargetConfig)
        .where(ToolTargetConfig.tool_id == tool.id, ToolTargetConfig.is_active == True)
        .order_by(ToolTargetConfig.version.desc())
        .limit(1)
    )
    config = config_result.scalar_one_or_none()
    if config:
        target_config_yaml = config.config_yaml

    # No longer error if no config — runner will try file-based fallback

    # Create new audit session
    session_code = _generate_session_code()
    agent_session_id = f"session-{uuid.uuid4().hex[:8]}"

    new_session = AuditSession(
        session_code=session_code,
        tool_id=tool.id,
        profile_id=original.profile_id,
        status="running",
        initiated_by=user.id,
        started_at=datetime.now(timezone.utc),
    )
    db.add(new_session)
    await db.commit()
    await db.refresh(new_session)

    # Launch background pipeline
    result = runner_start(
        session_id=agent_session_id,
        db_session_id=new_session.id,
        tool_name=tool.name_jp or tool.name,
        target_config_yaml=target_config_yaml,
        target_config_name=tool.slug,
        profile_id=original.profile_id,
    )

    if "error" in result:
        new_session.status = "failed"
        new_session.error_message = result["error"]
        await db.commit()
        logger.error("Audit retry failed for %s: %s", tool.slug, result["error"])
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="監査の再試行に失敗しました。管理者にお問い合わせください。",
        )

    return AuditStartResponse(
        session_id=agent_session_id,
        db_session_id=new_session.id,
        status="running",
        message=f"監査を再試行しました: {tool.name_jp or tool.name} ({session_code})",
    )


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_audit(
    session_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    _user: Annotated[User, Depends(require_analyst)],
):
    """Delete an audit session and all related data."""
    from sqlalchemy import delete as sql_delete

    result = await db.execute(
        select(AuditSession).where(AuditSession.id == session_id)
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="監査セッションが見つかりません",
        )

    if session.status in ("running", "waiting_login"):
        # Check if the process is actually still running
        from ...services.audit_runner import get_running_audit, list_running_audits
        actually_running = any(
            r.get("db_session_id") == session_id
            for r in list_running_audits()
        )
        if actually_running:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="実行中のセッションは削除できません。先に中止してください。",
            )
        # Process is dead — allow deletion of stale session

    # Delete related records first
    await db.execute(sql_delete(DBTestResult).where(DBTestResult.session_id == session_id))
    await db.execute(sql_delete(DBTestCase).where(DBTestCase.session_id == session_id))
    await db.execute(sql_delete(AxisScoreRecord).where(AxisScoreRecord.session_id == session_id))
    await db.execute(sql_delete(ManualChecklistRecord).where(ManualChecklistRecord.session_id == session_id))
    await db.delete(session)
    await db.commit()


class _StatusUpdateBody(BaseModel):
    status: str


@router.patch("/{session_id}/status")
async def update_audit_status(
    session_id: str,
    body: _StatusUpdateBody,
    db: Annotated[AsyncSession, Depends(get_db)],
    _user: Annotated[User, Depends(require_analyst)],
):
    """Update the status of an audit session (for manual corrections)."""
    result = await db.execute(
        select(AuditSession).where(AuditSession.id == session_id)
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="監査セッションが見つかりません",
        )

    if session.status in ("running", "waiting_login"):
        # Check if the process is actually still running
        from ...services.audit_runner import list_running_audits
        actually_running = any(
            r.get("db_session_id") == session_id
            for r in list_running_audits()
        )
        if actually_running:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="実行中のセッションのステータスは手動変更できません。",
            )
        # Process is dead — allow status change of stale session

    allowed_statuses = {"pending", "completed", "failed", "cancelled", "aborted", "awaiting_manual"}
    new_status = body.status
    if not new_status or new_status not in allowed_statuses:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"無効なステータスです。許可: {', '.join(sorted(allowed_statuses))}",
        )

    session.status = new_status
    await db.commit()
    return {"status": new_status, "session_id": session_id}


@router.post("/{session_id}/manual-scores", status_code=status.HTTP_201_CREATED)
async def submit_manual_scores(
    session_id: str,
    body: ManualScoreSubmit,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(require_analyst)],
):
    """Submit manual checklist scores for an audit session."""
    result = await db.execute(
        select(AuditSession).where(AuditSession.id == session_id)
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="監査セッションが見つかりません",
        )

    for item in body.items:
        record = ManualChecklistRecord(
            session_id=session_id,
            tool_id=session.tool_id,
            axis=item.axis,
            checklist_item_id=item.checklist_item_id,
            item_name_jp=item.item_name_jp,
            passed=item.passed,
            score=item.score,
            weight=item.weight,
            evidence=item.evidence,
            evidence_url=item.evidence_url,
            evaluated_by=user.id,
            evaluated_at=datetime.now(timezone.utc),
        )
        db.add(record)

    await db.commit()
    return {"status": "ok", "count": len(body.items)}


@router.post("/{session_id}/finalize")
async def finalize_audit(
    session_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(require_analyst)],
):
    """Finalize audit: merge auto+manual scores and publish."""
    result = await db.execute(
        select(AuditSession).where(AuditSession.id == session_id)
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="監査セッションが見つかりません",
        )

    if session.status not in ("awaiting_manual", "completed"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"現在のステータス '{session.status}' では確定できません",
        )

    session.status = "completed"
    session.completed_at = datetime.now(timezone.utc)
    await db.commit()

    return {"status": "finalized", "session_id": session_id}


# ---------------------------------------------------------------------------
# Proposal 1: SSE Progress Streaming
# ---------------------------------------------------------------------------

@router.get("/{session_id}/stream")
async def stream_audit_progress(
    session_id: str,
    _user: Annotated[User, Depends(require_analyst)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """SSE stream of audit progress for real-time UI updates."""
    from ...services.audit_runner import get_running_audit

    # Also check by db_session_id
    async def event_generator():
        terminal_statuses = {"completed", "failed", "aborted", "cancelled", "done"}
        for _ in range(600):  # Max ~20 minutes
            info = get_running_audit(session_id)
            if not info:
                # Check by db_session_id
                from ...services.audit_runner import list_running_audits as _list
                for r in _list():
                    if r.get("db_session_id") == session_id:
                        info = r
                        break

            if info:
                data = _json.dumps({
                    "phase": info.get("phase", ""),
                    "completed": info.get("completed", 0),
                    "total": info.get("total", 0),
                    "current_category": info.get("current_category", ""),
                    "status": info.get("status", ""),
                    "error": info.get("error"),
                }, ensure_ascii=False)
                yield f"data: {data}\n\n"

                if info.get("status") in terminal_statuses or info.get("phase") == "done":
                    yield f"data: {_json.dumps({'status': 'stream_end'})}\n\n"
                    return
            else:
                # No running audit found — check DB status
                result = await db.execute(
                    select(AuditSession).where(AuditSession.id == session_id)
                )
                session = result.scalar_one_or_none()
                if session and session.status in terminal_statuses:
                    data = _json.dumps({
                        "phase": "done",
                        "completed": session.total_executed or 0,
                        "total": session.total_planned or 0,
                        "status": session.status,
                    }, ensure_ascii=False)
                    yield f"data: {data}\n\ndata: {_json.dumps({'status': 'stream_end'})}\n\n"
                    return

            await _asyncio.sleep(2)

        yield f"data: {_json.dumps({'status': 'stream_timeout'})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# Proposal 4: Export Feature (CSV/JSON)
# ---------------------------------------------------------------------------

@router.get("/{session_id}/export/{format}")
async def export_audit(
    session_id: str,
    format: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    _user: Annotated[User, Depends(require_analyst)],
):
    """Export audit results as CSV or JSON."""
    if format not in ("csv", "json"):
        raise HTTPException(status_code=400, detail="サポートされるフォーマット: csv, json")

    # Get session
    result = await db.execute(select(AuditSession).where(AuditSession.id == session_id))
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="セッションが見つかりません")

    # Get tool name
    tool_result = await db.execute(select(Tool).where(Tool.id == session.tool_id))
    tool = tool_result.scalar_one_or_none()
    tool_name = tool.name_jp or tool.name if tool else "Unknown"

    # Get test results
    results_q = await db.execute(
        select(DBTestResult).where(DBTestResult.session_id == session_id).order_by(DBTestResult.id)
    )
    test_results = results_q.scalars().all()

    # Get axis scores
    scores_q = await db.execute(
        select(AxisScoreRecord).where(AxisScoreRecord.session_id == session_id)
    )
    axis_scores = scores_q.scalars().all()

    if format == "json":
        export_data = {
            "session": {
                "id": session.id,
                "session_code": session.session_code,
                "tool_name": tool_name,
                "status": session.status,
                "total_planned": session.total_planned,
                "total_executed": session.total_executed,
                "started_at": session.started_at.isoformat() if session.started_at else None,
                "completed_at": session.completed_at.isoformat() if session.completed_at else None,
            },
            "axis_scores": [
                {
                    "axis": s.axis,
                    "axis_name_jp": s.axis_name_jp,
                    "score": s.score,
                    "confidence": s.confidence,
                    "source": s.source,
                    "strengths": s.strengths or [],
                    "risks": s.risks or [],
                }
                for s in axis_scores
            ],
            "test_results": [
                {
                    "test_case_id": r.test_case_id,
                    "category": r.category,
                    "prompt_sent": r.prompt_sent,
                    "response_raw": (r.response_raw or "")[:500],
                    "response_time_ms": r.response_time_ms,
                    "error": r.error,
                    "executed_at": r.executed_at.isoformat() if r.executed_at else None,
                }
                for r in test_results
            ],
        }
        return JSONResponse(
            content=export_data,
            headers={
                "Content-Disposition": f'attachment; filename="audit-{session.session_code}.json"'
            },
        )

    # CSV format
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "test_case_id", "category", "prompt_sent", "response_preview",
        "response_time_ms", "error", "executed_at"
    ])
    for r in test_results:
        writer.writerow([
            r.test_case_id, r.category, r.prompt_sent,
            (r.response_raw or "")[:200], r.response_time_ms,
            r.error or "", r.executed_at.isoformat() if r.executed_at else "",
        ])

    return Response(
        content=output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="audit-{session.session_code}.csv"'
        },
    )


# ---------------------------------------------------------------------------
# Proposal 5: Manual Evaluation Guidelines
# ---------------------------------------------------------------------------

@router.get("/{session_id}/manual-guidelines")
async def get_manual_guidelines(
    session_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    _user: Annotated[User, Depends(require_analyst)],
):
    """Generate manual evaluation guidelines based on auto-scored results."""
    # Get axis scores
    scores_q = await db.execute(
        select(AxisScoreRecord).where(AxisScoreRecord.session_id == session_id)
    )
    axis_scores = scores_q.scalars().all()

    if not axis_scores:
        raise HTTPException(status_code=404, detail="スコアデータが見つかりません")

    guidelines = []
    for score in axis_scores:
        # Generate guidelines based on confidence and score
        axis_guidelines = {
            "axis": score.axis,
            "axis_name_jp": score.axis_name_jp,
            "auto_score": score.score,
            "confidence": score.confidence,
            "source": score.source,
            "focus_areas": [],
            "priority": "high" if score.confidence < 0.3 else ("medium" if score.confidence < 0.7 else "low"),
        }

        # Add focus areas based on risks
        risks = score.risks or []
        for risk in risks:
            axis_guidelines["focus_areas"].append({
                "type": "risk",
                "description": risk,
                "action": "この領域を重点的に手動確認してください",
            })

        # Add focus areas based on low detail scores
        details = score.details or []
        for detail in details:
            if isinstance(detail, dict) and detail.get("score", 5) < 2.5:
                axis_guidelines["focus_areas"].append({
                    "type": "low_score",
                    "rule": detail.get("rule_name_jp", detail.get("rule_id", "")),
                    "auto_score": detail.get("score", 0),
                    "action": f"自動評価スコアが低い項目です（{detail.get('score', 0):.1f}/5.0）。手動で再評価してください。",
                })

        # If confidence is 0 (manual only), add general guidance
        if score.confidence == 0:
            axis_guidelines["focus_areas"].append({
                "type": "manual_only",
                "description": f"{score.axis_name_jp}は手動評価のみの軸です",
                "action": "チェックリストの全項目を評価してください",
            })

        guidelines.append(axis_guidelines)

    # Sort by priority (high first)
    priority_order = {"high": 0, "medium": 1, "low": 2}
    guidelines.sort(key=lambda g: priority_order.get(g["priority"], 99))

    return {"session_id": session_id, "guidelines": guidelines}
