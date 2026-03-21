"""API Key schemas."""

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

# Allowed scopes for public API keys
ALLOWED_SCOPES = {"read:tools", "read:scores", "read:rankings", "agent:write"}

# Rate limit caps to prevent abuse
MAX_RATE_LIMIT_PER_MINUTE = 120
MAX_RATE_LIMIT_PER_DAY = 50000


class ApiKeyCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    scopes: list[str] = ["read:tools", "read:scores", "read:rankings"]
    rate_limit_per_minute: int = Field(60, ge=1, le=MAX_RATE_LIMIT_PER_MINUTE)
    rate_limit_per_day: int = Field(10000, ge=1, le=MAX_RATE_LIMIT_PER_DAY)

    @field_validator("scopes")
    @classmethod
    def validate_scopes(cls, v: list[str]) -> list[str]:
        invalid = set(v) - ALLOWED_SCOPES
        if invalid:
            raise ValueError(f"Invalid scopes: {invalid}. Allowed: {ALLOWED_SCOPES}")
        if not v:
            raise ValueError("At least one scope is required")
        return v


class ApiKeyResponse(BaseModel):
    id: str
    key_prefix: str
    name: str
    scopes: list[str]
    rate_limit_per_minute: int
    rate_limit_per_day: int
    is_active: bool
    last_used_at: datetime | None = None
    expires_at: datetime | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class ApiKeyCreatedResponse(ApiKeyResponse):
    raw_key: str  # Only returned once at creation time
