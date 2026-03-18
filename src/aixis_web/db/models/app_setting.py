"""Persistent key-value settings stored in PostgreSQL.

Survives container restarts and Railway re-deploys.
"""

from sqlalchemy import Column, String, Text, DateTime
from datetime import datetime, timezone

from ..base import Base


class AppSetting(Base):
    __tablename__ = "app_settings"

    key = Column(String(255), primary_key=True)
    value = Column(Text, nullable=False, default="")
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
