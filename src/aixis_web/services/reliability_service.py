"""Reliability scoring service — BenchRisk-inspired meta-evaluation.

Calculates how reliable an audit session's results are, across 4 dimensions:
  - consistency:        Response-time variance & error-rate stability within categories
  - comprehensiveness:  Category coverage & test plan completion
  - correctness:        Auto-score confidence, evidence-score alignment, grounding
  - intelligibility:    Evidence quality — non-empty responses, structured data coverage

Each dimension is scored 0-100 and stored as JSON in audit_sessions.reliability_scores.
The overall score is a weighted average emphasising correctness and comprehensiveness.
"""

from __future__ import annotations

import logging
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Depth targets per profile — how many tests/category is considered "thorough".
# Profiles with many narrow categories (e.g. translation: 7 cats, 1 test each)
# need a lower per-category target than profiles with fewer broad categories.
_DEPTH_TARGETS: dict[str, int] = {}  # populated lazily from YAML


def _get_depth_target(profile_id: str | None) -> int:
    """Return the ideal tests-per-category for a profile.

    Falls back to 8 (the universal default) if no profile-specific config exists.
    """
    if not profile_id:
        return 8

    if profile_id in _DEPTH_TARGETS:
        return _DEPTH_TARGETS[profile_id]

    # Try to read from profile YAML
    try:
        import yaml
        profile_path = Path(__file__).resolve().parent.parent.parent.parent / "config" / "profiles" / f"{profile_id}.yaml"
        if profile_path.exists():
            with open(profile_path) as f:
                data = yaml.safe_load(f) or {}
            num_cats = len(data.get("primary_categories", []))
            # Heuristic: if many categories, each only needs 1-2 tests;
            # if few categories, each needs deeper coverage.
            if num_cats >= 6:
                target = 2
            elif num_cats >= 4:
                target = 3
            elif num_cats >= 2:
                target = 5
            else:
                target = 8
            _DEPTH_TARGETS[profile_id] = target
            return target
    except Exception:
        pass

    _DEPTH_TARGETS[profile_id] = 8
    return 8


def calculate_reliability(
    results: list[Any],
    cases: list[Any],
    axis_scores_data: list[dict],
    total_planned: int,
    total_executed: int,
    *,
    profile_id: str | None = None,
    historical_scores: list[dict] | None = None,
) -> dict[str, Any]:
    """Calculate 4-dimensional reliability scores from audit data.

    Args:
        results: Test result rows from db_test_results.
        cases: Test case rows from db_test_cases.
        axis_scores_data: Per-axis score dicts with confidence/strengths/risks.
        total_planned: Number of tests planned.
        total_executed: Number of tests executed.
        profile_id: Optional profile name for adaptive depth targets.
        historical_scores: Optional list of previous session overall scores
            for the same tool, used for temporal stability bonus.

    Returns dict with keys: consistency, comprehensiveness, correctness,
    intelligibility, overall, details.
    """
    depth_target = _get_depth_target(profile_id)

    consistency = min(100, max(0, _calc_consistency(results, axis_scores_data)))
    comprehensiveness = min(100, max(0, _calc_comprehensiveness(
        results, cases, total_planned, total_executed, depth_target=depth_target,
    )))
    correctness = min(100, max(0, _calc_correctness(axis_scores_data, results)))
    intelligibility = min(100, max(0, _calc_intelligibility(results, axis_scores_data)))

    # Weighted average — correctness and comprehensiveness matter most for
    # audit credibility; consistency and intelligibility are supporting signals.
    _weights = {
        "correctness": 0.30,
        "comprehensiveness": 0.30,
        "consistency": 0.20,
        "intelligibility": 0.20,
    }
    overall = round(
        correctness * _weights["correctness"]
        + comprehensiveness * _weights["comprehensiveness"]
        + consistency * _weights["consistency"]
        + intelligibility * _weights["intelligibility"],
        1,
    )

    # Temporal stability bonus: if historical data shows consistent scores,
    # boost overall slightly (max +5 points). Large swings penalise.
    temporal_detail = {}
    if historical_scores and len(historical_scores) >= 2:
        hist_values = [h.get("overall_score", h.get("overall", 0)) for h in historical_scores if h]
        hist_values = [v for v in hist_values if v is not None and v > 0]
        if len(hist_values) >= 2:
            hist_stdev = statistics.stdev(hist_values)
            # Low stdev (< 0.3) = very stable = +5 bonus
            # High stdev (> 1.5) = unstable = -5 penalty
            if hist_stdev < 0.3:
                temporal_adj = 5
            elif hist_stdev < 0.8:
                temporal_adj = 2
            elif hist_stdev > 1.5:
                temporal_adj = -5
            else:
                temporal_adj = 0
            overall = round(min(100, max(0, overall + temporal_adj)), 1)
            temporal_detail = {
                "sessions_compared": len(hist_values),
                "score_stdev": round(hist_stdev, 3),
                "adjustment": temporal_adj,
            }

    result = {
        "consistency": round(consistency, 1),
        "comprehensiveness": round(comprehensiveness, 1),
        "correctness": round(correctness, 1),
        "intelligibility": round(intelligibility, 1),
        "overall": overall,
        "depth_target": depth_target,
        "profile_id": profile_id,
        "calculated_at": datetime.now(timezone.utc).isoformat(),
        "details": {
            "consistency": _consistency_details(results, axis_scores_data),
            "comprehensiveness": _comprehensiveness_details(
                results, cases, total_planned, total_executed, depth_target=depth_target,
            ),
            "correctness": _correctness_details(axis_scores_data, results),
            "intelligibility": _intelligibility_details(results, axis_scores_data),
        },
    }
    if temporal_detail:
        result["details"]["temporal_stability"] = temporal_detail
    return result


