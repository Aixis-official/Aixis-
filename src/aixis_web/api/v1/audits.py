"""Audit session endpoints."""
import csv
import io
import json as _json
import logging
import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status

logger = logging.getLogger(__name__)
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from ...db.base import get_db
from ...db.models.audit import AuditSession, DBTestResult
from ...db.models.score import AxisScoreRecord, ManualChecklistRecord
from ...db.models.tool import Tool
from ...db.models.user import User
from ...schemas.audit import (
    AuditCreate,
    AuditDetailResponse,
    AuditListResponse,
    AuditResponse,
    AuditStartRequest,
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


@router.post("", response_model=AuditResponse, status_code=status.HTTP_201_CREATED)
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


_NOT_IMPLEMENTED_MSG = "この機能はChrome拡張に移行しました。/api/v1/extension/ エンドポイントをご利用ください。"


@router.post("/start")
async def start_audit(
    body: AuditStartRequest,
    _user: Annotated[User, Depends(require_analyst)],
):
    """Start a new audit — migrated to Chrome extension."""
    return JSONResponse(status_code=501, content={"detail": _NOT_IMPLEMENTED_MSG})


@router.get("/running")
async def list_running_audits(
    _user: Annotated[User, Depends(require_analyst)],
):
    """List running audits — migrated to Chrome extension."""
    return JSONResponse(status_code=501, content={"detail": _NOT_IMPLEMENTED_MSG})


@router.get("/progress/{session_id}")
async def get_audit_progress(
    session_id: str,
    _user: Annotated[User, Depends(require_analyst)],
):
    """Get real-time audit progress — migrated to Chrome extension."""
    return JSONResponse(status_code=501, content={"detail": _NOT_IMPLEMENTED_MSG})


@router.get("")
async def list_audits(
    db: Annotated[AsyncSession, Depends(get_db)],
    _user: Annotated[User, Depends(require_analyst)],
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    tool_id: str | None = None,
    audit_status: str | None = Query(None, alias="status"),
):
    """List audit sessions (analyst+ only). Returns raw dict to avoid serialization errors."""
    try:
        query = select(AuditSession).where(AuditSession.deleted_at.is_(None))
        count_query = select(func.count()).select_from(AuditSession).where(AuditSession.deleted_at.is_(None))

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
            try:
                response_items.append({
                    "id": str(item.id),
                    "session_code": str(item.session_code or ""),
                    "tool_id": str(item.tool_id or ""),
                    "tool_name": tool_names.get(item.tool_id),
                    "profile_id": str(item.profile_id or ""),
                    "status": str(item.status or "unknown"),
                    "total_planned": item.total_planned or 0,
                    "total_executed": item.total_executed or 0,
                    "error_message": item.error_message,
                    "initiated_by": item.initiated_by,
                    "started_at": item.started_at.isoformat() if item.started_at else None,
                    "completed_at": item.completed_at.isoformat() if item.completed_at else None,
                    "created_at": item.created_at.isoformat() if item.created_at else None,
                })
            except Exception as e:
                logger.warning("Skipping audit row %s: %s", getattr(item, 'id', '?'), e)

        return {"items": response_items, "total": total}
    except Exception as e:
        logger.exception("list_audits failed: %s", e)
        return {"items": [], "total": 0}


@router.get("/deleted", response_model=AuditListResponse)
async def list_deleted_audits(
    db: Annotated[AsyncSession, Depends(get_db)],
    _user: Annotated[User, Depends(require_analyst)],
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """List soft-deleted audit sessions (for recovery)."""
    query = select(AuditSession).where(AuditSession.deleted_at.isnot(None))
    count_query = select(func.count()).select_from(AuditSession).where(AuditSession.deleted_at.isnot(None))

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    offset = (page - 1) * page_size
    query = query.order_by(AuditSession.deleted_at.desc()).offset(offset).limit(page_size)
    result = await db.execute(query)
    items = result.scalars().all()

    tool_ids = list({item.tool_id for item in items if item.tool_id})
    tool_names = {}
    if tool_ids:
        tools_result = await db.execute(select(Tool).where(Tool.id.in_(tool_ids)))
        for t in tools_result.scalars().all():
            tool_names[t.id] = t.name_jp or t.name

    response_items = []
    for item in items:
        try:
            data = AuditResponse.model_validate(item)
            data.tool_name = tool_names.get(item.tool_id)
            response_items.append(data)
        except Exception as e:
            logger.warning("Skipping deleted audit %s: %s", getattr(item, 'id', '?'), e)

    return AuditListResponse(items=response_items, total=total)


@router.get("/{session_id}", response_model=AuditDetailResponse)
async def get_audit(
    session_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    _user: Annotated[User, Depends(require_analyst)],
):
    """Get audit session detail with test results and scores."""
    import traceback as _tb
    try:
        return await _get_audit_impl(session_id, db)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("get_audit error for %s: %s\n%s", session_id, e, _tb.format_exc())
        raise HTTPException(500, f"Internal error: {type(e).__name__}: {e}")


async def _get_audit_impl(session_id: str, db: AsyncSession):
    """Internal implementation of get_audit."""
    result = await db.execute(
        select(AuditSession).where(AuditSession.id == session_id, AuditSession.deleted_at.is_(None))
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
        # Parse metadata_json for frontend consumption
        _metadata = r.metadata_json
        if isinstance(_metadata, str):
            try:
                _metadata = _json.loads(_metadata)
            except (ValueError, TypeError):
                _metadata = {}
        elif _metadata is None:
            _metadata = {}

        test_results.append({
            "id": r.id,
            "test_case_id": r.test_case_id,
            "category": r.category,
            "prompt_sent": r.prompt_sent[:200] if r.prompt_sent else "",
            "response_raw": r.response_raw[:500] if r.response_raw else None,
            "response_time_ms": r.response_time_ms,
            "error": r.error,
            "screenshot_path": r.screenshot_path,
            "executed_at": r.executed_at.isoformat() if r.executed_at else None,
            "ai_steps_taken": r.ai_steps_taken or 0,
            "ai_calls_used": r.ai_calls_used or 0,
            "metadata_json": _metadata,
        })

    # Get axis scores
    scores_query = select(AxisScoreRecord).where(
        AxisScoreRecord.session_id == session_id
    )
    scores_result = await db.execute(scores_query)
    axis_scores = []
    for s in scores_result.scalars().all():
        # Defensively parse JSON fields — they may come back as strings
        # from the ORM depending on the DB driver (SQLite vs PostgreSQL).
        _details = s.details
        if isinstance(_details, str):
            try:
                _details = _json.loads(_details)
            except (ValueError, TypeError):
                _details = []
        _strengths = s.strengths
        if isinstance(_strengths, str):
            try:
                _strengths = _json.loads(_strengths)
            except (ValueError, TypeError):
                _strengths = []
        _risks = s.risks
        if isinstance(_risks, str):
            try:
                _risks = _json.loads(_risks)
            except (ValueError, TypeError):
                _risks = []

        axis_scores.append({
            "axis": s.axis,
            "axis_name_jp": s.axis_name_jp,
            "score": s.score,
            "confidence": s.confidence,
            "source": s.source,
            "strengths": _strengths if isinstance(_strengths, list) else [],
            "risks": _risks if isinstance(_risks, list) else [],
            "details": _details if isinstance(_details, (dict, list)) else [],
        })

    # Build volume metrics
    volume_metrics = VolumeMetrics(
        executor_type=session.executor_type or "extension",
        ai_total_steps=session.ai_total_steps or 0,
        ai_total_api_calls=session.ai_total_api_calls or 0,
        ai_total_input_tokens=session.ai_total_input_tokens or 0,
        ai_total_output_tokens=session.ai_total_output_tokens or 0,
        ai_estimated_cost_usd=(session.ai_estimated_cost_usd or 0) / 100.0,  # cents → dollars
        ai_screenshots_captured=session.ai_screenshots_captured or 0,
        completeness_ratio=session.completeness_ratio or 0,
    )

    # Parse reliability_scores (stored as JSON string in some DB backends)
    reliability = session.reliability_scores
    if isinstance(reliability, str):
        import json as _json
        try:
            reliability = _json.loads(reliability)
        except Exception:
            reliability = None

    # Generate score diff against previous audit (best-effort)
    score_diff = None
    if session.status in ("completed", "awaiting_manual") and axis_scores:
        try:
            from ...services.score_service import generate_score_diff
            score_diff = await generate_score_diff(db, session.tool_id, session.id)
        except Exception:
            pass

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
        reliability_scores=reliability,
        score_diff=score_diff,
    )


@router.post("/{session_id}/continue")
async def continue_audit(
    session_id: str,
    _user: Annotated[User, Depends(require_analyst)],
):
    """Resume after login — migrated to Chrome extension."""
    return JSONResponse(status_code=501, content={"detail": _NOT_IMPLEMENTED_MSG})


@router.get("/{session_id}/browser/screenshot")
async def get_browser_screenshot(
    session_id: str,
    _user: Annotated[User, Depends(require_analyst)],
):
    """Browser screenshot — migrated to Chrome extension."""
    return JSONResponse(status_code=501, content={"detail": _NOT_IMPLEMENTED_MSG})


@router.post("/{session_id}/browser/click")
async def browser_click(
    session_id: str,
    body: dict,
    _user: Annotated[User, Depends(require_analyst)],
):
    """Browser click — migrated to Chrome extension."""
    return JSONResponse(status_code=501, content={"detail": _NOT_IMPLEMENTED_MSG})


@router.post("/{session_id}/browser/type")
async def browser_type(
    session_id: str,
    body: dict,
    _user: Annotated[User, Depends(require_analyst)],
):
    """Browser type — migrated to Chrome extension."""
    return JSONResponse(status_code=501, content={"detail": _NOT_IMPLEMENTED_MSG})


@router.post("/{session_id}/abort")
async def abort_audit(
    session_id: str,
    _user: Annotated[User, Depends(require_analyst)],
):
    """Abort audit — migrated to Chrome extension."""
    return JSONResponse(status_code=501, content={"detail": _NOT_IMPLEMENTED_MSG})


@router.post("/{session_id}/retry")
async def retry_audit(
    session_id: str,
    _user: Annotated[User, Depends(require_analyst)],
):
    """Retry audit — migrated to Chrome extension."""
    return JSONResponse(status_code=501, content={"detail": _NOT_IMPLEMENTED_MSG})


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_audit(
    session_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    _user: Annotated[User, Depends(require_analyst)],
):
    """Soft-delete an audit session (data is preserved and can be restored)."""
    result = await db.execute(
        select(AuditSession).where(
            AuditSession.id == session_id,
            AuditSession.deleted_at.is_(None),
        )
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="監査セッションが見つかりません",
        )

    if session.status in ("running", "waiting_login"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="実行中のセッションは削除できません。先に中止してください。",
        )

    # Soft-delete: mark as deleted instead of destroying data
    session.deleted_at = datetime.now(timezone.utc)
    session.deleted_by = _user.id
    await db.commit()

    # Log the action
    try:
        from ...db.models.audit_log import AuditLog
        db.add(AuditLog(
            entity_type="audit_session",
            entity_id=session_id,
            action="soft_delete",
            performed_by=_user.id,
            changes={"session_code": session.session_code, "tool_id": session.tool_id},
        ))
        await db.commit()
    except Exception:
        pass


@router.post("/{session_id}/restore")
async def restore_audit(
    session_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    _user: Annotated[User, Depends(require_analyst)],
):
    """Restore a soft-deleted audit session."""
    result = await db.execute(
        select(AuditSession).where(
            AuditSession.id == session_id,
            AuditSession.deleted_at.isnot(None),
        )
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="削除済みセッションが見つかりません",
        )

    session.deleted_at = None
    session.deleted_by = None
    await db.commit()

    try:
        from ...db.models.audit_log import AuditLog
        db.add(AuditLog(
            entity_type="audit_session",
            entity_id=session_id,
            action="restore",
            performed_by=_user.id,
            changes={"session_code": session.session_code},
        ))
        await db.commit()
    except Exception:
        pass

    return {"message": "セッションを復元しました", "session_id": session_id}


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
        select(AuditSession).where(AuditSession.id == session_id, AuditSession.deleted_at.is_(None))
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="監査セッションが見つかりません",
        )

    if session.status in ("running", "waiting_login"):
        # Server-side runner removed; allow status correction of stale sessions
        pass

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


class _EditAxisScoresBody(BaseModel):
    """Body for direct axis score editing (temporary admin feature)."""
    scores: dict[str, float]  # axis -> score (0.0-5.0)


AXIS_NAMES_JP = {
    "practicality": "実務適性",
    "cost_performance": "費用対効果",
    "localization": "日本語能力",
    "safety": "信頼性・安全性",
    "uniqueness": "革新性",
}


@router.put("/{session_id}/axis-scores")
async def edit_axis_scores(
    session_id: str,
    body: _EditAxisScoresBody,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(require_analyst)],
):
    """Directly edit axis scores for a session (admin/temporary feature).

    Upserts into axis_scores table. Overall score and grade are auto-calculated
    from the 5-axis equal-weight average.
    """
    session = (await db.execute(
        select(AuditSession).where(AuditSession.id == session_id, AuditSession.deleted_at.is_(None))
    )).scalar_one_or_none()
    if not session:
        raise HTTPException(404, "セッションが見つかりません")

    valid_axes = set(AXIS_NAMES_JP.keys())
    updated = []

    for axis, score in body.scores.items():
        if axis not in valid_axes:
            raise HTTPException(400, f"無効な軸: {axis}")
        score = max(0.0, min(5.0, round(score, 1)))

        # Upsert: find existing or create
        existing = (await db.execute(
            select(AxisScoreRecord).where(
                AxisScoreRecord.session_id == session_id,
                AxisScoreRecord.axis == axis,
            )
        )).scalar_one_or_none()

        if existing:
            existing.score = score
            existing.scored_at = datetime.now(timezone.utc)
            existing.scored_by = user.id
            # Keep existing source/details — only update score
        else:
            db.add(AxisScoreRecord(
                session_id=session_id,
                tool_id=session.tool_id,
                axis=axis,
                axis_name_jp=AXIS_NAMES_JP[axis],
                score=score,
                confidence=0.5,
                source="manual_edit",
                details=[],
                strengths=[],
                risks=[],
                scored_by=user.id,
            ))

        updated.append({"axis": axis, "score": score})

    await db.commit()

    # Calculate overall from all current axis scores
    all_scores_result = await db.execute(
        select(AxisScoreRecord).where(AxisScoreRecord.session_id == session_id)
    )
    all_scores = {r.axis: r.score for r in all_scores_result.scalars()}
    overall = round(sum(all_scores.values()) / len(all_scores), 1) if all_scores else 0.0

    from aixis_agent.core.enums import OverallGrade
    grade = OverallGrade.from_score(overall)

    return {
        "status": "ok",
        "updated": updated,
        "overall_score": overall,
        "overall_grade": grade.value,
        "all_scores": all_scores,
    }


