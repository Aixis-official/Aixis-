"""Webhook schemas."""

from datetime import datetime

from pydantic import BaseModel


class WebhookCreate(BaseModel):
    url: str
    events: list[str] = ["audit.completed"]
    secret: str | None = None  # Auto-generate if empty


class WebhookResponse(BaseModel):
    id: str
    url: str
    events: list[str]
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class WebhookDeliveryResponse(BaseModel):
    id: str
    event_type: str
    response_status: int | None = None
    response_body: str | None = None
    attempt_count: int
    delivered_at: datetime | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class WebhookTestRequest(BaseModel):
    event_type: str = "test"
