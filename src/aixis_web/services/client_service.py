"""Client management service — create, invite, suspend, trial management."""

import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db.models.user import Organization, User

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Password policy
# ---------------------------------------------------------------------------

_MIN_PASSWORD_LENGTH = 8


class PasswordPolicyError(ValueError):
    """Raised when a password doesn't meet the policy."""
    pass


def validate_password_policy(password: str) -> None:
    """Check password meets minimum requirements."""
    if len(password) < _MIN_PASSWORD_LENGTH:
        raise PasswordPolicyError(
            f"パスワードは{_MIN_PASSWORD_LENGTH}文字以上にしてください"
        )


async def check_password_hibp(password: str) -> bool:
    """Check if password appears in HaveIBeenPwned breached database.

    Uses k-anonymity: only first 5 chars of SHA-1 hash are sent.
    Returns True if password is breached (should be rejected).
    """
    sha1 = hashlib.sha1(password.encode("utf-8")).hexdigest().upper()
    prefix, suffix = sha1[:5], sha1[5:]

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"https://api.pwnedpasswords.com/range/{prefix}",
                timeout=5,
                headers={"User-Agent": "Aixis-Platform"},
            )
            if resp.status_code != 200:
                logger.warning("HIBP API returned %d, skipping check", resp.status_code)
                return False  # Fail open — don't block user if API is down

            for line in resp.text.splitlines():
                hash_suffix, count = line.split(":")
                if hash_suffix.strip() == suffix:
                    logger.info("Password found in HIBP (%s matches)", count.strip())
                    return True
    except Exception:
        logger.warning("HIBP check failed (network error), skipping")
        return False  # Fail open

    return False


# ---------------------------------------------------------------------------
# Invite token helpers
# ---------------------------------------------------------------------------


def _hash_token(raw_token: str) -> str:
    """SHA-256 hash of raw invite token for DB storage."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def generate_invite_token() -> tuple[str, str]:
    """Generate a secure invite token. Returns (raw_token, token_hash)."""
    raw = secrets.token_urlsafe(48)
    return raw, _hash_token(raw)


# ---------------------------------------------------------------------------
# Client CRUD
# ---------------------------------------------------------------------------


async def create_client(
    db: AsyncSession,
    *,
    email: str,
    name: str,
    name_jp: str | None = None,
    organization_name: str | None = None,
) -> tuple[User, str]:
    """Create a new client account and generate an invite token.

    Returns (user, raw_invite_token).
    """
    # Check for duplicate email
    existing = await db.execute(select(User).where(User.email == email))
    if existing.scalar_one_or_none():
        raise ValueError("このメールアドレスは既に登録されています")

    # Create or find organization
    org_id = None
    if organization_name:
        org_result = await db.execute(
            select(Organization).where(Organization.name == organization_name)
        )
        org = org_result.scalar_one_or_none()
        if not org:
            org = Organization(name=organization_name)
            db.add(org)
            await db.flush()
        org_id = org.id

    # Generate invite token
    raw_token, token_hash = generate_invite_token()
    now = datetime.now(timezone.utc)

    user = User(
        email=email,
        name=name,
        name_jp=name_jp,
        role="client",
        organization_id=org_id,
        is_active=False,  # Inactive until password is set
        account_status="pending",
        subscription_tier="trial",
        invite_token_hash=token_hash,
        invite_token_expires_at=now + timedelta(hours=24),
        invite_sent_at=now,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    return user, raw_token


async def validate_invite_token(db: AsyncSession, raw_token: str) -> User | None:
    """Validate an invite token and return the associated user, or None."""
    token_hash = _hash_token(raw_token)
    now = datetime.now(timezone.utc)

    result = await db.execute(
        select(User).where(
            User.invite_token_hash == token_hash,
            User.invite_token_expires_at > now,
            User.account_status == "pending",
        )
    )
    return result.scalar_one_or_none()


async def complete_invite(
    db: AsyncSession, raw_token: str, password: str
) -> User:
    """Complete the invite flow: validate token, set password, activate account.

    Raises PasswordPolicyError or ValueError on failure.
    """
    from ..api.deps import hash_password

    user = await validate_invite_token(db, raw_token)
    if not user:
        raise ValueError("招待リンクが無効または期限切れです")

    # Password policy checks
    validate_password_policy(password)

    # HIBP check (async)
    if await check_password_hibp(password):
        raise PasswordPolicyError(
            "このパスワードは過去のデータ漏洩で確認されています。"
            "別のパスワードを設定してください。"
        )

    # Activate account
    now = datetime.now(timezone.utc)
    user.hashed_password = hash_password(password)
    user.is_active = True
    user.account_status = "active"
    user.trial_start = now
    user.trial_end = now + timedelta(days=settings.trial_duration_days)
    user.invite_token_hash = None
    user.invite_token_expires_at = None

    await db.commit()
    await db.refresh(user)
    return user


# ---------------------------------------------------------------------------
# Account management
# ---------------------------------------------------------------------------


async def suspend_client(db: AsyncSession, user_id: str) -> User:
    """Suspend a client account."""
    user = await _get_client_or_raise(db, user_id)
    user.is_active = False
    user.account_status = "suspended"
    await db.commit()
    await db.refresh(user)
    return user


async def reactivate_client(db: AsyncSession, user_id: str) -> User:
    """Reactivate a suspended or expired client account."""
    user = await _get_client_or_raise(db, user_id)
    user.is_active = True
    user.account_status = "active"
    await db.commit()
    await db.refresh(user)
    return user


async def regenerate_invite(db: AsyncSession, user_id: str) -> tuple[User, str]:
    """Regenerate invite token for a pending client."""
    user = await _get_client_or_raise(db, user_id)
    if user.account_status != "pending":
        raise ValueError("招待の再送信はパスワード未設定のクライアントのみ可能です")

    raw_token, token_hash = generate_invite_token()
    now = datetime.now(timezone.utc)
    user.invite_token_hash = token_hash
    user.invite_token_expires_at = now + timedelta(hours=24)
    user.invite_sent_at = now

    await db.commit()
    await db.refresh(user)
    return user, raw_token


async def list_clients(
    db: AsyncSession, page: int = 1, per_page: int = 50
) -> tuple[list[User], int]:
    """List all client-role users with pagination."""
    # Count
    count_result = await db.execute(
        select(func.count(User.id)).where(User.role == "client")
    )
    total = count_result.scalar() or 0

    # Fetch
    offset = (page - 1) * per_page
    result = await db.execute(
        select(User)
        .where(User.role == "client")
        .order_by(User.created_at.desc())
        .offset(offset)
        .limit(per_page)
    )
    clients = list(result.scalars().all())
    return clients, total


async def get_client(db: AsyncSession, user_id: str) -> User | None:
    """Get a single client by ID."""
    result = await db.execute(
        select(User).where(User.id == user_id, User.role == "client")
    )
    return result.scalar_one_or_none()


async def _get_client_or_raise(db: AsyncSession, user_id: str) -> User:
    user = await get_client(db, user_id)
    if not user:
        raise ValueError("クライアントが見つかりません")
    return user
