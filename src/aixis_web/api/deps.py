"""API dependencies for authentication and database access."""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated

import bcrypt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db.base import get_db
from ..db.models.user import User
from ..db.models.api_key import ApiKey

security = HTTPBearer(auto_error=False)

ALGORITHM = "HS256"
COOKIE_NAME = "aixis_token"


def _prehash(password: str) -> bytes:
    """SHA-256 pre-hash to safely handle passwords longer than bcrypt's 72-byte limit.

    This is the standard approach recommended by OWASP: hash the password with
    SHA-256 first, then pass the fixed-length digest to bcrypt. This preserves
    the full entropy of long passwords while staying within bcrypt's limit.
    """
    return hashlib.sha256(password.encode("utf-8")).hexdigest().encode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(_prehash(plain), hashed.encode("utf-8"))


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_prehash(password), bcrypt.gensalt()).decode("utf-8")


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.access_token_expire_minutes)
    )
    to_encode.update({
        "exp": expire,
        "type": "access",
        "jti": secrets.token_urlsafe(16),
    })
    return jwt.encode(to_encode, settings.secret_key, algorithm=ALGORITHM)


def create_refresh_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(days=settings.refresh_token_expire_days)
    to_encode.update({
        "exp": expire,
        "type": "refresh",
        "jti": secrets.token_urlsafe(16),
    })
    return jwt.encode(to_encode, settings.secret_key, algorithm=ALGORITHM)


async def get_current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User | None:
    # Try Bearer header first, then fall back to cookie
    token = None
    if credentials:
        token = credentials.credentials
    else:
        token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
        user_id = payload.get("sub")
        if not user_id:
            return None

        # Reject refresh tokens used as access tokens
        token_type = payload.get("type")
        if token_type != "access":
            return None

        # Check if token has been revoked (logout support)
        jti = payload.get("jti")
        if jti:
            from ..db.models.revoked_token import RevokedToken
            revoked = await db.execute(
                select(RevokedToken).where(RevokedToken.jti == jti)
            )
            if revoked.scalar_one_or_none():
                return None

            # Verify session is still active (concurrent session enforcement)
            try:
                from ..db.models.user_session import UserSession
                session_result = await db.execute(
                    select(UserSession).where(
                        UserSession.jti == jti, UserSession.is_active == True
                    )
                )
                session = session_result.scalar_one_or_none()
                if session:
                    now = datetime.now(timezone.utc)
                    if not session.last_active_at or (now - session.last_active_at).total_seconds() > 300:
                        session.last_active_at = now
                        await db.commit()
            except Exception:
                pass  # Session tracking is non-critical; don't block auth

    except JWTError:
        return None
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user and not user.is_active:
        return None  # Deactivated accounts can't access anything
    return user


async def require_auth(
    user: Annotated[User | None, Depends(get_current_user)],
) -> User:
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="認証が必要です"
        )
    return user


async def require_analyst(user: Annotated[User, Depends(require_auth)]) -> User:
    if user.role not in ("admin", "analyst", "auditor"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="アナリスト権限が必要です"
        )
    return user


async def require_admin(user: Annotated[User, Depends(require_auth)]) -> User:
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="管理者権限が必要です"
        )
    return user


async def require_client(user: Annotated[User, Depends(require_auth)]) -> User:
    """Allow admin, analyst, auditor, and client roles."""
    if user.role not in ("admin", "analyst", "auditor", "client"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="クライアント権限が必要です"
        )
    return user


async def require_vendor(user: Annotated[User, Depends(require_auth)]) -> User:
    """Allow admin, analyst, auditor, and vendor roles."""
    if user.role not in ("admin", "analyst", "auditor", "vendor"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="ベンダー権限が必要です"
        )
    return user


async def require_viewer(user: Annotated[User, Depends(require_auth)]) -> User:
    """Allow any authenticated user (including viewer role)."""
    return user


async def require_active_subscription(
    user: Annotated[User, Depends(require_auth)],
) -> User:
    """Require an authenticated user with an active subscription (trial or paid)."""
    from ..services.subscription_service import get_subscription_info
    info = get_subscription_info(user)
    if not info.is_active or info.tier == "free":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="有効なサブスクリプションが必要です",
        )
    return user


async def get_api_key_user(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    """Authenticate a user via X-API-Key header for public API endpoints.

    Validates the API key by SHA256 hash lookup, checks expiry/active status,
    and returns the associated User object.
    """
    api_key_raw = request.headers.get("X-API-Key")
    if not api_key_raw:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-API-Key header is required",
        )

    key_hash = hashlib.sha256(api_key_raw.encode("utf-8")).hexdigest()
    result = await db.execute(
        select(ApiKey).where(ApiKey.key_hash == key_hash)
    )
    api_key = result.scalar_one_or_none()

    if not api_key or not api_key.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or inactive API key",
        )

    # Check expiry
    if api_key.expires_at and api_key.expires_at < datetime.now(timezone.utc):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key has expired",
        )

    # Update last_used_at
    api_key.last_used_at = datetime.now(timezone.utc)
    await db.commit()

    # Fetch associated user
    user_result = await db.execute(
        select(User).where(User.id == api_key.user_id)
    )
    user = user_result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key owner not found",
        )

    # Store scopes on request state for downstream scope checks
    request.state.api_key_scopes = api_key.scopes or []
    return user


async def require_agent_or_analyst(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    jwt_user: Annotated[User | None, Depends(get_current_user)] = None,
) -> User:
    """Authenticate via API key (Chrome extension) OR JWT (dashboard).

    Allows both API-key-based and session-based authentication for
    endpoints used by both the Chrome extension and the dashboard UI.
    """
    # 1. Try API key first
    api_key_raw = request.headers.get("X-API-Key")
    if api_key_raw:
        return await get_api_key_user(request, db)

    # 2. Fall back to JWT (dashboard)
    if jwt_user and jwt_user.role in ("admin", "analyst", "auditor"):
        return jwt_user

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="認証が必要です（APIキーまたはダッシュボードログイン）",
    )
