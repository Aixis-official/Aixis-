"""Risk and governance assessment schemas."""

from datetime import datetime

from pydantic import BaseModel


class RiskGovernanceCreate(BaseModel):
    # Risk classification
    risk_level: str | None = None  # low|medium|high|critical
    risk_level_rationale_jp: str | None = None

    # Data handling
    data_transparency_score: float | None = None
    data_retention_policy: str | None = None
    data_deletion_available: bool | None = None
    training_data_optout: bool | None = None
    data_residency_japan: bool | None = None
    data_handling_notes_jp: str | None = None

    # Japanese regulatory compliance
    ai_business_guideline_status: str | None = None
    ai_business_guideline_notes_jp: str | None = None
    appi_status: str | None = None
    appi_notes_jp: str | None = None
    gdpr_status: str | None = None
    gdpr_notes_jp: str | None = None

    # Industry-specific
    industry_compliance: list[dict] | None = None
    certifications: list[str] | None = None

    # Governance summary
    governance_summary_jp: str | None = None
    governance_summary_en: str | None = None

    source: str = "manual"


class RiskGovernanceUpdate(BaseModel):
    risk_level: str | None = None
    risk_level_rationale_jp: str | None = None
    data_transparency_score: float | None = None
    data_retention_policy: str | None = None
    data_deletion_available: bool | None = None
    training_data_optout: bool | None = None
    data_residency_japan: bool | None = None
    data_handling_notes_jp: str | None = None
    ai_business_guideline_status: str | None = None
    ai_business_guideline_notes_jp: str | None = None
    appi_status: str | None = None
    appi_notes_jp: str | None = None
    gdpr_status: str | None = None
    gdpr_notes_jp: str | None = None
    industry_compliance: list[dict] | None = None
    certifications: list[str] | None = None
    governance_summary_jp: str | None = None
    governance_summary_en: str | None = None
    source: str | None = None


class RiskGovernanceResponse(BaseModel):
    id: str
    tool_id: str
    version: int

    risk_level: str | None = None
    risk_level_rationale_jp: str | None = None

    data_transparency_score: float | None = None
    data_retention_policy: str | None = None
    data_deletion_available: bool | None = None
    training_data_optout: bool | None = None
    data_residency_japan: bool | None = None
    data_handling_notes_jp: str | None = None

    ai_business_guideline_status: str | None = None
    ai_business_guideline_notes_jp: str | None = None
    appi_status: str | None = None
    appi_notes_jp: str | None = None
    gdpr_status: str | None = None
    gdpr_notes_jp: str | None = None

    industry_compliance: list[dict] | None = None
    certifications: list[str] | None = None

    governance_score: float | None = None
    governance_grade: str | None = None
    governance_summary_jp: str | None = None

    assessed_at: datetime | None = None
    source: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class RegulatoryFrameworkResponse(BaseModel):
    id: str
    slug: str
    name_jp: str
    name_en: str | None = None
    category: str
    applicable_industries: list[str] | None = None
    country: str
    description_jp: str | None = None
    reference_url: str | None = None

    model_config = {"from_attributes": True}
