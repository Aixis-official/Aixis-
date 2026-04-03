"""Public API endpoints for tools, scores, and rankings.

All endpoints require a valid API key via the X-API-Key header.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ...db.base import get_db
from ...db.models.api_key import ApiKey
from ...db.models.audit import AuditSession
from ...db.models.score import ScoreHistory, ToolPublishedScore
from ...db.models.tool import Tool, ToolCategory
from ...middleware.rate_limit import require_scope

router = APIRouter()


# ──── Response schemas ────


class PublicToolResponse(BaseModel):
    slug: str
    name: str
    name_jp: str
    vendor: str | None = None
    url: str | None = None
    description: str | None = None
    description_jp: str | None = None
    category_id: str | None = None
    pricing_model: str | None = None
    logo_url: str | None = None
    features: list | None = None

    model_config = {"from_attributes": True}


class PublicToolListResponse(BaseModel):
    items: list[PublicToolResponse]
    total: int
    page: int
    page_size: int


class PublicScoreResponse(BaseModel):
    tool_slug: str
    practicality: float
    cost_performance: float
    localization: float
    safety: float
    uniqueness: float
    overall_score: float
    overall_grade: str | None = None
    version: int
    published_at: str | None = None

    model_config = {"from_attributes": True}


class ScoreHistoryItem(BaseModel):
    axis: str
    score: float
    recorded_at: str

    model_config = {"from_attributes": True}


class ScoreHistoryResponse(BaseModel):
    tool_slug: str
    history: list[ScoreHistoryItem]
    total: int
    page: int
    page_size: int


class RankingItem(BaseModel):
    rank: int
    slug: str
    name: str
    name_jp: str
    overall_score: float
    overall_grade: str | None = None
    vendor: str | None = None
    logo_url: str | None = None
    category_id: str | None = None


class RankingResponse(BaseModel):
    items: list[RankingItem]
    total: int


class CompareToolScore(BaseModel):
    slug: str
    name: str
    name_jp: str
    practicality: float
    cost_performance: float
    localization: float
    safety: float
    uniqueness: float
    overall_score: float
    overall_grade: str | None = None


class CompareResponse(BaseModel):
    tools: list[CompareToolScore]


# ──── Endpoints ────


@router.get("/tools", response_model=PublicToolListResponse)
async def list_tools(
    db: Annotated[AsyncSession, Depends(get_db)],
    _key: Annotated[ApiKey, Depends(require_scope("read:tools"))],
    page: int = Query(1, ge=1, le=1000),
    page_size: int = Query(20, ge=1, le=100),
    category: str | None = Query(None, description="Filter by category slug"),
    q: str | None = Query(None, description="Search query"),
):
    """List public tools with pagination, category filter, and search."""
    query = select(Tool).where(Tool.is_public.is_(True), Tool.is_active.is_(True))
    count_query = select(func.count()).select_from(Tool).where(
        Tool.is_public.is_(True), Tool.is_active.is_(True)
    )

    # Category filter by slug
    if category:
        cat_result = await db.execute(
            select(ToolCategory).where(ToolCategory.slug == category)
        )
        cat = cat_result.scalar_one_or_none()
        if cat:
            query = query.where(Tool.category_id == cat.id)
            count_query = count_query.where(Tool.category_id == cat.id)

    # Text search
    if q:
        safe_q = q.replace("%", r"\%").replace("_", r"\_")
        pattern = f"%{safe_q}%"
        search_filter = (
            Tool.name.ilike(pattern)
            | Tool.name_jp.ilike(pattern)
            | Tool.description.ilike(pattern)
            | Tool.description_jp.ilike(pattern)
        )
        query = query.where(search_filter)
        count_query = count_query.where(search_filter)

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    offset = (page - 1) * page_size
    query = query.order_by(Tool.name_jp).offset(offset).limit(page_size)
    result = await db.execute(query)
    items = result.scalars().all()

    return PublicToolListResponse(items=items, total=total, page=page, page_size=page_size)


@router.get("/tools/{slug}", response_model=PublicToolResponse)
async def get_tool(
    slug: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    _key: Annotated[ApiKey, Depends(require_scope("read:tools"))],
):
    """Get tool detail by slug."""
    result = await db.execute(
        select(Tool).where(Tool.slug == slug, Tool.is_public.is_(True), Tool.is_active.is_(True))
    )
    tool = result.scalar_one_or_none()
    if not tool:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tool not found"
        )
    return tool


@router.get("/scores/{tool_slug}", response_model=PublicScoreResponse)
async def get_tool_scores(
    tool_slug: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    _key: Annotated[ApiKey, Depends(require_scope("read:scores"))],
):
    """Get published scores for a tool (latest version)."""
    # Find tool
    tool_result = await db.execute(
        select(Tool).where(Tool.slug == tool_slug, Tool.is_public.is_(True), Tool.is_active.is_(True))
    )
    tool = tool_result.scalar_one_or_none()
    if not tool:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tool not found"
        )

    # Get latest published score
    score_result = await db.execute(
        select(ToolPublishedScore)
        .where(ToolPublishedScore.tool_id == tool.id)
        .order_by(ToolPublishedScore.version.desc())
        .limit(1)
    )
    score = score_result.scalar_one_or_none()
    if not score:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No published scores found"
        )

    return PublicScoreResponse(
        tool_slug=tool_slug,
        practicality=score.practicality,
        cost_performance=score.cost_performance,
        localization=score.localization,
        safety=score.safety,
        uniqueness=score.uniqueness,
        overall_score=score.overall_score,
        overall_grade=score.overall_grade,
        version=score.version,
        published_at=score.published_at.isoformat() if score.published_at else None,
    )


@router.get("/scores/{tool_slug}/history", response_model=ScoreHistoryResponse)
async def get_score_history(
    tool_slug: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    _key: Annotated[ApiKey, Depends(require_scope("read:scores"))],
    page: int = Query(1, ge=1, le=1000),
    page_size: int = Query(50, ge=1, le=200),
):
    """Get score history timeline for a tool (paginated)."""
    tool_result = await db.execute(
        select(Tool).where(Tool.slug == tool_slug, Tool.is_public.is_(True), Tool.is_active.is_(True))
    )
    tool = tool_result.scalar_one_or_none()
    if not tool:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tool not found"
        )

    # Count total entries (only from published/completed sessions)
    total_result = await db.execute(
        select(func.count())
        .select_from(ScoreHistory)
        .join(AuditSession, ScoreHistory.source_session_id == AuditSession.id)
        .where(
            ScoreHistory.tool_id == tool.id,
            AuditSession.status == "completed",
            AuditSession.deleted_at.is_(None),
        )
    )
    total = total_result.scalar() or 0

    offset = (page - 1) * page_size
    history_result = await db.execute(
        select(ScoreHistory)
        .join(AuditSession, ScoreHistory.source_session_id == AuditSession.id)
        .where(
            ScoreHistory.tool_id == tool.id,
            AuditSession.status == "completed",
            AuditSession.deleted_at.is_(None),
        )
        .order_by(ScoreHistory.recorded_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    rows = history_result.scalars().all()

    return ScoreHistoryResponse(
        tool_slug=tool_slug,
        history=[
            ScoreHistoryItem(
                axis=r.axis,
                score=r.score,
                recorded_at=r.recorded_at.isoformat() if r.recorded_at else "",
            )
            for r in rows
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/rankings", response_model=RankingResponse)
async def get_rankings(
    db: Annotated[AsyncSession, Depends(get_db)],
    _key: Annotated[ApiKey, Depends(require_scope("read:rankings"))],
    category: str | None = Query(None, description="Filter by category slug"),
    limit: int = Query(20, ge=1, le=100),
):
    """Get top tools ranked by overall score."""
    # Subquery for latest score per tool
    latest_scores = (
        select(
            ToolPublishedScore.tool_id,
            func.max(ToolPublishedScore.version).label("max_version"),
        )
        .group_by(ToolPublishedScore.tool_id)
        .subquery()
    )

    query = (
        select(Tool, ToolPublishedScore)
        .join(ToolPublishedScore, ToolPublishedScore.tool_id == Tool.id)
        .join(
            latest_scores,
            (ToolPublishedScore.tool_id == latest_scores.c.tool_id)
            & (ToolPublishedScore.version == latest_scores.c.max_version),
        )
        .where(Tool.is_public.is_(True), Tool.is_active.is_(True))
    )

    if category:
        cat_result = await db.execute(
            select(ToolCategory).where(ToolCategory.slug == category)
        )
        cat = cat_result.scalar_one_or_none()
        if cat:
            query = query.where(Tool.category_id == cat.id)

    # Count total ranked tools before applying limit
    count_result = await db.execute(select(func.count()).select_from(query.subquery()))
    ranking_total = count_result.scalar() or 0

    query = query.order_by(ToolPublishedScore.overall_score.desc()).limit(limit)

    result = await db.execute(query)
    rows = result.all()

    items = []
    for rank, (tool, score) in enumerate(rows, start=1):
        items.append(
            RankingItem(
                rank=rank,
                slug=tool.slug,
                name=tool.name,
                name_jp=tool.name_jp,
                overall_score=score.overall_score,
                overall_grade=score.overall_grade,
                vendor=tool.vendor,
                logo_url=tool.logo_url,
                category_id=tool.category_id,
            )
        )

    return RankingResponse(items=items, total=ranking_total)


@router.get("/compare", response_model=CompareResponse)
async def compare_tools(
    db: Annotated[AsyncSession, Depends(get_db)],
    _key: Annotated[ApiKey, Depends(require_scope("read:scores"))],
    tools: str = Query(..., description="Comma-separated tool slugs (e.g., slug1,slug2,slug3)"),
):
    """Compare multiple tools side by side."""
    slugs = [s.strip() for s in tools.split(",") if s.strip()]
    if len(slugs) < 2:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least 2 tool slugs required",
        )
    if len(slugs) > 10:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Maximum 10 tools can be compared at once",
        )

    # Batch query: fetch all matching tools + latest scores in one query
    latest_scores = (
        select(
            ToolPublishedScore.tool_id,
            func.max(ToolPublishedScore.version).label("max_version"),
        )
        .group_by(ToolPublishedScore.tool_id)
        .subquery()
    )

    batch_result = await db.execute(
        select(Tool, ToolPublishedScore)
        .join(ToolPublishedScore, ToolPublishedScore.tool_id == Tool.id)
        .join(
            latest_scores,
            (ToolPublishedScore.tool_id == latest_scores.c.tool_id)
            & (ToolPublishedScore.version == latest_scores.c.max_version),
        )
        .where(
            Tool.slug.in_(slugs),
            Tool.is_public.is_(True),
            Tool.is_active.is_(True),
        )
    )
    rows = batch_result.all()

    # Preserve original slug order
    tool_map = {tool.slug: (tool, score) for tool, score in rows}
    result_tools = []
    for slug in slugs:
        if slug in tool_map:
            tool, score = tool_map[slug]
            result_tools.append(
                CompareToolScore(
                    slug=tool.slug,
                    name=tool.name,
                    name_jp=tool.name_jp,
                    practicality=score.practicality,
                    cost_performance=score.cost_performance,
                    localization=score.localization,
                    safety=score.safety,
                    uniqueness=score.uniqueness,
                    overall_score=score.overall_score,
                    overall_grade=score.overall_grade,
                )
            )

    return CompareResponse(tools=result_tools)
