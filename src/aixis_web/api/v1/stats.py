"""Public platform statistics endpoint."""
import logging
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Response
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

_stats_cache: dict = {"data": None, "ts": 0}
_STATS_TTL = 300  # 5 minutes


class PlatformStats(BaseModel):
    audited_tools: int = 0
    categories: int = 0
    total_audits: int = 0
    last_updated: str | None = None
    new_this_month: int = 0


@router.get("", response_model=PlatformStats)
async def get_platform_stats(
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Get public platform statistics. No auth required."""
    # Prevent browser HTTP caching (especially aggressive mobile caching)
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"

    now_ts = time.time()
    if _stats_cache["data"] is not None and (now_ts - _stats_cache["ts"]) < _STATS_TTL:
        return _stats_cache["data"]

    try:
        # Count public, active tools that have published scores
        tools_with_scores = await db.execute(
            select(func.count(func.distinct(ToolPublishedScore.tool_id)))
        )
        audited_tools = tools_with_scores.scalar() or 0

        # Count categories that have at least one tool with published scores
        cat_count = await db.execute(
            select(func.count(func.distinct(Tool.category_id)))
            .join(ToolPublishedScore, ToolPublishedScore.tool_id == Tool.id)
            .where(
                Tool.is_public.is_(True),
                Tool.is_active.is_(True),
                Tool.category_id.isnot(None),
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
        last_updated = last_updated_dt.strftime("%Y.%m.%d") if last_updated_dt else None

        # New this month (distinct tools that got a new published score)
        now = datetime.now(timezone.utc)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        new_month = await db.execute(
            select(func.count(func.distinct(ToolPublishedScore.tool_id))).where(
                ToolPublishedScore.published_at >= month_start
            )
        )
        new_this_month = new_month.scalar() or 0

        result = PlatformStats(
            audited_tools=audited_tools,
            categories=categories,
            total_audits=total_audits,
            last_updated=last_updated,
            new_this_month=new_this_month,
        )
        _stats_cache["data"] = result
        _stats_cache["ts"] = time.time()
        return result
    except Exception:
        logger.exception("Failed to compute platform stats")
        return PlatformStats()
