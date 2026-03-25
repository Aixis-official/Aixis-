"""Client management API endpoints (admin-only + public invite completion)."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import settings
from ...db.base import get_db
from ...db.models.user import Organization, User
from ...schemas.client import (
    ClientCreate,
    ClientListResponse,
    ClientResponse,
    InviteCompleteRequest,
)
from ...services import client_service
from ...services.client_service import PasswordPolicyError
from ...services.email_service import send_invite_email
from ..deps import require_admin

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_invite_url(request: Request, raw_token: str) -> str:
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host", request.url.netloc)
    return f"{scheme}://{host}/invite/{raw_token}"


async def _client_to_response(db: AsyncSession, user: User) -> ClientResponse:
    org_name = None
    if user.organization_id:
        from sqlalchemy import select
        result = await db.execute(
            select(Organization.name).where(Organization.id == user.organization_id)
        )
        org_name = result.scalar_one_or_none()

    return ClientResponse(
        id=user.id,
        email=user.email,
        name=user.name,
        name_jp=user.name_jp,
        role=user.role,
        organization_id=user.organization_id,
        organization_name=org_name,
        account_status=getattr(user, "account_status", None),
        subscription_tier=getattr(user, "subscription_tier", None),
        is_active=user.is_active,
        trial_start=getattr(user, "trial_start", None),
        trial_end=getattr(user, "trial_end", None),
        trial_reminder_sent=getattr(user, "trial_reminder_sent", None),
        invite_sent_at=getattr(user, "invite_sent_at", None),
        created_at=user.created_at,
    )


# ---------------------------------------------------------------------------
# Admin endpoints
# ---------------------------------------------------------------------------


@router.get("", response_model=ClientListResponse)
async def list_clients(
    _admin: Annotated[User, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
):
    """List all client accounts with pagination."""
    clients, total = await client_service.list_clients(db, page, per_page)
    items = [await _client_to_response(db, c) for c in clients]
    return ClientListResponse(items=items, total=total, page=page, per_page=per_page)


@router.post("", response_model=ClientResponse, status_code=status.HTTP_201_CREATED)
async def create_client(
    body: ClientCreate,
    request: Request,
    _admin: Annotated[User, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Create a new client account and send invite email."""
    try:
        user, raw_token = await client_service.create_client(
            db,
            email=body.email,
            name=body.name,
            name_jp=body.name_jp,
            organization_name=body.organization_name,
        )
    except ValueError as e:
        error_msg = str(e)
        # Don't expose internal details
        if "internal" in error_msg.lower() or "sql" in error_msg.lower():
            error_msg = "リクエストの処理に失敗しました"
        raise HTTPException(status_code=400, detail=error_msg)

    # Send invite email (in background to not block response)
    invite_url = _build_invite_url(request, raw_token)
    try:
        send_invite_email(user.name, user.email, invite_url)
    except Exception:
        logger.exception("Failed to send invite email to %s", user.email)
        # Don't fail the request — account is created, can resend later

    return await _client_to_response(db, user)


@router.post("/{client_id}/suspend", response_model=ClientResponse)
async def suspend_client(
    client_id: str,
    _admin: Annotated[User, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Suspend a client account."""
    try:
        user = await client_service.suspend_client(db, client_id)
    except ValueError as e:
        error_msg = str(e)
        # Don't expose internal details
        if "internal" in error_msg.lower() or "sql" in error_msg.lower():
            error_msg = "リクエストの処理に失敗しました"
        raise HTTPException(status_code=404, detail=error_msg)
    return await _client_to_response(db, user)


@router.post("/{client_id}/reactivate", response_model=ClientResponse)
async def reactivate_client(
    client_id: str,
    _admin: Annotated[User, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Reactivate a suspended or expired client account."""
    try:
        user = await client_service.reactivate_client(db, client_id)
    except ValueError as e:
        error_msg = str(e)
        # Don't expose internal details
        if "internal" in error_msg.lower() or "sql" in error_msg.lower():
            error_msg = "リクエストの処理に失敗しました"
        raise HTTPException(status_code=404, detail=error_msg)
    return await _client_to_response(db, user)


@router.post("/{client_id}/resend-invite", response_model=ClientResponse)
async def resend_invite(
    client_id: str,
    request: Request,
    _admin: Annotated[User, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Regenerate invite token and resend invite email."""
    try:
        user, raw_token = await client_service.regenerate_invite(db, client_id)
    except ValueError as e:
        error_msg = str(e)
        # Don't expose internal details
        if "internal" in error_msg.lower() or "sql" in error_msg.lower():
            error_msg = "リクエストの処理に失敗しました"
        raise HTTPException(status_code=400, detail=error_msg)

    invite_url = _build_invite_url(request, raw_token)
    try:
        send_invite_email(user.name, user.email, invite_url)
    except Exception:
        logger.exception("Failed to resend invite email to %s", user.email)

    return await _client_to_response(db, user)


# ---------------------------------------------------------------------------
# Public invite completion (no auth required)
# ---------------------------------------------------------------------------


@router.post("/invite/{token}/complete")
async def complete_invite(
    token: str,
    body: InviteCompleteRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Complete invite: validate token and set password."""
    if body.password != body.password_confirm:
        raise HTTPException(status_code=400, detail="パスワードが一致しません")

    try:
        user = await client_service.complete_invite(db, token, body.password)
    except PasswordPolicyError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError as e:
        error_msg = str(e)
        # Don't expose internal details
        if "internal" in error_msg.lower() or "sql" in error_msg.lower():
            error_msg = "リクエストの処理に失敗しました"
        raise HTTPException(status_code=400, detail=error_msg)

    return {
        "message": "パスワードが設定されました。ログインしてください。",
        "email": user.email,
    }
