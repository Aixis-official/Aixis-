"""Free-registration service — self-registration, email verification, Turnstile.

This module handles the free-registration flow introduced 2026-04-15:
    1. User submits register form (email, password, profile)
    2. Optional: Cloudflare Turnstile verification
    3. Optional: disposable-email domain check
    4. Password policy + HIBP check
    5. Create User row (is_active=True, email_verified_at=None, subscription_tier="registered")
    6. Issue EmailVerificationToken, send verification email
    7. User clicks link in email → token consumed → email_verified_at set
    8. User can now log in and access registered-tier content
"""

import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db.models.email_verification import EmailVerificationToken
from ..db.models.user import User
from ..schemas.auth import RegisterRequest

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Disposable-email blocklist (seed list; expanded in Phase 5)
# ---------------------------------------------------------------------------

_DISPOSABLE_EMAIL_DOMAINS: frozenset[str] = frozenset({
    "10minutemail.com",
    "10minutemail.net",
    "guerrillamail.com",
    "guerrillamail.net",
    "guerrillamail.org",
    "guerrillamail.biz",
    "mailinator.com",
    "mailinator.net",
    "trashmail.com",
    "trashmail.net",
    "sharklasers.com",
    "yopmail.com",
    "yopmail.net",
    "temp-mail.org",
    "tempmail.com",
    "tempmailo.com",
    "throwawaymail.com",
    "dispostable.com",
    "fakeinbox.com",
    "getairmail.com",
    "mintemail.com",
    "maildrop.cc",
    "emailondeck.com",
    "mohmal.com",
    "mailnesia.com",
    "spamgourmet.com",
    "trbvm.com",
    "discard.email",
    "33mail.com",
    "moakt.com",
})


def is_disposable_email(email: str) -> bool:
    """Return True if the email's domain is on the disposable/throwaway list."""
    if "@" not in email:
        return False
    domain = email.rsplit("@", 1)[1].strip().lower()
    if domain in _DISPOSABLE_EMAIL_DOMAINS:
        return True
    # Also match subdomains like foo.mailinator.com
    for disposable in _DISPOSABLE_EMAIL_DOMAINS:
        if domain.endswith("." + disposable):
            return True
    return False


# ---------------------------------------------------------------------------
# Cloudflare Turnstile verification
# ---------------------------------------------------------------------------

_TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


async def verify_turnstile(token: str | None, remote_ip: str | None) -> bool:
    """Verify a Cloudflare Turnstile token server-side.

    Returns True if verified or if Turnstile is not configured (graceful degradation).
    Returns False on failed verification when Turnstile IS configured.
    """
    secret = getattr(settings, "turnstile_secret_key", "") or ""
    if not secret:
        # Turnstile not configured — allow through (degradation).
        return True
    if not token:
        return False
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.post(
                _TURNSTILE_VERIFY_URL,
                data={
                    "secret": secret,
                    "response": token,
                    "remoteip": remote_ip or "",
                },
            )
            if resp.status_code != 200:
                logger.warning("Turnstile verify returned %d", resp.status_code)
                return False
            data = resp.json()
            return bool(data.get("success"))
    except Exception as exc:
        logger.warning("Turnstile verification error (fail-closed): %s", exc)
        return False


# ---------------------------------------------------------------------------
# Email verification tokens
# ---------------------------------------------------------------------------

_VERIFICATION_TOKEN_TTL_HOURS = 24


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def generate_verification_token() -> tuple[str, str]:
    """Generate a secure verification token. Returns (raw_token, token_hash)."""
    raw = secrets.token_urlsafe(48)
    return raw, _hash_token(raw)


async def issue_verification_token(db: AsyncSession, user_id: str) -> str:
    """Create a new EmailVerificationToken row and return the raw token."""
    raw, token_hash = generate_verification_token()
    now = datetime.now(timezone.utc)
    token = EmailVerificationToken(
        user_id=user_id,
        token_hash=token_hash,
        expires_at=now + timedelta(hours=_VERIFICATION_TOKEN_TTL_HOURS),
    )
    db.add(token)
    return raw


