"""Comparison and benchmarking service."""
import bisect
import math
from sqlalchemy import delete, select, func
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.comparison import ComparisonGroup, ComparisonMember, ComparisonNormalizedScore
from ..db.models.score import ToolPublishedScore
from ..db.models.tool import Tool, ToolCategory
from aixis_agent.core.enums import ScoreAxis

ALL_AXES = [a.value for a in ScoreAxis]


async def create_comparison(db: AsyncSession, name: str, name_jp: str = "",
                             category_id: str | None = None, created_by: str | None = None) -> ComparisonGroup:
    group = ComparisonGroup(name=name, name_jp=name_jp, category_id=category_id, created_by=created_by)
    db.add(group)
    await db.commit()
    await db.refresh(group)
    return group


async def add_tool_to_comparison(db: AsyncSession, group_id: str, tool_id: str, session_id: str | None = None):
    member = ComparisonMember(group_id=group_id, tool_id=tool_id, session_id=session_id)
    db.add(member)
    await db.commit()


async def compute_normalized_scores(db: AsyncSession, group_id: str):
    """Compute z-score normalized scores within a comparison group."""
    members = await db.execute(select(ComparisonMember).where(ComparisonMember.group_id == group_id))
    member_list = members.scalars().all()

    if len(member_list) < 2:
        return  # Need at least 2 tools to compare

    tool_ids = [m.tool_id for m in member_list]

    # Get latest published scores for each tool
    tool_scores = {}
    for tool_id in tool_ids:
        result = await db.execute(
            select(ToolPublishedScore)
            .where(ToolPublishedScore.tool_id == tool_id)
            .order_by(ToolPublishedScore.version.desc())
        )
        score = result.scalar_one_or_none()
        if score:
            tool_scores[tool_id] = {
                "practicality": score.practicality,
                "cost_performance": score.cost_performance,
                "localization": score.localization,
                "safety": score.safety,
                "uniqueness": score.uniqueness,
            }

    if len(tool_scores) < 2:
        return

    # Compute z-scores per axis
    for axis in ALL_AXES:
        values = [tool_scores[tid][axis] for tid in tool_scores]
        mean = sum(values) / len(values)
        variance = sum((v - mean) ** 2 for v in values) / len(values)
        stddev = math.sqrt(variance) if variance > 0 else 1.0

        for tool_id in tool_scores:
            raw = tool_scores[tool_id][axis]
            z = (raw - mean) / stddev if stddev > 0 else 0.0
            normalized = max(0.0, min(5.0, 2.5 + z * 1.0))

            # Sort values to compute percentile (bisect avoids float equality issues)
            sorted_vals = sorted(values)
            rank = bisect.bisect_right(sorted_vals, raw)
            percentile = (rank / len(sorted_vals)) * 100

            # Delete existing if any
            await db.execute(
                delete(ComparisonNormalizedScore).where(
                    ComparisonNormalizedScore.group_id == group_id,
                    ComparisonNormalizedScore.tool_id == tool_id,
                    ComparisonNormalizedScore.axis == axis,
                )
            )

            db.add(ComparisonNormalizedScore(
                group_id=group_id,
                tool_id=tool_id,
                axis=axis,
                raw_score=raw,
                normalized_score=round(normalized, 1),
                percentile=round(percentile, 1),
            ))

    await db.commit()


async def auto_benchmark_category(db: AsyncSession, category_slug: str) -> ComparisonGroup | None:
    """Auto-create a benchmark comparison from all tools in a category."""
    cat = await db.execute(select(ToolCategory).where(ToolCategory.slug == category_slug))
    cat_obj = cat.scalar_one_or_none()
    if not cat_obj:
        return None

    # Find all tools in category with published scores
    tools = await db.execute(
        select(Tool).where(Tool.category_id == cat_obj.id, Tool.is_active.is_(True))
    )
    tool_list = tools.scalars().all()

    scored_tools = []
    for tool in tool_list:
        score = await db.execute(
            select(ToolPublishedScore).where(ToolPublishedScore.tool_id == tool.id).limit(1)
        )
        if score.scalar_one_or_none():
            scored_tools.append(tool)

    if len(scored_tools) < 2:
        return None

    group = await create_comparison(
        db,
        name=f"{cat_obj.name_jp} ベンチマーク",
        name_jp=f"{cat_obj.name_jp} ベンチマーク",
        category_id=cat_obj.id,
    )

    for tool in scored_tools:
        await add_tool_to_comparison(db, group.id, tool.id)

    await compute_normalized_scores(db, group.id)
    return group
