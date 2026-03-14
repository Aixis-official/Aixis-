"""Comparison endpoints."""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.base import get_db
from ...db.models.comparison import (
    ComparisonGroup,
    ComparisonMember,
    ComparisonNormalizedScore,
)
from ...db.models.score import ToolPublishedScore
from ...db.models.tool import Tool, ToolCategory
from ...db.models.user import User
from ...schemas.comparison import (
    AddToolRequest,
    ComparisonCreate,
    ComparisonMemberResponse,
    ComparisonResponse,
    NormalizedScoreResponse,
)
from ..deps import require_analyst

router = APIRouter()


@router.post("/", response_model=ComparisonResponse, status_code=status.HTTP_201_CREATED)
async def create_comparison(
    body: ComparisonCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(require_analyst)],
):
    """Create a comparison group."""
    group = ComparisonGroup(
        name=body.name,
        name_jp=body.name_jp,
        category_id=body.category_id,
        description_jp=body.description_jp,
        created_by=user.id,
    )
    db.add(group)
    await db.flush()

    for idx, tool_id in enumerate(body.tool_ids):
        member = ComparisonMember(
            group_id=group.id,
            tool_id=tool_id,
            sort_order=idx,
        )
        db.add(member)

    await db.commit()
    await db.refresh(group)
    return await _build_comparison_response(group, db)


@router.get("/{group_id}", response_model=ComparisonResponse)
async def get_comparison(
    group_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Get comparison data."""
    result = await db.execute(
        select(ComparisonGroup).where(ComparisonGroup.id == group_id)
    )
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="比較グループが見つかりません",
        )
    return await _build_comparison_response(group, db)


@router.post("/{group_id}/tools", status_code=status.HTTP_201_CREATED)
async def add_tool_to_comparison(
    group_id: str,
    body: AddToolRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    _user: Annotated[User, Depends(require_analyst)],
):
    """Add a tool to a comparison group."""
    result = await db.execute(
        select(ComparisonGroup).where(ComparisonGroup.id == group_id)
    )
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="比較グループが見つかりません",
        )

    # Check if tool already in group
    existing = await db.execute(
        select(ComparisonMember).where(
            ComparisonMember.group_id == group_id,
            ComparisonMember.tool_id == body.tool_id,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="このツールは既に比較グループに含まれています",
        )

    member = ComparisonMember(
        group_id=group_id,
        tool_id=body.tool_id,
        session_id=body.session_id,
    )
    db.add(member)
    await db.commit()
    return {"status": "ok"}


@router.get("/categories/{category_slug}/benchmark", response_model=ComparisonResponse)
async def auto_benchmark(
    category_slug: str,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Auto-generate benchmark comparison for a category."""
    cat_result = await db.execute(
        select(ToolCategory).where(ToolCategory.slug == category_slug)
    )
    category = cat_result.scalar_one_or_none()
    if not category:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="カテゴリが見つかりません",
        )

    # Find tools in this category that have published scores
    tools_result = await db.execute(
        select(Tool)
        .join(ToolPublishedScore, ToolPublishedScore.tool_id == Tool.id)
        .where(Tool.category_id == category.id, Tool.is_public.is_(True))
        .order_by(ToolPublishedScore.overall_score.desc())
    )
    tools = tools_result.scalars().all()

    # Create ad-hoc comparison response
    members = []
    for idx, tool in enumerate(tools):
        members.append(
            ComparisonMemberResponse(
                tool_id=tool.id,
                tool_name=tool.name,
                tool_name_jp=tool.name_jp,
                session_id=None,
                sort_order=idx,
            )
        )

    return ComparisonResponse(
        id="auto",
        name=f"{category.name_jp} ベンチマーク",
        name_jp=f"{category.name_jp} ベンチマーク",
        category_id=category.id,
        description_jp=f"{category.name_jp} カテゴリの自動ベンチマーク比較",
        created_at=category.created_at if hasattr(category, "created_at") else None,
        members=members,
        normalized_scores=[],
    )


async def _build_comparison_response(
    group: ComparisonGroup, db: AsyncSession
) -> ComparisonResponse:
    """Build full comparison response with members and scores."""
    members_result = await db.execute(
        select(ComparisonMember, Tool)
        .join(Tool, ComparisonMember.tool_id == Tool.id)
        .where(ComparisonMember.group_id == group.id)
        .order_by(ComparisonMember.sort_order)
    )
    member_rows = members_result.all()

    members = [
        ComparisonMemberResponse(
            tool_id=tool.id,
            tool_name=tool.name,
            tool_name_jp=tool.name_jp,
            session_id=member.session_id,
            sort_order=member.sort_order,
        )
        for member, tool in member_rows
    ]

    scores_result = await db.execute(
        select(ComparisonNormalizedScore).where(
            ComparisonNormalizedScore.group_id == group.id
        )
    )
    norm_scores = [
        NormalizedScoreResponse(
            tool_id=s.tool_id,
            axis=s.axis,
            raw_score=s.raw_score,
            normalized_score=s.normalized_score,
            percentile=s.percentile,
        )
        for s in scores_result.scalars().all()
    ]

    return ComparisonResponse(
        id=group.id,
        name=group.name,
        name_jp=group.name_jp,
        category_id=group.category_id,
        description_jp=group.description_jp,
        created_at=group.created_at,
        members=members,
        normalized_scores=norm_scores,
    )
