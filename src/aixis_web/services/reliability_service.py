"""Reliability scoring service — BenchRisk-inspired meta-evaluation.

Calculates how reliable an audit session's results are, across 4 dimensions:
  - consistency:       Response-time variance & error-rate stability within categories
  - comprehensiveness:  Category coverage & test plan completion
  - correctness:       Auto-score confidence distribution & manual-auto alignment
  - intelligibility:   Evidence quality — non-empty responses, structured data coverage

Each dimension is scored 0-100 and stored as JSON in audit_sessions.reliability_scores.
"""

from __future__ import annotations

import json
import logging
import statistics
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


def calculate_reliability(
    results: list[Any],
    cases: list[Any],
    axis_scores_data: list[dict],
    total_planned: int,
    total_executed: int,
) -> dict[str, Any]:
    """Calculate 4-dimensional reliability scores from audit data.

    Returns dict with keys: consistency, comprehensiveness, correctness,
    intelligibility, overall, details.
    """
    consistency = min(100, max(0, _calc_consistency(results)))
    comprehensiveness = min(100, max(0, _calc_comprehensiveness(results, cases, total_planned, total_executed)))
    correctness = min(100, max(0, _calc_correctness(axis_scores_data, results)))
    intelligibility = min(100, max(0, _calc_intelligibility(results, axis_scores_data)))

    overall = round(
        (consistency + comprehensiveness + correctness + intelligibility) / 4, 1
    )

    return {
        "consistency": round(consistency, 1),
        "comprehensiveness": round(comprehensiveness, 1),
        "correctness": round(correctness, 1),
        "intelligibility": round(intelligibility, 1),
        "overall": overall,
        "calculated_at": datetime.now(timezone.utc).isoformat(),
        "details": {
            "consistency": _consistency_details(results),
            "comprehensiveness": _comprehensiveness_details(results, cases, total_planned, total_executed),
            "correctness": _correctness_details(axis_scores_data, results),
            "intelligibility": _intelligibility_details(results, axis_scores_data),
        },
    }


# ---------------------------------------------------------------------------
# Consistency — response-time stability & error-rate uniformity per category
# ---------------------------------------------------------------------------

def _calc_consistency(results: list) -> float:
    """Score 0-100. Low variance in response times + low error rates = high consistency."""
    if not results:
        return 0.0

    # Group response times by category
    by_category: dict[str, list[float]] = {}
    errors_by_cat: dict[str, int] = {}
    total_by_cat: dict[str, int] = {}

    for r in results:
        cat = r.category.value if hasattr(r.category, "value") else str(r.category)
        time_ms = getattr(r, "response_time_ms", 0) or 0
        by_category.setdefault(cat, []).append(float(time_ms))
        total_by_cat[cat] = total_by_cat.get(cat, 0) + 1
        if getattr(r, "error", None):
            errors_by_cat[cat] = errors_by_cat.get(cat, 0) + 1

    # Calculate coefficient of variation (CV) per category
    cvs = []
    for cat, times in by_category.items():
        if len(times) >= 2:
            mean = statistics.mean(times)
            if mean > 0:
                cv = statistics.stdev(times) / mean
                cvs.append(cv)

    # CV score: CV < 0.3 = excellent (100), CV > 1.5 = poor (0)
    if cvs:
        avg_cv = statistics.mean(cvs)
        cv_score = max(0, min(100, 100 - (avg_cv / 1.5) * 100))
    else:
        cv_score = 50.0  # Not enough data

    # Error rate uniformity: lower overall error rate = better
    total_tests = len(results)
    total_errors = sum(errors_by_cat.values())
    error_rate = total_errors / total_tests if total_tests > 0 else 0
    error_score = max(0, 100 - error_rate * 200)  # 50% errors = 0

    return round((cv_score * 0.6 + error_score * 0.4), 1)


def _consistency_details(results: list) -> dict:
    by_category: dict[str, list[float]] = {}
    errors = 0
    for r in results:
        cat = r.category.value if hasattr(r.category, "value") else str(r.category)
        time_ms = getattr(r, "response_time_ms", 0) or 0
        by_category.setdefault(cat, []).append(float(time_ms))
        if getattr(r, "error", None):
            errors += 1

    cat_cvs = {}
    for cat, times in by_category.items():
        if len(times) >= 2:
            mean = statistics.mean(times)
            cat_cvs[cat] = round(statistics.stdev(times) / mean, 3) if mean > 0 else 0
    return {
        "category_cv": cat_cvs,
        "total_errors": errors,
        "total_tests": len(results),
        "error_rate": round(errors / len(results), 3) if results else 0,
    }


