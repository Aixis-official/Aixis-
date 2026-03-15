"""Vendor portal business logic."""

import re
import uuid
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.tool import Tool
from ..db.models.user import User
from ..db.models.vendor import ScoreDispute, ToolSubmission, VendorProfile
from ..schemas.vendor import (
    ScoreDisputeCreate,
    SubmissionReviewRequest,
    ToolSubmissionCreate,
    VendorRegister,
)


async def register_vendor(
    db: AsyncSession, user_id: str, data: VendorRegister
) -> VendorProfile:
    """Create a VendorProfile and update user role to 'vendor'."""
    # Check if user already has a vendor profile
    existing = await db.execute(
        select(VendorProfile).where(VendorProfile.user_id == user_id)
    )
    if existing.scalar_one_or_none():
        raise ValueError("既にベンダー登録済みです")

    profile = VendorProfile(
        user_id=user_id,
        company_name=data.company_name,
        company_name_jp=data.company_name_jp,
        company_url=data.company_url,
        contact_email=data.contact_email,
    )
    db.add(profile)

    # Update user role to vendor (unless already admin/analyst)
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user and user.role not in ("admin", "analyst", "auditor"):
        user.role = "vendor"

    await db.commit()
    await db.refresh(profile)
    return profile


async def submit_tool(
    db: AsyncSession, vendor_id: str, data: ToolSubmissionCreate
) -> ToolSubmission:
    """Create a ToolSubmission for review."""
    submission = ToolSubmission(
        vendor_id=vendor_id,
        tool_name=data.tool_name,
        tool_name_jp=data.tool_name_jp,
        tool_url=data.tool_url,
        category_id=data.category_id,
        description=data.description,
        description_jp=data.description_jp,
        target_config_yaml=data.target_config_yaml,
    )
    db.add(submission)
    await db.commit()
    await db.refresh(submission)
    return submission


def _name_to_slug(name: str) -> str:
    """Convert a tool name to a URL-safe slug."""
    slug = name.strip().lower()
    slug = re.sub(r"[^a-z0-9\-]", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug or f"tool-{uuid.uuid4().hex[:6]}"


async def approve_submission(
    db: AsyncSession,
    submission_id: str,
    reviewer_id: str,
    notes: str = "",
) -> ToolSubmission:
    """Approve a submission and create a Tool from it."""
    result = await db.execute(
        select(ToolSubmission).where(ToolSubmission.id == submission_id)
    )
    submission = result.scalar_one_or_none()
    if not submission:
        raise ValueError("申請が見つかりません")

    # Create Tool from submission
    slug = _name_to_slug(submission.tool_name)
    # Check uniqueness
    existing = await db.execute(select(Tool).where(Tool.slug == slug))
    if existing.scalar_one_or_none():
        slug = f"{slug}-{uuid.uuid4().hex[:4]}"

    tool = Tool(
        slug=slug,
        name=submission.tool_name,
        name_jp=submission.tool_name_jp or submission.tool_name,
        url=submission.tool_url,
        description=submission.description,
        description_jp=submission.description_jp,
        category_id=submission.category_id,
        is_public=False,
        is_active=True,
    )
    db.add(tool)
    await db.flush()  # get tool.id

    submission.status = "approved"
    submission.reviewed_by = reviewer_id
    submission.reviewer_notes = notes
    submission.approved_tool_id = tool.id
    submission.updated_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(submission)

    # Notify the vendor about approval (best-effort)
    try:
        vendor = await db.execute(
            select(VendorProfile).where(VendorProfile.id == submission.vendor_id)
        )
        vendor_obj = vendor.scalar_one_or_none()
        if vendor_obj:
            from .notification_service import dispatch_notification
            await dispatch_notification(
                db=db,
                user_id=vendor_obj.user_id,
                event_type="submission.approved",
                title=f"Tool approved: {submission.tool_name}",
                title_jp=f"ツール承認: {submission.tool_name}",
                body=f"Your tool '{submission.tool_name}' has been approved.",
                body_jp=f"ツール「{submission.tool_name}」が承認されました。",
                link="/vendor",
            )
            await db.commit()
    except Exception:
        pass

    return submission


async def reject_submission(
    db: AsyncSession,
    submission_id: str,
    reviewer_id: str,
    notes: str = "",
) -> ToolSubmission:
    """Reject a submission."""
    result = await db.execute(
        select(ToolSubmission).where(ToolSubmission.id == submission_id)
    )
    submission = result.scalar_one_or_none()
    if not submission:
        raise ValueError("申請が見つかりません")

    submission.status = "rejected"
    submission.reviewed_by = reviewer_id
    submission.reviewer_notes = notes
    submission.updated_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(submission)

    # Notify the vendor about rejection (best-effort)
    try:
        vendor = await db.execute(
            select(VendorProfile).where(VendorProfile.id == submission.vendor_id)
        )
        vendor_obj = vendor.scalar_one_or_none()
        if vendor_obj:
            from .notification_service import dispatch_notification
            await dispatch_notification(
                db=db,
                user_id=vendor_obj.user_id,
                event_type="submission.rejected",
                title=f"Tool submission rejected: {submission.tool_name}",
                title_jp=f"ツール申請却下: {submission.tool_name}",
                body=notes or "Your submission was not approved.",
                body_jp=notes or "申請は承認されませんでした。",
                link="/vendor",
            )
            await db.commit()
    except Exception:
        pass

    return submission


async def file_dispute(
    db: AsyncSession, vendor_id: str, data: ScoreDisputeCreate
) -> ScoreDispute:
    """File a score dispute."""
    dispute = ScoreDispute(
        vendor_id=vendor_id,
        tool_id=data.tool_id,
        axis=data.axis,
        reason=data.reason,
        evidence_urls=data.evidence_urls,
    )
    db.add(dispute)
    await db.commit()
    await db.refresh(dispute)
    return dispute
