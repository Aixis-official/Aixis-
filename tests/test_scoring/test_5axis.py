"""Comprehensive tests for the 5-axis scoring model (0.0-5.0 scale)."""

from datetime import datetime

from aixis_agent.core.enums import (
    OverallGrade,
    ScoreAxis,
    ScoreSource,
    Severity,
    TestCategory,
)
from aixis_agent.core.models import AxisScore, RuleResult, TestCase, TestResult
from aixis_agent.scoring.analyzers._base import aggregate_axis_scores
from aixis_agent.scoring.analyzers.cost_performance import score_cost_performance
from aixis_agent.scoring.analyzers.localization import score_localization
from aixis_agent.scoring.analyzers.practicality import score_practicality
from aixis_agent.scoring.analyzers.safety import score_safety
from aixis_agent.scoring.analyzers.uniqueness import score_uniqueness
from aixis_agent.scoring.rules import ScoringRule, make_rule


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_result(
    case_id: str = "test-001",
    category: TestCategory = TestCategory.DIALECT,
    response: str | None = "テストの応答です。日本語で回答しています。",
    error: str | None = None,
    response_time_ms: float = 1500.0,
) -> TestResult:
    return TestResult(
        test_case_id=case_id,
        target_tool="test-tool",
        category=category,
        prompt_sent="テストプロンプト",
        response_raw=response,
        response_time_ms=response_time_ms,
        error=error,
        timestamp=datetime.now(),
    )


def _make_case(
    case_id: str = "test-001",
    category: TestCategory = TestCategory.DIALECT,
) -> TestCase:
    return TestCase(
        id=case_id,
        category=category,
        prompt="テストプロンプト",
        expected_behaviors=["日本語で回答する"],
        failure_indicators=["エラーメッセージを返す"],
    )


def _build_cases_map(cases: list[TestCase]) -> dict[str, TestCase]:
    return {c.id: c for c in cases}


# ---------------------------------------------------------------------------
# Per-axis scorer tests
# ---------------------------------------------------------------------------


class TestPracticalityScorer:
    """Tests for the practicality (実務適性) axis scorer."""

    def test_returns_axis_score_in_range(self):
        results = [
            _make_result("c-001", TestCategory.CONTRADICTORY, "この指示には矛盾があります。確認をお願いします。"),
        ]
        cases = [_make_case("c-001", TestCategory.CONTRADICTORY)]
        score = score_practicality(results, _build_cases_map(cases), {})

        assert isinstance(score, AxisScore)
        assert score.axis == ScoreAxis.PRACTICALITY
        assert 0.0 <= score.score <= 5.0

    def test_source_is_auto(self):
        results = [_make_result("c-001", TestCategory.CONTRADICTORY)]
        cases = [_make_case("c-001", TestCategory.CONTRADICTORY)]
        score = score_practicality(results, _build_cases_map(cases), {})
        assert score.source == ScoreSource.AUTO

    def test_error_results_lower_score(self):
        results = [
            _make_result("c-001", TestCategory.CONTRADICTORY, response=None, error="Timeout"),
        ]
        cases = [_make_case("c-001", TestCategory.CONTRADICTORY)]
        score = score_practicality(results, _build_cases_map(cases), {})
        assert score.score <= 2.5

    def test_empty_results(self):
        score = score_practicality([], {}, {})
        assert score.score == 0.0


class TestLocalizationScorer:
    """Tests for the localization (日本語能力) axis scorer."""

    def test_returns_axis_score_in_range(self):
        results = [
            _make_result("d-001", TestCategory.DIALECT, "方言の意味を理解しました。日本語で丁寧にお答えいたします。"),
        ]
        cases = [_make_case("d-001", TestCategory.DIALECT)]
        score = score_localization(results, _build_cases_map(cases), {})

        assert isinstance(score, AxisScore)
        assert score.axis == ScoreAxis.LOCALIZATION
        assert 0.0 <= score.score <= 5.0

    def test_source_is_auto(self):
        results = [_make_result("d-001", TestCategory.DIALECT)]
        cases = [_make_case("d-001", TestCategory.DIALECT)]
        score = score_localization(results, _build_cases_map(cases), {})
        assert score.source == ScoreSource.AUTO

    def test_confidence_positive_with_results(self):
        results = [_make_result("d-001", TestCategory.DIALECT)]
        cases = [_make_case("d-001", TestCategory.DIALECT)]
        score = score_localization(results, _build_cases_map(cases), {})
        assert score.confidence > 0

    def test_empty_results(self):
        score = score_localization([], {}, {})
        assert score.score == 0.0


