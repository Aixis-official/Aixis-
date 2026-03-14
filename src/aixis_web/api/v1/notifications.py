"""Notification endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, func, update
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.base import get_db
from ...db.models.notification import Notification, NotificationPreference
from ...db.models.user import User
from ...schemas.notification import (
    NotificationListResponse,
    NotificationPreferenceResponse,
    NotificationPreferenceUpdate,
    NotificationResponse,
    UnreadCountResponse,
)
from ...services.notification_service import get_or_create_preferences
from ..deps import require_auth

router = APIRouter()


@router.get("/", response_model=NotificationListResponse)
async def list_notifications(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(require_auth)],
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    is_read: bool | None = None,
):
    """List notifications for the current user (paginated, filterable)."""
    query = select(Notification).where(Notification.user_id == user.id)
    count_query = select(func.count()).select_from(Notification).where(
        Notification.user_id == user.id
    )

    if is_read is not None:
        query = query.where(Notification.is_read == is_read)
        count_query = count_query.where(Notification.is_read == is_read)

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    offset = (page - 1) * page_size
    query = query.order_by(Notification.created_at.desc()).offset(offset).limit(page_size)
    result = await db.execute(query)
    items = result.scalars().all()

    return NotificationListResponse(items=items, total=total)


@router.put("/{notification_id}/read", response_model=NotificationResponse)
async def mark_as_read(
    notification_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(require_auth)],
):
    """Mark a single notification as read."""
    result = await db.execute(
        select(Notification).where(
            Notification.id == notification_id,
            Notification.user_id == user.id,
        )
    )
    notification = result.scalar_one_or_none()
    if not notification:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="通知が見つかりません",
        )
    notification.is_read = True
    await db.commit()
    await db.refresh(notification)
    return notification


@router.put("/read-all", status_code=status.HTTP_200_OK)
async def mark_all_as_read(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(require_auth)],
):
    """Mark all notifications as read for the current user."""
    await db.execute(
        update(Notification)
        .where(
            Notification.user_id == user.id,
            Notification.is_read.is_(False),
        )
        .values(is_read=True)
    )
    await db.commit()
    return {"message": "すべての通知を既読にしました"}


@router.get("/unread-count", response_model=UnreadCountResponse)
async def get_unread_count(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(require_auth)],
):
    """Get count of unread notifications."""
    result = await db.execute(
        select(func.count())
        .select_from(Notification)
        .where(
            Notification.user_id == user.id,
            Notification.is_read.is_(False),
        )
    )
    count = result.scalar() or 0
    return UnreadCountResponse(count=count)


@router.get("/preferences", response_model=NotificationPreferenceResponse)
async def get_preferences(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(require_auth)],
):
    """Get notification preferences for the current user."""
    pref = await get_or_create_preferences(db, user.id)
    await db.commit()
    return pref


@router.put("/preferences", response_model=NotificationPreferenceResponse)
async def update_preferences(
    body: NotificationPreferenceUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(require_auth)],
):
    """Update notification preferences."""
    pref = await get_or_create_preferences(db, user.id)

    update_data = body.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(pref, key, value)

    await db.commit()
    await db.refresh(pref)
    return pref
