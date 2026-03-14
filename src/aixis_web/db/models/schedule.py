"""Audit schedule models for periodic re-testing."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, JSON, String

from ..base import Base


def new_uuid():
    return str(uuid.uuid4())


class AuditSchedule(Base):
    __tablename__ = "audit_schedules"

    id = Column(String(36), primary_key=True, default=new_uuid)
    tool_id = Column(String(36), ForeignKey("tools.id"), nullable=False)
    profile_id = Column(String(100), default="")
    categories = Column(JSON, default=list)
    cron_expression = Column(String(100), nullable=False)  # e.g., "0 3 * * 0"
    is_active = Column(Boolean, default=True)
    last_run_at = Column(DateTime, nullable=True)
    next_run_at = Column(DateTime, nullable=True)
    run_count = Column(Integer, default=0)
    created_by = Column(String(36), ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
