"""Risk and governance assessment models, separate from the 5-axis scoring."""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
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


class ToolRiskGovernance(Base):
    """Per-tool risk and governance assessment, versioned independently."""

    __tablename__ = "tool_risk_governance"

    id = Column(String(36), primary_key=True, default=new_uuid)
    tool_id = Column(
        String(36), ForeignKey("tools.id", ondelete="CASCADE"), nullable=False
    )
    version = Column(Integer, default=1)

    # --- Overall Risk Classification ---
    risk_level = Column(String(20))  # low|medium|high|critical
    risk_level_rationale_jp = Column(Text)
    risk_level_rationale_en = Column(Text)

    # --- Data Handling Transparency ---
    data_transparency_score = Column(Float)  # 0.0-5.0
    data_retention_policy = Column(String(50))  # clear|partial|unclear|none
    data_deletion_available = Column(Boolean)
    training_data_optout = Column(Boolean)
    data_residency_japan = Column(Boolean)
    data_handling_notes_jp = Column(Text)

    # --- Japanese Regulatory Compliance ---
    # AI事業者ガイドライン
    ai_business_guideline_status = Column(String(20))  # compliant|partial|non_compliant|unknown
    ai_business_guideline_notes_jp = Column(Text)

    # 個人情報保護法 (APPI)
    appi_status = Column(String(20))
    appi_notes_jp = Column(Text)

    # GDPR
    gdpr_status = Column(String(20))
    gdpr_notes_jp = Column(Text)

    # --- Industry-Specific Compliance (flexible JSON) ---
    # e.g. [{"regulation": "金融庁AI指針", "status": "compliant", "notes": "..."}]
    industry_compliance = Column(JSON)

    # --- Security Certifications ---
    # e.g. ["SOC2_TYPE2", "ISO27001", "ISMAP", "ISO27017"]
    certifications = Column(JSON)

    # --- Composite Governance Score ---
    governance_score = Column(Float)  # 0.0-5.0 (computed)
    governance_grade = Column(String(2))  # S|A|B|C|D|F

    # --- SEO-ready governance summary ---
    governance_summary_jp = Column(Text)  # Markdown for article section
    governance_summary_en = Column(Text)

    # --- Metadata ---
    assessed_at = Column(DateTime)
    assessed_by = Column(String(36), ForeignKey("users.id"))
    source = Column(String(20), default="manual")  # manual|auto|hybrid
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    tool = relationship("Tool", back_populates="risk_governance")

    __table_args__ = (
        UniqueConstraint("tool_id", "version", name="uq_tool_risk_gov_version"),
    )


class RegulatoryFramework(Base):
    """Master list of regulations that tools can be assessed against."""

    __tablename__ = "regulatory_frameworks"

    id = Column(String(36), primary_key=True, default=new_uuid)
    slug = Column(String(100), unique=True, nullable=False)
    name_jp = Column(String(200), nullable=False)
    name_en = Column(String(200))
    category = Column(String(50), nullable=False)  # general|industry_specific|international
    applicable_industries = Column(JSON)  # ["finance", "medical"] or null for all
    country = Column(String(10), default="JP")  # JP|US|EU|global
    description_jp = Column(Text)
    reference_url = Column(String(500))
    is_active = Column(Boolean, default=True)
    sort_order = Column(Integer, default=0)
