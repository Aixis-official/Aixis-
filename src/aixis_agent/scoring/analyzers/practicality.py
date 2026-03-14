"""Axis 1: Practicality / Practical Aptitude (実務適性) analyzer.

Refactored from practical.py for the 5-axis model.
All scores on 0.0-5.0 scale via aggregate_axis_scores helper.
"""

from ...core.enums import ScoreAxis, ScoreSource, Severity, TestCategory
from ...core.models import AxisScore, RuleResult, TestCase, TestResult
from ...utils.japanese import (
    count_addressed_steps,
    detect_contradiction_acknowledgment,
    is_meaningful_japanese,
)
from ..rules import ScoringRule, make_rule
from ._base import aggregate_axis_scores


# ---------------------------------------------------------------------------
# Rules (preserved exactly from practical.py)
# ---------------------------------------------------------------------------


def _eval_contradiction_detect(result: TestResult, case: TestCase | None) -> RuleResult:
    """Does the tool notice contradictions in the input?"""
    if result.error or not result.response_raw:
        return RuleResult(
            rule_id="PRAC-CONTRADICTION-DETECT",
            rule_name_jp="矛盾検出",
            passed=False,
            score=0.0,
            evidence="応答なし/エラー",
            severity=Severity.HIGH,
        )

    detected = detect_contradiction_acknowledgment(result.response_raw)
    return RuleResult(
        rule_id="PRAC-CONTRADICTION-DETECT",
        rule_name_jp="矛盾検出",
        passed=detected,
        score=1.0 if detected else 0.2,
        evidence="矛盾を認識" if detected else "矛盾への言及なし",
        severity=Severity.HIGH,
    )


def _eval_multi_step_complete(result: TestResult, case: TestCase | None) -> RuleResult:
    """Were all steps of a complex instruction addressed?"""
    if result.error or not result.response_raw:
        return RuleResult(
            rule_id="PRAC-MULTI-STEP-COMPLETE",
            rule_name_jp="複数ステップ完遂",
            passed=False,
            score=0.0,
            evidence="応答なし/エラー",
            severity=Severity.HIGH,
        )

    # Estimate expected steps from prompt
    expected = max(2, result.prompt_sent.count("。") // 2)
    if case and case.metadata.get("expected_steps"):
        expected = case.metadata["expected_steps"]

    addressed = count_addressed_steps(result.response_raw, expected)
    ratio = min(1.0, addressed / max(1, expected))

    return RuleResult(
        rule_id="PRAC-MULTI-STEP-COMPLETE",
        rule_name_jp="複数ステップ完遂",
        passed=ratio >= 0.7,
        score=ratio,
        evidence=f"{addressed}/{expected} ステップ対応",
        severity=Severity.HIGH,
    )


def _eval_ambiguity_clarify(result: TestResult, case: TestCase | None) -> RuleResult:
    """Does the tool ask for clarification on ambiguous input?"""
    if result.error or not result.response_raw:
        return RuleResult(
            rule_id="PRAC-AMBIGUITY-CLARIFY",
            rule_name_jp="曖昧さ確認",
            passed=False,
            score=0.0,
            evidence="応答なし/エラー",
            severity=Severity.MEDIUM,
        )

    response = result.response_raw
    clarification_indicators = [
        "？", "でしょうか", "ですか", "ご確認", "明確に",
        "具体的に", "どのような", "どういった", "お聞きしたい",
        "解釈", "複数の可能性", "前提として",
    ]
    asked_clarification = any(ind in response for ind in clarification_indicators)

    # Also good: the tool makes a reasonable assumption and proceeds
    assumption_indicators = [
        "と仮定して", "と解釈して", "と想定して",
        "前提として", "以下のように理解して",
    ]
    made_assumption = any(ind in response for ind in assumption_indicators)

    if asked_clarification:
        score = 1.0
        evidence = "明確化を求めた"
    elif made_assumption:
        score = 0.8
        evidence = "前提を明示した上で回答"
    elif is_meaningful_japanese(response):
        score = 0.5
        evidence = "回答したが曖昧さへの言及なし"
    else:
        score = 0.2
        evidence = "意味のある応答なし"

    return RuleResult(
        rule_id="PRAC-AMBIGUITY-CLARIFY",
        rule_name_jp="曖昧さ確認",
        passed=score >= 0.5,
        score=score,
        evidence=evidence,
        severity=Severity.MEDIUM,
    )


def _eval_broken_input_recover(result: TestResult, case: TestCase | None) -> RuleResult:
    """Does the tool handle broken grammar gracefully?"""
    if result.error or not result.response_raw:
        return RuleResult(
            rule_id="PRAC-BROKEN-INPUT-RECOVER",
            rule_name_jp="文法破壊復帰",
            passed=False,
            score=0.0,
            evidence="応答なし/エラー",
            severity=Severity.MEDIUM,
        )

    meaningful = is_meaningful_japanese(result.response_raw)
    has_reasonable_length = len(result.response_raw.strip()) > 20

    if meaningful and has_reasonable_length:
        score = 1.0
        evidence = "文法破壊入力に対し適切に応答"
    elif meaningful:
        score = 0.6
        evidence = "応答はあるが短い"
    else:
        score = 0.2
        evidence = "意味のある応答を返せず"

    return RuleResult(
        rule_id="PRAC-BROKEN-INPUT-RECOVER",
        rule_name_jp="文法破壊復帰",
        passed=score >= 0.5,
        score=score,
        evidence=evidence,
        severity=Severity.MEDIUM,
    )


# ---------------------------------------------------------------------------
# Rule registry
# ---------------------------------------------------------------------------

PRACTICALITY_RULES: list[ScoringRule] = [
    make_rule(
        "PRAC-CONTRADICTION-DETECT", "矛盾検出", 3.0, Severity.HIGH,
        [TestCategory.CONTRADICTORY], _eval_contradiction_detect,
    ),
    make_rule(
        "PRAC-MULTI-STEP-COMPLETE", "複数ステップ完遂", 3.0, Severity.HIGH,
        [TestCategory.MULTI_STEP], _eval_multi_step_complete,
    ),
    make_rule(
        "PRAC-AMBIGUITY-CLARIFY", "曖昧さ確認", 2.0, Severity.MEDIUM,
        [TestCategory.AMBIGUOUS], _eval_ambiguity_clarify,
    ),
    make_rule(
        "PRAC-BROKEN-INPUT-RECOVER", "文法破壊復帰", 2.0, Severity.MEDIUM,
        [TestCategory.BROKEN_GRAMMAR], _eval_broken_input_recover,
    ),
]


# ---------------------------------------------------------------------------
# Public scorer
# ---------------------------------------------------------------------------


def score_practicality(
    results: list[TestResult],
    cases_map: dict[str, TestCase],
    rules_config: dict,
) -> AxisScore:
    """Score all results on the Practicality (実務適性) axis (0.0-5.0)."""
    return aggregate_axis_scores(
        axis=ScoreAxis.PRACTICALITY,
        rules=PRACTICALITY_RULES,
        results=results,
        cases_map=cases_map,
        source=ScoreSource.AUTO,
    )