@router.get("/{session_id}/manual-scores")
async def get_manual_scores(
    session_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    _user: Annotated[User, Depends(require_analyst)],
):
    """Return saved manual checklist entries for a session."""
    result = await db.execute(
        select(ManualChecklistRecord).where(
            ManualChecklistRecord.session_id == session_id
        )
    )
    records = result.scalars().all()
    return {
        "items": [
            {
                "checklist_item_id": r.checklist_item_id,
                "axis": r.axis,
                "item_name_jp": r.item_name_jp,
                "passed": r.passed,
                "score": r.score,
                "weight": r.weight,
                "evidence": r.evidence or "",
                "evidence_url": r.evidence_url or "",
            }
            for r in records
        ]
    }


@router.post("/{session_id}/manual-scores", status_code=status.HTTP_201_CREATED)
async def submit_manual_scores(
    session_id: str,
    body: ManualScoreSubmit,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(require_analyst)],
):
    """Submit manual checklist scores for an audit session.

    After saving, automatically merges with auto scores and publishes.
    """
    result = await db.execute(
        select(AuditSession).where(AuditSession.id == session_id, AuditSession.deleted_at.is_(None))
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="監査セッションが見つかりません",
        )

    if session.status in ("scoring", "running", "pending"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"セッションは「{session.status}」状態です。自動評価完了後に手動評価を送信してください。",
        )

    for item in body.items:
        # Upsert: update existing entry or create new one
        existing = await db.execute(
            select(ManualChecklistRecord).where(
                ManualChecklistRecord.session_id == session_id,
                ManualChecklistRecord.checklist_item_id == item.checklist_item_id,
            )
        )
        existing_record = existing.scalar_one_or_none()

        if existing_record:
            existing_record.axis = item.axis
            existing_record.item_name_jp = item.item_name_jp
            existing_record.passed = item.passed
            existing_record.score = item.score
            existing_record.weight = item.weight
            existing_record.evidence = item.evidence
            existing_record.evidence_url = item.evidence_url
            existing_record.evaluated_by = user.id
            existing_record.evaluated_at = datetime.now(timezone.utc)
        else:
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

    # Auto-merge: combine auto + manual scores and publish
    from ...services.score_service import merge_and_publish

    try:
        published = await merge_and_publish(
            db=db,
            session_id=session_id,
            tool_id=session.tool_id,
            published_by=user.id,
        )
        return {
            "status": "ok",
            "count": len(body.items),
            "merged": True,
            "overall_score": published.overall_score,
            "overall_grade": published.overall_grade,
        }
    except Exception as e:
        logger.warning("Auto-merge after manual scores failed for %s: %s", session_id, e)
        return {"status": "ok", "count": len(body.items), "merged": False}


