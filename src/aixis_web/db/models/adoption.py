"""Industry adoption and benchmark models."""

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
    JSON,
    UniqueConstraint,
)

from ..base import Base


def new_uuid():
    return str(uuid.uuid4())


class IndustryAdoptionPattern(Base):
    """Tracks AI tool adoption patterns by industry.

    Early stage: editorial estimates from public information.
    Later: real data from anonymous surveys and press releases.
    """

    __tablename__ = "industry_adoption_patterns"

    id = Column(String(36), primary_key=True, default=new_uuid)
    tool_id = Column(
        String(36), ForeignKey("tools.id", ondelete="CASCADE"), nullable=False
    )
    industry_id = Column(
        String(36), ForeignKey("industry_tags.id", ondelete="CASCADE"), nullable=False
    )

    # Adoption indicators
    adoption_level = Column(String(20))  # emerging|growing|established|dominant
    estimated_adoption_pct = Column(Integer)  # 0-100 rough estimate
    data_source = Column(String(30), default="editorial")  # editorial|survey|public_data|anonymous

    # How companies in this industry use the tool
    primary_use_cases_jp = Column(JSON)  # list of strings
    typical_company_size = Column(JSON)  # ["startup", "smb", "mid", "enterprise"]
    typical_departments = Column(JSON)  # ["legal", "compliance", "engineering"]

    # Qualitative assessment
    industry_fit_notes_jp = Column(Text)  # Markdown
    industry_fit_notes_en = Column(Text)

    # For SEO: 導入企業事例 section
    case_study_summary_jp = Column(Text)  # Markdown, anonymized or public
    case_study_url = Column(String(500))

    # Confidence and freshness
    confidence = Column(Float, default=0.5)  # 0.0-1.0
    last_verified_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("tool_id", "industry_id", name="uq_adoption_tool_industry"),
    )


class AdoptionSurveyResponse(Base):
    """Anonymous adoption data collection (future phase).

    Embeddable widget on tool detail pages for users to share
    which tools their company uses anonymously.
    """

    __tablename__ = "adoption_survey_responses"

    id = Column(String(36), primary_key=True, default=new_uuid)
    industry_id = Column(String(36), ForeignKey("industry_tags.id"))
    company_size = Column(String(20))  # startup|smb|mid|enterprise
    tool_id = Column(String(36), ForeignKey("tools.id"))
    use_case_tags = Column(JSON)  # list of use_case_tag slugs
    satisfaction_score = Column(Integer)  # 1-5
    anonymous_hash = Column(String(64))  # SHA256 of IP+UA for dedup
    submitted_at = Column(DateTime, default=datetime.utcnow)
