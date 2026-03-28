"""Scoring engine orchestrator: runs all 5 axis scorers and aggregates results.

Refactored for the 5-axis scoring model (0.0-5.0 scale).
Grade thresholds: S>=4.5, A>=3.8, B>=3.0, C>=2.0, D<2.0.
"""

from collections import defaultdict
from pathlib import Path
from typing import Callable

import yaml

from ..core.enums import OverallGrade, ScoreAxis
from ..core.models import (
    AuditReport,
    AxisScore,
    CategoryResult,
    TestCase,
    TestResult,
)
from .analyzers.cost_performance import score_cost_performance
from .analyzers.localization import score_localization
from .analyzers.practicality import score_practicality
from .analyzers.safety import score_safety
from .analyzers.uniqueness import score_uniqueness

# ---------------------------------------------------------------------------
# Axis → scorer mapping
# ---------------------------------------------------------------------------

_ScorerFn = Callable[[list[TestResult], dict[str, TestCase], dict], AxisScore]

AXIS_SCORERS: dict[ScoreAxis, _ScorerFn] = {
    ScoreAxis.PRACTICALITY: score_practicality,
    ScoreAxis.COST_PERFORMANCE: score_cost_performance,
    ScoreAxis.LOCALIZATION: score_localization,
    ScoreAxis.SAFETY: score_safety,
    ScoreAxis.UNIQUENESS: score_uniqueness,
}

# Category display names (9 categories, unchanged)
CATEGORY_NAMES = {
    "dialect": "方言対応テスト",
    "long_input": "長文入力テスト",
    "contradictory": "矛盾指示テスト",
    "ambiguous": "曖昧指示テスト",
    "keigo_mixing": "敬語混合テスト",
    "unicode_edge": "Unicode特殊文字テスト",
    "business_jp": "商習慣テスト",
    "multi_step": "複合指示テスト",
    "broken_grammar": "文法破壊テスト",
}

# Default axis weights (profile can override)
DEFAULT_AXIS_WEIGHTS: dict[str, float] = {
    "practicality": 1.0,
    "cost_performance": 1.0,
    "localization": 1.0,
    "safety": 1.0,
    "uniqueness": 1.0,
}


def load_scoring_rules(config_path: Path) -> dict:
    """Load scoring rules configuration from YAML."""
    if not config_path.exists():
        return {}
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


