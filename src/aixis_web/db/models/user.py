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
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


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
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    # Trial & subscription management (legacy; kept for future optional paid features)
    account_status = Column(String(20), default="active")  # pending|active|suspended|expired
    subscription_tier = Column(String(20), default="registered")  # registered|standard|professional|enterprise
    trial_start = Column(DateTime(timezone=True), nullable=True)
    trial_end = Column(DateTime(timezone=True), nullable=True)
    trial_reminder_sent = Column(Boolean, default=False)

    # Invite flow (legacy admin-initiated onboarding)
    invite_token_hash = Column(String(128), nullable=True, unique=True)
    invite_token_expires_at = Column(DateTime(timezone=True), nullable=True)
    invite_sent_at = Column(DateTime(timezone=True), nullable=True)

    # Free registration (2026-04-15 pivot) — required profile fields
    company_name = Column(String(200), nullable=True)
    job_title = Column(String(100), nullable=True)
    industry = Column(String(50), nullable=True)  # slug from industry taxonomy
    employee_count = Column(String(20), nullable=True)  # 1-10|11-50|51-200|201-1000|1001+

    # Free registration — optional profile fields (progressive profiling)
    phone = Column(String(30), nullable=True)
    interest_areas = Column(Text, nullable=True)  # JSON array of slugs
    referral_source = Column(String(100), nullable=True)  # how they heard about us

    # Email verification
    email_verified_at = Column(DateTime(timezone=True), nullable=True)

    # Consent and compliance (APPI)
    agreed_to_terms_at = Column(DateTime(timezone=True), nullable=True)
    agreed_to_privacy_at = Column(DateTime(timezone=True), nullable=True)
    marketing_opt_in = Column(Boolean, default=False)

    # Lead-gen metadata
    registration_source = Column(String(100), nullable=True)  # landing|tool_detail|compare|etc.
    lead_score = Column(Integer, default=0, nullable=False)  # behavior-driven
    last_active_at = Column(DateTime(timezone=True), nullable=True)
    sales_status = Column(String(30), default="uncontacted")  # uncontacted|contacted|in_discussion|won|lost
    sales_notes = Column(Text, nullable=True)  # admin-only

    # Onboarding wizard progress
    onboarding_completed_at = Column(DateTime(timezone=True), nullable=True)


class AuditReportRecord(Base):
    __tablename__ = "audit_reports"

    id = Column(String(36), primary_key=True, default=new_uuid)
    session_id = Column(String(36), ForeignKey("audit_sessions.id"), nullable=False)
    report_type = Column(String(30), nullable=False)  # full|summary|executive
    format = Column(String(10), nullable=False)  # pdf|html|json
    file_path = Column(String(500))
    file_size_bytes = Column(Integer)
    is_public = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
