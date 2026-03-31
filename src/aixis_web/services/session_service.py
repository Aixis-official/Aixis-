"""Session management service — concurrent session tracking and enforcement."""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db.models.user_session import UserSession

logger = logging.getLogger(__name__)


async def create_session(
    db: AsyncSession,
    *,
    user_id: str,
    jti: str,
    ip_address: str = "",
    user_agent: str = "",
) -> UserSession:
    """Create a new session record for the user."""
    session = UserSession(
        user_id=user_id,
        jti=jti,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    db.add(session)
    await db.flush()
    return session


async def enforce_session_limit(db: AsyncSession, user_id: str) -> int:
    """Enforce max concurrent sessions. Evicts oldest sessions if over limit.

    Returns the number of sessions evicted.
    """
    max_sessions = settings.max_sessions_per_user

    # Get all active sessions ordered by creation (newest first)
    result = await db.execute(
        select(UserSession)
        .where(UserSession.user_id == user_id, UserSession.is_active == True)
        .order_by(UserSession.created_at.desc())
    )
    sessions = list(result.scalars().all())

    if len(sessions) <= max_sessions:
        return 0

    # Evict oldest sessions (beyond the limit)
    to_evict = sessions[max_sessions:]
    evicted = 0

    from ..db.models.revoked_token import RevokedToken

    for old_session in to_evict:
        old_session.is_active = False
        # Also revoke the JWT so it can't be used anymore
        revoked = RevokedToken(
            jti=old_session.jti,
            expires_at=datetime.now(timezone.utc) + timedelta(days=settings.refresh_token_expire_days),
        )
        db.add(revoked)
        evicted += 1
        logger.info(
            "Evicted session %s for user %s (ip=%s)",
            old_session.jti[:8],
            user_id[:8],
            old_session.ip_address,
        )

    await db.flush()
    return evicted


async def deactivate_session(db: AsyncSession, jti: str) -> None:
    """Deactivate a session by its JWT ID."""
    await db.execute(
        update(UserSession)
        .where(UserSession.jti == jti)
        .values(is_active=False)
    )


async def get_active_sessions(db: AsyncSession, user_id: str) -> list[UserSession]:
    """Get all active sessions for a user."""
    result = await db.execute(
        select(UserSession)
        .where(UserSession.user_id == user_id, UserSession.is_active == True)
        .order_by(UserSession.created_at.desc())
    )
    return list(result.scalars().all())


async def revoke_all_user_sessions(db: AsyncSession, user_id: str) -> int:
    """Revoke all active sessions for a user (e.g., on password change).

    Returns the number of sessions revoked.
    """
    from ..db.models.revoked_token import RevokedToken

    result = await db.execute(
        select(UserSession)
        .where(UserSession.user_id == user_id, UserSession.is_active == True)
    )
    sessions = list(result.scalars().all())

    revoked = 0
    for session in sessions:
        session.is_active = False
        db.add(RevokedToken(
            jti=session.jti,
            expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
        ))
        revoked += 1

    if revoked:
        await db.flush()
        logger.info("Revoked %d sessions for user %s (password change)", revoked, user_id[:8])

    return revoked


async def update_session_activity(db: AsyncSession, jti: str) -> None:
    """Update last_active_at timestamp for a session.

    Called during token validation, but throttled to avoid excessive DB writes.
    """
    now = datetime.now(timezone.utc)
    await db.execute(
        update(UserSession)
        .where(UserSession.jti == jti)
        .values(last_active_at=now)
    )