async def consume_verification_token(db: AsyncSession, raw_token: str) -> User | None:
    """Validate + consume a verification token. Returns the User on success."""
    token_hash = _hash_token(raw_token)
    now = datetime.now(timezone.utc)

    result = await db.execute(
        select(EmailVerificationToken).where(
            EmailVerificationToken.token_hash == token_hash,
            EmailVerificationToken.used_at.is_(None),
            EmailVerificationToken.expires_at > now,
        )
    )
    token: EmailVerificationToken | None = result.scalar_one_or_none()
    if not token:
        return None

    # Fetch user
    user_result = await db.execute(select(User).where(User.id == token.user_id))
    user = user_result.scalar_one_or_none()
    if not user:
        return None

    # Mark token as used and set email_verified_at if not already set
    token.used_at = now
    if not user.email_verified_at:
        user.email_verified_at = now

    return user


# ---------------------------------------------------------------------------
# Self-registration (main entry point)
# ---------------------------------------------------------------------------

class RegistrationError(ValueError):
    """Raised when registration cannot proceed."""
    pass


async def register_new_user(
    db: AsyncSession,
    payload: RegisterRequest,
    *,
    client_ip: str | None = None,
    user_agent: str | None = None,
) -> tuple[User, str]:
    """Create a new free-registered user and issue a verification token.

    Returns (user, raw_verification_token).

    Raises:
        RegistrationError: on any validation or duplicate-email failure.
    """
    from ..api.deps import hash_password
    from .client_service import PasswordPolicyError, check_password_hibp, validate_password_policy

    email = payload.email.strip().lower()

    # 1. Password match
    if payload.password != payload.password_confirm:
        raise RegistrationError("パスワードと確認用パスワードが一致しません")

    # 2. Password policy (length, uppercase, digit)
    try:
        validate_password_policy(payload.password)
    except PasswordPolicyError as exc:
        raise RegistrationError(str(exc)) from exc

    # 3. HIBP (have I been pwned) check
    if await check_password_hibp(payload.password):
        raise RegistrationError(
            "このパスワードは過去のデータ漏洩で確認されています。別のパスワードを設定してください。"
        )

    # 4. Disposable-email block
    if is_disposable_email(email):
        raise RegistrationError(
            "使い捨てメールアドレスは登録に使用できません。お勤め先のメールアドレスをお使いください。"
        )

    # 5. Duplicate email check
    existing = await db.execute(select(User).where(User.email == email))
    if existing.scalar_one_or_none():
        raise RegistrationError("このメールアドレスは既に登録されています")

    # 6. Create the user
    now = datetime.now(timezone.utc)
    user = User(
        email=email,
        name=payload.name.strip(),
        hashed_password=hash_password(payload.password),
        role="client",
        is_active=True,
        account_status="active",
        subscription_tier="registered",
        # profile
        company_name=payload.company_name.strip(),
        job_title=payload.job_title.strip(),
        industry=payload.industry,
        employee_count=payload.employee_count,
        phone=(payload.phone or "").strip() or None,
        # compliance
        agreed_to_terms_at=now,
        agreed_to_privacy_at=now,
        marketing_opt_in=bool(payload.marketing_opt_in),
        # lead gen
        registration_source=payload.registration_source or "register_page",
        lead_score=0,
        last_active_at=now,
        sales_status="uncontacted",
        # email verification pending
        email_verified_at=None,
    )
    db.add(user)
    await db.flush()  # assign user.id

    # 7. Issue verification token
    raw_token = await issue_verification_token(db, user.id)

    # Commit is the caller's responsibility (so they can stage more changes atomically)
    return user, raw_token


async def resend_verification_email_token(
    db: AsyncSession, email: str
) -> tuple[User, str] | None:
    """Re-issue a verification token for a still-unverified user.

    Returns (user, raw_token) or None if no matching unverified user exists.
    Caller must commit.
    """
    normalized = email.strip().lower()
    result = await db.execute(select(User).where(User.email == normalized))
    user = result.scalar_one_or_none()
    if not user:
        return None
    if user.email_verified_at:
        # Already verified — nothing to do, but don't reveal this (enumeration)
        return None
    raw = await issue_verification_token(db, user.id)
    return user, raw
