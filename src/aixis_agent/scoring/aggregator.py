"""Score aggregation and normalization utilities."""

from ..core.enums import OverallGrade
from ..core.models import AxisScore


def weighted_average(axis_scores: list[AxisScore], weights: dict[str, float]) -> float:
    """Calculate weighted average of axis scores."""
    weighted_sum = 0.0
    total_weight = 0.0
    for axis_score in axis_scores:
        weight = weights.get(axis_score.axis.value, 1.0)
        if axis_score.confidence > 0:
            weighted_sum += axis_score.score * weight
            total_weight += weight
    return weighted_sum / total_weight if total_weight > 0 else 0.0


def normalize_score(score: float, min_val: float = 0.0, max_val: float = 100.0) -> float:
    """Clamp score to the valid range."""
    return max(min_val, min(max_val, score))


def score_to_grade(score: float) -> OverallGrade:
    """Convert a numeric score to a letter grade."""
    return OverallGrade.from_score(score)
