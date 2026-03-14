"""Notification schemas."""

from datetime import datetime

from pydantic import BaseModel


class NotificationResponse(BaseModel):
    id: str
    type: str
    title: str
    title_jp: str
    body: str = ""
    body_jp: str = ""
    link: str | None = None
    is_read: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class NotificationListResponse(BaseModel):
    items: list[NotificationResponse]
    total: int


class NotificationPreferenceResponse(BaseModel):
    email_enabled: bool
    in_app_enabled: bool
    slack_webhook_url: str | None = None
    discord_webhook_url: str | None = None
    subscribed_events: list[str]

    model_config = {"from_attributes": True}


class NotificationPreferenceUpdate(BaseModel):
    email_enabled: bool | None = None
    in_app_enabled: bool | None = None
    slack_webhook_url: str | None = None
    discord_webhook_url: str | None = None
    subscribed_events: list[str] | None = None


class UnreadCountResponse(BaseModel):
    count: int
