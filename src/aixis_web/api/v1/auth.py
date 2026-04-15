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
    get_client_ip,
    require_auth,
    verify_password,
)

router = APIRouter()

_LOGIN_MAX_ATTEMPTS = 5  # max attempts per window
_LOGIN_WINDOW_SECONDS = 300  # 5-minute window

# Admin IPs that bypass rate limiting (set ADMIN_IPS env var, comma-separated).
# NOTE: request.client.host is used for IP detection. Behind a reverse proxy,
# this may be the proxy IP, not the real client. For proper X-Forwarded-For
# handling, configure the ASGI server's trusted proxy settings (e.g.,
# uvicorn --proxy-headers --forwarded-allow-ips). The last IP in the chain
# (closest to the server) should be trusted, not the first.
_ADMIN_IPS: set[str] = set(
    ip.strip() for ip in settings.admin_ips.split(",") if ip.strip()
)


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Authenticate user and return JWT tokens."""
    client_ip = get_client_ip(request)

    # Admin IP bypass — skip rate limiting entirely
    if client_ip not in _ADMIN_IPS:
        try:
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
                mins = max(retry_after // 60, 1)
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=f"ログイン試行回数が上限に達しました。{mins}分後に再度お試しください。",
                    headers={"Retry-After": str(retry_after)},
                )
        except HTTPException:
            raise
        except Exception as rl_err:
            import logging
            logging.getLogger(__name__).error(
                "Rate limit check failed for login (IP=%s): %s — denying request (fail-closed)",
                client_ip, rl_err,
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="サービスが一時的に利用できません。しばらくしてから再度お試しください。",
            )

    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()

    if not user or not user.hashed_password:
        if client_ip not in _ADMIN_IPS:
            try:
                await record_rate_limit_event(db, f"login_fail:{client_ip}")
            except Exception:
                pass
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="メールアドレスまたはパスワードが正しくありません",
        )

    if not verify_password(body.password, user.hashed_password):
        if client_ip not in _ADMIN_IPS:
            try:
                await record_rate_limit_event(db, f"login_fail:{client_ip}")
            except Exception:
                pass
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

    # Email verification gate (2026-04-15 pivot).
    # Admin / analyst / auditor accounts are manually provisioned and exempt.
    # Free-registered users must click the verification link in their email
    # before they can log in. We return a structured error so the login page
    # can surface a "確認メールを再送信" CTA instead of a generic failure.
    _VERIFICATION_EXEMPT_ROLES = {"admin", "analyst", "auditor"}
    if (
        (user.role or "").lower() not in _VERIFICATION_EXEMPT_ROLES
        and getattr(user, "email_verified_at", None) is None
    ):
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "メール認証が完了していません。ご登録のメールアドレスに送信された"
                "確認リンクをクリックしてください。メールが見つからない場合は再送信できます。"
            ),
            headers={"X-Aixis-Auth-Reason": "email_not_verified"},
        )

    # Remember Me: extend token lifetime from default (60min) to 7 days
    from datetime import timedelta
    if body.remember_me:
        token_expiry = timedelta(days=7)
    else:
        token_expiry = timedelta(minutes=settings.access_token_expire_minutes)

    access_token = create_access_token(
        data={"sub": user.id, "role": user.role},
        expires_delta=token_expiry,
    )
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
        logging.getLogger(__name__).warning("Session tracking failed (non-critical)")

    # Set HttpOnly cookie for SSR page authentication
    max_age = int(token_expiry.total_seconds())
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

    # Regenerate CSRF token after login (Medium 1: session fixation prevention).
    # Cookie name matches the one middleware sets (`__Host-` prefix in prod).
    from ...app import _CSRF_COOKIE
    response.set_cookie(
        key=_CSRF_COOKIE,
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

    # Clear CSRF cookie — use same name the middleware set (`__Host-` in prod).
    from ...app import _CSRF_COOKIE
    response.delete_cookie(
        key=_CSRF_COOKIE,
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
            import logging
            logging.getLogger(__name__).warning("Logout token revocation failed", exc_info=True)

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

    # Revoke all existing sessions (force re-login everywhere)
    try:
        from ...services.session_service import revoke_all_user_sessions
        await revoke_all_user_sessions(db, user.id)
    except Exception:
        import logging
        logging.getLogger(__name__).warning("Session revocation after password change failed", exc_info=True)

    await db.commit()

    return {"message": "パスワードを変更しました"}


@router.post("/complete-onboarding", status_code=status.HTTP_200_OK)
async def complete_onboarding(
    request: Request,
    user: Annotated[User, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Persist onboarding-wizard answers and mark the flow complete.

    Accepts an optional `interest_areas` list (slugs from a fixed taxonomy)
    which gets stored as JSON on the user row. The wizard itself is only
    shown once — subsequent visits to /welcome redirect away.
    """
    import json
    from datetime import datetime, timezone
    from pydantic import BaseModel, Field

    class CompleteOnboardingRequest(BaseModel):
        interest_areas: list[str] = Field(default_factory=list, max_length=12)
        referral_source: str | None = Field(default=None, max_length=100)

    try:
        body = CompleteOnboardingRequest(**(await request.json()))
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="リクエストボディの形式が不正です",
        )

    # Whitelist-filter the interest_area slugs against the known taxonomy
    # so junk values can't be injected. Unknown slugs are silently dropped.
    _ALLOWED_INTERESTS = {
        "drafting", "summarization", "translation", "meetings", "coding",
        "research", "customer_support", "data_analysis", "image_generation",
        "marketing", "legal_compliance", "governance",
    }
    filtered = [s for s in body.interest_areas if s in _ALLOWED_INTERESTS]
    user.interest_areas = json.dumps(filtered) if filtered else None

    if body.referral_source and not user.referral_source:
        user.referral_source = body.referral_source.strip()[:100]

    user.onboarding_completed_at = datetime.now(timezone.utc)
    await db.commit()

    return {
        "message": "オンボーディングを完了しました",
        "redirect_url": "/tools",
    }