# ---------------------------------------------------------------------------
# Consistency — response-time stability, error-rate uniformity, internal coherence
# ---------------------------------------------------------------------------

def _calc_internal_coherence(axis_scores_data: list[dict]) -> tuple[float, dict]:
    """Zero-cost reproducibility check: are confidence/score values across axes coherent?

    Returns (score 0-100, detail_dict).

    Two checks:
      (a) Confidence spread — low spread across axes is good (narrow = stable LLM grading).
      (b) Outlier detection — any axis score > 2 stdev from the mean is suspicious.
    """
    if not axis_scores_data:
        return 70.0, {"note": "no axis data — defaulting to 70"}

    confidences = [s.get("confidence", 0) for s in axis_scores_data if s.get("confidence") is not None]
    scores = [s.get("score", 0) for s in axis_scores_data if s.get("score") is not None]

    # --- (a) Confidence spread ---
    if len(confidences) >= 2:
        conf_stdev = statistics.stdev(confidences)
        # stdev < 0.05 = very tight = 100; stdev > 0.30 = very spread = 0
        conf_spread_score = max(0.0, min(100.0, 100.0 - (conf_stdev / 0.30) * 100.0))
    else:
        conf_stdev = 0.0
        conf_spread_score = 70.0  # Insufficient data — neutral

    # --- (b) Score outlier detection ---
    outlier_axes: list[str] = []
    if len(scores) >= 3:
        score_mean = statistics.mean(scores)
        score_stdev = statistics.stdev(scores)
        if score_stdev > 0:
            for s in axis_scores_data:
                sc = s.get("score", score_mean)
                if abs(sc - score_mean) > 2 * score_stdev:
                    outlier_axes.append(s.get("axis", "?"))
        outlier_penalty = len(outlier_axes) * 15  # -15 per outlier axis
        outlier_score = max(0.0, 100.0 - outlier_penalty)
    else:
        score_mean = statistics.mean(scores) if scores else 0.0
        score_stdev = 0.0
        outlier_score = 70.0  # Insufficient data — neutral

    coherence_score = round(conf_spread_score * 0.6 + outlier_score * 0.4, 1)
    detail = {
        "confidence_stdev": round(conf_stdev, 4),
        "confidence_spread_score": round(conf_spread_score, 1),
        "score_mean": round(score_mean, 3) if scores else None,
        "score_stdev": round(score_stdev, 4) if len(scores) >= 3 else None,
        "outlier_axes": outlier_axes,
        "outlier_score": round(outlier_score, 1),
        "coherence_score": coherence_score,
    }
    return coherence_score, detail


