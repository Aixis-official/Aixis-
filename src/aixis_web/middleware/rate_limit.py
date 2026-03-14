"""Sliding window rate limiter and API key validation middleware."""

import hashlib
import time
from collections import defaultdict, deque
from datetime import datetime
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.base import get_db
from ..db.models.api_key import ApiKey


# In-memory rate tracking: key_hash -> deque of timestamps
_rate_windows: dict[str, deque] = defaultdict(deque)
_daily_counts: dict[str, tuple[str, int]] = {}  # key_hash -> (date_str, count)


def _hash_api_key(raw_key: str) -> str:
    """SHA256 hash an API key."""
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def _check_rate_limit(key_hash: str, per_minute: int, per_day: int) -> tuple[bool, int]:
    """Check if the request is within rate limits.

    Returns (allowed, retry_after_seconds).
    """
    now = time.time()

    # Per-minute sliding window
    window = _rate_windows[key_hash]
    # Remove entries older than 60 seconds
    while window and window[0] < now - 60:
        window.popleft()

    if len(window) >= per_minute:
        retry_after = int(60 - (now - window[0])) + 1
        return False, retry_after

    # Per-day counter
    today = datetime.utcnow().strftime("%Y-%m-%d")
    if key_hash in _daily_counts:
        date_str, count = _daily_counts[key_hash]
        if date_str == today:
            if count >= per_day:
                return False, 3600  # Retry after 1 hour
        else:
            _daily_counts[key_hash] = (today, 0)
    else:
        _daily_counts[key_hash] = (today, 0)

    # Record this request
    window.append(now)
    date_str, count = _daily_counts[key_hash]
    _daily_counts[key_hash] = (date_str, count + 1)

    return True, 0


async def get_api_key_record(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ApiKey:
    """Validate the X-API-Key header and return the ApiKey record.

    Use as a FastAPI dependency for public API endpoints.
    """
    raw_key = request.headers.get("X-API-Key")
    if not raw_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-API-Key header is required",
        )

    key_hash = _hash_api_key(raw_key)

    result = await db.execute(
        select(ApiKey).where(ApiKey.key_hash == key_hash)
    )
    api_key = result.scalar_one_or_none()

    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )

    if not api_key.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="API key has been deactivated",
        )

    if api_key.expires_at and api_key.expires_at < datetime.utcnow():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="API key has expired",
        )

    # Rate limiting
    allowed, retry_after = _check_rate_limit(
        key_hash, api_key.rate_limit_per_minute, api_key.rate_limit_per_day
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded",
            headers={"Retry-After": str(retry_after)},
        )

    # Update last_used_at
    api_key.last_used_at = datetime.utcnow()
    await db.commit()

    return api_key


def require_scope(scope: str):
    """Create a dependency that checks if the API key has the required scope."""

    async def _check_scope(
        api_key: Annotated[ApiKey, Depends(get_api_key_record)],
    ) -> ApiKey:
        if scope not in (api_key.scopes or []):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"API key does not have the required scope: {scope}",
            )
        return api_key

    return _check_scope