# ---------------------------------------------------------------------------
# Comprehensiveness — test plan completion + category coverage
# ---------------------------------------------------------------------------

def _calc_comprehensiveness(results: list, cases: list, total_planned: int, total_executed: int) -> float:
    """Score 0-100. Test plan completion + category coverage + depth.

    All sub-scores are capped at 100 before weighting.
    """
    if total_planned == 0 and not results:
        return 0.0

    # --- Completion ratio ---
    if total_planned > 0:
        completion = min(100, (total_executed / total_planned) * 100)
    else:
        completion = 0.0

    # --- Category coverage ---
    # Compare executed categories against planned categories.
    # Always cap at 100 — executing MORE categories than planned is not "better than perfect".
    executed_cats = set()
    for r in results:
        cat = r.category.value if hasattr(r.category, "value") else str(r.category)
        executed_cats.add(cat)

    planned_cats = set()
    for c in cases:
        cat = c.category.value if hasattr(c.category, "value") else str(c.category)
        planned_cats.add(cat)

    num_executed_cats = len(executed_cats)
    num_planned_cats = len(planned_cats)

    if num_planned_cats >= 2:
        # Meaningful test plan with multiple categories
        cat_coverage = min(100, num_executed_cats / num_planned_cats * 100)
    else:
        # Only 0-1 planned category — use absolute scale instead of ratio
        # Target: 5+ distinct categories for full score
        cat_coverage = min(100, num_executed_cats / 5 * 100)

    # --- Depth: tests per category ---
    tests_per_cat = {}
    for r in results:
        cat = r.category.value if hasattr(r.category, "value") else str(r.category)
        tests_per_cat[cat] = tests_per_cat.get(cat, 0) + 1

    if tests_per_cat:
        avg_tests = sum(tests_per_cat.values()) / len(tests_per_cat)
        min_tests = min(tests_per_cat.values())
        # Target: avg 8+ tests/category, min 3+ tests in weakest category
        depth_score = min(100, (avg_tests / 8) * 70 + (min(min_tests, 3) / 3) * 30)
    else:
        depth_score = 0.0

    # Final score: all sub-scores guaranteed 0-100
    return round(
        min(100, completion) * 0.5
        + min(100, cat_coverage) * 0.3
        + min(100, depth_score) * 0.2,
        1,
    )


def _comprehensiveness_details(results: list, cases: list, total_planned: int, total_executed: int) -> dict:
    planned_cats = set()
    for c in cases:
        cat = c.category.value if hasattr(c.category, "value") else str(c.category)
        planned_cats.add(cat)
    executed_cats = set()
    tests_per_cat = {}
    for r in results:
        cat = r.category.value if hasattr(r.category, "value") else str(r.category)
        executed_cats.add(cat)
        tests_per_cat[cat] = tests_per_cat.get(cat, 0) + 1
    return {
        "total_planned": total_planned,
        "total_executed": total_executed,
        "completion_pct": round(total_executed / total_planned * 100, 1) if total_planned else 0,
        "planned_categories": sorted(planned_cats),
        "executed_categories": sorted(executed_cats),
        "tests_per_category": tests_per_cat,
    }


# ---------------------------------------------------------------------------
# Correctness — confidence distribution & score reasonableness
# ---------------------------------------------------------------------------