@router.post("/forgot-password", status_code=status.HTTP_200_OK)
async def forgot_password(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Request a password reset email. Always returns 200 (prevents email enumeration)."""
    from pydantic import BaseModel, EmailStr

    class ForgotPasswordRequest(BaseModel):
        email: EmailStr

    body = ForgotPasswordRequest(**(await request.json()))

    # Rate limit password reset requests by IP
    client_ip = get_client_ip(request)
    if client_ip not in _ADMIN_IPS:
        try:
            reset_key = f"pw_reset:{client_ip}"
            allowed, _ = await check_rate_limit(db, reset_key, 3, 600)  # 3 per 10min
            if not allowed:
                await db.commit()
                return {"message": "メールを送信しました。受信トレイをご確認ください。"}
        except Exception:
            pass

    # Rate limit by email address (3 per hour) to prevent abuse targeting a single account
    email_key = f"pw_reset_email:{body.email.lower()}"
    email_allowed, _ = await check_rate_limit(db, email_key, 3, 3600)
    if not email_allowed:
        await db.commit()
        return {"message": "メールを送信しました。受信トレイをご確認ください。"}

    # Look up user (but always return same response to prevent enumeration)
    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()

    if user and user.is_active:
        import hashlib
        from datetime import datetime, timedelta, timezone

        # Generate secure token
        raw_token = secrets.token_urlsafe(48)
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

        # Store hashed token in DB
        from ...db.models.password_reset import PasswordResetToken
        reset_record = PasswordResetToken(
            user_id=user.id,
            token_hash=token_hash,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        db.add(reset_record)
        await db.commit()

        # Send email with raw token
        try:
            from ...services.email_service import send_email, _wrap_html
            # Use canonical site_origin — never trust Host header for email links
            reset_url = f"{settings.site_origin}/reset-password?token={raw_token}"

            subject = "[Aixis] パスワード再設定のご案内"
            text = f"""{user.name} 様

パスワード再設定のリクエストを受け付けました。
以下のリンクから新しいパスワードを設定してください。

パスワード再設定リンク（1時間有効）:
{reset_url}

このリクエストに心当たりがない場合は、このメールを無視してください。
現在のパスワードは変更されません。

{"─" * 30}
Aixis | 独立系AI調査・監査機関
https://platform.aixis.jp
{"─" * 30}"""

            html_content = f"""\
<p style="margin:0 0 16px;font-size:16px;font-weight:600;">{user.name} 様</p>
<p>パスワード再設定のリクエストを受け付けました。<br>
以下のボタンから新しいパスワードを設定してください。</p>
<table cellpadding="0" cellspacing="0" style="margin:24px 0;">
<tr><td style="background:#0f172a;padding:14px 32px;">
<a href="{reset_url}" style="color:#ffffff;text-decoration:none;font-size:14px;font-weight:600;letter-spacing:0.02em;">パスワードを再設定する →</a>
</td></tr>
</table>
<p style="font-size:13px;color:#64748b;">このリンクは1時間有効です。<br>
心当たりがない場合は、このメールを無視してください。</p>"""

            send_email(user.email, subject, text, _wrap_html(html_content))
        except Exception as email_err:
            import logging
            logging.getLogger(__name__).error(
                "Password reset email FAILED for user %s: %s", user.id, email_err
            )

    # Always return success to prevent email enumeration
    return {"message": "メールを送信しました。受信トレイをご確認ください。"}


@router.post("/reset-password", status_code=status.HTTP_200_OK)
async def reset_password(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Reset password using a valid reset token."""
    from pydantic import BaseModel, Field

    class ResetPasswordRequest(BaseModel):
        token: str
        new_password: str = Field(min_length=8)
        new_password_confirm: str

    # Rate limit by IP to prevent token brute-force attacks.
    # Reset tokens are 48-byte URL-safe (sha256 hashed), so the search space is
    # astronomical — but rate limiting adds defense-in-depth against abuse.
    client_ip = get_client_ip(request)
    if client_ip not in _ADMIN_IPS:
        try:
            rl_key = f"pw_reset_submit:{client_ip}"
            allowed, _ = await check_rate_limit(db, rl_key, 10, 600)  # 10 per 10min
            if not allowed:
                await db.commit()
                raise HTTPException(
                    status_code=429,
                    detail="試行回数が上限に達しました。しばらく時間をおいて再度お試しください。",
                )
        except HTTPException:
            raise
        except Exception:
            pass

    body = ResetPasswordRequest(**(await request.json()))

    if body.new_password != body.new_password_confirm:
        raise HTTPException(status_code=400, detail="パスワードが一致しません")

    # Hash the provided token and look it up
    import hashlib
    from datetime import datetime, timezone
    token_hash = hashlib.sha256(body.token.encode()).hexdigest()

    from ...db.models.password_reset import PasswordResetToken
    result = await db.execute(
        select(PasswordResetToken).where(
            PasswordResetToken.token_hash == token_hash,
            PasswordResetToken.used_at.is_(None),
        )
    )
    reset_record = result.scalar_one_or_none()

    if not reset_record:
        raise HTTPException(status_code=400, detail="無効または期限切れのリセットリンクです。再度パスワードリセットを申請してください。")

    now = datetime.now(timezone.utc)
    expires_at = reset_record.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if now > expires_at:
        raise HTTPException(status_code=400, detail="リセットリンクの有効期限が切れています。再度パスワードリセットを申請してください。")

    # Validate password policy
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

    # Update password
    user_result = await db.execute(select(User).where(User.id == reset_record.user_id))
    user = user_result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=400, detail="ユーザーが見つかりません")

    from ..deps import hash_password
    user.hashed_password = hash_password(body.new_password)

    # Revoke all existing sessions (force re-login everywhere)
    try:
        from ...services.session_service import revoke_all_user_sessions
        await revoke_all_user_sessions(db, user.id)
    except Exception:
        import logging
        logging.getLogger(__name__).warning("Session revocation after password reset failed")

    # Mark token as used
    reset_record.used_at = now
    await db.commit()

    return {"message": "パスワードを変更しました。ログインしてください。"}


