"""User session tracking for concurrent session management."""

from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String

from ..base import Base
from .user import new_uuid


class UserSession(Base):
    __tablename__ = "user_sessions"

    id = Column(String(36), primary_key=True, default=new_uuid)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    jti = Column(String(64), unique=True, nullable=False)  # JWT ID for revocation linkage
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    last_active_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    ip_address = Column(String(45))  # IPv6 max length
    user_agent = Column(String(500))
    is_active = Column(Boolean, default=True)
