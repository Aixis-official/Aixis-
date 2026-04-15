"""Lead-activity beacon endpoint — client-side tracking for the Leads Dashboard.

Public page scripts post here to record fine-grained interactions that the
server-side page handlers cannot observe on their own: scrolling into the
safety-axis section, expanding the governance detail panel, clicking an
advisory-audit CTA, etc. Rows are fed into the same ``lead_service`` that
backs server-side tracking, so the resulting ``lead_score`` is consistent
regardless of the event origin.

Only a whitelisted set of event types is accepted and all payloads are
size-bounded to keep the endpoint abuse-resistant.
"""

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.base import get_db
from ...db.models.user import User
from ...services import lead_service
from ...services.rate_limit_service import check_rate_limit
from ..deps import get_client_ip, get_current_user

logger = logging.getLogger(__name__)
router = APIRouter()


# Subset of events the public beacon may post. Server-side events
# (tool_view / pricing_view / tool_compare) are intentionally excluded —
# those are recorded authoritatively by the page handler and must not be
# double-counted via the client.
_ALLOWED_EVENT_TYPES: frozenset[str] = frozenset(
    {
        lead_service.EVENT_SAFETY_AXIS_VIEW,
        lead_service.EVENT_GOVERNANCE_VIEW,
        lead_service.EVENT_ADVISORY_CTA_CLICK,
        lead_service.EVENT_PDF_DOWNLOAD,
        lead_service.EVENT_ONBOARDING_DONE,
    }
)

_MAX_METADATA_KEYS = 10
_MAX_METADATA_VALUE_LEN = 200


class TrackRequest(BaseModel):
    """Public beacon payload."""

    event_type: str = Field(..., min_length=1, max_length=50)
    tool_slug: str | None = Field(None, max_length=200)
    page_path: str | None = Field(None, max_length=500)
    metadata: dict[str, Any] | None = None

    @field_validator("event_type")
    @classmethod
    def _validate_event_type(cls, v: str) -> str:
        if v not in _ALLOWED_EVENT_TYPES:
            raise ValueError(f"Unsupported event_type: {v}")
        return v

    @field_validator("metadata")
    @classmethod
    def _validate_metadata(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        if v is None:
            return None
        if len(v) > _MAX_METADATA_KEYS:
            raise ValueError(f"metadata has too many keys (max {_MAX_METADATA_KEYS})")
        cleaned: dict[str, Any] = {}
        for key, raw_value in v.items():
            if not isinstance(key, str) or len(key) > 40:
                continue
            if isinstance(raw_value, (str, int, float, bool)) or raw_value is None:
                if isinstance(raw_value, str) and len(raw_value) > _MAX_METADATA_VALUE_LEN:
                    raw_value = raw_value[:_MAX_METADATA_VALUE_LEN]
                cleaned[key] = raw_value
        return cleaned


class TrackResponse(BaseModel):
    ok: bool = True
    recorded: bool
    lead_score: int | None = None


@router.post("", response_model=TrackResponse, status_code=status.HTTP_200_OK)
async def track_event(
    payload: TrackRequest,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User | None, Depends(get_current_user)] = None,
) -> TrackResponse:
    """Record a client-originated lead event.

    The endpoint is unauthenticated — anonymous visitors can record
    events against their ``aixis_sid`` cookie, and the service will
    reattach them on registration. A registered user's score updates
    are reflected in the response so the page can show a live total.
    """
    # Rate limit by IP to protect against abuse. The service itself
    # debounces passive events, but a fixed ceiling is still useful
    # against a determined bot.
    client_ip = get_client_ip(request)
    allowed, _retry = await check_rate_limit(
        db,
        key=f"track:{client_ip}",
        max_requests=60,
        window_seconds=60,
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many events — slow down.",
        )

    session_id = getattr(request.state, "session_id", None)

    try:
        row = await lead_service.track_activity(
            db,
            event_type=payload.event_type,
            user_id=user.id if user else None,
            session_id=session_id,
            tool_slug=payload.tool_slug,
            page_path=payload.page_path,
            metadata=payload.metadata,
            ip=client_ip,
            user_agent=request.headers.get("user-agent"),
        )
        await db.commit()
    except Exception:
        logger.exception("track_event failed")
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to record event.",
        )

    lead_score: int | None = None
    if user and row is not None:
        await db.refresh(user, ["lead_score"])
        lead_score = user.lead_score

    return TrackResponse(
        ok=True,
        recorded=row is not None,
        lead_score=lead_score,
    )
