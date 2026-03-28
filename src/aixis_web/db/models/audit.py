"""Audit session and test result models."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Text, JSON
from sqlalchemy.orm import relationship

from ..base import Base


def new_uuid():
    return str(uuid.uuid4())


class AuditSession(Base):
    __tablename__ = "audit_sessions"

    id = Column(String(36), primary_key=True, default=new_uuid)
    session_code = Column(String(50), unique=True, nullable=False)
    tool_id = Column(String(36), ForeignKey("tools.id"), nullable=False)
    profile_id = Column(String(100), nullable=False, default="")
    status = Column(
        String(20), default="pending"
    )  # pending|running|scoring|waiting_login|aborting|awaiting_manual|completed|failed|cancelled|aborted
    total_planned = Column(Integer, default=0)
    total_executed = Column(Integer, default=0)
    error_message = Column(Text)
    initiated_by = Column(String(36), ForeignKey("users.id"))
    started_at = Column(DateTime(timezone=True))
    completed_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    # Soft-delete support (audit data must never be permanently lost)
    deleted_at = Column(DateTime(timezone=True), nullable=True, default=None)
    deleted_by = Column(String(36), ForeignKey("users.id"), nullable=True, default=None)

    # AI agent volume tracking
    executor_type = Column(String(20), default="extension")
    ai_total_steps = Column(Integer, default=0)
    ai_total_api_calls = Column(Integer, default=0)
    ai_total_input_tokens = Column(Integer, default=0)
    ai_total_output_tokens = Column(Integer, default=0)
    ai_estimated_cost_usd = Column(Integer, default=0)  # stored in cents
    ai_screenshots_captured = Column(Integer, default=0)
    completeness_ratio = Column(Integer, default=0)  # 0-100 percentage

    # BenchRisk-inspired reliability meta-scores (JSON)
    # {consistency, comprehensiveness, correctness, intelligibility, overall, details}
    reliability_scores = Column(JSON)

    # relationships
    tool = relationship("Tool", back_populates="audit_sessions")
    test_results = relationship("DBTestResult", back_populates="session")
    axis_scores = relationship("AxisScoreRecord", back_populates="session")
    checklist_entries = relationship("ManualChecklistRecord", back_populates="session")

    __table_args__ = (
        Index("ix_audit_sessions_tool_id", "tool_id"),
        Index("ix_audit_sessions_status", "status"),
    )


class DBTestCase(Base):
    __tablename__ = "db_test_cases"

    id = Column(String(200), primary_key=True)
    session_id = Column(
        String(36),
        ForeignKey("audit_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    category = Column(String(50), nullable=False)
    prompt = Column(Text, nullable=False)
    metadata_json = Column(JSON, default=dict)
    expected_behaviors = Column(JSON, default=list)
    failure_indicators = Column(JSON, default=list)
    tags = Column(JSON, default=list)

    __table_args__ = (
        Index("ix_db_test_cases_session_id", "session_id"),
        Index("ix_db_test_cases_category", "category"),
    )


class DBTestResult(Base):
    __tablename__ = "db_test_results"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(
        String(36),
        ForeignKey("audit_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    test_case_id = Column(String(200), nullable=False)
    category = Column(String(50), nullable=False)
    prompt_sent = Column(Text, nullable=False)
    response_raw = Column(Text)
    response_time_ms = Column(Integer, default=0)
    error = Column(Text)
    screenshot_path = Column(String(500))
    page_url = Column(String(500))
    executed_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    metadata_json = Column(JSON, default=dict)
    # AI agent per-test metrics
    ai_steps_taken = Column(Integer, default=0)
    ai_calls_used = Column(Integer, default=0)
    ai_tokens_input = Column(Integer, default=0)
    ai_tokens_output = Column(Integer, default=0)

    session = relationship("AuditSession", back_populates="test_results")

    __table_args__ = (
        Index("ix_db_test_results_session_id", "session_id"),
        Index("ix_db_test_results_category", "category"),
        Index("ix_db_test_results_test_case_id", "test_case_id"),
    )
