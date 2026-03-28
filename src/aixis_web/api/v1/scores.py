"""Score and ranking endpoints."""
import json
import logging
from typing import Annotated

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.base import get_db
from ...db.models.audit import AuditSession, DBTestCase, DBTestResult
from ...db.models.score import AxisScoreRecord, ScoreHistory, ToolPublishedScore
from ...db.models.tool import Tool, ToolCategory
from ...db.models.risk_governance import ToolRiskGovernance
from ...schemas.score import (
    RankingEntry,
    RankingResponse,
    ScoreHistoryItem,
    ScoreHistoryResponse,
    ScoreResponse,
)

router = APIRouter()


@router.get("/rankings", response_model=RankingResponse)
async def get_rankings(
    db: Annotated[AsyncSession, Depends(get_db)],
    category_id: str | None = None,
    limit: int = Query(20, ge=1, le=100),
):
    """Get category rankings (public)."""
    # Subquery: latest version per tool to avoid duplicate entries
    latest_scores = (
        select(
            ToolPublishedScore.tool_id,
            func.max(ToolPublishedScore.version).label("max_version"),
        )
        .group_by(ToolPublishedScore.tool_id)
        .subquery()
    )

    query = (
        select(ToolPublishedScore, Tool)
        .join(Tool, ToolPublishedScore.tool_id == Tool.id)
        .join(
            latest_scores,
            (ToolPublishedScore.tool_id == latest_scores.c.tool_id)
            & (ToolPublishedScore.version == latest_scores.c.max_version),
        )
        .where(Tool.is_public.is_(True), Tool.is_active.is_(True))
    )

    category_name_jp = None
    if category_id:
        query = query.where(Tool.category_id == category_id)
        cat_result = await db.execute(
            select(ToolCategory).where(ToolCategory.id == category_id)
        )
        cat = cat_result.scalar_one_or_none()
        if cat:
            category_name_jp = cat.name_jp

    query = query.order_by(ToolPublishedScore.overall_score.desc()).limit(limit)
    result = await db.execute(query)
    rows = result.all()

    entries = []
    for rank, (score, tool) in enumerate(rows, start=1):
        entries.append(
            RankingEntry(
                tool_id=tool.id,
                tool_name=tool.name,
                tool_name_jp=tool.name_jp,
                tool_slug=tool.slug,
                overall_score=score.overall_score,
                overall_grade=score.overall_grade,
                rank=rank,
            )
        )

    return RankingResponse(
        category_id=category_id,
        category_name_jp=category_name_jp,
        entries=entries,
        total=len(entries),
    )


@router.get("/{tool_slug}", response_model=ScoreResponse)
async def get_tool_scores(
    tool_slug: str,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Get published scores for a tool (public)."""
    tool_result = await db.execute(select(Tool).where(Tool.slug == tool_slug))
    tool = tool_result.scalar_one_or_none()
    if not tool:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="ツールが見つかりません"
        )

    score_result = await db.execute(
        select(ToolPublishedScore)
        .where(ToolPublishedScore.tool_id == tool.id)
        .order_by(ToolPublishedScore.version.desc())
        .limit(1)
    )
    score = score_result.scalar_one_or_none()
    if not score:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="スコアが公開されていません",
        )

    return score


@router.get("/{tool_slug}/history", response_model=ScoreHistoryResponse)
async def get_score_history(
    tool_slug: str,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Get score history for a tool."""
    tool_result = await db.execute(select(Tool).where(Tool.slug == tool_slug))
    tool = tool_result.scalar_one_or_none()
    if not tool:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="ツールが見つかりません"
        )

    history_result = await db.execute(
        select(ScoreHistory)
        .where(ScoreHistory.tool_id == tool.id)
        .order_by(ScoreHistory.recorded_at.desc())
    )
    items = history_result.scalars().all()

    return ScoreHistoryResponse(tool_id=tool.id, items=items)