class TestSafetyScorer:
    """Tests for the safety (信頼性・安全性) axis scorer."""

    def test_returns_axis_score_in_range(self):
        results = [
            _make_result("s-001", TestCategory.DIALECT, "安全な応答です。"),
        ]
        cases = [_make_case("s-001", TestCategory.DIALECT)]
        score = score_safety(results, _build_cases_map(cases), {})

        assert isinstance(score, AxisScore)
        assert score.axis == ScoreAxis.SAFETY
        assert 0.0 <= score.score <= 5.0

    def test_source_is_auto(self):
        results = [_make_result("s-001", TestCategory.DIALECT)]
        cases = [_make_case("s-001", TestCategory.DIALECT)]
        score = score_safety(results, _build_cases_map(cases), {})
        assert score.source == ScoreSource.AUTO

    def test_errors_reduce_safety_score(self):
        results = [
            _make_result("s-001", TestCategory.DIALECT, response=None, error="Timeout"),
            _make_result("s-002", TestCategory.DIALECT, response=None, error="Connection refused"),
        ]
        cases = [
            _make_case("s-001", TestCategory.DIALECT),
            _make_case("s-002", TestCategory.DIALECT),
        ]
        score = score_safety(results, _build_cases_map(cases), {})
        assert score.score < 2.5

    def test_fast_responses_score_higher(self):
        fast_results = [
            _make_result("s-001", TestCategory.DIALECT, response_time_ms=500.0),
        ]
        slow_results = [
            _make_result("s-001", TestCategory.DIALECT, response_time_ms=50000.0),
        ]
        cases = [_make_case("s-001", TestCategory.DIALECT)]
        cases_map = _build_cases_map(cases)

        fast_score = score_safety(fast_results, cases_map, {})
        slow_score = score_safety(slow_results, cases_map, {})
        assert fast_score.score >= slow_score.score

    def test_empty_results(self):
        score = score_safety([], {}, {})
        assert score.score == 0.0


class TestCostPerformanceScorer:
    """Tests for the cost_performance (費用対効果) axis scorer -- manual only."""

    def test_returns_axis_score_in_range(self):
        results = [_make_result("cp-001", TestCategory.DIALECT)]
        cases = [_make_case("cp-001", TestCategory.DIALECT)]
        score = score_cost_performance(results, _build_cases_map(cases), {})

        assert isinstance(score, AxisScore)
        assert score.axis == ScoreAxis.COST_PERFORMANCE
        assert 0.0 <= score.score <= 5.0

    def test_confidence_is_zero(self):
        """Cost performance is manual-only, so auto confidence must be 0."""
        results = [_make_result("cp-001", TestCategory.DIALECT)]
        cases = [_make_case("cp-001", TestCategory.DIALECT)]
        score = score_cost_performance(results, _build_cases_map(cases), {})
        assert score.confidence == 0.0

    def test_source_is_manual(self):
        score = score_cost_performance([], {}, {})
        assert score.source == ScoreSource.MANUAL

    def test_score_is_zero_without_manual_input(self):
        score = score_cost_performance([], {}, {})
        assert score.score == 0.0


class TestUniquenessScorer:
    """Tests for the uniqueness (革新性) axis scorer -- manual only."""

    def test_returns_axis_score_in_range(self):
        results = [_make_result("u-001", TestCategory.DIALECT)]
        cases = [_make_case("u-001", TestCategory.DIALECT)]
        score = score_uniqueness(results, _build_cases_map(cases), {})

        assert isinstance(score, AxisScore)
        assert score.axis == ScoreAxis.UNIQUENESS
        assert 0.0 <= score.score <= 5.0

    def test_confidence_is_zero(self):
        """Uniqueness is manual-only, so auto confidence must be 0."""
        results = [_make_result("u-001", TestCategory.DIALECT)]
        cases = [_make_case("u-001", TestCategory.DIALECT)]
        score = score_uniqueness(results, _build_cases_map(cases), {})
        assert score.confidence == 0.0

    def test_source_is_manual(self):
        score = score_uniqueness([], {}, {})
        assert score.source == ScoreSource.MANUAL

    def test_score_is_zero_without_manual_input(self):
        score = score_uniqueness([], {}, {})
        assert score.score == 0.0


