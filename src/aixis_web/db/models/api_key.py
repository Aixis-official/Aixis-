"""API Key model for public API access."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, JSON, String

from ..base import Base


def new_uuid():
    return str(uuid.uuid4())


class ApiKey(Base):
    __tablename__ = "api_keys"

    id = Column(String(36), primary_key=True, default=new_uuid)
    key_hash = Column(String(128), unique=True, nullable=False, index=True)  # SHA256 hash
    key_prefix = Column(String(12), nullable=False)  # e.g., "axk_a1b2c3d4"
    name = Column(String(200), nullable=False)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    scopes = Column(JSON, default=lambda: ["read:tools", "read:scores", "read:rankings"])
    rate_limit_per_minute = Column(Integer, default=60)
    rate_limit_per_day = Column(Integer, default=10000)
    is_active = Column(Boolean, default=True)
    last_used_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