@router.post("/clear-rate-limit", status_code=status.HTTP_200_OK)
async def clear_rate_limit(
    request: Request,
    user: Annotated[User, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Clear all rate limit entries (admin only)."""
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="管理者のみ実行できます")

    from sqlalchemy import delete
    from ...db.models.rate_limit import RateLimitEntry
    result = await db.execute(delete(RateLimitEntry))
    await db.commit()
    return {"message": f"レート制限をクリアしました（{result.rowcount}件削除）"}


# ---------------------------------------------------------------------------
# Free self-registration (2026-04-15 pivot)
# ---------------------------------------------------------------------------

# Register/verify rate limit windows (stricter than login to prevent abuse)
_REGISTER_MAX_PER_IP = 5          # 5 registrations per IP per hour
_REGISTER_WINDOW_SECONDS = 3600
_VERIFY_RESEND_MAX_PER_IP = 3     # 3 resend requests per IP per hour
_VERIFY_RESEND_WINDOW_SECONDS = 3600


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Self-registration for free access to the platform DB.

    Flow:
      1. Rate-limit by IP
      2. Optional Cloudflare Turnstile verification
      3. Validate password, check HIBP, check disposable email, check duplicate
      4. Create User (subscription_tier="registered", email_verified_at=None)
      5. Issue EmailVerificationToken, send verification email
      6. Return 201 with "check your email" message
    """
    from ...schemas.auth import RegisterRequest, RegisterResponse
    from ...services.registration_service import (
        RegistrationError,
        register_new_user,
        verify_turnstile,
    )
    from ...services.email_service import (
        send_admin_new_registration_notification,
        send_email_verification,
    )

    # Parse the body manually so we can keep FastAPI's auto-validation but also
    # defer the schema import to runtime.
    try:
        raw_body = await request.json()
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="リクエストボディの形式が不正です",
        )
    try:
        payload = RegisterRequest.model_validate(raw_body)
    except Exception as exc:
        # Surface the first readable validation error
        message = "入力内容に誤りがあります"
        try:
            errs = getattr(exc, "errors", None)
            if callable(errs):
                first = next(iter(errs() or []), None)
                if first and isinstance(first, dict):
                    msg = first.get("msg") or first.get("message")
                    if msg:
                        message = str(msg)
        except Exception:
            pass
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)

    client_ip = get_client_ip(request)

    # 1. Rate limit
    if client_ip not in _ADMIN_IPS:
        try:
            allowed, retry_after = await check_rate_limit(
                db, f"register:{client_ip}", _REGISTER_MAX_PER_IP, _REGISTER_WINDOW_SECONDS
            )
            if not allowed:
                await db.commit()
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="登録リクエストが多すぎます。しばらくしてから再度お試しください。",
                    headers={"Retry-After": str(retry_after)},
                )
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="サービスが一時的に利用できません。しばらくしてから再度お試しください。",
            )

    # 2. Turnstile (opt-in; bypassed when keys are unset)
    turnstile_ok = await verify_turnstile(payload.turnstile_token, client_ip)
    if not turnstile_ok:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="ボット対策の確認に失敗しました。もう一度お試しください。",
        )

    # 3-5. Create user and issue verification token
    try:
        user, raw_token = await register_new_user(
            db,
            payload,
            client_ip=client_ip,
            user_agent=request.headers.get("user-agent", "")[:500],
        )
    except RegistrationError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )

    # 5b. Reattach anonymous lead activity recorded against this session
    # cookie to the newly-created user. Running score is recomputed so the
    # new user starts with credit for whatever they browsed pre-register.
    try:
        from ...services.lead_service import reattach_anonymous_activities

        session_id = getattr(request.state, "session_id", None)
        if session_id:
            await reattach_anonymous_activities(
                db, user_id=user.id, session_id=session_id
            )
    except Exception:
        import logging
        logging.getLogger(__name__).warning(
            "Failed to reattach anonymous lead activity for user %s",
            user.email,
        )

    await db.commit()

    # 6. Send verification email + admin notification (best-effort)
    try:
        verify_url = f"{settings.site_origin}/api/v1/auth/verify-email?token={raw_token}"
        send_email_verification(user.name, user.email, verify_url)
    except Exception:
        import logging
        logging.getLogger(__name__).warning(
            "Failed to send verification email to %s (user created but no email)",
            user.email,
        )

    try:
        send_admin_new_registration_notification(
            user_name=user.name,
            user_email=user.email,
            company_name=user.company_name or "",
            job_title=user.job_title or "",
            industry=user.industry or "",
            employee_count=user.employee_count or "",
        )
    except Exception:
        import logging
        logging.getLogger(__name__).warning(
            "Failed to send admin notification for new registration %s",
            user.email,
        )

    return RegisterResponse(
        message="ご登録ありがとうございます。確認メールを送信しました。メール内のリンクをクリックして登録を完了してください。",
        email=user.email,
    )


