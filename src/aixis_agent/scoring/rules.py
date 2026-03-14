"""Scoring rule definitions and evaluation logic."""

from dataclasses import dataclass
from typing import Callable

from ..core.enums import Severity, TestCategory
from ..core.models import RuleResult, TestCase, TestResult


@dataclass
class ScoringRule:
    """A single scoring rule that evaluates a test result."""

    rule_id: str
    rule_name_jp: str
    weight: float
    severity: Severity
    applicable_categories: list[TestCategory] | None  # None = all categories
    evaluate: Callable[[TestResult, TestCase | None], RuleResult]


def make_rule(
    rule_id: str,
    name_jp: str,
    weight: float,
    severity: Severity,
    categories: list[TestCategory] | None,
    eval_fn: Callable[[TestResult, TestCase | None], RuleResult],
) -> ScoringRule:
    return ScoringRule(
        rule_id=rule_id,
        rule_name_jp=name_jp,
        weight=weight,
        severity=severity,
        applicable_categories=categories,
        evaluate=eval_fn,
    )


def is_rule_applicable(rule: ScoringRule, result: TestResult) -> bool:
    """Check if a rule applies to a given test result's category."""
    if rule.applicable_categories is None:
        return True
    return result.category in rule.applicable_categories
