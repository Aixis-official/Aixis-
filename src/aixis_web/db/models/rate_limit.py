"""Rate limit tracking model for multi-worker environments."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Index, Integer, String

from ..base import Base


def new_uuid():
    return str(uuid.uuid4())


class RateLimitEntry(Base):
    __tablename__ = "rate_limit_entries"

    id = Column(String(36), primary_key=True, default=new_uuid)
    key = Column(String(255), nullable=False, index=True)  # e.g. "login:<ip>" or "contact:<ip>"
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_rate_limit_key_created", "key", "created_at"),
    )
