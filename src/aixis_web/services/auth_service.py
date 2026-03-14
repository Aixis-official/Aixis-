"""Authentication service."""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ..db.models.user import User
from ..api.deps import verify_password, hash_password, create_access_token


async def authenticate_user(db: AsyncSession, email: str, password: str) -> User | None:
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if not user or not user.hashed_password:
        return None
    if not verify_password(password, user.hashed_password):
        return None
    return user


async def create_user(db: AsyncSession, email: str, password: str, name: str, role: str = "client", **kwargs) -> User:
    user = User(
        email=email,
        hashed_password=hash_password(password),
        name=name,
        role=role,
        **kwargs,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user
