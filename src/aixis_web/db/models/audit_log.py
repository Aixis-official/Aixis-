"""Audit log for tracking destructive and critical operations."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Index, String, JSON

from ..base import Base


def new_uuid():
    return str(uuid.uuid4())


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(String(36), primary_key=True, default=new_uuid)
    entity_type = Column(String(50), nullable=False)  # "audit_session", "tool", etc.
    entity_id = Column(String(36), nullable=False)
    action = Column(String(30), nullable=False)  # "soft_delete", "restore", "finalize", etc.
    performed_by = Column(String(36), ForeignKey("users.id"), nullable=True)
    performed_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    changes = Column(JSON, default=dict)  # Context data (old/new values, etc.)

    __table_args__ = (
        Index("ix_audit_logs_entity", "entity_type", "entity_id"),
        Index("ix_audit_logs_performed_at", "performed_at"),
    )
