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


def invalidate_stats_cache() -> None:
    """Clear the in-memory stats cache.

    Called from ``score_service.publish_score`` when a new published score is
    written so the next ``/api/v1/stats`` request recomputes instead of
    serving up to 5-minute-stale counts on landing/tools pages.
    """
    _stats_cache["data"] = None
    _stats_cache["ts"] = 0


class PlatformStats(BaseModel):
    audited_tools: int = 0
    categories: int = 0
    total_audits: int = 0
    last_updated: str | None = None
    new_this_month: int = 0
    # Platform-wide average of the latest published overall score per tool.
    # ``None`` when no tool has a published score yet. Exposed publicly —
    # the aggregate does not reveal individual tool scores.
    average_score: float | None = None


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

        # Total audits = total published score versions (each publication is
        # one audit output). Legacy note: earlier revisions counted only
        # ``AuditSession.status == "completed"`` rows, but published scores can
        # be inserted without a corresponding AuditSession (e.g., admin
        # manual publish, historical data), which caused the public widget to
        # show ``1 評価済みツール / 0 総監査回数`` — internally inconsistent.
        # Counting ToolPublishedScore rows aligns the metric with what users
        # actually see on the database.
        audit_count = await db.execute(
            select(func.count()).select_from(ToolPublishedScore)
        )
        total_audits_from_publish = audit_count.scalar() or 0
        # Fall back to AuditSession count if it happens to be higher (e.g.,
        # in-flight sessions not yet publishing a score).
        session_count = await db.execute(
            select(func.count()).select_from(AuditSession).where(
                AuditSession.status == "completed"
            )
        )
        total_audits_from_sessions = session_count.scalar() or 0
        total_audits = max(total_audits_from_publish, total_audits_from_sessions)

        # Last updated (most recent completed audit or published score)
        # Convert to JST (UTC+9) for Japanese users
        JST = timezone(timedelta(hours=9))
        last_score = await db.execute(
            select(func.max(ToolPublishedScore.published_at))
        )
        last_updated_dt = last_score.scalar()
        if last_updated_dt:
            # Ensure timezone-aware, then convert to JST
            if last_updated_dt.tzinfo is None:
                last_updated_dt = last_updated_dt.replace(tzinfo=timezone.utc)
            last_updated_jst = last_updated_dt.astimezone(JST)
            last_updated = last_updated_jst.strftime("%Y.%m.%d")
        else:
            last_updated = None

        # New this month (distinct tools that got a new published score)
        now = datetime.now(timezone.utc)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        new_month = await db.execute(
            select(func.count(func.distinct(ToolPublishedScore.tool_id))).where(
                ToolPublishedScore.published_at >= month_start
            )
        )
        new_this_month = new_month.scalar() or 0

        # Platform-wide average of each tool's latest published overall score.
        # A tool can have multiple published versions — we only want the newest
        # version per tool, then average across tools. ``overall_score`` is
        # nullable so we filter it out before averaging.
        latest_ver_sub = (
            select(
                ToolPublishedScore.tool_id,
                func.max(ToolPublishedScore.version).label("max_ver"),
            )
            .group_by(ToolPublishedScore.tool_id)
            .subquery()
        )
        avg_result = await db.execute(
            select(func.avg(ToolPublishedScore.overall_score))
            .join(
                latest_ver_sub,
                (ToolPublishedScore.tool_id == latest_ver_sub.c.tool_id)
                & (ToolPublishedScore.version == latest_ver_sub.c.max_ver),
            )
            .where(ToolPublishedScore.overall_score.isnot(None))
        )
        avg_raw = avg_result.scalar()
        average_score = round(float(avg_raw), 2) if avg_raw is not None else None

        result = PlatformStats(
            audited_tools=audited_tools,
            categories=categories,
            total_audits=total_audits,
            last_updated=last_updated,
            new_this_month=new_this_month,
            average_score=average_score,
        )
        _stats_cache["data"] = result
        _stats_cache["ts"] = time.time()
        return result
    except Exception:
        logger.exception("Failed to compute platform stats")
        return PlatformStats()
