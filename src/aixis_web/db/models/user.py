"""User and organization models."""

import uuid
from datetime import datetime

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
    created_at = Column(DateTime, default=datetime.utcnow)


class User(Base):
    __tablename__ = "users"

    id = Column(String(36), primary_key=True, default=new_uuid)
    email = Column(String(200), unique=True, nullable=False)
    name = Column(String(200), nullable=False)
    name_jp = Column(String(200))
    hashed_password = Column(String(200))
    role = Column(String(20), default="client")  # admin|auditor|client
    organization_id = Column(String(36), ForeignKey("organizations.id"))
    preferred_language = Column(String(5), default="ja")  # ja, en
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class AuditReportRecord(Base):
    __tablename__ = "audit_reports"

    id = Column(String(36), primary_key=True, default=new_uuid)
    session_id = Column(String(36), ForeignKey("audit_sessions.id"), nullable=False)
    report_type = Column(String(30), nullable=False)  # full|summary|executive
    format = Column(String(10), nullable=False)  # pdf|html|json
    file_path = Column(String(500))
    file_size_bytes = Column(Integer)
    is_public = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
