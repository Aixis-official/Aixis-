"""API key management endpoints (require authentication)."""

import hashlib
import secrets
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.base import get_db
from ...db.models.api_key import ApiKey
from ...db.models.user import User
from ...schemas.api_key import ApiKeyCreate, ApiKeyCreatedResponse, ApiKeyResponse
from ..deps import require_auth

router = APIRouter()


def _generate_raw_key() -> str:
    """Generate a random API key with 'axk_' prefix."""
    random_part = secrets.token_hex(24)  # 48 hex chars
    return f"axk_{random_part}"


def _hash_key(raw_key: str) -> str:
    """SHA256 hash an API key for storage."""
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


@router.post("/", response_model=ApiKeyCreatedResponse, status_code=status.HTTP_201_CREATED)
async def create_api_key(
    body: ApiKeyCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(require_auth)],
):
    """Create a new API key. The raw key is returned ONLY once."""
    raw_key = _generate_raw_key()
    key_hash = _hash_key(raw_key)
    key_prefix = raw_key[:12]  # "axk_" + first 8 hex chars

    api_key = ApiKey(
        id=str(uuid.uuid4()),
        key_hash=key_hash,
        key_prefix=key_prefix,
        name=body.name,
        user_id=user.id,
        scopes=body.scopes,
        rate_limit_per_minute=body.rate_limit_per_minute,
        rate_limit_per_day=body.rate_limit_per_day,
    )
    db.add(api_key)
    await db.commit()
    await db.refresh(api_key)

    return ApiKeyCreatedResponse(
        id=api_key.id,
        key_prefix=api_key.key_prefix,
        name=api_key.name,
        scopes=api_key.scopes or [],
        rate_limit_per_minute=api_key.rate_limit_per_minute,
        rate_limit_per_day=api_key.rate_limit_per_day,
        is_active=api_key.is_active,
        last_used_at=api_key.last_used_at,
        expires_at=api_key.expires_at,
        created_at=api_key.created_at,
        raw_key=raw_key,
    )


@router.get("/", response_model=list[ApiKeyResponse])
async def list_api_keys(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(require_auth)],
):
    """List the current user's API keys."""
    result = await db.execute(
        select(ApiKey)
        .where(ApiKey.user_id == user.id)
        .order_by(ApiKey.created_at.desc())
    )
    return result.scalars().all()


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_api_key(
    key_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(require_auth)],
):
    """Deactivate an API key."""
    result = await db.execute(
        select(ApiKey).where(ApiKey.id == key_id, ApiKey.user_id == user.id)
    )
    api_key = result.scalar_one_or_none()
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="APIキーが見つかりません",
        )
    api_key.is_active = False
    await db.commit()