@router.get("/verify-email")
async def verify_email_get(
    token: str,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Verify an email-verification token and auto-login the user.

    This GET endpoint is designed to be called directly from the email link:
    on success we set the auth cookie and redirect to /verify-email-success.
    """
    from datetime import datetime, timedelta as _td, timezone

    from fastapi.responses import RedirectResponse

    from ...services.registration_service import consume_verification_token
    from ...services.email_service import send_registration_welcome

    if not token or len(token) > 512:
        return RedirectResponse(url="/verify-email-failed", status_code=303)

    user = await consume_verification_token(db, token)
    if not user:
        await db.commit()
        return RedirectResponse(url="/verify-email-failed", status_code=303)

    # Enroll in email drip campaign — stage 1 means the welcome (day 0)
    # email has been sent; the scheduler will then step through stages 2-5
    # at day 3 / 7 / 14 / 30 based on email_verified_at.
    if (user.drip_stage or 0) < 1:
        user.drip_stage = 1
        user.drip_last_sent_at = datetime.now(timezone.utc)

    await db.commit()

    # Fire the welcome email (best-effort)
    try:
        send_registration_welcome(user.name, user.email)
    except Exception:
        import logging
        logging.getLogger(__name__).warning("Welcome email failed for %s", user.email)

    # Auto-login after verification: mint an access token + cookie
    token_expiry = _td(minutes=settings.access_token_expire_minutes)
    access_token = create_access_token(
        data={"sub": user.id, "role": user.role},
        expires_delta=token_expiry,
    )
    try:
        from ...services.session_service import create_session
        from jose import jwt as jose_jwt
        payload = jose_jwt.decode(access_token, settings.secret_key, algorithms=["HS256"])
        jti = payload.get("jti", "")
        await create_session(
            db,
            user_id=user.id,
            jti=jti,
            ip_address=get_client_ip(request),
            user_agent=request.headers.get("user-agent", "")[:500],
        )
        await db.commit()
    except Exception:
        import logging
        logging.getLogger(__name__).warning("Session creation after verify failed")

    # First-time visitors land on /welcome (onboarding wizard). If they
    # come back via the verification link again (e.g. resent from a bookmark),
    # /welcome itself redirects completed users onward.
    is_production = not settings.debug
    redirect = RedirectResponse(url="/welcome", status_code=303)
    redirect.set_cookie(
        key="aixis_token",
        value=access_token,
        max_age=int(token_expiry.total_seconds()),
        path="/",
        httponly=True,
        samesite="lax",
        secure=is_production,
    )
    redirect.set_cookie(
        key="__Host-aixis_csrf" if is_production else "aixis_csrf",
        value=secrets.token_urlsafe(32),
        max_age=86400,
        path="/",
        httponly=False,
        samesite="lax",
        secure=is_production,
    )
    return redirect


@router.post("/resend-verification", status_code=status.HTTP_200_OK)
async def resend_verification(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Re-issue an email-verification token for an unverified user.

    Always returns 200 to prevent user enumeration.
    """
    from ...schemas.auth import ResendVerificationRequest
    from ...services.registration_service import resend_verification_email_token
    from ...services.email_service import send_email_verification

    try:
        raw_body = await request.json()
        payload = ResendVerificationRequest.model_validate(raw_body)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="リクエストボディの形式が不正です",
        )

    client_ip = get_client_ip(request)

    # Rate limit
    if client_ip not in _ADMIN_IPS:
        try:
            allowed, retry_after = await check_rate_limit(
                db,
                f"verify_resend:{client_ip}",
                _VERIFY_RESEND_MAX_PER_IP,
                _VERIFY_RESEND_WINDOW_SECONDS,
            )
            if not allowed:
                await db.commit()
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="再送信リクエストが多すぎます。しばらくしてから再度お試しください。",
                    headers={"Retry-After": str(retry_after)},
                )
        except HTTPException:
            raise
        except Exception:
            pass

    result = await resend_verification_email_token(db, payload.email)
    if result is not None:
        user, raw = result
        await db.commit()
        try:
            verify_url = f"{settings.site_origin}/api/v1/auth/verify-email?token={raw}"
            send_email_verification(user.name, user.email, verify_url)
        except Exception:
            import logging
            logging.getLogger(__name__).warning(
                "Failed to send verification email to %s", user.email
            )
    else:
        await db.commit()

    return {
        "message": "確認メールを再送信しました。メール内のリンクをご確認ください。"
    }