@router.post("/{session_id}/rescore")
async def rescore_audit(
    session_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    _user: Annotated[User, Depends(require_analyst)],
    background_tasks: BackgroundTasks,
):
    """Re-run LLM scoring for an existing session."""

    session = (await db.execute(
        select(AuditSession).where(AuditSession.id == session_id, AuditSession.deleted_at.is_(None))
    )).scalar_one_or_none()
    if not session:
        raise HTTPException(404, "セッションが見つかりません")

    if session.status == "scoring":
        raise HTTPException(400, "すでにスコアリング中です。完了をお待ちください。")
    if session.status in ("running", "pending"):
        raise HTTPException(400, "監査がまだ実行中です。完了後に再スコアリングしてください。")

    tool_id = session.tool_id

    # Only update status here — scores are deleted in background task
    # right before new scores are written (ON CONFLICT handles upsert).
    # This prevents data loss if background task fails.
    await db.execute(text("""
        UPDATE audit_sessions SET status = 'scoring', error_message = NULL WHERE id = :sid
    """), {"sid": session_id})
    await db.commit()

    # Schedule re-scoring via FastAPI BackgroundTasks (reliable execution)
    background_tasks.add_task(_run_rescore_bg, session_id, tool_id)

    return {"status": "scoring", "message": "再スコアリングを開始しました"}


