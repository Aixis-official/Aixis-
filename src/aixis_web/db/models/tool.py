"""Tool catalog models."""

import uuid
from datetime import datetime, timezone

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

    # === SEO Article Content Fields ===

    # ユースケース (Use Cases)
    use_cases_jp = Column(JSON)  # [{title, description, industry}]
    use_cases_en = Column(JSON)

    # 料金詳細 (Pricing Detail) - extends pricing_model/price_min/max
    pricing_detail_jp = Column(Text)  # Markdown pricing breakdown
    pricing_detail_en = Column(Text)
    pricing_tiers = Column(JSON)  # [{"name":"Free","price_jpy":0,"features":[...]}]
    free_trial_available = Column(Boolean)
    free_trial_days = Column(Integer)

    # リスク・注意点 (Risks)
    risks_jp = Column(Text)  # Markdown
    risks_en = Column(Text)

    # 導入企業像 (Target Company Profile)
    target_company_profile_jp = Column(Text)  # Markdown
    target_company_profile_en = Column(Text)
    target_company_sizes = Column(JSON)  # ["startup","smb","mid","enterprise"]
    target_departments = Column(JSON)  # ["sales","legal","hr","engineering"]

    # メリット・デメリット (Pros/Cons)
    pros_jp = Column(JSON)  # list of strings
    pros_en = Column(JSON)
    cons_jp = Column(JSON)
    cons_en = Column(JSON)

    # 競合ツール (Alternatives)
    alternatives_slugs = Column(JSON)  # list of tool slugs

    # SEO metadata
    seo_title_jp = Column(String(200))
    seo_description_jp = Column(String(500))
    seo_keywords_jp = Column(JSON)  # list of strings
    content_updated_at = Column(DateTime(timezone=True))

    # auth: Playwright storage_state JSON for authenticated sessions
    # Format: {"cookies": [...], "origins": [...]} (Playwright storage_state format)
    auth_storage_state = Column(JSON)

    # status
    is_public = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)

    # timestamps
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    # relationships
    category = relationship("ToolCategory", back_populates="tools")
    target_configs = relationship("ToolTargetConfig", back_populates="tool")
    audit_sessions = relationship("AuditSession", back_populates="tool")
    scores = relationship("ToolPublishedScore", back_populates="tool")
    industry_mappings = relationship("ToolIndustryMapping", back_populates="tool")
    use_case_mappings = relationship("ToolUseCaseMapping", back_populates="tool")
    risk_governance = relationship("ToolRiskGovernance", back_populates="tool")


class ToolTargetConfig(Base):
    __tablename__ = "tool_target_configs"

    id = Column(String(36), primary_key=True, default=new_uuid)
    tool_id = Column(
        String(36), ForeignKey("tools.id", ondelete="CASCADE"), nullable=False
    )
    config_yaml = Column(Text, nullable=False)
    version = Column(Integer, default=1)
    is_active = Column(Boolean, default=True)
    validated_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    tool = relationship("Tool", back_populates="target_configs")