def _calc_consistency(results: list, axis_scores_data: list[dict] | None = None) -> float:
    """Score 0-100.

    Sub-metrics (new weights):
      - CV of response times per category:  40%
      - Error rate uniformity:              30%
      - Internal coherence of axis scores:  30%
    """
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
        # Single-test-per-category designs (e.g. many diverse categories):
        # No intra-category variance is calculable — use cross-category CV instead.
        all_times = [t for times in by_category.values() for t in times]
        if len(all_times) >= 2:
            mean = statistics.mean(all_times)
            if mean > 0:
                cross_cv = statistics.stdev(all_times) / mean
                cv_score = max(0, min(100, 100 - (cross_cv / 2.0) * 100))
            else:
                cv_score = 70.0
        else:
            cv_score = 50.0  # Truly insufficient data

    # Error rate uniformity: lower overall error rate = better
    total_tests = len(results)
    total_errors = sum(errors_by_cat.values())
    error_rate = total_errors / total_tests if total_tests > 0 else 0
    error_score = max(0, 100 - error_rate * 200)  # 50% errors = 0

    # Internal coherence (zero-cost, uses axis_scores_data already computed upstream)
    coherence_score, _ = _calc_internal_coherence(axis_scores_data or [])

    return round(cv_score * 0.40 + error_score * 0.30 + coherence_score * 0.30, 1)


def _consistency_details(results: list, axis_scores_data: list[dict] | None = None) -> dict:
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

    _, coherence_detail = _calc_internal_coherence(axis_scores_data or [])

    return {
        "category_cv": cat_cvs,
        "total_errors": errors,
        "total_tests": len(results),
        "error_rate": round(errors / len(results), 3) if results else 0,
        "internal_coherence": coherence_detail,
    }


# ---------------------------------------------------------------------------
# Comprehensiveness — test plan completion + category coverage
# ---------------------------------------------------------------------------

def _calc_comprehensiveness(
    results: list, cases: list,
    total_planned: int, total_executed: int,
    *, depth_target: int = 8,
) -> float:
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
        cat_coverage = min(100, num_executed_cats / num_planned_cats * 100)
    else:
        cat_coverage = min(100, num_executed_cats / 5 * 100)

    # --- Depth: tests per category (adaptive target) ---
    tests_per_cat = {}
    for r in results:
        cat = r.category.value if hasattr(r.category, "value") else str(r.category)
        tests_per_cat[cat] = tests_per_cat.get(cat, 0) + 1

    if tests_per_cat:
        avg_tests = sum(tests_per_cat.values()) / len(tests_per_cat)
        min_tests = min(tests_per_cat.values())
        # Adaptive: use profile-specific depth_target instead of fixed 8
        min_target = max(1, depth_target // 3)  # minimum tests in weakest category
        depth_score = min(100,
            (avg_tests / depth_target) * 70
            + (min(min_tests, min_target) / min_target) * 30
        )
    else:
        depth_score = 0.0

    return round(
        min(100, completion) * 0.5
        + min(100, cat_coverage) * 0.3
        + min(100, depth_score) * 0.2,
        1,
    )


def _comprehensiveness_details(
    results: list, cases: list,
    total_planned: int, total_executed: int,
    *, depth_target: int = 8,
) -> dict:
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
        "depth_target": depth_target,
    }


# ---------------------------------------------------------------------------
# Correctness — confidence distribution & score reasonableness
# ---------------------------------------------------------------------------