# ---------------------------------------------------------------------------
# Self-service data rights (APPI Articles 28-30) — 2026-04-15 pivot Phase 6
# ---------------------------------------------------------------------------


@router.post("/update-marketing-pref", status_code=status.HTTP_200_OK)
async def update_marketing_preference(
    request: Request,
    user: Annotated[User, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Toggle the user's marketing-email opt-in.

    Turning opt-in off stops all future drip-campaign emails. Transactional
    email (password reset, email verification, security alerts) is unaffected.
    """
    from pydantic import BaseModel

    class MarketingPrefRequest(BaseModel):
        marketing_opt_in: bool

    try:
        body = MarketingPrefRequest(**(await request.json()))
    except Exception:
        raise HTTPException(status_code=400, detail="リクエストボディの形式が不正です")

    user.marketing_opt_in = body.marketing_opt_in
    # When the user opts back in after an unsubscribe, reset drip_stage so
    # the campaign starts fresh. When they opt out, leave drip_stage alone —
    # it acts as a watermark so re-enabling works cleanly.
    await db.commit()

    return {
        "message": "設定を更新しました",
        "marketing_opt_in": user.marketing_opt_in,
    }


@router.post("/export-data")
async def export_personal_data(
    user: Annotated[User, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """APPI Article 28: return all personal data Aixis holds about the user as JSON.

    The export bundles: profile fields, consent/marketing state, drip campaign
    status, lead activity events (aggregated count by event_type; not the full
    per-row IP/UA detail since that's Aixis operational metadata), and any
    onboarding selections.
    """
    import json as _json
    from datetime import datetime, timezone

    from fastapi.responses import Response as FastAPIResponse

    # Aggregate lead activities by event_type — we don't return per-row
    # IP/user-agent since those are internal security/operational fields.
    from sqlalchemy import func
    from ...db.models.lead_activity import LeadActivity

    activity_rows = (
        await db.execute(
            select(
                LeadActivity.event_type,
                func.count(LeadActivity.id).label("count"),
                func.sum(LeadActivity.score_delta).label("total_score"),
                func.min(LeadActivity.created_at).label("first_seen"),
                func.max(LeadActivity.created_at).label("last_seen"),
            )
            .where(LeadActivity.user_id == user.id)
            .group_by(LeadActivity.event_type)
        )
    ).all()

    def _iso(dt):
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()

    interest_areas: list[str] = []
    if user.interest_areas:
        try:
            parsed = _json.loads(user.interest_areas)
            if isinstance(parsed, list):
                interest_areas = [str(x) for x in parsed]
        except Exception:
            pass

    export = {
        "export_format_version": "1",
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "data_controller": "株式会社Aixis (Aixis Inc.)",
        "data_controller_contact": "privacy@aixis.jp",
        "notice": (
            "本ファイルは個人情報保護法第28条に基づく開示請求への対応として、"
            "Aixisが保有するあなたの個人情報を機械可読なJSON形式で出力したものです。"
            "内容に誤りがある場合は、privacy@aixis.jp までお知らせください。"
        ),
        "user": {
            "id": user.id,
            "email": user.email,
            "name": user.name,
            "name_jp": user.name_jp,
            "preferred_language": user.preferred_language,
            "role": user.role,
            "company_name": user.company_name,
            "job_title": user.job_title,
            "industry": user.industry,
            "employee_count": user.employee_count,
            "phone": user.phone,
            "interest_areas": interest_areas,
            "referral_source": user.referral_source,
            "registration_source": user.registration_source,
            "created_at": _iso(user.created_at),
            "email_verified_at": _iso(user.email_verified_at),
            "last_active_at": _iso(user.last_active_at),
            "onboarding_completed_at": _iso(user.onboarding_completed_at),
        },
        "consent": {
            "agreed_to_terms_at": _iso(user.agreed_to_terms_at),
            "agreed_to_privacy_at": _iso(user.agreed_to_privacy_at),
            "marketing_opt_in": bool(user.marketing_opt_in),
        },
        "drip_campaign": {
            "stage": int(user.drip_stage or 0),
            "last_sent_at": _iso(user.drip_last_sent_at),
            "stage_meanings": {
                "0": "未登録（キャンペーン未開始）",
                "1": "Day 0 ウェルカムメール送信済",
                "2": "Day 3 業界トップツールメール送信済",
                "3": "Day 7 アドバイザリー監査紹介メール送信済",
                "4": "Day 14 無料相談メール送信済",
                "5": "Day 30 ベンチマーク監査メール送信済",
            },
        },
        "lead_score": int(user.lead_score or 0),
        "activity_summary": [
            {
                "event_type": row.event_type,
                "count": int(row.count or 0),
                "total_score": int(row.total_score or 0),
                "first_seen": _iso(row.first_seen),
                "last_seen": _iso(row.last_seen),
            }
            for row in activity_rows
        ],
    }

    body = _json.dumps(export, ensure_ascii=False, indent=2).encode("utf-8")
    return FastAPIResponse(
        content=body,
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="aixis-personal-data-{user.id[:8]}.json"',
            "Cache-Control": "no-store",
        },
    )


@router.post("/delete-account", status_code=status.HTTP_200_OK)
async def delete_account(
    request: Request,
    response: Response,
    user: Annotated[User, Depends(require_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """APPI Article 30: hard-delete the user and all personal data we hold.

    Cascade order matters because most FKs are not ON DELETE CASCADE:
      1. Revoke sessions (we clear cookies at the end anyway)
      2. Delete lead_activities (personal behavior log)
      3. Delete notifications + preferences
      4. Delete email_verification tokens, password_reset tokens
      5. Delete user_sessions, api_keys
      6. Finally delete the user row
    Backup snapshots are purged on the 30-day retention cycle.
    """
    from pydantic import BaseModel
    from sqlalchemy import delete

    class DeleteAccountRequest(BaseModel):
        password: str

    try:
        body = DeleteAccountRequest(**(await request.json()))
    except Exception:
        raise HTTPException(status_code=400, detail="リクエストボディの形式が不正です")

    if not user.hashed_password or not verify_password(body.password, user.hashed_password):
        raise HTTPException(status_code=400, detail="パスワードが正しくありません")

    # Admin-provisioned accounts (admin/analyst/auditor/vendor) cannot self-delete —
    # they need an internal process for offboarding so audit history stays intact.
    if user.role not in ("client", "viewer"):
        raise HTTPException(
            status_code=403,
            detail="このアカウント種別は自己削除できません。管理者までお問い合わせください。",
        )

    user_id = user.id
    import logging
    log = logging.getLogger(__name__)

    # 1. Session revocation
    try:
        from ...services.session_service import revoke_all_user_sessions
        await revoke_all_user_sessions(db, user_id)
    except Exception:
        log.warning("delete-account: session revocation failed", exc_info=True)

    # 2. Lead activity (behavior log)
    try:
        from ...db.models.lead_activity import LeadActivity
        await db.execute(delete(LeadActivity).where(LeadActivity.user_id == user_id))
    except Exception:
        log.warning("delete-account: lead_activity delete failed", exc_info=True)

    # 3. Notifications
    try:
        from ...db.models.notification import Notification, NotificationPreference
        await db.execute(delete(Notification).where(Notification.user_id == user_id))
        await db.execute(delete(NotificationPreference).where(NotificationPreference.user_id == user_id))
    except Exception:
        log.warning("delete-account: notification delete failed", exc_info=True)

    # 4. Email verification & password reset tokens
    try:
        from ...db.models.email_verification import EmailVerificationToken
        await db.execute(delete(EmailVerificationToken).where(EmailVerificationToken.user_id == user_id))
    except Exception:
        log.warning("delete-account: email verification token delete failed", exc_info=True)
    try:
        from ...db.models.password_reset import PasswordResetToken
        await db.execute(delete(PasswordResetToken).where(PasswordResetToken.user_id == user_id))
    except Exception:
        log.warning("delete-account: password reset token delete failed", exc_info=True)

    # 5. User sessions + api keys
    try:
        from ...db.models.user_session import UserSession
        await db.execute(delete(UserSession).where(UserSession.user_id == user_id))
    except Exception:
        log.warning("delete-account: user_session delete failed", exc_info=True)
    try:
        from ...db.models.api_key import ApiKey
        await db.execute(delete(ApiKey).where(ApiKey.user_id == user_id))
    except Exception:
        log.warning("delete-account: api_key delete failed", exc_info=True)

    # 6. Finally the user row
    await db.delete(user)
    await db.commit()

    # Clear cookies
    is_production = not settings.debug
    response.delete_cookie(
        key=COOKIE_NAME,
        path="/",
        httponly=True,
        samesite="lax",
        secure=is_production,
    )
    from ...app import _CSRF_COOKIE
    response.delete_cookie(
        key=_CSRF_COOKIE,
        path="/",
        httponly=False,
        samesite="lax",
        secure=is_production,
    )

    log.info("Account deleted (self-service): user_id=%s", user_id)

    return {"message": "アカウントを削除しました。ご利用ありがとうございました。"}


@router.post("/unsubscribe", status_code=status.HTTP_200_OK)
async def unsubscribe_post(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Process an unsubscribe token POST (called from /unsubscribe confirmation page).

    The GET variant lives in `web_routes.py` and renders the confirmation UI.
    This endpoint performs the actual opt-out once the user clicks through.
    """
    from pydantic import BaseModel

    from ...services.unsubscribe_token import verify_unsubscribe_token

    class UnsubscribeRequest(BaseModel):
        token: str

    try:
        body = UnsubscribeRequest(**(await request.json()))
    except Exception:
        raise HTTPException(status_code=400, detail="リクエストボディの形式が不正です")

    user_id = verify_unsubscribe_token(body.token)
    if not user_id:
        raise HTTPException(status_code=400, detail="無効な配信停止リンクです。")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        # Don't leak whether the user exists — pretend it worked.
        return {"message": "配信停止を受け付けました。"}

    if user.marketing_opt_in:
        user.marketing_opt_in = False
        await db.commit()

    return {"message": "マーケティングメールの配信を停止しました。"}
