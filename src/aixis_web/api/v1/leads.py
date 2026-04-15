"""Leads Dashboard API — list, filter, annotate, and export free-registered users.

The 2026-04-15 free-registration pivot repurposed the platform DB as a
lead-acquisition funnel feeding Aixis Advisory Audit. This endpoint set
powers the in-app ``/dashboard/leads`` view: staff can scan hot leads,
see each visitor's behavior history, update sales status, attach notes,
and export a CSV snapshot.

All endpoints require ``admin | analyst | auditor`` (the same whitelist
used by the rest of ``/dashboard/*``).
"""

import csv
import io
import logging
from datetime import datetime, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.base import get_db
from ...db.models.lead_activity import LeadActivity
from ...db.models.user import User
from ...services.lead_service import (
    HOT_LEAD_THRESHOLD,
    get_recent_activities_for_user,
)
from ..deps import require_analyst

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

_VALID_SALES_STATUSES = {"uncontacted", "contacted", "in_discussion", "won", "lost"}


class LeadSummary(BaseModel):
    id: str
    email: str
    name: str
    company_name: str | None = None
    job_title: str | None = None
    industry: str | None = None
    employee_count: str | None = None
    phone: str | None = None
    lead_score: int = 0
    sales_status: str = "uncontacted"
    is_hot: bool = False
    email_verified: bool = False
    registration_source: str | None = None
    created_at: datetime
    last_active_at: datetime | None = None


class LeadListResponse(BaseModel):
    items: list[LeadSummary]
    total: int
    page: int
    per_page: int
    hot_total: int


class LeadActivityEntry(BaseModel):
    event_type: str
    score_delta: int
    tool_slug: str | None
    page_path: str | None
    created_at: datetime


class LeadDetailResponse(BaseModel):
    lead: LeadSummary
    sales_notes: str | None = None
    recent_activities: list[LeadActivityEntry]


class LeadUpdateRequest(BaseModel):
    sales_status: Literal[
        "uncontacted", "contacted", "in_discussion", "won", "lost"
    ] | None = None
    sales_notes: str | None = Field(None, max_length=5000)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_summary(u: User) -> LeadSummary:
    return LeadSummary(
        id=u.id,
        email=u.email,
        name=u.name,
        company_name=u.company_name,
        job_title=u.job_title,
        industry=u.industry,
        employee_count=u.employee_count,
        phone=u.phone,
        lead_score=int(u.lead_score or 0),
        sales_status=u.sales_status or "uncontacted",
        is_hot=(int(u.lead_score or 0) >= HOT_LEAD_THRESHOLD),
        email_verified=bool(u.email_verified_at),
        registration_source=u.registration_source,
        created_at=u.created_at,
        last_active_at=u.last_active_at,
    )


def _base_leads_query(
    *,
    hot_only: bool,
    status_filter: str | None,
    search: str | None,
):
    """Build the shared filter for list + count + export queries.

    Only users created via the free-registration flow are leads — role is
    ``client`` and ``registration_source`` is non-null. Staff accounts and
    admin-invited clients are excluded.
    """
    stmt = select(User).where(
        User.role == "client",
        User.registration_source.isnot(None),
    )
    if hot_only:
        stmt = stmt.where(User.lead_score >= HOT_LEAD_THRESHOLD)
    if status_filter and status_filter in _VALID_SALES_STATUSES:
        stmt = stmt.where(User.sales_status == status_filter)
    if search:
        pattern = f"%{search.lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(User.email).like(pattern),
                func.lower(User.name).like(pattern),
                func.lower(User.company_name).like(pattern),
            )
        )
    return stmt


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("", response_model=LeadListResponse)
async def list_leads(
    _staff: Annotated[User, Depends(require_analyst)],
    db: Annotated[AsyncSession, Depends(get_db)],
    page: int = Query(1, ge=1, le=1000),
    per_page: int = Query(50, ge=1, le=200),
    hot_only: bool = Query(False),
    status_filter: str | None = Query(None, alias="status"),
    search: str | None = Query(None, max_length=200),
    sort: Literal["score", "recent", "created"] = Query("score"),
) -> LeadListResponse:
    """Paginated list of leads, sorted by score / recent activity / join date."""
    stmt = _base_leads_query(
        hot_only=hot_only, status_filter=status_filter, search=search
    )

    # Total count (separate query so the paginated result can share filters)
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total_result = await db.execute(count_stmt)
    total = int(total_result.scalar() or 0)

    # Hot-lead count (always computed, regardless of the active hot_only filter —
    # the dashboard shows it as a header stat)
    hot_stmt = select(func.count()).select_from(
        _base_leads_query(
            hot_only=True, status_filter=status_filter, search=search
        ).subquery()
    )
    hot_total_result = await db.execute(hot_stmt)
    hot_total = int(hot_total_result.scalar() or 0)

    # Sort
    if sort == "score":
        stmt = stmt.order_by(User.lead_score.desc(), User.created_at.desc())
    elif sort == "recent":
        stmt = stmt.order_by(
            User.last_active_at.desc().nullslast(), User.created_at.desc()
        )
    else:  # created
        stmt = stmt.order_by(User.created_at.desc())

    stmt = stmt.limit(per_page).offset((page - 1) * per_page)
    result = await db.execute(stmt)
    users = list(result.scalars().all())

    return LeadListResponse(
        items=[_to_summary(u) for u in users],
        total=total,
        page=page,
        per_page=per_page,
        hot_total=hot_total,
    )


