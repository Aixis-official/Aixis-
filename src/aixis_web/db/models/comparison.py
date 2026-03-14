"""Comparison group models."""

import uuid
from datetime import datetime

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from ..base import Base


def new_uuid():
    return str(uuid.uuid4())


class ComparisonGroup(Base):
    __tablename__ = "comparison_groups"

    id = Column(String(36), primary_key=True, default=new_uuid)
    name = Column(String(200), nullable=False)
    name_jp = Column(String(200))
    category_id = Column(String(36), ForeignKey("tool_categories.id"))
    description_jp = Column(Text)
    created_by = Column(String(36), ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)

    members = relationship("ComparisonMember", back_populates="group")


class ComparisonMember(Base):
    __tablename__ = "comparison_members"

    id = Column(String(36), primary_key=True, default=new_uuid)
    group_id = Column(
        String(36),
        ForeignKey("comparison_groups.id", ondelete="CASCADE"),
        nullable=False,
    )
    tool_id = Column(String(36), ForeignKey("tools.id"), nullable=False)
    session_id = Column(String(36), ForeignKey("audit_sessions.id"))
    sort_order = Column(Integer, default=0)

    group = relationship("ComparisonGroup", back_populates="members")

    __table_args__ = (
        UniqueConstraint(
            "group_id", "tool_id", name="uq_comparison_members_group_tool"
        ),
    )


class ComparisonNormalizedScore(Base):
    __tablename__ = "comparison_normalized_scores"

    id = Column(String(36), primary_key=True, default=new_uuid)
    group_id = Column(
        String(36),
        ForeignKey("comparison_groups.id", ondelete="CASCADE"),
        nullable=False,
    )
    tool_id = Column(String(36), ForeignKey("tools.id"), nullable=False)
    axis = Column(String(30), nullable=False)
    raw_score = Column(Float, nullable=False)
    normalized_score = Column(Float, nullable=False)
    percentile = Column(Float)

    __table_args__ = (
        UniqueConstraint(
            "group_id",
            "tool_id",
            "axis",
            name="uq_comparison_normalized_group_tool_axis",
        ),
    )
