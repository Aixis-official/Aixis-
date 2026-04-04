"""SVG badge generator for Aixis Certified badges."""

from pathlib import Path
from xml.sax.saxutils import escape

from ..core.enums import OverallGrade


# Grade-specific colors (right side of badge)
_GRADE_COLORS: dict[OverallGrade, str] = {
    OverallGrade.S: "#C9A84C",  # gold
    OverallGrade.A: "#8BA8C4",  # cool silver
    OverallGrade.B: "#6B8A7A",  # neutral
    OverallGrade.C: "#8A7A6B",  # warm gray
    OverallGrade.D: "#8A5A5A",  # muted red
}

_GRADE_COLORS_DARK: dict[OverallGrade, str] = {
    OverallGrade.S: "#A8893E",
    OverallGrade.A: "#7090A8",
    OverallGrade.B: "#567060",
    OverallGrade.C: "#706254",
    OverallGrade.D: "#704848",
}

# Badge layout constants
_LEFT_WIDTH = 108
_RIGHT_WIDTH = 72
_TOTAL_WIDTH = _LEFT_WIDTH + _RIGHT_WIDTH
_BADGE_HEIGHT = 28
_DATE_HEIGHT = 14
_TOTAL_HEIGHT = _BADGE_HEIGHT + _DATE_HEIGHT
_RADIUS = 4

_SVG_TEMPLATE = """\
<svg xmlns="http://www.w3.org/2000/svg" width="{total_width}" height="{total_height}" role="img" aria-label="Aixis Certified: {grade} {score}">
  <title>Aixis Certified | {tool_name} | {grade} {score}</title>
  <defs>
    <linearGradient id="bg-left" x2="0" y2="100%">
      <stop offset="0" stop-color="#234e80"/>
      <stop offset="1" stop-color="{left_color}"/>
    </linearGradient>
    <linearGradient id="bg-right" x2="0" y2="100%">
      <stop offset="0" stop-color="{right_color_dark}"/>
      <stop offset="1" stop-color="{right_color}"/>
    </linearGradient>
    <clipPath id="clip">
      <rect width="{total_width}" height="{badge_height}" rx="{radius}"/>
    </clipPath>
  </defs>
  <g clip-path="url(#clip)">
    <rect width="{left_width}" height="{badge_height}" fill="url(#bg-left)"/>
    <rect x="{left_width}" width="{right_width}" height="{badge_height}" fill="url(#bg-right)"/>
  </g>
  <!-- Left text: Aixis Certified -->
  <g fill="#fff" text-anchor="middle" font-family="Verdana,Geneva,DejaVu Sans,sans-serif" font-size="11">
    <text x="{left_center}" y="19.5" fill="#010101" fill-opacity=".3">Aixis Certified</text>
    <text x="{left_center}" y="18.5">Aixis Certified</text>
  </g>
  <!-- Right text: Grade + Score -->
  <g fill="#fff" text-anchor="middle" font-family="Verdana,Geneva,DejaVu Sans,sans-serif" font-size="11" font-weight="bold">
    <text x="{right_center}" y="19.5" fill="#010101" fill-opacity=".3">{grade} {score}</text>
    <text x="{right_center}" y="18.5">{grade} {score}</text>
  </g>
  <!-- Date footer -->
  <text x="{date_center}" y="{date_y}" text-anchor="middle" font-family="Verdana,Geneva,DejaVu Sans,sans-serif" font-size="9" fill="#718096">{date}</text>
</svg>"""


class BadgeGenerator:
    """Generates Aixis Certified SVG badges."""

    def generate(
        self,
        tool_name: str,
        grade: OverallGrade,
        score: float,
        date: str,
        output_path: Path,
    ) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        svg_path = output_path.with_suffix(".svg")

        right_color = _GRADE_COLORS.get(grade, "#718096")
        right_color_dark = _GRADE_COLORS_DARK.get(grade, "#4a5568")

        svg_content = _SVG_TEMPLATE.format(
            total_width=_TOTAL_WIDTH,
            total_height=_TOTAL_HEIGHT,
            badge_height=_BADGE_HEIGHT,
            radius=_RADIUS,
            left_width=_LEFT_WIDTH,
            right_width=_RIGHT_WIDTH,
            left_color="#1a365d",
            right_color=right_color,
            right_color_dark=right_color_dark,
            left_center=_LEFT_WIDTH / 2,
            right_center=_LEFT_WIDTH + _RIGHT_WIDTH / 2,
            date_center=_TOTAL_WIDTH / 2,
            date_y=_BADGE_HEIGHT + 11,
            tool_name=escape(tool_name),
            grade=escape(grade.value),
            score=f"{score:.1f}",
            date=escape(date),
        )

        with open(svg_path, "w", encoding="utf-8") as f:
            f.write(svg_content)

        return svg_path