class ScoringEngine:
    """Runs all 5 axis scorers and produces the final audit report (0.0-5.0 scale)."""

    def __init__(self, rules_config: dict | None = None):
        self.rules_config = rules_config or {}

    @staticmethod
    def _is_infrastructure_error(result: TestResult) -> bool:
        """Check if an error is an infrastructure failure (not a tool quality issue).

        Aborted audits, budget exhaustion, and executor init errors should
        NOT penalize the tool's quality scores.
        """
        if not result.error:
            return False
        infra_markers = [
            "監査が中止されました",
            "予算上限",
            "Budget exhausted",
            "not initialized",
            "連続失敗",
            "最大ステップ数",
            "AUTH_FAILURE:",
        ]
        return any(marker in result.error for marker in infra_markers)

    def score_all(
        self,
        results: list[TestResult],
        cases: list[TestCase],
        target_tool: str,
    ) -> AuditReport:
        """Score all results across all 5 axes and build the audit report."""
        cases_map = {case.id: case for case in cases}

        # Filter out infrastructure errors — they don't reflect tool quality
        scoreable_results = [r for r in results if not self._is_infrastructure_error(r)]

        # Run per-axis scoring (only on scoreable results)
        axis_scores: list[AxisScore] = []
        for axis, scorer_fn in AXIS_SCORERS.items():
            axis_score = scorer_fn(scoreable_results, cases_map, self.rules_config)
            axis_scores.append(axis_score)

        # Calculate overall score (weighted average of axes with confidence > 0)
        axis_weights = self.rules_config.get("axis_weights", DEFAULT_AXIS_WEIGHTS)

        weighted_sum = 0.0
        total_weight = 0.0
        for axis_score in axis_scores:
            weight = axis_weights.get(axis_score.axis.value, 1.0)
            # Only include axes that have evaluations (confidence > 0)
            if axis_score.confidence > 0:
                weighted_sum += axis_score.score * weight
                total_weight += weight

        overall_score = round(weighted_sum / total_weight, 1) if total_weight > 0 else 0.0
        overall_score = min(5.0, max(0.0, overall_score))
        overall_grade = OverallGrade.from_score(overall_score)

        # Build category breakdowns
        category_breakdowns = self._build_category_breakdowns(results)

        # Count results (infrastructure errors are counted separately)
        infra_errors = sum(1 for r in results if self._is_infrastructure_error(r))
        tool_errors = sum(1 for r in results if r.error and not self._is_infrastructure_error(r))
        total_errors = tool_errors
        total_with_response = sum(1 for r in results if r.response_raw and not r.error)
        total_failed = max(0, len(results) - total_with_response - total_errors - infra_errors)

        # Generate executive summaries
        summary_jp = self._generate_summary_jp(
            target_tool, axis_scores, overall_score, overall_grade, len(results)
        )
        summary_en = self._generate_summary_en(
            target_tool, axis_scores, overall_score, overall_grade, len(results)
        )

        return AuditReport(
            report_id=f"report-{target_tool}",
            target_tool=target_tool,
            total_tests=len(results),
            total_passed=total_with_response,
            total_failed=total_failed,
            total_errors=total_errors,
            axis_scores=axis_scores,
            overall_score=overall_score,
            overall_grade=overall_grade,
            executive_summary_jp=summary_jp,
            executive_summary_en=summary_en,
            category_breakdowns={cat.category: cat for cat in category_breakdowns},
            raw_results=results,
        )

    def _build_category_breakdowns(self, results: list[TestResult]) -> list[CategoryResult]:
        """Group results by category and compute per-category stats."""
        by_category: dict[str, list[TestResult]] = defaultdict(list)
        for r in results:
            by_category[r.category.value].append(r)

        breakdowns = []
        for cat_value, cat_results in sorted(by_category.items()):
            errors = sum(1 for r in cat_results if r.error)
            passed = sum(1 for r in cat_results if r.response_raw and not r.error)
            failed = len(cat_results) - passed - errors
            times = [r.response_time_ms for r in cat_results if r.response_time_ms > 0]
            avg_time = sum(times) / len(times) if times else 0

            breakdowns.append(CategoryResult(
                category=cat_results[0].category,
                category_name_jp=CATEGORY_NAMES.get(cat_value, cat_value),
                total_tests=len(cat_results),
                passed_tests=passed,
                failed_tests=failed,
                error_tests=errors,
                pass_rate=passed / len(cat_results) if cat_results else 0,
                avg_response_time_ms=avg_time,
            ))
        return breakdowns

    def _generate_summary_jp(
        self, tool: str, axes: list[AxisScore],
        overall: float, grade: OverallGrade, total: int,
    ) -> str:
        lines = [
            f"本監査は{tool}に対し、{total}件の破壊的テストを実施した結果をまとめたものです。",
            f"総合評価は {grade.value} ランク（{overall:.1f}点/5.0点）です。",
            "",
        ]
        for axis in axes:
            if axis.confidence > 0:
                lines.append(f"【{axis.axis_name_jp}】{axis.score:.1f}点")
                for s in axis.strengths:
                    lines.append(f"  強み: {s}")
                for r in axis.risks:
                    lines.append(f"  リスク: {r}")
                lines.append("")
        # Note manually-evaluated axes
        manual_axes = [a for a in axes if a.confidence == 0]
        if manual_axes:
            names = "、".join(a.axis_name_jp for a in manual_axes)
            lines.append(f"※ {names} は手動チェックリストによる評価が必要です。")
        return "\n".join(lines)

    def _generate_summary_en(
        self, tool: str, axes: list[AxisScore],
        overall: float, grade: OverallGrade, total: int,
    ) -> str:
        lines = [
            f"This audit summarizes {total} destructive tests conducted against {tool}.",
            f"Overall grade: {grade.value} ({overall:.1f}/5.0).",
            "",
        ]
        for axis in axes:
            if axis.confidence > 0:
                name = axis.axis.name_en
                lines.append(f"[{name}] {axis.score:.1f}")
                for s in axis.strengths:
                    lines.append(f"  Strength: {s}")
                for r in axis.risks:
                    lines.append(f"  Risk: {r}")
                lines.append("")
        manual_axes = [a for a in axes if a.confidence == 0]
        if manual_axes:
            names = ", ".join(a.axis.name_en for a in manual_axes)
            lines.append(f"Note: {names} require manual checklist evaluation.")
        return "\n".join(lines)