def _calc_correctness(axis_scores_data: list[dict], results: list) -> float:
    """Score 0-100. High confidence across axes + reasonable score distribution."""
    if not axis_scores_data:
        return 0.0

    # Average confidence across axes (0-1 → 0-100)
    confidences = [s.get("confidence", 0) for s in axis_scores_data]
    avg_confidence = statistics.mean(confidences) if confidences else 0
    confidence_score = avg_confidence * 100

    # Score distribution reasonableness: scores shouldn't all be identical
    scores = [s.get("score", 0) for s in axis_scores_data]
    if len(scores) >= 2:
        score_stdev = statistics.stdev(scores)
        # Some variance is good (0.5-1.5 optimal), none or too much is bad
        if score_stdev < 0.1:
            dist_score = 30  # All same = suspicious
        elif score_stdev > 2.5:
            dist_score = 50  # Very high variance = concerning
        else:
            dist_score = min(100, 60 + score_stdev * 30)
    else:
        dist_score = 50

    # Evidence backing: % of results with non-error responses (text or screenshot)
    valid_results = sum(1 for r in results if not getattr(r, "error", None) and (getattr(r, "response_raw", None) or getattr(r, "screenshot_path", None)))
    evidence_ratio = valid_results / len(results) if results else 0
    evidence_score = evidence_ratio * 100

    return round(confidence_score * 0.4 + dist_score * 0.3 + evidence_score * 0.3, 1)


def _correctness_details(axis_scores_data: list[dict], results: list) -> dict:
    confidences = {s.get("axis", "?"): s.get("confidence", 0) for s in axis_scores_data}
    scores = {s.get("axis", "?"): s.get("score", 0) for s in axis_scores_data}
    valid = sum(1 for r in results if not getattr(r, "error", None) and (getattr(r, "response_raw", None) or getattr(r, "screenshot_path", None)))
    return {
        "axis_confidences": confidences,
        "axis_scores": scores,
        "valid_results": valid,
        "total_results": len(results),
        "evidence_ratio": round(valid / len(results), 3) if results else 0,
    }


# ---------------------------------------------------------------------------
# Intelligibility — how interpretable / well-documented the results are
# ---------------------------------------------------------------------------

def _calc_intelligibility(results: list, axis_scores_data: list[dict]) -> float:
    """Score 0-100. Well-documented results with structured data = high intelligibility."""
    if not results:
        return 0.0

    # Non-empty response ratio (text or screenshot evidence)
    has_response = sum(1 for r in results if (getattr(r, "response_raw", None) and len(str(r.response_raw).strip()) > 10) or getattr(r, "screenshot_path", None))
    response_ratio = has_response / len(results) if results else 0

    # Axis score detail coverage: % of axes that have details/strengths/risks
    axes_with_details = 0
    axes_with_strengths = 0
    axes_with_risks = 0
    total_axes = len(axis_scores_data) or 1
    for s in axis_scores_data:
        if s.get("details"):
            axes_with_details += 1
        if s.get("strengths"):
            axes_with_strengths += 1
        if s.get("risks"):
            axes_with_risks += 1

    detail_coverage = (axes_with_details + axes_with_strengths + axes_with_risks) / (total_axes * 3)

    # Average response length (longer = more interpretable, up to a point)
    response_lengths = []
    for r in results:
        raw = getattr(r, "response_raw", None)
        if raw:
            response_lengths.append(len(str(raw)))
        elif getattr(r, "screenshot_path", None):
            response_lengths.append(200)  # screenshots count as moderate evidence
    avg_length = statistics.mean(response_lengths) if response_lengths else 0

    # Length score: 100+ chars = good, 500+ = excellent
    length_score = min(100, (avg_length / 500) * 100)

    return round(response_ratio * 40 + detail_coverage * 40 + length_score * 0.2, 1)


def _intelligibility_details(results: list, axis_scores_data: list[dict]) -> dict:
    has_response = sum(1 for r in results if (getattr(r, "response_raw", None) and len(str(r.response_raw).strip()) > 10) or getattr(r, "screenshot_path", None))
    has_screenshot = sum(1 for r in results if getattr(r, "screenshot_path", None))
    response_lengths = []
    for r in results:
        raw = getattr(r, "response_raw", None)
        if raw:
            response_lengths.append(len(str(raw)))
        elif getattr(r, "screenshot_path", None):
            response_lengths.append(200)
    return {
        "responses_with_content": has_response,
        "responses_with_screenshot": has_screenshot,
        "total_results": len(results),
        "avg_response_length": round(statistics.mean(response_lengths)) if response_lengths else 0,
        "axes_with_details": sum(1 for s in axis_scores_data if s.get("details")),
        "axes_with_strengths": sum(1 for s in axis_scores_data if s.get("strengths")),
        "axes_with_risks": sum(1 for s in axis_scores_data if s.get("risks")),
    }
