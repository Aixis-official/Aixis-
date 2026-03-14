"""Shared axis scoring aggregation logic."""

from ...core.enums import ScoreAxis, ScoreSource
from ...core.models import AxisScore, ScoreDetail, TestCase, TestResult
from ..rules import ScoringRule, is_rule_applicable


def aggregate_axis_scores(
    axis: ScoreAxis,
    rules: list[ScoringRule],
    results: list[TestResult],
    cases_map: dict[str, TestCase],
    source: ScoreSource = ScoreSource.AUTO,
) -> AxisScore:
    """Run rules against results and aggregate into an AxisScore (0.0-5.0 scale).

    Rule scores are 0.0-1.0 internally, final axis score is 0.0-5.0.
    """
    rule_scores: dict[str, list[float]] = {}
    rule_case_ids: dict[str, list[str]] = {}

    for result in results:
        case = cases_map.get(result.test_case_id)
        for rule in rules:
            if not is_rule_applicable(rule, result):
                continue
            eval_result = rule.evaluate(result, case)
            rule_scores.setdefault(rule.rule_id, []).append(eval_result.score)
            rule_case_ids.setdefault(rule.rule_id, []).append(result.test_case_id)

    details = []
    weighted_sum = 0.0
    total_weight = 0.0
    strengths = []
    risks = []

    for rule in rules:
        scores = rule_scores.get(rule.rule_id, [])
        if not scores:
            continue

        avg_score = sum(scores) / len(scores)
        weighted_sum += avg_score * rule.weight
        total_weight += rule.weight

        # Detail scores are on 0-5 scale for display
        details.append(ScoreDetail(
            rule_id=rule.rule_id,
            rule_name_jp=rule.rule_name_jp,
            score=round(avg_score * 5.0, 2),
            weight=rule.weight,
            evidence=f"平均: {avg_score:.2f} ({len(scores)}件評価)",
            severity=rule.severity,
            test_case_ids=rule_case_ids.get(rule.rule_id, [])[:5],
        ))

        if avg_score >= 0.8:
            strengths.append(f"{rule.rule_name_jp}: 高水準 ({avg_score:.0%})")
        elif avg_score < 0.5:
            risks.append(f"{rule.rule_name_jp}: 要改善 ({avg_score:.0%})")

    # Final score: weighted average * 5.0 to convert 0-1 → 0-5
    raw = (weighted_sum / total_weight) if total_weight > 0 else 0
    axis_score = round(min(5.0, max(0.0, raw * 5.0)), 1)

    # Count unique results evaluated (not total rule evaluations)
    evaluated_case_ids: set[str] = set()
    for case_ids in rule_case_ids.values():
        evaluated_case_ids.update(case_ids)
    unique_evaluated = len(evaluated_case_ids)

    return AxisScore(
        axis=axis,
        axis_name_jp=axis.name_jp,
        score=axis_score,
        confidence=min(1.0, unique_evaluated / max(1, len(results))),
        source=source,
        details=details,
        strengths=strengths,
        risks=risks,
    )
