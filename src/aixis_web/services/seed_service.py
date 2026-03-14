"""Seed master data from YAML config files on first startup."""

import logging
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.tool_industry import IndustryTag, UseCaseTag
from ..db.models.risk_governance import RegulatoryFramework

logger = logging.getLogger(__name__)

SEED_DIR = Path(__file__).resolve().parent.parent.parent.parent / "config" / "seed"


async def seed_all(db: AsyncSession) -> None:
    """Seed all master data tables if they are empty."""
    await _seed_industry_tags(db)
    await _seed_use_case_tags(db)
    await _seed_regulatory_frameworks(db)
    await db.commit()


async def _seed_industry_tags(db: AsyncSession) -> None:
    result = await db.execute(select(IndustryTag).limit(1))
    if result.scalar_one_or_none() is not None:
        return

    path = SEED_DIR / "industry_tags.yaml"
    if not path.exists():
        logger.warning("Seed file not found: %s", path)
        return

    items = yaml.safe_load(path.read_text(encoding="utf-8"))
    for item in items:
        db.add(IndustryTag(
            slug=item["slug"],
            name_jp=item["name_jp"],
            name_en=item.get("name_en"),
            parent_slug=item.get("parent_slug"),
            profile_id=item.get("profile_id"),
            sort_order=item.get("sort_order", 0),
        ))
    logger.info("Seeded %d industry tags", len(items))


async def _seed_use_case_tags(db: AsyncSession) -> None:
    result = await db.execute(select(UseCaseTag).limit(1))
    if result.scalar_one_or_none() is not None:
        return

    path = SEED_DIR / "use_case_tags.yaml"
    if not path.exists():
        logger.warning("Seed file not found: %s", path)
        return

    items = yaml.safe_load(path.read_text(encoding="utf-8"))
    for item in items:
        db.add(UseCaseTag(
            slug=item["slug"],
            name_jp=item["name_jp"],
            name_en=item.get("name_en"),
            category=item.get("category"),
            sort_order=item.get("sort_order", 0),
        ))
    logger.info("Seeded %d use case tags", len(items))


async def _seed_regulatory_frameworks(db: AsyncSession) -> None:
    result = await db.execute(select(RegulatoryFramework).limit(1))
    if result.scalar_one_or_none() is not None:
        return

    path = SEED_DIR / "regulatory_frameworks.yaml"
    if not path.exists():
        logger.warning("Seed file not found: %s", path)
        return

    items = yaml.safe_load(path.read_text(encoding="utf-8"))
    for item in items:
        db.add(RegulatoryFramework(
            slug=item["slug"],
            name_jp=item["name_jp"],
            name_en=item.get("name_en"),
            category=item["category"],
            applicable_industries=item.get("applicable_industries"),
            country=item.get("country", "JP"),
            description_jp=item.get("description_jp"),
            reference_url=item.get("reference_url"),
            sort_order=item.get("sort_order", 0),
        ))
    logger.info("Seeded %d regulatory frameworks", len(items))
