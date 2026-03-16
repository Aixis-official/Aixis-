"""Client management schemas."""

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


class ClientCreate(BaseModel):
    email: EmailStr
    name: str = Field(..., min_length=1, max_length=200)
    name_jp: str | None = None
    organization_name: str | None = None


class ClientResponse(BaseModel):
    id: str
    email: str
    name: str
    name_jp: str | None = None
    role: str
    organization_id: str | None = None
    organization_name: str | None = None
    account_status: str | None = None
    subscription_tier: str | None = None
    is_active: bool
    trial_start: datetime | None = None
    trial_end: datetime | None = None
    trial_reminder_sent: bool | None = None
    invite_sent_at: datetime | None = None
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class ClientListResponse(BaseModel):
    items: list[ClientResponse]
    total: int
    page: int
    per_page: int


class InviteCompleteRequest(BaseModel):
    password: str = Field(..., min_length=8, max_length=256)
    password_confirm: str = Field(..., min_length=8, max_length=256)
