"""Webhook models."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, JSON, String, Text

from ..base import Base


def new_uuid():
    return str(uuid.uuid4())


class WebhookSubscription(Base):
    __tablename__ = "webhook_subscriptions"

    id = Column(String(36), primary_key=True, default=new_uuid)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    url = Column(String(500), nullable=False)
    secret = Column(String(200), nullable=False)  # For HMAC-SHA256 signatures
    events = Column(JSON, default=lambda: ["audit.completed"])
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"

    id = Column(String(36), primary_key=True, default=new_uuid)
    subscription_id = Column(
        String(36), ForeignKey("webhook_subscriptions.id"), nullable=False
    )
    event_type = Column(String(50), nullable=False)
    payload = Column(JSON, nullable=False)
    response_status = Column(Integer, nullable=True)
    response_body = Column(Text, nullable=True)  # truncated to 1000 chars
    attempt_count = Column(Integer, default=0)
    next_retry_at = Column(DateTime, nullable=True)
    delivered_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
