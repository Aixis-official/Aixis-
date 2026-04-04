"""SVG badge generation service (shields.io style)."""

# Grade colors
GRADE_COLORS = {
    "S": "#C9A84C",  # gold
    "A": "#8BA8C4",  # cool silver
    "B": "#6B8A7A",  # neutral
    "C": "#8A7A6B",  # warm gray
    "D": "#8A5A5A",  # muted red
    "F": "#8A5A5A",  # muted red
}

# Axis label mapping
AXIS_LABELS = {
    "practicality": "Practicality",
    "cost_performance": "Cost Performance",
    "localization": "Localization",
    "safety": "Safety",
    "uniqueness": "Uniqueness",
}

AXIS_LABELS_JP = {
    "practicality": "実用性",
    "cost_performance": "コスパ",
    "localization": "日本語対応",
    "safety": "安全性",
    "uniqueness": "独自性",
}


def _text_width(text: str) -> int:
    """Estimate text width in pixels (rough heuristic)."""
    # Average character width for 11px Verdana
    width = 0
    for ch in text:
        if ord(ch) > 0x7F:
            width += 11  # CJK characters are wider
        elif ch in "mwMW":
            width += 8
        elif ch in "il1|!.,;:":
            width += 4
        else:
            width += 6.5
    return int(width) + 10  # padding


def _score_to_grade(score: float) -> str:
    """Convert a numeric score (0-5) to a letter grade.

    Must match enums.py OverallGrade.from_score thresholds.
    """
    if score >= 4.5:
        return "S"
    elif score >= 3.8:
        return "A"
    elif score >= 3.0:
        return "B"
    elif score >= 2.0:
        return "C"
    else:
        return "D"


def generate_svg_badge(
    label: str,
    value: str,
    color: str,
    style: str = "flat",
) -> str:
    """Generate an SVG badge similar to shields.io.

    Args:
        label: Left side text (e.g., "Aixis Score")
        value: Right side text (e.g., "A")
        color: Hex color for the right side (e.g., "#4c1")
        style: "flat" or "for-the-badge"
    """
    label_width = _text_width(label)
    value_width = _text_width(value)
    total_width = label_width + value_width

    if style == "for-the-badge":
        return _svg_for_the_badge(label, value, color, label_width, value_width, total_width)
    return _svg_flat(label, value, color, label_width, value_width, total_width)


def _svg_flat(
    label: str, value: str, color: str,
    label_width: int, value_width: int, total_width: int,
) -> str:
    label_x = label_width / 2
    value_x = label_width + value_width / 2

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{total_width}" height="20" role="img" aria-label="{label}: {value}">
  <title>{label}: {value}</title>
  <linearGradient id="s" x2="0" y2="100%">
    <stop offset="0" stop-color="#bbb" stop-opacity=".1"/>
    <stop offset="1" stop-opacity=".1"/>
  </linearGradient>
  <clipPath id="r">
    <rect width="{total_width}" height="20" rx="3" fill="#fff"/>
  </clipPath>
  <g clip-path="url(#r)">
    <rect width="{label_width}" height="20" fill="#555"/>
    <rect x="{label_width}" width="{value_width}" height="20" fill="{color}"/>
    <rect width="{total_width}" height="20" fill="url(#s)"/>
  </g>
  <g fill="#fff" text-anchor="middle" font-family="Verdana,Geneva,DejaVu Sans,sans-serif" text-rendering="geometricPrecision" font-size="11">
    <text aria-hidden="true" x="{label_x}" y="15" fill="#010101" fill-opacity=".3">{label}</text>
    <text x="{label_x}" y="14">{label}</text>
    <text aria-hidden="true" x="{value_x}" y="15" fill="#010101" fill-opacity=".3">{value}</text>
    <text x="{value_x}" y="14">{value}</text>
  </g>
</svg>"""


def _svg_for_the_badge(
    label: str, value: str, color: str,
    label_width: int, value_width: int, total_width: int,
) -> str:
    height = 28
    label_width += 10
    value_width += 10
    total_width = label_width + value_width
    label_x = label_width / 2
    value_x = label_width + value_width / 2

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{total_width}" height="{height}" role="img" aria-label="{label}: {value}">
  <title>{label}: {value}</title>
  <clipPath id="r">
    <rect width="{total_width}" height="{height}" rx="4" fill="#fff"/>
  </clipPath>
  <g clip-path="url(#r)">
    <rect width="{label_width}" height="{height}" fill="#555"/>
    <rect x="{label_width}" width="{value_width}" height="{height}" fill="{color}"/>
  </g>
  <g fill="#fff" text-anchor="middle" font-family="Verdana,Geneva,DejaVu Sans,sans-serif" text-rendering="geometricPrecision" font-size="10">
    <text x="{label_x}" y="18" textLength="{label_width - 16}" font-weight="bold" text-transform="uppercase">{label}</text>
    <text x="{value_x}" y="18" textLength="{value_width - 16}" font-weight="bold" text-transform="uppercase">{value}</text>
  </g>
</svg>"""


def generate_tool_badge(
    tool_name: str,
    overall_grade: str | None,
    overall_score: float | None = None,
    style: str = "flat",
) -> str:
    """Generate a badge for a tool's overall score."""
    grade = overall_grade or (
        _score_to_grade(overall_score) if overall_score is not None else "N/A"
    )
    color = GRADE_COLORS.get(grade, "#9f9f9f")
    label = f"Aixis | {tool_name}"
    return generate_svg_badge(label, grade, color, style)


def generate_evaluated_badge(
    tool_name: str,
    overall_grade: str | None = None,
    overall_score: float | None = None,
    style: str = "flat",
) -> str:
    """Generate an 'Aixis Evaluated' badge — factual, not a recommendation.

    This badge indicates that the tool has been independently evaluated
    by Aixis and has published 5-axis scores.
    """
    grade = overall_grade or (
        _score_to_grade(overall_score) if overall_score is not None else None
    )
    if grade:
        value = f"Evaluated — {grade}"
        color = GRADE_COLORS.get(grade, "#6366f1")
    else:
        value = "Evaluated"
        color = "#6366f1"  # Aixis indigo
    label = "Aixis"
    return generate_svg_badge(label, value, color, style)


def generate_axis_badge(
    tool_name: str,
    axis: str,
    score: float,
    style: str = "flat",
) -> str:
    """Generate a badge for a specific axis score."""
    grade = _score_to_grade(score)
    color = GRADE_COLORS.get(grade, "#9f9f9f")
    axis_label = AXIS_LABELS.get(axis, axis)
    label = f"Aixis {axis_label}"
    value = f"{score:.1f} ({grade})"
    return generate_svg_badge(label, value, color, style)
