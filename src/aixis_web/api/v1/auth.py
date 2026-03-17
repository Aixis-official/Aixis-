"""Authentication endpoints."""
import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import settings
from ...db.base import get_db
from ...db.models.user import User
from ...schemas.auth import LoginRequest, TokenResponse, UserResponse
from ...services.rate_limit_service import check_rate_limit, count_recent_events, record_rate_limit_event
from ..deps import (
    COOKIE_NAME,
    create_access_token,
    create_refresh_token,
    require_auth,
    verify_password,
)

router = APIRouter()

_LOGIN_MAX_ATTEMPTS = 3  # max attempts per window
_LOGIN_WINDOW_SECONDS = 300  # 5-minute window


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Authenticate user and return JWT tokens."""
    # Rate limit by client IP (DB-backed, works across multiple workers)
    client_ip = request.client.host if request.client else "unknown"
    rate_key = f"login:{client_ip}"

    # Progressive lockout: check failure count to determine window multiplier
    failure_count = await count_recent_events(db, f"login_fail:{client_ip}", _LOGIN_WINDOW_SECONDS * 4)
    multiplier = min(1 + failure_count // _LOGIN_MAX_ATTEMPTS, 4)  # Cap at 20min
    effective_window = _LOGIN_WINDOW_SECONDS * multiplier

    allowed, retry_after = await check_rate_limit(
        db, rate_key, _LOGIN_MAX_ATTEMPTS, effective_window
    )
    if not allowed:
        await db.commit()
        mins = retry_after // 60
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"ログイン試行回数が上限に達しました。{mins}分後に再度お試しください。",
            headers={"Retry-After": str(retry_after)},
        )

    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()

    if not user or not user.hashed_password:
        await record_rate_limit_event(db, f"login_fail:{client_ip}")
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="メールアドレスまたはパスワードが正しくありません",
        )

    if not verify_password(body.password, user.hashed_password):
        await record_rate_limit_event(db, f"login_fail:{client_ip}")
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="メールアドレスまたはパスワードが正しくありません",
        )

    if not user.is_active:
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="アカウントが無効化されています",
        )

    access_token = create_access_token(data={"sub": user.id, "role": user.role})
    refresh_token = create_refresh_token(data={"sub": user.id})

    # Session tracking: create session record and enforce concurrent session limit
    try:
        from ...services.session_service import create_session, enforce_session_limit
        from jose import jwt as jose_jwt
        payload = jose_jwt.decode(access_token, settings.secret_key, algorithms=["HS256"])
        jti = payload.get("jti", "")
        await create_session(
            db,
            user_id=user.id,
            jti=jti,
            ip_address=client_ip,
            user_agent=request.headers.get("user-agent", "")[:500],
        )
        await enforce_session_limit(db, user.id)
    except Exception:
        import logging
        logging.getLogger(__name__).warning("Session tracking failed (non-critical)", exc_info=True)

    # Set HttpOnly cookie for SSR page authentication (more secure than localStorage)
    max_age = settings.access_token_expire_minutes * 60
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

    # Regenerate CSRF token after login (Medium 1: session fixation prevention)
    response.set_cookie(
        key="aixis_csrf",
        value=secrets.token_urlsafe(32),
        max_age=86400,
        path="/",
        httponly=False,
        samesite="lax",
        secure=is_production,
    )

    await db.commit()

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        expires_in=max_age,
    )


@router.post("/logout", status_code=status.HTTP_200_OK)
async def logout(
    request: Request,
    response: Response,
    user: Annotated[User, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Log out by clearing auth cookies and recording token revocation."""
    is_production = not settings.debug

    # Clear the auth cookie
    response.delete_cookie(
        key=COOKIE_NAME,
        path="/",
        httponly=True,
        samesite="lax",
        secure=is_production,
    )

    # Clear CSRF cookie
    response.delete_cookie(
        key="aixis_csrf",
        path="/",
        httponly=False,
        samesite="lax",
        secure=is_production,
    )

    # Record token revocation in DB for stateless JWT invalidation
    from ...db.models.revoked_token import RevokedToken
    from ..deps import ALGORITHM
    from jose import jwt as jose_jwt

    token = None
    if auth_header := request.headers.get("Authorization", ""):
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
    if not token:
        token = request.cookies.get(COOKIE_NAME)

    if token:
        try:
            payload = jose_jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
            jti = payload.get("jti")
            exp = payload.get("exp")
            if jti and exp:
                from datetime import datetime, timezone
                revoked = RevokedToken(
                    jti=jti,
                    expires_at=datetime.fromtimestamp(exp, tz=timezone.utc),
                )
                db.add(revoked)
                # Also deactivate the session record
                from ...services.session_service import deactivate_session
                await deactivate_session(db, jti)
                await db.commit()
        except Exception:
            pass  # Token parse failure — cookie cleared anyway

    return {"message": "ログアウトしました"}


@router.get("/me", response_model=UserResponse)
async def get_me(user: Annotated[User, Depends(require_auth)]):
    """Get the currently authenticated user."""
    return user


@router.post("/change-password", status_code=status.HTTP_200_OK)
async def change_password(
    request: Request,
    user: Annotated[User, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Change the authenticated user's password."""
    from pydantic import BaseModel, Field

    class ChangePasswordRequest(BaseModel):
        current_password: str
        new_password: str = Field(min_length=8)
        new_password_confirm: str

    body = ChangePasswordRequest(**(await request.json()))

    if body.new_password != body.new_password_confirm:
        raise HTTPException(status_code=400, detail="新しいパスワードが一致しません")

    if not user.hashed_password or not verify_password(body.current_password, user.hashed_password):
        raise HTTPException(status_code=400, detail="現在のパスワードが正しくありません")

    # Password policy
    from ...services.client_service import validate_password_policy, check_password_hibp, PasswordPolicyError
    try:
        validate_password_policy(body.new_password)
    except PasswordPolicyError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if await check_password_hibp(body.new_password):
        raise HTTPException(
            status_code=400,
            detail="このパスワードは過去のデータ漏洩で確認されています。別のパスワードを設定してください。",
        )

    from ..deps import hash_password
    user.hashed_password = hash_password(body.new_password)
    await db.commit()

    return {"message": "パスワードを変更しました"}