async def _run_rescore_bg(session_id: str, tool_id: str):
    """Background task for re-scoring. Runs after response is sent."""
    from ...db.base import async_session

    logger.info("=== Re-scoring START for session %s (tool %s) ===", session_id, tool_id)
    try:
        from ...services.llm_scorer import LLMScorer

        async with async_session() as scoring_db:
            # Delete old scores right before generating new ones (not in foreground)
            # so data is preserved if this task never runs
            await scoring_db.execute(
                text("DELETE FROM axis_scores WHERE session_id = :sid"),
                {"sid": session_id},
            )
            await scoring_db.commit()

            scorer = LLMScorer()
            scores = await scorer.score_session(session_id, tool_id, scoring_db)
            logger.info("Re-scoring produced %d axis scores for %s", len(scores), session_id)

            # Check if any scores were actually written
            count_result = await scoring_db.execute(
                text("SELECT COUNT(*) FROM axis_scores WHERE session_id = :sid"),
                {"sid": session_id},
            )
            score_count = count_result.scalar() or 0

            if score_count > 0:
                await scoring_db.execute(
                    text("UPDATE audit_sessions SET status = 'awaiting_manual' WHERE id = :sid"),
                    {"sid": session_id},
                )
                logger.info("=== Re-scoring COMPLETED for session %s: %d axes scored, awaiting manual ===", session_id, score_count)
            else:
                err_msg = f"LLMスコアリングが0件のスコアを返しました（scores returned: {len(scores)}）"
                await scoring_db.execute(
                    text("UPDATE audit_sessions SET status = 'failed', error_message = :err WHERE id = :sid"),
                    {"sid": session_id, "err": err_msg},
                )
                logger.error("=== Re-scoring FAILED for session %s: 0 scores written ===", session_id)
            await scoring_db.commit()

    except Exception as e:
        logger.exception("=== Re-scoring CRASHED for session %s: %s ===", session_id, e)
        try:
            async with async_session() as err_db:
                await err_db.execute(text("""
                    UPDATE audit_sessions SET status = 'failed', error_message = :err WHERE id = :sid
                """), {"err": str(e)[:2000], "sid": session_id})
                await err_db.commit()
        except Exception as e2:
            logger.error("Failed to write error status for session %s: %s", session_id, e2)


