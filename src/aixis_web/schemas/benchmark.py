"""Benchmark and leaderboard schemas."""

from datetime import datetime

from pydantic import BaseModel


class BenchmarkSuiteCreate(BaseModel):
    slug: str
    name: str
    name_jp: str
    description: str = ""
    description_jp: str = ""
    version: str = "v1.0"
    category_id: str | None = None


class BenchmarkSuiteResponse(BaseModel):
    id: str
    slug: str
    name: str
    name_jp: str
    description: str
    description_jp: str
    version: str
    category_id: str | None = None
    test_case_count: int
    is_published: bool
    published_at: datetime | None = None
    created_by: str
    created_at: datetime

    model_config = {"from_attributes": True}


class BenchmarkTestCaseCreate(BaseModel):
    category: str
    prompt: str
    expected_behaviors: list[str] = []
    failure_indicators: list[str] = []
    weight: float = 1.0
    tags: list[str] = []


class BenchmarkRunResponse(BaseModel):
    id: str
    suite_id: str
    tool_id: str
    suite_version: str
    score: float
    total_cases: int
    passed_cases: int
    axis_scores: dict = {}
    details: dict = {}
    started_at: datetime
    completed_at: datetime | None = None
    tool_name: str | None = None
    tool_slug: str | None = None

    model_config = {"from_attributes": True}


class LeaderboardEntryResponse(BaseModel):
    rank: int
    tool_id: str
    tool_slug: str | None = None
    tool_name: str | None = None
    tool_name_jp: str | None = None
    best_score: float
    updated_at: datetime

    model_config = {"from_attributes": True}


class BenchmarkRunRequest(BaseModel):
    tool_id: str
