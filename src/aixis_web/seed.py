"""Seed script to create initial admin user and organization.

Usage:
    uv run python -m aixis_web.seed
"""

import asyncio
import logging

from sqlalchemy import select

from aixis_web.api.deps import hash_password
from aixis_web.config import settings
from aixis_web.db.base import async_session, init_db
from aixis_web.db.models.user import Organization, User

logger = logging.getLogger(__name__)


async def seed() -> None:
    """初期管理者ユーザーと組織を作成する。"""
    await init_db()

    async with async_session() as session:
        # --- 既存ユーザー確認 ---
        result = await session.execute(
            select(User).where(User.email == settings.admin_email)
        )
        existing = result.scalar_one_or_none()
        if existing:
            # Update password to match current config (supports password rotation)
            existing.hashed_password = hash_password(settings.admin_password)
            existing.is_active = True
            await session.commit()
            logger.info("[更新] 管理者ユーザー (%s) のパスワードを更新しました。", settings.admin_email)
            return

        # --- 組織の作成（存在しなければ） ---
        org_name = "Aixis Inc."
        result = await session.execute(
            select(Organization).where(Organization.name == org_name)
        )
        org = result.scalar_one_or_none()
        if org is None:
            org = Organization(name=org_name, name_jp="Aixis株式会社")
            session.add(org)
            await session.flush()  # org.id を確定
            logger.info("[作成] 組織 '%s' を作成しました。", org_name)
        else:
            logger.info("[スキップ] 組織 '%s' は既に存在します。", org_name)

        # --- 管理者ユーザーの作成 ---
        admin = User(
            email=settings.admin_email,
            name="管理者",
            name_jp="管理者",
            hashed_password=hash_password(settings.admin_password),
            role="admin",
            is_active=True,
            organization_id=org.id,
        )
        session.add(admin)
        await session.commit()
        logger.info("[作成] 管理者ユーザー (%s) を作成しました。", settings.admin_email)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(seed())
