"""Industry tag and use-case tag models for tool classification."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import relationship

from ..base import Base


def new_uuid():
    return str(uuid.uuid4())


class IndustryTag(Base):
    """Master list of industries, seeded from config/profiles/ YAML files."""

    __tablename__ = "industry_tags"

    id = Column(String(36), primary_key=True, default=new_uuid)
    slug = Column(String(100), unique=True, nullable=False)
    name_jp = Column(String(100), nullable=False)
    name_en = Column(String(100))
    parent_slug = Column(String(100))
    sort_order = Column(Integer, default=0)
    # Links to config/profiles/*.yaml for test generation
    profile_id = Column(String(100))

    tool_mappings = relationship("ToolIndustryMapping", back_populates="industry")


class ToolIndustryMapping(Base):
    """Maps tools to industries with fit indicators."""

    __tablename__ = "tool_industry_mappings"

    id = Column(String(36), primary_key=True, default=new_uuid)
    tool_id = Column(
        String(36), ForeignKey("tools.id", ondelete="CASCADE"), nullable=False
    )
    industry_id = Column(
        String(36), ForeignKey("industry_tags.id", ondelete="CASCADE"), nullable=False
    )
    fit_level = Column(String(20), default="recommended")  # recommended|compatible|limited
    use_case_summary_jp = Column(Text)
    use_case_summary_en = Column(Text)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    industry = relationship("IndustryTag", back_populates="tool_mappings")
    tool = relationship("Tool", back_populates="industry_mappings")

    __table_args__ = (
        UniqueConstraint("tool_id", "industry_id", name="uq_tool_industry"),
    )


class UseCaseTag(Base):
    """Normalized use-case categories for cross-tool comparison."""

    __tablename__ = "use_case_tags"

    id = Column(String(36), primary_key=True, default=new_uuid)
    slug = Column(String(100), unique=True, nullable=False)
    name_jp = Column(String(100), nullable=False)
    name_en = Column(String(100))
    category = Column(String(50))  # productivity|analysis|communication|creation
    sort_order = Column(Integer, default=0)

    tool_mappings = relationship("ToolUseCaseMapping", back_populates="use_case")


class ToolUseCaseMapping(Base):
    """Maps tools to use cases."""

    __tablename__ = "tool_use_case_mappings"

    id = Column(String(36), primary_key=True, default=new_uuid)
    tool_id = Column(
        String(36), ForeignKey("tools.id", ondelete="CASCADE"), nullable=False
    )
    use_case_id = Column(
        String(36), ForeignKey("use_case_tags.id", ondelete="CASCADE"), nullable=False
    )
    relevance = Column(String(20), default="primary")  # primary|secondary
    description_jp = Column(Text)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    use_case = relationship("UseCaseTag", back_populates="tool_mappings")
    tool = relationship("Tool", back_populates="use_case_mappings")

    __table_args__ = (
        UniqueConstraint("tool_id", "use_case_id", name="uq_tool_use_case"),
    )
