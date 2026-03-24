"""Scoring and evaluation models."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    JSON,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from ..base import Base


def new_uuid():
    return str(uuid.uuid4())


class AxisScoreRecord(Base):
    __tablename__ = "axis_scores"

    id = Column(String(36), primary_key=True, default=new_uuid)
    session_id = Column(
        String(36),
        ForeignKey("audit_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    tool_id = Column(String(36), ForeignKey("tools.id"), nullable=False)
    axis = Column(String(30), nullable=False)
    axis_name_jp = Column(String(50), nullable=False)
    score = Column(Float, nullable=False)  # 0.0-5.0
    confidence = Column(Float, default=0.0)
    source = Column(String(20), default="auto")  # auto|manual|hybrid
    details = Column(JSON, default=list)
    strengths = Column(JSON, default=list)
    risks = Column(JSON, default=list)
    scored_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    scored_by = Column(String(36), ForeignKey("users.id"))

    session = relationship("AuditSession", back_populates="axis_scores")

    __table_args__ = (
        UniqueConstraint("session_id", "axis", name="uq_axis_scores_session_axis"),
    )


class ToolPublishedScore(Base):
    __tablename__ = "tool_scores"

    id = Column(String(36), primary_key=True, default=new_uuid)
    tool_id = Column(String(36), ForeignKey("tools.id"), nullable=False)
    # Legacy axes (kept for backward compatibility with existing data)
    practicality = Column(Float, default=0.0)
    cost_performance = Column(Float, default=0.0)
    localization = Column(Float, default=0.0)
    safety = Column(Float, default=0.0)
    uniqueness = Column(Float, default=0.0)
    # New slide-creation-specific axes
    instruction_adherence = Column(Float, default=0.0)
    japanese_quality = Column(Float, default=0.0)
    structure_logic = Column(Float, default=0.0)
    contradiction_handling = Column(Float, default=0.0)
    accuracy = Column(Float, default=0.0)
    overall_score = Column(Float, default=0.0)
    overall_grade = Column(String(2))  # S|A|B|C|D|F
    source_session_id = Column(String(36), ForeignKey("audit_sessions.id"))
    version = Column(Integer, default=1)
    published_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    published_by = Column(String(36), ForeignKey("users.id"))

    tool = relationship("Tool", back_populates="scores")

    __table_args__ = (
        UniqueConstraint("tool_id", "version", name="uq_tool_scores_tool_version"),
    )


class ScoreHistory(Base):
    __tablename__ = "score_history"

    id = Column(String(36), primary_key=True, default=new_uuid)
    tool_id = Column(String(36), ForeignKey("tools.id"), nullable=False)
    axis = Column(String(30), nullable=False)
    score = Column(Float, nullable=False)
    overall_score = Column(Float, nullable=True)
    overall_grade = Column(String(2), nullable=True)
    recorded_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    source_session_id = Column(String(36), ForeignKey("audit_sessions.id"))

    __table_args__ = (
        Index("ix_score_history_tool_recorded", "tool_id", "recorded_at"),
    )


class ManualChecklistRecord(Base):
    __tablename__ = "manual_checklist_entries"

    id = Column(String(36), primary_key=True, default=new_uuid)
    session_id = Column(
        String(36),
        ForeignKey("audit_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    tool_id = Column(String(36), ForeignKey("tools.id"), nullable=False)
    axis = Column(String(30), nullable=False)
    checklist_item_id = Column(String(100), nullable=False)
    item_name_jp = Column(String(200), nullable=False)
    passed = Column(Boolean)
    score = Column(Float)  # 0.0-5.0
    weight = Column(Float, default=1.0)
    evidence = Column(Text)
    evidence_url = Column(String(500))
    evaluated_by = Column(String(36), ForeignKey("users.id"))
    evaluated_at = Column(DateTime(timezone=True))

    session = relationship("AuditSession", back_populates="checklist_entries")

    __table_args__ = (
        UniqueConstraint(
            "session_id",
            "checklist_item_id",
            name="uq_manual_checklist_session_item",
        ),
    )
