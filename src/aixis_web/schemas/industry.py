"""Industry tag, use-case tag, and adoption schemas."""

from datetime import datetime

from pydantic import BaseModel


# --- Industry Tags ---

class IndustryTagResponse(BaseModel):
    id: str
    slug: str
    name_jp: str
    name_en: str | None = None
    parent_slug: str | None = None
    profile_id: str | None = None
    sort_order: int

    model_config = {"from_attributes": True}


class ToolIndustryMappingCreate(BaseModel):
    industry_id: str
    fit_level: str = "recommended"  # recommended|compatible|limited
    use_case_summary_jp: str | None = None


class ToolIndustryMappingResponse(BaseModel):
    id: str
    tool_id: str
    industry_id: str
    fit_level: str
    use_case_summary_jp: str | None = None
    industry: IndustryTagResponse | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


# --- Use Case Tags ---

class UseCaseTagResponse(BaseModel):
    id: str
    slug: str
    name_jp: str
    name_en: str | None = None
    category: str | None = None
    sort_order: int

    model_config = {"from_attributes": True}


class ToolUseCaseMappingCreate(BaseModel):
    use_case_id: str
    relevance: str = "primary"  # primary|secondary
    description_jp: str | None = None


class ToolUseCaseMappingResponse(BaseModel):
    id: str
    tool_id: str
    use_case_id: str
    relevance: str
    description_jp: str | None = None
    use_case: UseCaseTagResponse | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


# --- Adoption Patterns ---

class IndustryAdoptionPatternCreate(BaseModel):
    industry_id: str
    adoption_level: str | None = None
    estimated_adoption_pct: int | None = None
    data_source: str = "editorial"
    primary_use_cases_jp: list[str] | None = None
    typical_company_size: list[str] | None = None
    typical_departments: list[str] | None = None
    industry_fit_notes_jp: str | None = None
    case_study_summary_jp: str | None = None
    case_study_url: str | None = None
    confidence: float = 0.5


class IndustryAdoptionPatternResponse(BaseModel):
    id: str
    tool_id: str
    industry_id: str
    adoption_level: str | None = None
    estimated_adoption_pct: int | None = None
    data_source: str
    primary_use_cases_jp: list[str] | None = None
    typical_company_size: list[str] | None = None
    typical_departments: list[str] | None = None
    industry_fit_notes_jp: str | None = None
    case_study_summary_jp: str | None = None
    case_study_url: str | None = None
    confidence: float
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
