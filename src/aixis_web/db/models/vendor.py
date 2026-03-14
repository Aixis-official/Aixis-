"""Vendor portal models."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, JSON, String, Text

from ..base import Base


def new_uuid():
    return str(uuid.uuid4())


class VendorProfile(Base):
    __tablename__ = "vendor_profiles"

    id = Column(String(36), primary_key=True, default=new_uuid)
    user_id = Column(String(36), ForeignKey("users.id"), unique=True, nullable=False)
    company_name = Column(String(200), nullable=False)
    company_name_jp = Column(String(200), default="")
    company_url = Column(String(500), default="")
    contact_email = Column(String(200), default="")
    verified = Column(Boolean, default=False)
    verified_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class ToolSubmission(Base):
    __tablename__ = "tool_submissions"

    id = Column(String(36), primary_key=True, default=new_uuid)
    vendor_id = Column(String(36), ForeignKey("vendor_profiles.id"), nullable=False)
    tool_name = Column(String(200), nullable=False)
    tool_name_jp = Column(String(200), default="")
    tool_url = Column(String(500), nullable=False)
    category_id = Column(String(36), ForeignKey("tool_categories.id"), nullable=True)
    description = Column(Text, default="")
    description_jp = Column(Text, default="")
    target_config_yaml = Column(Text, default="")
    status = Column(String(20), default="pending")  # pending|reviewing|approved|rejected
    reviewer_notes = Column(Text, nullable=True)
    reviewed_by = Column(String(36), ForeignKey("users.id"), nullable=True)
    approved_tool_id = Column(String(36), ForeignKey("tools.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ScoreDispute(Base):
    __tablename__ = "score_disputes"

    id = Column(String(36), primary_key=True, default=new_uuid)
    vendor_id = Column(String(36), ForeignKey("vendor_profiles.id"), nullable=False)
    tool_id = Column(String(36), ForeignKey("tools.id"), nullable=False)
    axis = Column(String(30), nullable=True)  # specific axis or null for overall
    reason = Column(Text, nullable=False)
    evidence_urls = Column(JSON, default=list)
    status = Column(String(20), default="open")  # open|under_review|resolved|rejected
    resolution_notes = Column(Text, nullable=True)
    resolved_by = Column(String(36), ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    resolved_at = Column(DateTime, nullable=True)
