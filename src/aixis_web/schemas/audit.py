"""Audit session schemas."""
from datetime import datetime
from typing import Any

from pydantic import BaseModel


class AuditCreate(BaseModel):
    tool_id: str
    profile_id: str


class AuditStartRequest(BaseModel):
    """Request to start an audit run against a registered tool."""
    tool_id: str
    profile_id: str = ""
    categories: list[str] | None = None
    target_config_name: str | None = None  # e.g. "gamma"


class AuditStartResponse(BaseModel):
    session_id: str
    db_session_id: str
    status: str
    message: str


class AuditProgressResponse(BaseModel):
    """Real-time progress info for a running audit."""
    session_id: str
    db_session_id: str
    status: str  # starting|running|scoring|saving|completed|failed
    phase: str
    tool_name: str
    error: str | None = None
    started_at: str | None = None
    # DB-side info
    total_planned: int = 0
    total_executed: int = 0
    db_status: str = ""
    # Real-time progress from in-memory tracking
    completed: int = 0
    total: int = 0
    current_category: str = ""


class ManualScoreItem(BaseModel):
    checklist_item_id: str
    item_name_jp: str
    axis: str
    passed: bool | None = None
    score: float | None = None
    weight: float = 1.0
    evidence: str | None = None
    evidence_url: str | None = None


class ManualScoreSubmit(BaseModel):
    items: list[ManualScoreItem]


class AuditResponse(BaseModel):
    id: str
    session_code: str
    tool_id: str
    profile_id: str | None = None
    status: str
    total_planned: int = 0
    total_executed: int = 0
    error_message: str | None = None
    initiated_by: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime | None = None
    tool_name: str | None = None

    model_config = {"from_attributes": True}


class VolumeMetrics(BaseModel):
    """AI agent volume metrics for an audit session."""
    executor_type: str = "extension"
    ai_total_steps: int | None = 0
    ai_total_api_calls: int | None = 0
    ai_total_input_tokens: int | None = 0
    ai_total_output_tokens: int | None = 0
    ai_estimated_cost_usd: float | None = 0.0
    ai_screenshots_captured: int | None = 0
    completeness_ratio: int | None = 0


class AuditDetailResponse(AuditResponse):
    """Extended response with test results and scores."""
    test_results: list[dict[str, Any]] = []
    axis_scores: list[dict[str, Any]] = []
    tool_name: str | None = None
    volume_metrics: VolumeMetrics | None = None
    reliability_scores: dict[str, Any] | None = None
    score_diff: dict[str, Any] | None = None
    published_overall_score: float | None = None
    published_overall_grade: str | None = None
    score_breakdown: dict[str, Any] | None = None  # per-axis auto/manual/final breakdown
    is_published: bool = False  # whether scores have been published to public DB


class AuditListResponse(BaseModel):
    items: list[AuditResponse]
    total: int