@router.post("/{session_id}/finalize")
async def finalize_audit(
    session_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(require_analyst)],
):
    """Finalize audit: merge auto+manual scores and publish."""
    result = await db.execute(
        select(AuditSession).where(AuditSession.id == session_id, AuditSession.deleted_at.is_(None))
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
            detail=f"セッションは {session.status} 状態です。完了済みまたは手動評価待ちのセッションのみファイナライズできます。",
        )

    # Merge auto + manual scores and publish to ToolPublishedScore
    from ...services.score_service import merge_and_publish

    try:
        published_score = await merge_and_publish(
            db=db,
            session_id=session_id,
            tool_id=session.tool_id,
            published_by=user.id,
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"スコア公開に失敗しました: {e}",
        )

    session.status = "completed"
    session.completed_at = datetime.now(timezone.utc)
    await db.commit()

    return {
        "message": "監査結果を公開しました",
        "overall_score": published_score.overall_score,
        "overall_grade": published_score.overall_grade,
        "version": published_score.version,
    }


# ---------------------------------------------------------------------------
# Proposal 1: SSE Progress Streaming
# ---------------------------------------------------------------------------

@router.get("/{session_id}/stream")
async def stream_audit_progress(
    session_id: str,
    _user: Annotated[User, Depends(require_analyst)],
):
    """SSE audit progress stream — migrated to Chrome extension."""
    return JSONResponse(status_code=501, content={"detail": _NOT_IMPLEMENTED_MSG})


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
    result = await db.execute(select(AuditSession).where(AuditSession.id == session_id, AuditSession.deleted_at.is_(None)))
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
                    "strengths": (_json.loads(s.strengths) if isinstance(s.strengths, str) else s.strengths) or [],
                    "risks": (_json.loads(s.risks) if isinstance(s.risks, str) else s.risks) or [],
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
