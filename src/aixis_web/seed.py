"""Seed script to create initial admin user and organization.

Usage:
    uv run python -m aixis_web.seed
"""

import asyncio

from sqlalchemy import select

from aixis_web.api.deps import hash_password
from aixis_web.config import settings
from aixis_web.db.base import async_session, init_db
from aixis_web.db.models.user import Organization, User


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
            print(f"[更新] 管理者ユーザー ({settings.admin_email}) のパスワードを更新しました。")
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
            print(f"[作成] 組織 '{org_name}' を作成しました。")
        else:
            print(f"[スキップ] 組織 '{org_name}' は既に存在します。")

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
        print(f"[作成] 管理者ユーザー ({settings.admin_email}) を作成しました。")


if __name__ == "__main__":
    asyncio.run(seed())
