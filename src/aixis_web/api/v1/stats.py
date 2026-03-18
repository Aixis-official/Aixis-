"""Public platform statistics endpoint."""
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Annotated

from ...db.base import get_db
from ...db.models.tool import Tool, ToolCategory
from ...db.models.audit import AuditSession
from ...db.models.score import ToolPublishedScore

logger = logging.getLogger(__name__)
router = APIRouter()


class PlatformStats(BaseModel):
    audited_tools: int = 0
    categories: int = 0
    total_audits: int = 0
    last_updated: str | None = None
    new_this_month: int = 0


@router.get("", response_model=PlatformStats)
async def get_platform_stats(db: Annotated[AsyncSession, Depends(get_db)]):
    """Get public platform statistics. No auth required."""
    try:
        # Count public, active tools that have published scores
        tools_with_scores = await db.execute(
            select(func.count(func.distinct(ToolPublishedScore.tool_id)))
        )
        audited_tools = tools_with_scores.scalar() or 0

        # Count active categories that have at least one public tool
        cat_count = await db.execute(
            select(func.count(func.distinct(Tool.category_id))).where(
                Tool.is_public.is_(True), Tool.is_active.is_(True)
            )
        )
        categories = cat_count.scalar() or 0

        # Total completed audits
        audit_count = await db.execute(
            select(func.count()).select_from(AuditSession).where(
                AuditSession.status == "completed"
            )
        )
        total_audits = audit_count.scalar() or 0

        # Last updated (most recent completed audit or published score)
        last_score = await db.execute(
            select(func.max(ToolPublishedScore.published_at))
        )
        last_updated_dt = last_score.scalar()
        last_updated = last_updated_dt.strftime("%Y年%m月%d日") if last_updated_dt else None

        # New this month
        now = datetime.now(timezone.utc)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        new_month = await db.execute(
            select(func.count()).select_from(ToolPublishedScore).where(
                ToolPublishedScore.published_at >= month_start
            )
        )
        new_this_month = new_month.scalar() or 0

        return PlatformStats(
            audited_tools=audited_tools,
            categories=categories,
            total_audits=total_audits,
            last_updated=last_updated,
            new_this_month=new_this_month,
        )
    except Exception:
        logger.exception("Failed to compute platform stats")
        return PlatformStats()
