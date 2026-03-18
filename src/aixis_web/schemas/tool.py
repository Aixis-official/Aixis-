"""Tool catalog schemas."""
from datetime import datetime

from pydantic import BaseModel, HttpUrl


class ToolCreate(BaseModel):
    slug: str
    name: str
    name_jp: str
    vendor: str | None = None
    url: str | None = None
    description: str | None = None
    description_jp: str | None = None
    category_id: str | None = None
    profile_id: str | None = None
    pricing_model: str | None = None
    price_min_jpy: int | None = None
    price_max_jpy: int | None = None
    pricing_notes: str | None = None
    logo_url: str | None = None
    screenshots: list[str] = []
    features: list[str] = []
    supported_languages: list[str] = ["ja"]
    is_public: bool = False
    # SEO article content fields
    use_cases_jp: list[dict] | None = None
    use_cases_en: list[dict] | None = None
    pricing_detail_jp: str | None = None
    pricing_detail_en: str | None = None
    pricing_tiers: list[dict] | None = None
    free_trial_available: bool | None = None
    free_trial_days: int | None = None
    risks_jp: str | None = None
    risks_en: str | None = None
    target_company_profile_jp: str | None = None
    target_company_profile_en: str | None = None
    target_company_sizes: list[str] | None = None
    target_departments: list[str] | None = None
    pros_jp: list[str] | None = None
    pros_en: list[str] | None = None
    cons_jp: list[str] | None = None
    cons_en: list[str] | None = None
    alternatives_slugs: list[str] | None = None
    seo_title_jp: str | None = None
    seo_description_jp: str | None = None
    seo_keywords_jp: list[str] | None = None


class ToolUpdate(BaseModel):
    name: str | None = None
    name_jp: str | None = None
    vendor: str | None = None
    url: str | None = None
    description: str | None = None
    description_jp: str | None = None
    category_id: str | None = None
    profile_id: str | None = None
    pricing_model: str | None = None
    price_min_jpy: int | None = None
    price_max_jpy: int | None = None
    pricing_notes: str | None = None
    logo_url: str | None = None
    screenshots: list[str] | None = None
    features: list[str] | None = None
    supported_languages: list[str] | None = None
    is_public: bool | None = None
    is_active: bool | None = None
    # SEO article content fields
    use_cases_jp: list[dict] | None = None
    use_cases_en: list[dict] | None = None
    pricing_detail_jp: str | None = None
    pricing_detail_en: str | None = None
    pricing_tiers: list[dict] | None = None
    free_trial_available: bool | None = None
    free_trial_days: int | None = None
    risks_jp: str | None = None
    risks_en: str | None = None
    target_company_profile_jp: str | None = None
    target_company_profile_en: str | None = None
    target_company_sizes: list[str] | None = None
    target_departments: list[str] | None = None
    pros_jp: list[str] | None = None
    pros_en: list[str] | None = None
    cons_jp: list[str] | None = None
    cons_en: list[str] | None = None
    alternatives_slugs: list[str] | None = None
    seo_title_jp: str | None = None
    seo_description_jp: str | None = None
    seo_keywords_jp: list[str] | None = None
    auth_storage_state: dict | None = None


class CategoryResponse(BaseModel):
    id: str
    slug: str
    name_jp: str
    name_en: str | None = None
    parent_id: str | None = None
    sort_order: int
    description_jp: str | None = None

    model_config = {"from_attributes": True}


class ToolResponse(BaseModel):
    id: str
    slug: str
    name: str
    name_jp: str
    vendor: str | None = None
    url: str | None = None
    description: str | None = None
    description_jp: str | None = None
    category_id: str | None = None
    category_name_jp: str | None = None
    pricing_model: str | None = None
    price_min_jpy: int | None = None
    price_max_jpy: int | None = None
    logo_url: str | None = None
    screenshots: list[str] = []
    features: list[str] = []
    supported_languages: list[str] = []
    is_public: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime
    # SEO article content fields
    use_cases_jp: list[dict] | None = None
    use_cases_en: list[dict] | None = None
    pricing_detail_jp: str | None = None
    pricing_detail_en: str | None = None
    pricing_tiers: list[dict] | None = None
    free_trial_available: bool | None = None
    free_trial_days: int | None = None
    risks_jp: str | None = None
    risks_en: str | None = None
    target_company_profile_jp: str | None = None
    target_company_profile_en: str | None = None
    target_company_sizes: list[str] | None = None
    target_departments: list[str] | None = None
    pros_jp: list[str] | None = None
    pros_en: list[str] | None = None
    cons_jp: list[str] | None = None
    cons_en: list[str] | None = None
    alternatives_slugs: list[str] | None = None
    seo_title_jp: str | None = None
    seo_description_jp: str | None = None
    seo_keywords_jp: list[str] | None = None
    content_updated_at: datetime | None = None
    auth_storage_state: dict | None = None

    model_config = {"from_attributes": True}


class ToolListResponse(BaseModel):
    items: list[ToolResponse]
    total: int
    page: int
    page_size: int


class TargetConfigCreate(BaseModel):
    config_yaml: str


class TargetConfigResponse(BaseModel):
    id: str
    tool_id: str
    config_yaml: str
    version: int
    is_active: bool
    validated_at: datetime | None = None
    created_at: datetime

    model_config = {"from_attributes": True}
