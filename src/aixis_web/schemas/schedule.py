"""Audit schedule schemas."""

from datetime import datetime

from pydantic import BaseModel


class ScheduleCreate(BaseModel):
    tool_id: str
    profile_id: str = ""
    categories: list[str] = []
    cron_expression: str


class ScheduleUpdate(BaseModel):
    is_active: bool | None = None
    cron_expression: str | None = None


class ScheduleResponse(BaseModel):
    id: str
    tool_id: str
    profile_id: str
    categories: list[str]
    cron_expression: str
    is_active: bool
    last_run_at: datetime | None = None
    next_run_at: datetime | None = None
    run_count: int
    created_by: str
    created_at: datetime
    tool_name: str | None = None

    model_config = {"from_attributes": True}
