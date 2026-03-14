"""Pydantic domain models for the Aixis AI audit platform."""

from datetime import datetime
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


class TargetConfig(BaseModel):
    name: str
    url: str
    executor_type: str = "playwright"  # "playwright" or "ai_browser"
    # CSS selectors — for simple input→submit→response flows
    input_selector: str | None = None
    submit_selector: str | None = None
    response_selector: str | None = None
    wait_for_response_timeout_ms: int = 30000
    login_steps: list[dict[str, str]] = Field(default_factory=list)
    pre_prompt_actions: list[dict[str, str]] = Field(default_factory=list)
    post_submit_actions: list[dict[str, str]] = Field(default_factory=list)
    inter_prompt_delay_ms: int = 1000
    headless: bool = True
    locale: str = "ja-JP"
    input_method: str = "fill"  # "fill" (standard) or "type" (contenteditable)
    wait_for_manual_login: bool = False  # True: pause after goto for manual login
    # Workflow-driven execution: list of step dicts
    # When defined, overrides the simple input→submit→response flow.
    # Each step: {"action": "...", "selector": "...", "value": "...", "timeout": "..."}
    # Supported actions:
    #   click        — click element by selector
    #   click_text   — click button by visible text (value)
    #   fill         — fill input by selector with value ({prompt} placeholder supported)
    #   type         — type char-by-char into selector ({prompt} placeholder supported)
    #   clear_type   — click, select all, delete, then type value ({prompt} placeholder)
    #   wait         — wait for selector to be visible
    #   wait_ms      — wait fixed milliseconds (value)
    #   wait_hidden  — wait for selector to disappear (e.g. loading spinner)
    #   scroll_down  — scroll page down
    #   extract      — extract text from selector as the response (terminal step)
    #   goto         — navigate to URL (value), supports {url} placeholder for target URL
    #   press        — press key (value) on selector
    #   screenshot   — take screenshot with label (value)
    workflow_steps: list[dict[str, str]] = Field(default_factory=list)
    # Reset steps: run after each test to return to initial state for next test
    reset_steps: list[dict[str, str]] = Field(default_factory=list)
    # AI browser agent fields
    tool_description: str = ""         # What this tool does (for AI agent context)
    tool_workflow_hint: str = ""       # Expected workflow hint for the AI agent
    ai_budget_max_calls: int = 200     # Max Claude API calls per audit
    ai_budget_max_calls_per_case: int = 15  # Max steps per test case
    ai_budget_max_cost_jpy: int = 20  # Cost cap per audit in JPY (0=unlimited)


class ExecutionResult(BaseModel):
    text: str | None = None
    error: str | None = None
    response_time_ms: float = 0.0
    screenshot_path: str | None = None
    page_url: str | None = None
    # AI browser agent metrics
    ai_steps_taken: int = 0
    ai_calls_used: int = 0
    ai_tokens_input: int = 0
    ai_tokens_output: int = 0


class BudgetTracker(BaseModel):
    """Tracks API call budget for an AI-driven audit.

    Enforces both call-count and cost limits. Whichever is hit first
    causes is_exhausted to return True, halting further API usage.
    """
    max_calls_total: int = 200
    max_calls_per_case: int = 15
    max_cost_usd: float = 0.0  # 0 = unlimited; >0 = hard cost cap
    calls_used: int = 0
    tokens_input: int = 0
    tokens_output: int = 0
    estimated_cost_usd: float = 0.0

    @property
    def budget_remaining(self) -> int:
        return max(0, self.max_calls_total - self.calls_used)

    @property
    def is_exhausted(self) -> bool:
        if self.calls_used >= self.max_calls_total:
            return True
        if self.max_cost_usd > 0 and self.estimated_cost_usd >= self.max_cost_usd:
            return True
        return False

    @property
    def exhaustion_reason(self) -> str:
        """Human-readable reason why the budget is exhausted."""
        if self.max_cost_usd > 0 and self.estimated_cost_usd >= self.max_cost_usd:
            return f"コスト上限 ${self.max_cost_usd:.3f} (≈{self.max_cost_usd * 150:.0f}円) に到達"
        if self.calls_used >= self.max_calls_total:
            return f"API呼び出し上限 {self.max_calls_total}回 に到達"
        return ""

    def record_call(self, input_tokens: int, output_tokens: int) -> None:
        self.calls_used += 1
        self.tokens_input += input_tokens
        self.tokens_output += output_tokens
        # Claude Haiku 4.5 pricing: $1/M input, $5/M output
        self.estimated_cost_usd += (input_tokens * 1.0 + output_tokens * 5.0) / 1_000_000


class AgentAction(BaseModel):
    """A single action the AI browser agent decides to take."""
    action: str  # click | type | key | scroll | wait | complete | fail
    x: int | None = None             # For click
    y: int | None = None             # For click
    text: str | None = None          # For type, key, complete, fail
    direction: str | None = None     # For scroll: up/down/left/right
    seconds: float | None = None     # For wait
    reasoning: str = ""              # Agent's explanation


class TestResult(BaseModel):
    test_case_id: str
    target_tool: str
    category: TestCategory
    prompt_sent: str
    response_raw: str | None = None
    response_time_ms: float = 0.0
    error: str | None = None
    screenshot_path: str | None = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)
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
