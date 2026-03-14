"""Vendor portal schemas."""

from datetime import datetime

from pydantic import BaseModel


class VendorRegister(BaseModel):
    company_name: str
    company_name_jp: str = ""
    company_url: str = ""
    contact_email: str = ""


class VendorProfileResponse(BaseModel):
    id: str
    user_id: str
    company_name: str
    company_name_jp: str
    company_url: str
    contact_email: str
    verified: bool
    verified_at: datetime | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class VendorProfileUpdate(BaseModel):
    company_name: str | None = None
    company_name_jp: str | None = None
    company_url: str | None = None
    contact_email: str | None = None


class ToolSubmissionCreate(BaseModel):
    tool_name: str
    tool_name_jp: str = ""
    tool_url: str
    category_id: str | None = None
    description: str = ""
    description_jp: str = ""
    target_config_yaml: str = ""


class ToolSubmissionResponse(BaseModel):
    id: str
    vendor_id: str
    tool_name: str
    tool_name_jp: str
    tool_url: str
    category_id: str | None = None
    description: str
    description_jp: str
    target_config_yaml: str
    status: str
    reviewer_notes: str | None = None
    reviewed_by: str | None = None
    approved_tool_id: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ScoreDisputeCreate(BaseModel):
    tool_id: str
    axis: str | None = None
    reason: str
    evidence_urls: list[str] = []


class ScoreDisputeResponse(BaseModel):
    id: str
    vendor_id: str
    tool_id: str
    axis: str | None = None
    reason: str
    evidence_urls: list[str]
    status: str
    resolution_notes: str | None = None
    resolved_by: str | None = None
    created_at: datetime
    resolved_at: datetime | None = None

    model_config = {"from_attributes": True}


class SubmissionReviewRequest(BaseModel):
    action: str  # "approve" or "reject"
    notes: str = ""
