"""Authentication service."""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ..db.models.user import User
from ..api.deps import verify_password, hash_password, create_access_token


async def authenticate_user(db: AsyncSession, email: str, password: str) -> User | None:
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    # Always perform bcrypt verification to prevent timing-based user enumeration.
    # If user doesn't exist or has no password, verify against a dummy hash.
    _DUMMY_HASH = "$2b$12$LJ3m4ys3Rl3hIoS3MUOZ6.J8pJwZV3K4VFg7Xe3/ZQrCwMfNqXFfi"  # hash of "dummy"
    target_hash = (user.hashed_password if user and user.hashed_password else _DUMMY_HASH)
    password_ok = verify_password(password, target_hash)

    if not user or not user.hashed_password or not password_ok:
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