@router.get("/export.csv")
async def export_leads_csv(
    _staff: Annotated[User, Depends(require_analyst)],
    db: Annotated[AsyncSession, Depends(get_db)],
    hot_only: bool = Query(False),
    status_filter: str | None = Query(None, alias="status"),
    search: str | None = Query(None, max_length=200),
) -> StreamingResponse:
    """CSV export of leads matching the current filters.

    Exports up to 5000 rows — the dashboard is for sales triage, not bulk
    data extraction, and pagination protects the DB.
    """
    stmt = (
        _base_leads_query(
            hot_only=hot_only, status_filter=status_filter, search=search
        )
        .order_by(User.lead_score.desc(), User.created_at.desc())
        .limit(5000)
    )
    result = await db.execute(stmt)
    users = list(result.scalars().all())

    buf = io.StringIO()
    # UTF-8 BOM so Excel opens the file correctly
    buf.write("\ufeff")
    writer = csv.writer(buf)
    writer.writerow(
        [
            "氏名",
            "メール",
            "会社名",
            "役職",
            "業種",
            "従業員規模",
            "電話",
            "リードスコア",
            "ホット",
            "営業ステータス",
            "メール確認",
            "登録経路",
            "登録日時",
            "最終アクティブ",
        ]
    )
    for u in users:
        writer.writerow(
            [
                u.name or "",
                u.email,
                u.company_name or "",
                u.job_title or "",
                u.industry or "",
                u.employee_count or "",
                u.phone or "",
                int(u.lead_score or 0),
                "〇" if int(u.lead_score or 0) >= HOT_LEAD_THRESHOLD else "",
                u.sales_status or "uncontacted",
                "済" if u.email_verified_at else "未",
                u.registration_source or "",
                u.created_at.strftime("%Y-%m-%d %H:%M") if u.created_at else "",
                u.last_active_at.strftime("%Y-%m-%d %H:%M") if u.last_active_at else "",
            ]
        )

    csv_bytes = buf.getvalue().encode("utf-8")
    filename = f"aixis-leads-{datetime.now(timezone.utc).strftime('%Y%m%d')}.csv"
    return StreamingResponse(
        iter([csv_bytes]),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


@router.get("/{lead_id}", response_model=LeadDetailResponse)
async def get_lead_detail(
    lead_id: str,
    _staff: Annotated[User, Depends(require_analyst)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> LeadDetailResponse:
    """Full lead record including recent activity history."""
    result = await db.execute(
        select(User).where(
            User.id == lead_id,
            User.role == "client",
            User.registration_source.isnot(None),
        )
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="該当のリードが見つかりません"
        )

    activities = await get_recent_activities_for_user(db, user.id, limit=50)

    return LeadDetailResponse(
        lead=_to_summary(user),
        sales_notes=user.sales_notes,
        recent_activities=[
            LeadActivityEntry(
                event_type=a.event_type,
                score_delta=a.score_delta or 0,
                tool_slug=a.tool_slug,
                page_path=a.page_path,
                created_at=a.created_at,
            )
            for a in activities
        ],
    )


@router.patch("/{lead_id}", response_model=LeadDetailResponse)
async def update_lead(
    lead_id: str,
    payload: LeadUpdateRequest,
    _staff: Annotated[User, Depends(require_analyst)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> LeadDetailResponse:
    """Update the sales status and/or notes attached to a lead.

    Returns the same envelope as the detail endpoint so the UI can
    re-render without an extra fetch.
    """
    result = await db.execute(
        select(User).where(
            User.id == lead_id,
            User.role == "client",
            User.registration_source.isnot(None),
        )
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="該当のリードが見つかりません"
        )

    changed = False
    if payload.sales_status is not None:
        user.sales_status = payload.sales_status
        changed = True
    if payload.sales_notes is not None:
        user.sales_notes = payload.sales_notes.strip() or None
        changed = True

    if changed:
        await db.commit()

    activities = await get_recent_activities_for_user(db, user.id, limit=50)
    return LeadDetailResponse(
        lead=_to_summary(user),
        sales_notes=user.sales_notes,
        recent_activities=[
            LeadActivityEntry(
                event_type=a.event_type,
                score_delta=a.score_delta or 0,
                tool_slug=a.tool_slug,
                page_path=a.page_path,
                created_at=a.created_at,
            )
            for a in activities
        ],
    )
