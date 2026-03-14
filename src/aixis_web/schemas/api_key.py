"""API Key schemas."""

from datetime import datetime

from pydantic import BaseModel


class ApiKeyCreate(BaseModel):
    name: str
    scopes: list[str] = ["read:tools", "read:scores", "read:rankings"]
    rate_limit_per_minute: int = 60
    rate_limit_per_day: int = 10000


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
