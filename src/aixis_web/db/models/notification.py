"""Notification models."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, JSON, String, Text

from ..base import Base


def new_uuid():
    return str(uuid.uuid4())


class Notification(Base):
    __tablename__ = "notifications"

    id = Column(String(36), primary_key=True, default=new_uuid)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    type = Column(String(50), nullable=False)  # audit_complete, score_published, manual_eval_needed, etc
    title = Column(String(200), nullable=False)
    title_jp = Column(String(200), nullable=False)
    body = Column(Text, default="")
    body_jp = Column(Text, default="")
    link = Column(String(500), nullable=True)  # in-app link
    is_read = Column(Boolean, default=False, index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)


class NotificationPreference(Base):
    __tablename__ = "notification_preferences"

    id = Column(String(36), primary_key=True, default=new_uuid)
    user_id = Column(String(36), ForeignKey("users.id"), unique=True, nullable=False)
    email_enabled = Column(Boolean, default=True)
    in_app_enabled = Column(Boolean, default=True)
    slack_webhook_url = Column(String(500), nullable=True)
    discord_webhook_url = Column(String(500), nullable=True)
    subscribed_events = Column(
        JSON, default=lambda: ["audit_complete", "score_published", "manual_eval_needed"]
    )
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
