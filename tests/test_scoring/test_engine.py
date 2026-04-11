"""Tests for the scoring engine."""

from datetime import datetime

from aixis_agent.core.enums import OverallGrade, ScoreAxis, TestCategory
from aixis_agent.core.models import TestCase, TestResult
from aixis_agent.scoring.engine import ScoringEngine


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


def test_scoring_engine_basic():
    engine = ScoringEngine()

    results = [
        _make_result("d-001", TestCategory.DIALECT),
        _make_result("d-002", TestCategory.DIALECT),
        _make_result("u-001", TestCategory.UNICODE_EDGE, "テスト応答。絵文字も🎉正常です。"),
    ]
    cases = [
        _make_case("d-001", TestCategory.DIALECT),
        _make_case("d-002", TestCategory.DIALECT),
        _make_case("u-001", TestCategory.UNICODE_EDGE),
    ]

    report = engine.score_all(results, cases, "test-tool")

    assert report.total_tests == 3
    assert report.overall_score >= 0
    assert report.overall_score <= 5.0
    assert report.overall_grade in list(OverallGrade)
    assert len(report.axis_scores) == 5


def test_scoring_with_errors():
    engine = ScoringEngine()

    results = [
        _make_result("e-001", TestCategory.DIALECT, response=None, error="Timeout"),
        _make_result("e-002", TestCategory.DIALECT, response=None, error="Connection refused"),
    ]
    cases = [
        _make_case("e-001", TestCategory.DIALECT),
        _make_case("e-002", TestCategory.DIALECT),
    ]

    report = engine.score_all(results, cases, "test-tool")
    assert report.total_errors == 2
    # Score should be low when all tests fail
    safety_axis = next(
        a for a in report.axis_scores if a.axis == ScoreAxis.SAFETY
    )
    assert safety_axis.score < 2.5


def test_overall_grade_mapping():
    # Canonical thresholds per the methodology whitepaper:
    # S >= 4.5, A >= 3.8, B >= 3.0, C >= 2.0, D < 2.0
    assert OverallGrade.from_score(4.5) == OverallGrade.S
    assert OverallGrade.from_score(3.8) == OverallGrade.A
    assert OverallGrade.from_score(3.0) == OverallGrade.B
    assert OverallGrade.from_score(2.0) == OverallGrade.C
    assert OverallGrade.from_score(1.9) == OverallGrade.D
