"""Audit schedule CRUD endpoints."""

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.base import get_db
from ...db.models.schedule import AuditSchedule
from ...db.models.tool import Tool
from ...db.models.user import User
from ...schemas.schedule import ScheduleCreate, ScheduleResponse, ScheduleUpdate
from ...services.scheduler_service import _calculate_next_run
from ..deps import require_analyst

router = APIRouter()


@router.post("", response_model=ScheduleResponse, status_code=status.HTTP_201_CREATED)
async def create_schedule(
    body: ScheduleCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(require_analyst)],
):
    """Create a new audit schedule."""
    # Verify tool exists
    tool_result = await db.execute(select(Tool).where(Tool.id == body.tool_id))
    tool = tool_result.scalar_one_or_none()
    if not tool:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="ツールが見つかりません"
        )

    # Calculate initial next_run_at
    now = datetime.now(timezone.utc)
    next_run = _calculate_next_run(body.cron_expression, now)

    schedule = AuditSchedule(
        tool_id=body.tool_id,
        profile_id=body.profile_id,
        categories=body.categories,
        cron_expression=body.cron_expression,
        next_run_at=next_run,
        created_by=user.id,
    )
    db.add(schedule)
    await db.commit()
    await db.refresh(schedule)

    # Attach tool name for response
    resp = ScheduleResponse.model_validate(schedule)
    resp.tool_name = tool.name
    return resp


@router.get("", response_model=list[ScheduleResponse])
async def list_schedules(
    db: Annotated[AsyncSession, Depends(get_db)],
    _user: Annotated[User, Depends(require_analyst)],
):
    """List all audit schedules with tool names."""
    result = await db.execute(
        select(AuditSchedule, Tool.name)
        .outerjoin(Tool, AuditSchedule.tool_id == Tool.id)
        .order_by(AuditSchedule.created_at.desc())
    )
    rows = result.all()

    schedules = []
    for schedule, tool_name in rows:
        resp = ScheduleResponse.model_validate(schedule)
        resp.tool_name = tool_name
        schedules.append(resp)
    return schedules


@router.put("/{schedule_id}", response_model=ScheduleResponse)
async def update_schedule(
    schedule_id: str,
    body: ScheduleUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    _user: Annotated[User, Depends(require_analyst)],
):
    """Update a schedule (toggle active, change cron)."""
    result = await db.execute(
        select(AuditSchedule).where(AuditSchedule.id == schedule_id)
    )
    schedule = result.scalar_one_or_none()
    if not schedule:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="スケジュールが見つかりません"
        )

    if body.is_active is not None:
        schedule.is_active = body.is_active

    if body.cron_expression is not None:
        schedule.cron_expression = body.cron_expression
        schedule.next_run_at = _calculate_next_run(
            body.cron_expression, datetime.now(timezone.utc)
        )

    await db.commit()
    await db.refresh(schedule)

    # Get tool name
    tool_result = await db.execute(select(Tool).where(Tool.id == schedule.tool_id))
    tool = tool_result.scalar_one_or_none()

    resp = ScheduleResponse.model_validate(schedule)
    resp.tool_name = tool.name if tool else None
    return resp


@router.delete("/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_schedule(
    schedule_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    _user: Annotated[User, Depends(require_analyst)],
):
    """Delete a schedule."""
    result = await db.execute(
        select(AuditSchedule).where(AuditSchedule.id == schedule_id)
    )
    schedule = result.scalar_one_or_none()
    if not schedule:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="スケジュールが見つかりません"
        )
    await db.delete(schedule)
    await db.commit()


@router.post("/{schedule_id}/trigger", response_model=dict)
async def trigger_schedule(
    schedule_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    _user: Annotated[User, Depends(require_analyst)],
):
    """Manually trigger a scheduled audit now."""
    import uuid

    from ...services.audit_runner import start_audit

    result = await db.execute(
        select(AuditSchedule).where(AuditSchedule.id == schedule_id)
    )
    schedule = result.scalar_one_or_none()
    if not schedule:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="スケジュールが見つかりません"
        )

    # Get tool
    tool_result = await db.execute(select(Tool).where(Tool.id == schedule.tool_id))
    tool = tool_result.scalar_one_or_none()
    if not tool:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="ツールが見つかりません"
        )

    now = datetime.now(timezone.utc)
    session_id = f"sched-manual-{uuid.uuid4().hex[:8]}"
    db_session_id = str(uuid.uuid4())
    session_code = f"AX-{now.strftime('%Y%m%d')}-{uuid.uuid4().hex[:4].upper()}"

    # Create audit session via raw SQL to match the pattern
    from sqlalchemy import text

    await db.execute(
        text(
            """
        INSERT INTO audit_sessions (id, session_code, tool_id, profile_id, status, total_planned, total_executed, created_at)
        VALUES (:id, :code, :tool_id, :profile_id, 'pending', 0, 0, :now)
    """
        ),
        {
            "id": db_session_id,
            "code": session_code,
            "tool_id": schedule.tool_id,
            "profile_id": schedule.profile_id or "",
            "now": now.isoformat(),
        },
    )
    await db.commit()

    categories = schedule.categories if schedule.categories else None

    audit_result = start_audit(
        session_id=session_id,
        db_session_id=db_session_id,
        tool_name=tool.name,
        target_config_name=None,
        profile_id=schedule.profile_id or None,
        categories=categories,
    )

    # Update schedule last_run
    schedule.last_run_at = now
    schedule.run_count += 1
    await db.commit()

    return {
        "message": "スケジュール監査を手動トリガーしました",
        "session_id": session_id,
        "db_session_id": db_session_id,
    }
