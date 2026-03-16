"""User and organization models."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text

from ..base import Base


def new_uuid():
    return str(uuid.uuid4())


class Organization(Base):
    __tablename__ = "organizations"

    id = Column(String(36), primary_key=True, default=new_uuid)
    name = Column(String(200), nullable=False)
    name_jp = Column(String(200))
    subscription_tier = Column(String(20), default="free")
    max_users = Column(Integer, default=5)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class User(Base):
    __tablename__ = "users"

    id = Column(String(36), primary_key=True, default=new_uuid)
    email = Column(String(200), unique=True, nullable=False)
    name = Column(String(200), nullable=False)
    name_jp = Column(String(200))
    hashed_password = Column(String(200))
    role = Column(String(20), default="client")  # admin|auditor|analyst|client|vendor|viewer
    organization_id = Column(String(36), ForeignKey("organizations.id"))
    preferred_language = Column(String(5), default="ja")  # ja, en
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    # Trial & subscription management
    account_status = Column(String(20), default="active")  # pending|active|suspended|expired
    subscription_tier = Column(String(20), default="trial")  # trial|standard|professional|enterprise
    trial_start = Column(DateTime, nullable=True)
    trial_end = Column(DateTime, nullable=True)
    trial_reminder_sent = Column(Boolean, default=False)

    # Invite flow
    invite_token_hash = Column(String(128), nullable=True, unique=True)
    invite_token_expires_at = Column(DateTime, nullable=True)
    invite_sent_at = Column(DateTime, nullable=True)


class AuditReportRecord(Base):
    __tablename__ = "audit_reports"

    id = Column(String(36), primary_key=True, default=new_uuid)
    session_id = Column(String(36), ForeignKey("audit_sessions.id"), nullable=False)
    report_type = Column(String(30), nullable=False)  # full|summary|executive
    format = Column(String(10), nullable=False)  # pdf|html|json
    file_path = Column(String(500))
    file_size_bytes = Column(Integer)
    is_public = Column(Boolean, default=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
