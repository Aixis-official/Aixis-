"""Authentication endpoints."""
import time
from collections import defaultdict, deque
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import settings
from ...db.base import get_db
from ...db.models.user import User
from ...schemas.auth import LoginRequest, TokenResponse, UserResponse
from ..deps import (
    create_access_token,
    create_refresh_token,
    require_auth,
    verify_password,
)

router = APIRouter()

# Login rate limiting: IP -> deque of timestamps
_login_attempts: dict[str, deque] = defaultdict(deque)
_LOGIN_MAX_ATTEMPTS = 3  # max attempts per window (stricter: 3 instead of 5)
_LOGIN_WINDOW_SECONDS = 300  # 5-minute window
# Track consecutive failures for progressive lockout
_login_failures: dict[str, int] = defaultdict(int)


def _check_login_rate(ip: str) -> tuple[bool, int]:
    """Return (allowed, retry_after_seconds). Applies progressive lockout."""
    now = time.time()
    window = _login_attempts[ip]
    while window and window[0] < now - _LOGIN_WINDOW_SECONDS:
        window.popleft()
    if len(window) >= _LOGIN_MAX_ATTEMPTS:
        # Progressive lockout: base 5min * (1 + failure_count // 3)
        multiplier = 1 + _login_failures[ip] // _LOGIN_MAX_ATTEMPTS
        retry_after = _LOGIN_WINDOW_SECONDS * min(multiplier, 4)  # Cap at 20min
        return False, retry_after
    window.append(now)
    return True, 0


def _record_login_failure(ip: str) -> None:
    """Track consecutive failures for progressive lockout."""
    _login_failures[ip] += 1


def _reset_login_failures(ip: str) -> None:
    """Reset failure counter on successful login."""
    _login_failures.pop(ip, None)


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Authenticate user and return JWT tokens."""
    # Rate limit by client IP (progressive lockout)
    client_ip = request.client.host if request.client else "unknown"
    allowed, retry_after = _check_login_rate(client_ip)
    if not allowed:
        mins = retry_after // 60
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"ログイン試行回数が上限に達しました。{mins}分後に再度お試しください。",
            headers={"Retry-After": str(retry_after)},
        )

    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()

    if not user or not user.hashed_password:
        _record_login_failure(client_ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="メールアドレスまたはパスワードが正しくありません",
        )

    if not verify_password(body.password, user.hashed_password):
        _record_login_failure(client_ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="メールアドレスまたはパスワードが正しくありません",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="アカウントが無効化されています",
        )

    # Successful login — reset failure counter
    _reset_login_failures(client_ip)

    access_token = create_access_token(data={"sub": user.id, "role": user.role})
    refresh_token = create_refresh_token(data={"sub": user.id})

    # Set HttpOnly cookie for SSR page authentication (more secure than localStorage)
    max_age = settings.access_token_expire_minutes * 60
    # Always use Secure flag unless explicitly running in local debug mode
    is_production = not settings.debug
    response.set_cookie(
        key="aixis_token",
        value=access_token,
        max_age=max_age,
        path="/",
        httponly=True,
        samesite="lax",
        secure=is_production,
    )

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        expires_in=max_age,
    )


@router.get("/me", response_model=UserResponse)
async def get_me(user: Annotated[User, Depends(require_auth)]):
    """Get the currently authenticated user."""
    return user
