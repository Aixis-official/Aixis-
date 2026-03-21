"""Pydantic domain models for the Aixis AI audit platform."""

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from .enums import (
    AuditStatus,
    OverallGrade,
    ReportType,
    ScoreAxis,
    ScoreSource,
    Severity,
    TestCategory,
)


# --- Test Pattern Models ---


class TestCase(BaseModel):
    id: str
    category: TestCategory
    prompt: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    expected_behaviors: list[str] = Field(default_factory=list)
    failure_indicators: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


# --- Execution Models ---


class TestResult(BaseModel):
    test_case_id: str
    target_tool: str
    category: TestCategory
    prompt_sent: str
    response_raw: str | None = None
    response_time_ms: float = 0.0
    error: str | None = None
    screenshot_path: str | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = Field(default_factory=dict)


# --- Scoring Models (5-axis, 0.0-5.0 scale) ---


class RuleResult(BaseModel):
    rule_id: str
    rule_name_jp: str
    passed: bool
    score: float = Field(ge=0.0, le=1.0)
    evidence: str = ""
    severity: Severity = Severity.MEDIUM


class ScoreDetail(BaseModel):
    rule_id: str
    rule_name_jp: str
    score: float
    weight: float
    evidence: str
    severity: Severity
    test_case_ids: list[str] = Field(default_factory=list)


class AxisScore(BaseModel):
    """Score for a single axis on 0.0-5.0 scale."""
    axis: ScoreAxis
    axis_name_jp: str
    score: float = Field(ge=0.0, le=5.0)
    confidence: float = Field(ge=0.0, le=1.0)
    source: ScoreSource = ScoreSource.AUTO
    details: list[ScoreDetail] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)


class CategoryResult(BaseModel):
    category: TestCategory
    category_name_jp: str
    total_tests: int
    passed_tests: int
    failed_tests: int
    error_tests: int
    pass_rate: float
    avg_response_time_ms: float
    notable_findings: list[str] = Field(default_factory=list)


# --- Manual Checklist Models ---


class ChecklistItem(BaseModel):
    """A single item in a manual evaluation checklist."""
    id: str
    name_jp: str
    name_en: str = ""
    weight: float = 1.0
    scoring_guide: str = ""
    category: str = ""


class ChecklistEntry(BaseModel):
    """An analyst's evaluation of a single checklist item."""
    item_id: str
    item_name_jp: str
    score: float = Field(ge=0.0, le=5.0)
    weight: float = 1.0
    evidence: str = ""
    evidence_url: str = ""
    evaluated_by: str = ""
    evaluated_at: datetime = Field(default_factory=datetime.now)


class ManualAxisScore(BaseModel):
    """Manual evaluation score for one axis, derived from checklist entries."""
    axis: ScoreAxis
    entries: list[ChecklistEntry] = Field(default_factory=list)
    score: float = Field(ge=0.0, le=5.0, default=0.0)

    def calculate_score(self) -> float:
        """Weighted average of checklist entries."""
        if not self.entries:
            return 0.0
        total_weight = sum(e.weight for e in self.entries)
        if total_weight == 0:
            return 0.0
        weighted_sum = sum(e.score * e.weight for e in self.entries)
        self.score = min(5.0, weighted_sum / total_weight)
        return self.score


# --- Report Models ---


class AuditReport(BaseModel):
    report_id: str
    report_type: ReportType = ReportType.INDIVIDUAL
    target_tool: str
    generated_at: datetime = Field(default_factory=datetime.now)
    total_tests: int
    total_passed: int
    total_failed: int
    total_errors: int
    axis_scores: list[AxisScore] = Field(default_factory=list)
    overall_score: float = Field(ge=0.0, le=5.0, default=0.0)
    overall_grade: OverallGrade = OverallGrade.D
    executive_summary_jp: str = ""
    executive_summary_en: str = ""
    category_breakdowns: dict[str, CategoryResult] = Field(default_factory=dict)
    raw_results: list[TestResult] = Field(default_factory=list)
    test_metadata: dict[str, Any] = Field(default_factory=dict)


class ComparisonReport(BaseModel):
    """Side-by-side comparison of multiple tools in the same category."""
    report_id: str
    report_type: ReportType = ReportType.COMPARISON
    category_name_jp: str
    generated_at: datetime = Field(default_factory=datetime.now)
    tools: list[str] = Field(default_factory=list)
    tool_scores: dict[str, dict[str, float]] = Field(default_factory=dict)
    rankings: dict[str, list[str]] = Field(default_factory=dict)
    summary_jp: str = ""


# --- Tool Catalog Models ---


class ToolInfo(BaseModel):
    """AI tool catalog entry."""
    slug: str
    name: str
    name_jp: str
    vendor: str = ""
    url: str = ""
    description: str = ""
    description_jp: str = ""
    category_slug: str = ""
    category_name_jp: str = ""
    profile_id: str = ""
    pricing_model: str = ""
    price_min_jpy: int | None = None
    price_max_jpy: int | None = None
    logo_url: str = ""
    features: list[str] = Field(default_factory=list)
    is_public: bool = False
    is_active: bool = True


class ToolScore(BaseModel):
    """Published 5-axis scores for a tool."""
    tool_slug: str
    practicality: float = Field(ge=0.0, le=5.0, default=0.0)
    cost_performance: float = Field(ge=0.0, le=5.0, default=0.0)
    localization: float = Field(ge=0.0, le=5.0, default=0.0)
    safety: float = Field(ge=0.0, le=5.0, default=0.0)
    uniqueness: float = Field(ge=0.0, le=5.0, default=0.0)
    overall_score: float = Field(ge=0.0, le=5.0, default=0.0)
    overall_grade: OverallGrade = OverallGrade.D
    version: int = 1
    published_at: datetime = Field(default_factory=datetime.now)


# --- Session Models ---


class SessionInfo(BaseModel):
    session_id: str
    target_tool: str
    profile_id: str = ""
    started_at: datetime = Field(default_factory=datetime.now)
    completed_at: datetime | None = None
    total_planned: int = 0
    total_executed: int = 0
    status: AuditStatus = AuditStatus.PENDING
    db_path: str = ""
