"""Vendor self-service portal endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.base import get_db
from ...db.models.tool import Tool
from ...db.models.user import User
from ...db.models.vendor import ScoreDispute, ToolSubmission, VendorProfile
from ...schemas.vendor import (
    ScoreDisputeCreate,
    ScoreDisputeResponse,
    SubmissionReviewRequest,
    ToolSubmissionCreate,
    ToolSubmissionResponse,
    VendorProfileResponse,
    VendorProfileUpdate,
    VendorRegister,
)
from ...services.vendor_service import (
    approve_submission,
    file_dispute,
    register_vendor,
    reject_submission,
    submit_tool,
)
from ..deps import require_analyst, require_auth, require_vendor

router = APIRouter()


# ──── Vendor Self-Service ────


@router.post(
    "/register",
    response_model=VendorProfileResponse,
    status_code=status.HTTP_201_CREATED,
)
async def vendor_register(
    body: VendorRegister,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(require_auth)],
):
    """Register as a vendor (any authenticated user)."""
    try:
        profile = await register_vendor(db, user.id, body)
        return profile
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))


@router.get("/profile", response_model=VendorProfileResponse)
async def get_vendor_profile(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(require_vendor)],
):
    """Get the current user's vendor profile."""
    result = await db.execute(
        select(VendorProfile).where(VendorProfile.user_id == user.id)
    )
    profile = result.scalar_one_or_none()
    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="ベンダープロフィールが見つかりません",
        )
    return profile


@router.put("/profile", response_model=VendorProfileResponse)
async def update_vendor_profile(
    body: VendorProfileUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(require_vendor)],
):
    """Update vendor profile."""
    result = await db.execute(
        select(VendorProfile).where(VendorProfile.user_id == user.id)
    )
    profile = result.scalar_one_or_none()
    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="ベンダープロフィールが見つかりません",
        )

    update_data = body.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(profile, key, value)

    await db.commit()
    await db.refresh(profile)
    return profile


@router.post(
    "/submissions",
    response_model=ToolSubmissionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_submission(
    body: ToolSubmissionCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(require_vendor)],
):
    """Submit a tool for audit review."""
    result = await db.execute(
        select(VendorProfile).where(VendorProfile.user_id == user.id)
    )
    profile = result.scalar_one_or_none()
    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="先にベンダー登録を行ってください",
        )

    submission = await submit_tool(db, profile.id, body)
    return submission


@router.get("/submissions", response_model=list[ToolSubmissionResponse])
async def list_own_submissions(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(require_vendor)],
):
    """List the current vendor's submissions."""
    profile_result = await db.execute(
        select(VendorProfile).where(VendorProfile.user_id == user.id)
    )
    profile = profile_result.scalar_one_or_none()
    if not profile:
        return []

    result = await db.execute(
        select(ToolSubmission)
        .where(ToolSubmission.vendor_id == profile.id)
        .order_by(ToolSubmission.created_at.desc())
    )
    return result.scalars().all()


@router.get("/tools", response_model=list[dict])
async def list_vendor_tools(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(require_vendor)],
):
    """List tools approved from the current vendor's submissions."""
    profile_result = await db.execute(
        select(VendorProfile).where(VendorProfile.user_id == user.id)
    )
    profile = profile_result.scalar_one_or_none()
    if not profile:
        return []

    result = await db.execute(
        select(ToolSubmission, Tool)
        .outerjoin(Tool, ToolSubmission.approved_tool_id == Tool.id)
        .where(
            ToolSubmission.vendor_id == profile.id,
            ToolSubmission.status == "approved",
        )
        .order_by(ToolSubmission.updated_at.desc())
    )
    rows = result.all()

    tools = []
    for submission, tool in rows:
        if tool:
            tools.append(
                {
                    "id": tool.id,
                    "slug": tool.slug,
                    "name": tool.name,
                    "name_jp": tool.name_jp,
                    "is_public": tool.is_public,
                }
            )
    return tools


@router.post(
    "/disputes",
    response_model=ScoreDisputeResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_dispute(
    body: ScoreDisputeCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(require_vendor)],
):
    """File a score dispute."""
    profile_result = await db.execute(
        select(VendorProfile).where(VendorProfile.user_id == user.id)
    )
    profile = profile_result.scalar_one_or_none()
    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="先にベンダー登録を行ってください",
        )

    dispute = await file_dispute(db, profile.id, body)
    return dispute


@router.get("/disputes", response_model=list[ScoreDisputeResponse])
async def list_own_disputes(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(require_vendor)],
):
    """List the current vendor's disputes."""
    profile_result = await db.execute(
        select(VendorProfile).where(VendorProfile.user_id == user.id)
    )
    profile = profile_result.scalar_one_or_none()
    if not profile:
        return []

    result = await db.execute(
        select(ScoreDispute)
        .where(ScoreDispute.vendor_id == profile.id)
        .order_by(ScoreDispute.created_at.desc())
    )
    return result.scalars().all()


# ──── Admin Review Endpoints ────


@router.get("/admin/submissions", response_model=list[ToolSubmissionResponse])
async def list_all_submissions(
    db: Annotated[AsyncSession, Depends(get_db)],
    _user: Annotated[User, Depends(require_analyst)],
    status_filter: str | None = None,
):
    """List all submissions (admin/analyst). Optionally filter by status."""
    query = select(ToolSubmission).order_by(ToolSubmission.created_at.desc())
    if status_filter:
        query = query.where(ToolSubmission.status == status_filter)

    result = await db.execute(query)
    return result.scalars().all()


@router.put(
    "/admin/submissions/{submission_id}/review",
    response_model=ToolSubmissionResponse,
)
async def review_submission(
    submission_id: str,
    body: SubmissionReviewRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(require_analyst)],
):
    """Approve or reject a tool submission."""
    try:
        if body.action == "approve":
            submission = await approve_submission(
                db, submission_id, user.id, body.notes
            )
        elif body.action == "reject":
            submission = await reject_submission(
                db, submission_id, user.id, body.notes
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="actionは'approve'または'reject'を指定してください",
            )
        return submission
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(e)
        )
