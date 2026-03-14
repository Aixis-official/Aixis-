"""Webhook management endpoints."""

import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.base import get_db
from ...db.models.user import User
from ...db.models.webhook import WebhookDelivery, WebhookSubscription
from ...schemas.webhook import (
    WebhookCreate,
    WebhookDeliveryResponse,
    WebhookResponse,
    WebhookTestRequest,
)
from ...services.webhook_service import send_test_event
from ..deps import require_auth

router = APIRouter()


@router.post("/", response_model=WebhookResponse, status_code=status.HTTP_201_CREATED)
async def create_webhook(
    body: WebhookCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(require_auth)],
):
    """Register a new webhook subscription."""
    secret = body.secret or secrets.token_hex(32)

    subscription = WebhookSubscription(
        user_id=user.id,
        url=body.url,
        secret=secret,
        events=body.events,
    )
    db.add(subscription)
    await db.commit()
    await db.refresh(subscription)
    return subscription


@router.get("/", response_model=list[WebhookResponse])
async def list_webhooks(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(require_auth)],
):
    """List the current user's webhook subscriptions."""
    result = await db.execute(
        select(WebhookSubscription)
        .where(
            WebhookSubscription.user_id == user.id,
            WebhookSubscription.is_active.is_(True),
        )
        .order_by(WebhookSubscription.created_at.desc())
    )
    return result.scalars().all()


@router.delete("/{webhook_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_webhook(
    webhook_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(require_auth)],
):
    """Deactivate a webhook subscription."""
    result = await db.execute(
        select(WebhookSubscription).where(
            WebhookSubscription.id == webhook_id,
            WebhookSubscription.user_id == user.id,
        )
    )
    sub = result.scalar_one_or_none()
    if not sub:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Webhookが見つかりません",
        )
    sub.is_active = False
    await db.commit()


@router.get("/{webhook_id}/deliveries", response_model=list[WebhookDeliveryResponse])
async def list_deliveries(
    webhook_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(require_auth)],
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """List delivery log for a webhook (paginated)."""
    # Verify ownership
    sub_result = await db.execute(
        select(WebhookSubscription).where(
            WebhookSubscription.id == webhook_id,
            WebhookSubscription.user_id == user.id,
        )
    )
    if not sub_result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Webhookが見つかりません",
        )

    offset = (page - 1) * page_size
    result = await db.execute(
        select(WebhookDelivery)
        .where(WebhookDelivery.subscription_id == webhook_id)
        .order_by(WebhookDelivery.created_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    return result.scalars().all()


@router.post("/{webhook_id}/test", status_code=status.HTTP_202_ACCEPTED)
async def test_webhook(
    webhook_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(require_auth)],
):
    """Send a test event to a webhook."""
    # Verify ownership
    sub_result = await db.execute(
        select(WebhookSubscription).where(
            WebhookSubscription.id == webhook_id,
            WebhookSubscription.user_id == user.id,
        )
    )
    if not sub_result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Webhookが見つかりません",
        )

    delivery_id = await send_test_event(webhook_id, db)
    return {"message": "テストイベントを送信しました", "delivery_id": delivery_id}
