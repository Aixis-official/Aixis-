"""Score and ranking schemas."""
from datetime import datetime

from pydantic import BaseModel


class AxisScoreResponse(BaseModel):
    axis: str
    axis_name_jp: str
    score: float
    confidence: float
    source: str

    model_config = {"from_attributes": True}


class ScoreResponse(BaseModel):
    id: str
    tool_id: str
    practicality: float
    cost_performance: float
    localization: float
    safety: float
    uniqueness: float
    overall_score: float
    overall_grade: str | None = None
    version: int
    published_at: datetime
    axis_scores: list[AxisScoreResponse] = []

    model_config = {"from_attributes": True}


class ScoreHistoryItem(BaseModel):
    axis: str
    score: float
    overall_score: float | None = None
    overall_grade: str | None = None
    recorded_at: datetime
    source_session_id: str | None = None

    model_config = {"from_attributes": True}


class ScoreHistoryResponse(BaseModel):
    tool_id: str
    items: list[ScoreHistoryItem]


class RankingEntry(BaseModel):
    tool_id: str
    tool_name: str
    tool_name_jp: str
    tool_slug: str
    overall_score: float
    overall_grade: str | None = None
    rank: int


class RankingResponse(BaseModel):
    category_id: str | None = None
    category_name_jp: str | None = None
    entries: list[RankingEntry]
    total: int