@router.get("/{tool_slug}/analysis")
async def get_tool_analysis(
    tool_slug: str,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Get per-axis analysis data (strengths, risks, details) from the latest audit.

    Returns analysis from the most recent completed audit session for public tools.
    """
    tool_result = await db.execute(
        select(Tool).where(Tool.slug == tool_slug, Tool.is_public.is_(True))
    )
    tool = tool_result.scalar_one_or_none()
    if not tool:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="ツールが見つかりません"
        )

    # Find the latest completed audit session for this tool
    session_result = await db.execute(
        select(AuditSession)
        .where(
            AuditSession.tool_id == tool.id,
            AuditSession.status == "completed",
            AuditSession.deleted_at.is_(None),
        )
        .order_by(AuditSession.completed_at.desc())
        .limit(1)
    )
    session = session_result.scalar_one_or_none()
    if not session:
        return {"tool_id": tool.id, "axes": []}

    # Get per-axis score records with analysis data
    axis_result = await db.execute(
        select(AxisScoreRecord).where(AxisScoreRecord.session_id == session.id)
    )
    axes = []
    for record in axis_result.scalars():
        strengths = record.strengths or []
        risks = record.risks or []
        if isinstance(strengths, str):
            try:
                strengths = json.loads(strengths)
            except Exception:
                strengths = []
        if isinstance(risks, str):
            try:
                risks = json.loads(risks)
            except Exception:
                risks = []

        # Parse details JSON if stored as string
        details = record.details or {}
        if isinstance(details, str):
            try:
                details = json.loads(details)
            except Exception:
                details = {}

        axes.append({
            "axis": record.axis,
            "axis_name_jp": record.axis_name_jp,
            "score": record.score,
            "confidence": record.confidence,
            "source": record.source,
            "strengths": strengths if isinstance(strengths, list) else [],
            "risks": risks if isinstance(risks, list) else [],
            "details": details if isinstance(details, dict) else {},
        })

    # Include reliability scores from the audit session
    reliability = session.reliability_scores or {}
    if isinstance(reliability, str):
        try:
            reliability = json.loads(reliability)
        except Exception:
            reliability = {}

    # If no reliability data exists, calculate it on-the-fly from audit data
    if not reliability:
        try:
            from ...services.reliability_service import calculate_reliability

            results_q = await db.execute(
                select(DBTestResult).where(DBTestResult.session_id == session.id)
            )
            results_rows = results_q.scalars().all()

            cases_q = await db.execute(
                select(DBTestCase).where(DBTestCase.session_id == session.id)
            )
            cases_rows = cases_q.scalars().all()

            total_planned = session.total_planned or len(cases_rows)
            total_executed = session.total_executed or len(results_rows)

            axis_scores_data = [{
                "axis": a["axis"], "score": a["score"],
                "confidence": a["confidence"],
                "details": a.get("details"), "strengths": a.get("strengths"),
                "risks": a.get("risks"),
            } for a in axes]

            reliability = calculate_reliability(
                results_rows, cases_rows, axis_scores_data,
                total_planned, total_executed,
            )
            # Persist for future requests
            session.reliability_scores = reliability
            await db.commit()
        except Exception as e:
            logger.warning("On-the-fly reliability calculation failed: %s", e)
            reliability = {}

    # --- Audit metadata (date, version) ---
    audit_meta = {
        "completed_at": session.completed_at.isoformat() if session.completed_at else None,
        "started_at": session.started_at.isoformat() if session.started_at else None,
        "session_code": session.session_code,
    }

    # Get published score version for this tool
    score_result = await db.execute(
        select(ToolPublishedScore)
        .where(ToolPublishedScore.tool_id == tool.id)
        .order_by(ToolPublishedScore.version.desc())
        .limit(1)
    )
    pub_score = score_result.scalar_one_or_none()
    if pub_score:
        audit_meta["score_version"] = pub_score.version
        audit_meta["published_at"] = pub_score.published_at.isoformat() if pub_score.published_at else None
    else:
        audit_meta["score_version"] = None
        audit_meta["published_at"] = None

    # --- Category positioning: rank within same category ---
    positioning = None
    if tool.category_id and pub_score:
        # Count total tools in same category with published scores
        latest_per_tool = (
            select(
                ToolPublishedScore.tool_id,
                func.max(ToolPublishedScore.version).label("max_version"),
            )
            .group_by(ToolPublishedScore.tool_id)
            .subquery()
        )
        cat_tools_q = (
            select(ToolPublishedScore, Tool)
            .join(Tool, ToolPublishedScore.tool_id == Tool.id)
            .join(
                latest_per_tool,
                (ToolPublishedScore.tool_id == latest_per_tool.c.tool_id)
                & (ToolPublishedScore.version == latest_per_tool.c.max_version),
            )
            .where(
                Tool.category_id == tool.category_id,
                Tool.is_public.is_(True),
                Tool.is_active.is_(True),
            )
            .order_by(ToolPublishedScore.overall_score.desc())
        )
        cat_result = await db.execute(cat_tools_q)
        cat_rows = cat_result.all()
        total_in_cat = len(cat_rows)

        # Find this tool's rank and per-axis ranks
        this_rank = None
        axis_keys = ["practicality", "cost_performance", "localization", "safety", "uniqueness"]
        axis_ranks = {}

        for rank_idx, (score_rec, tool_rec) in enumerate(cat_rows, start=1):
            if tool_rec.id == tool.id:
                this_rank = rank_idx

        # Per-axis ranking
        for axis_key in axis_keys:
            sorted_by_axis = sorted(
                cat_rows,
                key=lambda row: getattr(row[0], axis_key, 0) or 0,
                reverse=True,
            )
            for rank_idx, (score_rec, tool_rec) in enumerate(sorted_by_axis, start=1):
                if tool_rec.id == tool.id:
                    axis_ranks[axis_key] = rank_idx
                    break

        # Get category name
        cat_name_result = await db.execute(
            select(ToolCategory.name_jp).where(ToolCategory.id == tool.category_id)
        )
        cat_name = cat_name_result.scalar_one_or_none()

        positioning = {
            "category_name_jp": cat_name,
            "overall_rank": this_rank,
            "total_in_category": total_in_cat,
            "axis_ranks": axis_ranks,
        }

    return {
        "tool_id": tool.id,
        "session_id": session.id,
        "axes": axes,
        "reliability": reliability,
        "audit_meta": audit_meta,
        "positioning": positioning,
    }
