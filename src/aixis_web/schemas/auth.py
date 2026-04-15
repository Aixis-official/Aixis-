"""Authentication schemas."""
from pydantic import BaseModel, EmailStr, Field, field_validator


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=256)
    remember_me: bool = False


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str | None = None
    token_type: str = "bearer"
    expires_in: int


class UserResponse(BaseModel):
    id: str
    email: str
    name: str
    name_jp: str | None = None
    role: str
    organization_id: str | None = None
    is_active: bool

    model_config = {"from_attributes": True}


# -------- Free registration (2026-04-15 pivot) --------

# Industry taxonomy (aligned with ToolIndustryMapping slugs where possible)
ALLOWED_INDUSTRIES = {
    "it_software",
    "manufacturing",
    "finance_insurance",
    "retail_ecommerce",
    "healthcare_pharma",
    "legal_professional",
    "consulting",
    "education",
    "media_entertainment",
    "real_estate_construction",
    "logistics_transport",
    "energy_utility",
    "government_public",
    "nonprofit",
    "other",
}

ALLOWED_EMPLOYEE_COUNTS = {
    "1-10",
    "11-50",
    "51-200",
    "201-1000",
    "1001+",
}


class RegisterRequest(BaseModel):
    """Public self-registration payload."""

    email: EmailStr
    password: str = Field(..., min_length=8, max_length=256)
    password_confirm: str = Field(..., min_length=8, max_length=256)
    name: str = Field(..., min_length=1, max_length=100)
    company_name: str = Field(..., min_length=1, max_length=200)
    job_title: str = Field(..., min_length=1, max_length=100)
    industry: str = Field(..., min_length=1, max_length=50)
    employee_count: str = Field(..., min_length=1, max_length=20)
    phone: str | None = Field(default=None, max_length=30)
    agreed_to_terms: bool
    agreed_to_privacy: bool
    marketing_opt_in: bool = False
    registration_source: str | None = Field(default=None, max_length=100)
    turnstile_token: str | None = Field(default=None, max_length=4096)

    @field_validator("industry")
    @classmethod
    def _validate_industry(cls, v: str) -> str:
        if v not in ALLOWED_INDUSTRIES:
            raise ValueError("業種の選択が不正です")
        return v

    @field_validator("employee_count")
    @classmethod
    def _validate_employee_count(cls, v: str) -> str:
        if v not in ALLOWED_EMPLOYEE_COUNTS:
            raise ValueError("会社規模の選択が不正です")
        return v

    @field_validator("agreed_to_terms")
    @classmethod
    def _require_terms(cls, v: bool) -> bool:
        if not v:
            raise ValueError("利用規約への同意が必要です")
        return v

    @field_validator("agreed_to_privacy")
    @classmethod
    def _require_privacy(cls, v: bool) -> bool:
        if not v:
            raise ValueError("プライバシーポリシーへの同意が必要です")
        return v


class RegisterResponse(BaseModel):
    message: str
    email: str


class ResendVerificationRequest(BaseModel):
    email: EmailStr


class VerifyEmailRequest(BaseModel):
    token: str = Field(..., min_length=16, max_length=512)
