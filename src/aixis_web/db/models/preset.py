"""Audit preset / template models."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, JSON, Boolean
from sqlalchemy.orm import relationship

from ..base import Base


def new_uuid():
    return str(uuid.uuid4())


class AuditPreset(Base):
    __tablename__ = "audit_presets"

    id = Column(String(36), primary_key=True, default=new_uuid)
    name = Column(String(200), nullable=False)
    name_jp = Column(String(200))
    description = Column(Text)
    tool_id = Column(String(36), ForeignKey("tools.id"))
    profile_id = Column(String(100))
    categories = Column(JSON, default=list)  # list of category strings
    budget_max_calls = Column(Integer, default=200)
    budget_max_cost_jpy = Column(Integer, default=20)
    is_default = Column(Boolean, default=False)
    created_by = Column(String(36), ForeignKey("users.id"))
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