# ---------------------------------------------------------------------------
# aggregate_axis_scores helper tests
# ---------------------------------------------------------------------------


def _dummy_eval_pass(result: TestResult, case: TestCase | None) -> RuleResult:
    return RuleResult(
        rule_id="DUMMY-PASS",
        rule_name_jp="ダミールール（合格）",
        passed=True,
        score=1.0,
        evidence="テスト用",
        severity=Severity.MEDIUM,
    )


def _dummy_eval_fail(result: TestResult, case: TestCase | None) -> RuleResult:
    return RuleResult(
        rule_id="DUMMY-FAIL",
        rule_name_jp="ダミールール（不合格）",
        passed=False,
        score=0.0,
        evidence="テスト用",
        severity=Severity.MEDIUM,
    )


def _dummy_eval_half(result: TestResult, case: TestCase | None) -> RuleResult:
    return RuleResult(
        rule_id="DUMMY-HALF",
        rule_name_jp="ダミールール（半分）",
        passed=True,
        score=0.5,
        evidence="テスト用",
        severity=Severity.MEDIUM,
    )


class TestAggregateAxisScores:
    """Tests for the aggregate_axis_scores helper in _base.py."""

    def test_all_pass_gives_max_score(self):
        rules = [
            make_rule("DUMMY-PASS", "合格", 1.0, Severity.MEDIUM, None, _dummy_eval_pass),
        ]
        results = [_make_result("t-001")]
        cases = [_make_case("t-001")]

        axis = aggregate_axis_scores(
            ScoreAxis.SAFETY, rules, results, _build_cases_map(cases),
        )
        assert axis.score == 5.0

    def test_all_fail_gives_zero(self):
        rules = [
            make_rule("DUMMY-FAIL", "不合格", 1.0, Severity.MEDIUM, None, _dummy_eval_fail),
        ]
        results = [_make_result("t-001")]
        cases = [_make_case("t-001")]

        axis = aggregate_axis_scores(
            ScoreAxis.SAFETY, rules, results, _build_cases_map(cases),
        )
        assert axis.score == 0.0

    def test_half_score_gives_2_5(self):
        rules = [
            make_rule("DUMMY-HALF", "半分", 1.0, Severity.MEDIUM, None, _dummy_eval_half),
        ]
        results = [_make_result("t-001")]
        cases = [_make_case("t-001")]

        axis = aggregate_axis_scores(
            ScoreAxis.SAFETY, rules, results, _build_cases_map(cases),
        )
        assert axis.score == 2.5

    def test_score_range_0_to_5(self):
        rules = [
            make_rule("DUMMY-PASS", "合格", 1.0, Severity.MEDIUM, None, _dummy_eval_pass),
            make_rule("DUMMY-FAIL", "不合格", 1.0, Severity.MEDIUM, None, _dummy_eval_fail),
        ]
        results = [_make_result("t-001")]
        cases = [_make_case("t-001")]

        axis = aggregate_axis_scores(
            ScoreAxis.SAFETY, rules, results, _build_cases_map(cases),
        )
        assert 0.0 <= axis.score <= 5.0

    def test_weighted_aggregation(self):
        rules = [
            make_rule("DUMMY-PASS", "合格", 3.0, Severity.MEDIUM, None, _dummy_eval_pass),
            make_rule("DUMMY-FAIL", "不合格", 1.0, Severity.MEDIUM, None, _dummy_eval_fail),
        ]
        results = [_make_result("t-001")]
        cases = [_make_case("t-001")]

        axis = aggregate_axis_scores(
            ScoreAxis.SAFETY, rules, results, _build_cases_map(cases),
        )
        # Weighted: (1.0*3 + 0.0*1) / (3+1) = 0.75 -> 0.75*5.0 = 3.75 -> rounded to 3.8
        assert 3.5 <= axis.score <= 4.0

    def test_no_results_gives_zero(self):
        rules = [
            make_rule("DUMMY-PASS", "合格", 1.0, Severity.MEDIUM, None, _dummy_eval_pass),
        ]
        axis = aggregate_axis_scores(ScoreAxis.SAFETY, rules, [], {})
        assert axis.score == 0.0

    def test_no_rules_gives_zero(self):
        results = [_make_result("t-001")]
        cases = [_make_case("t-001")]
        axis = aggregate_axis_scores(
            ScoreAxis.SAFETY, [], results, _build_cases_map(cases),
        )
        assert axis.score == 0.0

    def test_confidence_scales_with_evaluations(self):
        rules = [
            make_rule("DUMMY-PASS", "合格", 1.0, Severity.MEDIUM, None, _dummy_eval_pass),
        ]
        results = [_make_result("t-001")]
        cases = [_make_case("t-001")]

        axis = aggregate_axis_scores(
            ScoreAxis.SAFETY, rules, results, _build_cases_map(cases),
        )
        assert 0.0 <= axis.confidence <= 1.0

    def test_details_populated(self):
        rules = [
            make_rule("DUMMY-PASS", "合格", 1.0, Severity.MEDIUM, None, _dummy_eval_pass),
        ]
        results = [_make_result("t-001")]
        cases = [_make_case("t-001")]

        axis = aggregate_axis_scores(
            ScoreAxis.SAFETY, rules, results, _build_cases_map(cases),
        )
        assert len(axis.details) == 1
        assert axis.details[0].rule_id == "DUMMY-PASS"

    def test_category_filtering(self):
        """Rules with specific categories should only apply to matching results."""
        rules = [
            make_rule(
                "DUMMY-PASS", "合格", 1.0, Severity.MEDIUM,
                [TestCategory.UNICODE_EDGE], _dummy_eval_pass,
            ),
        ]
        # Result is DIALECT, rule only applies to UNICODE_EDGE
        results = [_make_result("t-001", TestCategory.DIALECT)]
        cases = [_make_case("t-001", TestCategory.DIALECT)]

        axis = aggregate_axis_scores(
            ScoreAxis.SAFETY, rules, results, _build_cases_map(cases),
        )
        # Rule was not applicable, so no evaluations happened -> score 0
        assert axis.score == 0.0


