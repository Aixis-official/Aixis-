"""DB-backed rate limiter and API key validation middleware.

Uses the same DB-backed rate limiter as login to work correctly with
multiple Uvicorn workers.
"""

import hashlib
import logging
from datetime import datetime, timezone
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.base import get_db
from ..db.models.api_key import ApiKey
from ..services.rate_limit_service import check_rate_limit as _db_check_rate_limit

logger = logging.getLogger(__name__)


def _hash_api_key(raw_key: str) -> str:
    """SHA256 hash an API key."""
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


async def _check_api_rate_limit(
    db: AsyncSession, key_hash: str, per_minute: int, per_day: int,
) -> tuple[bool, int]:
    """Check per-minute and per-day rate limits using DB backend.

    Returns (allowed, retry_after_seconds).
    """
    # Per-minute check
    minute_key = f"api_min:{key_hash[:16]}"
    allowed, retry_after = await _db_check_rate_limit(db, minute_key, per_minute, 60)
    if not allowed:
        return False, retry_after

    # Per-day check
    day_key = f"api_day:{key_hash[:16]}"
    allowed, retry_after = await _db_check_rate_limit(db, day_key, per_day, 86400)
    if not allowed:
        return False, max(retry_after, 3600)

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

    if api_key.expires_at and api_key.expires_at < datetime.now(timezone.utc):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="API key has expired",
        )

    # Rate limiting (DB-backed, works across multiple workers)
    try:
        allowed, retry_after = await _check_api_rate_limit(
            db, key_hash, api_key.rate_limit_per_minute, api_key.rate_limit_per_day
        )
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded",
                headers={"Retry-After": str(retry_after)},
            )
    except HTTPException:
        raise
    except Exception:
        logger.warning("API rate limit check failed (non-critical)")

    # Update last_used_at
    api_key.last_used_at = datetime.now(timezone.utc)
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
