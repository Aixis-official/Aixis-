"""Comparison schemas."""
from datetime import datetime

from pydantic import BaseModel


class ComparisonCreate(BaseModel):
    name: str
    name_jp: str | None = None
    category_id: str | None = None
    description_jp: str | None = None
    tool_ids: list[str] = []


class ComparisonMemberResponse(BaseModel):
    tool_id: str
    tool_name: str
    tool_name_jp: str
    session_id: str | None = None
    sort_order: int


class NormalizedScoreResponse(BaseModel):
    tool_id: str
    axis: str
    raw_score: float
    normalized_score: float
    percentile: float | None = None


class ComparisonResponse(BaseModel):
    id: str
    name: str
    name_jp: str | None = None
    category_id: str | None = None
    description_jp: str | None = None
    created_at: datetime
    members: list[ComparisonMemberResponse] = []
    normalized_scores: list[NormalizedScoreResponse] = []

    model_config = {"from_attributes": True}


class AddToolRequest(BaseModel):
    tool_id: str
    session_id: str | None = None
