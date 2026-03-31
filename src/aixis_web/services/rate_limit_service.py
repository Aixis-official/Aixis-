"""DB-backed rate limiting service for multi-worker environments.

Replaces in-memory rate limiters that don't work with multiple Uvicorn workers.
"""

import logging
import secrets
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.rate_limit import RateLimitEntry


async def check_rate_limit(
    db: AsyncSession,
    key: str,
    max_requests: int,
    window_seconds: int,
) -> tuple[bool, int]:
    """Check if a request is within rate limits (DB-backed).

    Returns (allowed, retry_after_seconds).
    """
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=window_seconds)

    # Probabilistic cleanup (~1% of requests) to prevent unbounded table growth
    if secrets.randbelow(100) == 0:
        try:
            await db.execute(
                delete(RateLimitEntry).where(
                    RateLimitEntry.created_at < now - timedelta(hours=24)
                )
            )
        except Exception as e:
            logger.debug("Rate limit cleanup failed: %s", e)

    # Count recent entries within the window
    count_result = await db.execute(
        select(func.count())
        .select_from(RateLimitEntry)
        .where(RateLimitEntry.key == key, RateLimitEntry.created_at >= cutoff)
    )
    count = count_result.scalar() or 0

    if count >= max_requests:
        # Find the oldest entry in the window to calculate retry_after
        oldest_result = await db.execute(
            select(RateLimitEntry.created_at)
            .where(RateLimitEntry.key == key, RateLimitEntry.created_at >= cutoff)
            .order_by(RateLimitEntry.created_at.asc())
            .limit(1)
        )
        oldest = oldest_result.scalar_one_or_none()
        if oldest:
            retry_after = int(window_seconds - (now - oldest).total_seconds()) + 1
        else:
            retry_after = window_seconds
        return False, max(retry_after, 1)

    # Record this request
    entry = RateLimitEntry(key=key, created_at=now)
    db.add(entry)
    await db.flush()

    return True, 0


async def record_rate_limit_event(
    db: AsyncSession,
    key: str,
) -> None:
    """Record a rate limit event without checking (e.g., for login failures)."""
    entry = RateLimitEntry(key=key, created_at=datetime.now(timezone.utc))
    db.add(entry)
    await db.flush()


async def count_recent_events(
    db: AsyncSession,
    key: str,
    window_seconds: int,
) -> int:
    """Count events within a time window."""
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=window_seconds)
    result = await db.execute(
        select(func.count())
        .select_from(RateLimitEntry)
        .where(RateLimitEntry.key == key, RateLimitEntry.created_at >= cutoff)
    )
    return result.scalar() or 0


async def cleanup_expired_entries(
    db: AsyncSession,
    max_age_seconds: int = 86400,
) -> int:
    """Delete entries older than max_age. Call periodically to prevent table bloat."""
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=max_age_seconds)
    result = await db.execute(
        delete(RateLimitEntry).where(RateLimitEntry.created_at < cutoff)
    )
    return result.rowcount or 0
