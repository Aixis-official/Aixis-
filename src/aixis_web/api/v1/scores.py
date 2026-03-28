"""Score and ranking endpoints."""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.base import get_db
from ...db.models.audit import AuditSession
from ...db.models.score import AxisScoreRecord, ScoreHistory, ToolPublishedScore
from ...db.models.tool import Tool, ToolCategory
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
            import json
            try:
                strengths = json.loads(strengths)
            except Exception:
                strengths = []
        if isinstance(risks, str):
            import json
            try:
                risks = json.loads(risks)
            except Exception:
                risks = []

        axes.append({
            "axis": record.axis,
            "axis_name_jp": record.axis_name_jp,
            "score": record.score,
            "confidence": record.confidence,
            "source": record.source,
            "strengths": strengths if isinstance(strengths, list) else [],
            "risks": risks if isinstance(risks, list) else [],
        })

    return {"tool_id": tool.id, "session_id": session.id, "axes": axes}
