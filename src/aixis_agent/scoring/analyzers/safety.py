"""Axis 4: Safety & Reliability (信頼性・安全性) analyzer.

Refactored from reliability.py for the 5-axis model.
All scores on 0.0-5.0 scale via aggregate_axis_scores helper.
"""

from ...core.enums import ScoreAxis, ScoreSource, Severity, TestCategory
from ...core.models import AxisScore, RuleResult, TestCase, TestResult
from ...utils.japanese import contains_mojibake, is_meaningful_japanese
from ..rules import ScoringRule, make_rule
from ._base import aggregate_axis_scores


# ---------------------------------------------------------------------------
# Rules (preserved exactly from reliability.py)
# ---------------------------------------------------------------------------


def _eval_no_crash(result: TestResult, case: TestCase | None) -> RuleResult:
    """Did the tool return a response without crashing?"""
    passed = result.error is None and result.response_raw is not None
    return RuleResult(
        rule_id="REL-NO-CRASH",
        rule_name_jp="クラッシュなし",
        passed=passed,
        score=1.0 if passed else 0.0,
        evidence="正常応答" if passed else f"エラー: {result.error or '応答なし'}",
        severity=Severity.CRITICAL,
    )


def _eval_response_time(result: TestResult, case: TestCase | None) -> RuleResult:
    """Was the response within acceptable time?"""
    threshold_ms = 30000  # 30 seconds
    fast_threshold_ms = 5000  # 5 seconds

    if result.response_time_ms <= fast_threshold_ms:
        score = 1.0
        evidence = f"高速応答 ({result.response_time_ms:.0f}ms)"
    elif result.response_time_ms <= threshold_ms:
        score = 0.7
        evidence = f"許容範囲 ({result.response_time_ms:.0f}ms)"
    else:
        score = max(0.0, 1.0 - (result.response_time_ms - threshold_ms) / threshold_ms)
        evidence = f"低速応答 ({result.response_time_ms:.0f}ms)"

    return RuleResult(
        rule_id="REL-RESPONSE-TIME",
        rule_name_jp="応答時間",
        passed=result.response_time_ms <= threshold_ms,
        score=score,
        evidence=evidence,
        severity=Severity.MEDIUM,
    )


def _eval_long_input_stability(result: TestResult, case: TestCase | None) -> RuleResult:
    """Does the tool handle long inputs gracefully?"""
    if result.error:
        return RuleResult(
            rule_id="REL-LONG-INPUT-STABILITY",
            rule_name_jp="長文入力安定性",
            passed=False,
            score=0.0,
            evidence=f"エラー: {result.error}",
            severity=Severity.HIGH,
        )

    if not result.response_raw:
        return RuleResult(
            rule_id="REL-LONG-INPUT-STABILITY",
            rule_name_jp="長文入力安定性",
            passed=False,
            score=0.1,
            evidence="応答なし",
            severity=Severity.HIGH,
        )

    # Check response is meaningful
    meaningful = is_meaningful_japanese(result.response_raw, min_jp_ratio=0.05)
    truncated = len(result.response_raw) < 10

    score = 1.0
    evidence = "正常処理"

    if not meaningful:
        score = 0.3
        evidence = "応答が意味のある日本語でない"
    elif truncated:
        score = 0.5
        evidence = "応答が極端に短い"

    return RuleResult(
        rule_id="REL-LONG-INPUT-STABILITY",
        rule_name_jp="長文入力安定性",
        passed=score >= 0.5,
        score=score,
        evidence=evidence,
        severity=Severity.HIGH,
    )


def _eval_unicode_handling(result: TestResult, case: TestCase | None) -> RuleResult:
    """Does the tool handle special Unicode characters?"""
    if result.error or not result.response_raw:
        return RuleResult(
            rule_id="REL-UNICODE-HANDLING",
            rule_name_jp="Unicode処理",
            passed=False,
            score=0.0,
            evidence="応答なし/エラー",
            severity=Severity.HIGH,
        )

    has_mojibake = contains_mojibake(result.response_raw)
    meaningful = is_meaningful_japanese(result.response_raw, min_jp_ratio=0.05)

    score = 1.0
    issues = []
    if has_mojibake:
        score -= 0.5
        issues.append("文字化け検出")
    if not meaningful:
        score -= 0.3
        issues.append("意味のある応答なし")

    score = max(0.0, score)
    evidence = "正常処理" if not issues else "、".join(issues)

    return RuleResult(
        rule_id="REL-UNICODE-HANDLING",
        rule_name_jp="Unicode処理",
        passed=score >= 0.5,
        score=score,
        evidence=evidence,
        severity=Severity.HIGH,
    )


# ---------------------------------------------------------------------------
# Rule registry
# ---------------------------------------------------------------------------

SAFETY_RULES: list[ScoringRule] = [
    make_rule(
        "REL-NO-CRASH", "クラッシュなし", 4.0, Severity.CRITICAL,
        None, _eval_no_crash,  # All categories
    ),
    make_rule(
        "REL-RESPONSE-TIME", "応答時間", 2.0, Severity.MEDIUM,
        None, _eval_response_time,
    ),
    make_rule(
        "REL-LONG-INPUT-STABILITY", "長文入力安定性", 3.0, Severity.HIGH,
        [TestCategory.LONG_INPUT], _eval_long_input_stability,
    ),
    make_rule(
        "REL-UNICODE-HANDLING", "Unicode処理", 3.0, Severity.HIGH,
        [TestCategory.UNICODE_EDGE], _eval_unicode_handling,
    ),
]


# ---------------------------------------------------------------------------
# Public scorer
# ---------------------------------------------------------------------------


def score_safety(
    results: list[TestResult],
    cases_map: dict[str, TestCase],
    rules_config: dict,
) -> AxisScore:
    """Score all results on the Safety & Reliability (信頼性・安全性) axis (0.0-5.0)."""
    return aggregate_axis_scores(
        axis=ScoreAxis.SAFETY,
        rules=SAFETY_RULES,
        results=results,
        cases_map=cases_map,
        source=ScoreSource.AUTO,
    )
