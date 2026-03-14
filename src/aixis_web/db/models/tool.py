"""Tool catalog models."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text, JSON
from sqlalchemy.orm import relationship

from ..base import Base


def new_uuid():
    return str(uuid.uuid4())


class ToolCategory(Base):
    __tablename__ = "tool_categories"

    id = Column(String(36), primary_key=True, default=new_uuid)
    slug = Column(String(100), unique=True, nullable=False)
    name_jp = Column(String(100), nullable=False)
    name_en = Column(String(100))
    parent_id = Column(String(36), ForeignKey("tool_categories.id"))
    sort_order = Column(Integer, default=0)
    description_jp = Column(Text)

    # relationships
    tools = relationship("Tool", back_populates="category")
    children = relationship("ToolCategory")


class Tool(Base):
    __tablename__ = "tools"

    id = Column(String(36), primary_key=True, default=new_uuid)
    slug = Column(String(100), unique=True, nullable=False)
    name = Column(String(200), nullable=False)
    name_jp = Column(String(200), nullable=False)
    vendor = Column(String(200))
    url = Column(String(500))
    description = Column(Text)
    description_jp = Column(Text)
    category_id = Column(String(36), ForeignKey("tool_categories.id"))
    profile_id = Column(String(100))

    # pricing
    pricing_model = Column(String(50))  # free|freemium|paid|enterprise
    price_min_jpy = Column(Integer)
    price_max_jpy = Column(Integer)
    pricing_notes = Column(Text)

    # metadata
    logo_url = Column(String(500))
    screenshots = Column(JSON, default=list)
    features = Column(JSON, default=list)
    supported_languages = Column(JSON, default=lambda: ["ja"])

    # status
    is_public = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)

    # timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # relationships
    category = relationship("ToolCategory", back_populates="tools")
    target_configs = relationship("ToolTargetConfig", back_populates="tool")
    audit_sessions = relationship("AuditSession", back_populates="tool")
    scores = relationship("ToolPublishedScore", back_populates="tool")


class ToolTargetConfig(Base):
    __tablename__ = "tool_target_configs"

    id = Column(String(36), primary_key=True, default=new_uuid)
    tool_id = Column(
        String(36), ForeignKey("tools.id", ondelete="CASCADE"), nullable=False
    )
    config_yaml = Column(Text, nullable=False)
    version = Column(Integer, default=1)
    is_active = Column(Boolean, default=True)
    validated_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)

    tool = relationship("Tool", back_populates="target_configs")