# ---------------------------------------------------------------------------
# Grade mapping on 0-5.0 scale
# ---------------------------------------------------------------------------


class TestGradeMapping:
    """Tests for OverallGrade.from_score on the 0.0-5.0 scale."""

    def test_s_grade_at_threshold(self):
        assert OverallGrade.from_score(4.5) == OverallGrade.S

    def test_s_grade_above_threshold(self):
        assert OverallGrade.from_score(5.0) == OverallGrade.S

    def test_a_grade_at_threshold(self):
        assert OverallGrade.from_score(3.5) == OverallGrade.A

    def test_a_grade_mid_range(self):
        assert OverallGrade.from_score(4.0) == OverallGrade.A

    def test_a_grade_just_below_s(self):
        assert OverallGrade.from_score(4.4) == OverallGrade.A

    def test_b_grade_at_threshold(self):
        assert OverallGrade.from_score(2.5) == OverallGrade.B

    def test_b_grade_mid_range(self):
        assert OverallGrade.from_score(3.0) == OverallGrade.B

    def test_c_grade_at_threshold(self):
        assert OverallGrade.from_score(1.5) == OverallGrade.C

    def test_c_grade_mid_range(self):
        assert OverallGrade.from_score(2.0) == OverallGrade.C

    def test_d_grade_below_threshold(self):
        assert OverallGrade.from_score(1.4) == OverallGrade.D

    def test_d_grade_zero(self):
        assert OverallGrade.from_score(0.0) == OverallGrade.D

    def test_d_grade_very_low(self):
        assert OverallGrade.from_score(0.5) == OverallGrade.D


# ---------------------------------------------------------------------------
# Score axis enum completeness
# ---------------------------------------------------------------------------


class TestScoreAxisEnum:
    """Ensure all 5 axes exist in the enum."""

    def test_has_five_axes(self):
        assert len(ScoreAxis) == 5

    def test_axis_names(self):
        expected = {"practicality", "cost_performance", "localization", "safety", "uniqueness"}
        assert {a.value for a in ScoreAxis} == expected

    def test_each_axis_has_jp_name(self):
        for axis in ScoreAxis:
            assert axis.name_jp, f"{axis.value} missing name_jp"

    def test_each_axis_has_en_name(self):
        for axis in ScoreAxis:
            assert axis.name_en, f"{axis.value} missing name_en"
