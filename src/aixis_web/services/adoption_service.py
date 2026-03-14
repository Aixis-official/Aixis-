"""Industry adoption and benchmark query service."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..db.models.adoption import IndustryAdoptionPattern
from ..db.models.tool_industry import (
    IndustryTag,
    ToolIndustryMapping,
    UseCaseTag,
    ToolUseCaseMapping,
)


# --- Industry Tags ---

async def list_industry_tags(db: AsyncSession) -> list[IndustryTag]:
    result = await db.execute(
        select(IndustryTag).order_by(IndustryTag.sort_order)
    )
    return list(result.scalars().all())


async def get_industry_tag(db: AsyncSession, slug: str) -> IndustryTag | None:
    result = await db.execute(
        select(IndustryTag).where(IndustryTag.slug == slug)
    )
    return result.scalar_one_or_none()


# --- Use Case Tags ---

async def list_use_case_tags(db: AsyncSession) -> list[UseCaseTag]:
    result = await db.execute(
        select(UseCaseTag).order_by(UseCaseTag.sort_order)
    )
    return list(result.scalars().all())


# --- Tool Industry Mappings ---

async def get_tool_industries(
    db: AsyncSession, tool_id: str
) -> list[ToolIndustryMapping]:
    result = await db.execute(
        select(ToolIndustryMapping)
        .options(selectinload(ToolIndustryMapping.industry))
        .where(ToolIndustryMapping.tool_id == tool_id)
    )
    return list(result.scalars().all())


async def add_tool_industry(
    db: AsyncSession, tool_id: str, industry_id: str,
    fit_level: str = "recommended", use_case_summary_jp: str | None = None,
) -> ToolIndustryMapping:
    mapping = ToolIndustryMapping(
        tool_id=tool_id,
        industry_id=industry_id,
        fit_level=fit_level,
        use_case_summary_jp=use_case_summary_jp,
    )
    db.add(mapping)
    await db.commit()
    await db.refresh(mapping)
    return mapping


async def remove_tool_industry(
    db: AsyncSession, tool_id: str, industry_id: str
) -> bool:
    result = await db.execute(
        select(ToolIndustryMapping).where(
            ToolIndustryMapping.tool_id == tool_id,
            ToolIndustryMapping.industry_id == industry_id,
        )
    )
    mapping = result.scalar_one_or_none()
    if not mapping:
        return False
    await db.delete(mapping)
    await db.commit()
    return True


# --- Tool Use Case Mappings ---

async def get_tool_use_cases(
    db: AsyncSession, tool_id: str
) -> list[ToolUseCaseMapping]:
    result = await db.execute(
        select(ToolUseCaseMapping)
        .options(selectinload(ToolUseCaseMapping.use_case))
        .where(ToolUseCaseMapping.tool_id == tool_id)
    )
    return list(result.scalars().all())


async def add_tool_use_case(
    db: AsyncSession, tool_id: str, use_case_id: str,
    relevance: str = "primary", description_jp: str | None = None,
) -> ToolUseCaseMapping:
    mapping = ToolUseCaseMapping(
        tool_id=tool_id,
        use_case_id=use_case_id,
        relevance=relevance,
        description_jp=description_jp,
    )
    db.add(mapping)
    await db.commit()
    await db.refresh(mapping)
    return mapping


async def remove_tool_use_case(
    db: AsyncSession, tool_id: str, use_case_id: str
) -> bool:
    result = await db.execute(
        select(ToolUseCaseMapping).where(
            ToolUseCaseMapping.tool_id == tool_id,
            ToolUseCaseMapping.use_case_id == use_case_id,
        )
    )
    mapping = result.scalar_one_or_none()
    if not mapping:
        return False
    await db.delete(mapping)
    await db.commit()
    return True


# --- Adoption Patterns ---

async def get_tool_adoption_patterns(
    db: AsyncSession, tool_id: str
) -> list[IndustryAdoptionPattern]:
    result = await db.execute(
        select(IndustryAdoptionPattern)
        .where(IndustryAdoptionPattern.tool_id == tool_id)
    )
    return list(result.scalars().all())


async def get_industry_adoption(
    db: AsyncSession, industry_id: str
) -> list[IndustryAdoptionPattern]:
    """Get all tool adoption patterns for a given industry (benchmark view)."""
    result = await db.execute(
        select(IndustryAdoptionPattern)
        .where(IndustryAdoptionPattern.industry_id == industry_id)
        .order_by(IndustryAdoptionPattern.estimated_adoption_pct.desc().nullslast())
    )
    return list(result.scalars().all())


async def create_adoption_pattern(
    db: AsyncSession, tool_id: str, data: dict
) -> IndustryAdoptionPattern:
    pattern = IndustryAdoptionPattern(tool_id=tool_id, **data)
    db.add(pattern)
    await db.commit()
    await db.refresh(pattern)
    return pattern


async def get_tools_by_industry(
    db: AsyncSession, industry_slug: str
) -> list[ToolIndustryMapping]:
    """Get all tools mapped to an industry (for benchmark pages)."""
    result = await db.execute(
        select(ToolIndustryMapping)
        .join(IndustryTag)
        .where(IndustryTag.slug == industry_slug)
        .order_by(ToolIndustryMapping.fit_level)
    )
    return list(result.scalars().all())
