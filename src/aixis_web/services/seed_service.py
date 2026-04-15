"""Seed master data from YAML config files on first startup."""

import logging
from datetime import datetime, timezone
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db.models.tool_industry import IndustryTag, UseCaseTag
from ..db.models.risk_governance import RegulatoryFramework
from ..db.models.user import Organization, User

logger = logging.getLogger(__name__)

SEED_DIR = Path(__file__).resolve().parent.parent.parent.parent / "config" / "seed"


async def seed_all(db: AsyncSession) -> None:
    """Seed all master data tables if they are empty."""
    await _seed_admin_user(db)
    await _seed_industry_tags(db)
    await _seed_use_case_tags(db)
    await _seed_regulatory_frameworks(db)
    await db.commit()


async def _seed_admin_user(db: AsyncSession) -> None:
    """Create or update the admin user on every startup."""
    from ..api.deps import hash_password

    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(User).where(User.email == settings.admin_email)
    )
    existing = result.scalar_one_or_none()
    if existing:
        existing.is_active = True
        # Grandfather the admin through the email-verification gate added
        # 2026-04-15. Admin accounts are manually provisioned and are not
        # required to verify their mailbox.
        if existing.email_verified_at is None:
            existing.email_verified_at = now
        logger.info("Admin user already exists, skipping password overwrite: %s", settings.admin_email)
        return

    # Ensure organization exists
    org_name = "Aixis Inc."
    result = await db.execute(
        select(Organization).where(Organization.name == org_name)
    )
    org = result.scalar_one_or_none()
    if org is None:
        org = Organization(name=org_name, name_jp="Aixis株式会社")
        db.add(org)
        await db.flush()
        logger.info("Created organization: %s", org_name)

    admin = User(
        email=settings.admin_email,
        name="管理者",
        name_jp="管理者",
        hashed_password=hash_password(settings.admin_password),
        role="admin",
        is_active=True,
        organization_id=org.id,
        # Admin accounts skip email verification — provisioned by humans.
        email_verified_at=now,
    )
    db.add(admin)
    logger.info("Created admin user: %s", settings.admin_email)


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