def _calc_correctness(axis_scores_data: list[dict], results: list) -> float:
    """Score 0-100. High confidence + reasonable distribution + evidence quality."""
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

    # Evidence richness: axes with both strengths AND risks listed are better grounded
    grounded_axes = sum(1 for s in axis_scores_data if s.get("strengths") and s.get("risks"))
    grounded_ratio = grounded_axes / len(axis_scores_data) if axis_scores_data else 0
    grounded_score = grounded_ratio * 100

    # Evidence-score alignment: high-scoring axes should have more strengths than risks,
    # and low-scoring axes should have more risks than strengths.
    alignment_checks = 0
    aligned = 0
    for s in axis_scores_data:
        score = s.get("score", 0)
        n_strengths = len(s.get("strengths", []) or [])
        n_risks = len(s.get("risks", []) or [])
        if n_strengths == 0 and n_risks == 0:
            continue
        alignment_checks += 1
        if score >= 3.5 and n_strengths >= n_risks:
            aligned += 1
        elif score < 3.5 and n_risks >= n_strengths:
            aligned += 1
        elif abs(n_strengths - n_risks) <= 1:
            aligned += 1  # Close enough — borderline scores can go either way

    alignment_score = (aligned / alignment_checks * 100) if alignment_checks > 0 else 70

    return round(
        confidence_score * 0.25
        + dist_score * 0.15
        + evidence_score * 0.25
        + grounded_score * 0.15
        + alignment_score * 0.20,
        1,
    )


def _correctness_details(axis_scores_data: list[dict], results: list) -> dict:
    confidences = {s.get("axis", "?"): s.get("confidence", 0) for s in axis_scores_data}
    scores = {s.get("axis", "?"): s.get("score", 0) for s in axis_scores_data}
    valid = sum(1 for r in results if not getattr(r, "error", None) and (getattr(r, "response_raw", None) or getattr(r, "screenshot_path", None)))
    grounded = sum(1 for s in axis_scores_data if s.get("strengths") and s.get("risks"))

    # Per-axis alignment
    alignment = {}
    for s in axis_scores_data:
        axis = s.get("axis", "?")
        n_s = len(s.get("strengths", []) or [])
        n_r = len(s.get("risks", []) or [])
        sc = s.get("score", 0)
        if n_s > 0 or n_r > 0:
            expected = "strengths >= risks" if sc >= 3.5 else "risks >= strengths"
            actual = f"strengths={n_s}, risks={n_r}"
            alignment[axis] = {"score": sc, "expected": expected, "actual": actual}

    return {
        "axis_confidences": confidences,
        "axis_scores": scores,
        "valid_results": valid,
        "total_results": len(results),
        "evidence_ratio": round(valid / len(results), 3) if results else 0,
        "grounded_axes": grounded,
        "total_axes": len(axis_scores_data),
        "evidence_score_alignment": alignment,
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
            response_lengths.append(500)  # screenshots = substantial visual evidence
    avg_length = statistics.mean(response_lengths) if response_lengths else 0

    # Length score: 200+ chars = good, 1500+ = excellent
    length_score = min(100, (avg_length / 1500) * 100)

    # Weights: response_ratio (0-1)*35, detail_coverage (0-1)*35, length_score (0-100)*0.3
    return round(response_ratio * 35 + detail_coverage * 35 + length_score * 0.3, 1)


def _intelligibility_details(results: list, axis_scores_data: list[dict]) -> dict:
    has_response = sum(1 for r in results if (getattr(r, "response_raw", None) and len(str(r.response_raw).strip()) > 10) or getattr(r, "screenshot_path", None))
    has_text = sum(1 for r in results if getattr(r, "response_raw", None) and len(str(r.response_raw).strip()) > 10)
    has_screenshot = sum(1 for r in results if getattr(r, "screenshot_path", None))
    response_lengths = []
    for r in results:
        raw = getattr(r, "response_raw", None)
        if raw:
            response_lengths.append(len(str(raw)))
        elif getattr(r, "screenshot_path", None):
            response_lengths.append(500)
    return {
        "responses_with_content": has_response,
        "responses_with_text": has_text,
        "responses_with_screenshot": has_screenshot,
        "total_results": len(results),
        "avg_response_length": round(statistics.mean(response_lengths)) if response_lengths else 0,
        "axes_with_details": sum(1 for s in axis_scores_data if s.get("details")),
        "axes_with_strengths": sum(1 for s in axis_scores_data if s.get("strengths")),
        "axes_with_risks": sum(1 for s in axis_scores_data if s.get("risks")),
    }
