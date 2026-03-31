"""Badge and widget endpoints -- NO API key required (fully public)."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.base import get_db
from ...db.models.score import ToolPublishedScore
from ...db.models.tool import Tool
from ...services.badge_service import (
    GRADE_COLORS,
    generate_axis_badge,
    generate_evaluated_badge,
    generate_tool_badge,
)

router = APIRouter()


async def _get_tool_and_score(
    slug: str, db: AsyncSession
) -> tuple[Tool, ToolPublishedScore | None]:
    """Look up a public tool and its latest score."""
    result = await db.execute(
        select(Tool).where(Tool.slug == slug, Tool.is_public.is_(True), Tool.is_active.is_(True))
    )
    tool = result.scalar_one_or_none()
    if not tool:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tool not found"
        )

    score_result = await db.execute(
        select(ToolPublishedScore)
        .where(ToolPublishedScore.tool_id == tool.id)
        .order_by(ToolPublishedScore.version.desc())
        .limit(1)
    )
    score = score_result.scalar_one_or_none()
    return tool, score


@router.get("/badge/{tool_slug}.svg")
async def tool_badge_svg(
    tool_slug: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    style: str = Query("flat", pattern="^(flat|for-the-badge)$"),
):
    """SVG badge showing the tool's overall grade."""
    tool, score = await _get_tool_and_score(tool_slug, db)

    if score:
        svg = generate_tool_badge(
            tool_name=tool.name,
            overall_grade=score.overall_grade,
            overall_score=score.overall_score,
            style=style,
        )
    else:
        svg = generate_tool_badge(
            tool_name=tool.name,
            overall_grade=None,
            overall_score=None,
            style=style,
        )

    return Response(
        content=svg,
        media_type="image/svg+xml",
        headers={
            "Cache-Control": "public, max-age=3600",
            "Access-Control-Allow-Origin": "*",  # Intentional: badges are embedded on external sites
        },
    )


@router.get("/badge/{tool_slug}/axis/{axis}.svg")
async def axis_badge_svg(
    tool_slug: str,
    axis: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    style: str = Query("flat", pattern="^(flat|for-the-badge)$"),
):
    """SVG badge for a specific axis score."""
    valid_axes = ["practicality", "cost_performance", "localization", "safety", "uniqueness"]
    if axis not in valid_axes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid axis. Must be one of: {', '.join(valid_axes)}",
        )

    tool, score = await _get_tool_and_score(tool_slug, db)

    if not score:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No published scores found",
        )

    axis_score = getattr(score, axis, 0.0)
    svg = generate_axis_badge(
        tool_name=tool.name,
        axis=axis,
        score=axis_score,
        style=style,
    )

    return Response(
        content=svg,
        media_type="image/svg+xml",
        headers={
            "Cache-Control": "public, max-age=3600",
            "Access-Control-Allow-Origin": "*",  # Intentional: badges are embedded on external sites
        },
    )


@router.get("/embed/{tool_slug}", response_class=HTMLResponse)
async def embed_widget(
    tool_slug: str,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Iframe-friendly HTML page with radar chart for embedding."""
    tool, score = await _get_tool_and_score(tool_slug, db)

    # Score values for radar chart
    if score:
        scores = {
            "practicality": score.practicality,
            "cost_performance": score.cost_performance,
            "localization": score.localization,
            "safety": score.safety,
            "uniqueness": score.uniqueness,
        }
        overall_score = score.overall_score
        overall_grade = score.overall_grade or "N/A"
    else:
        scores = {
            "practicality": 0,
            "cost_performance": 0,
            "localization": 0,
            "safety": 0,
            "uniqueness": 0,
        }
        overall_score = 0
        overall_grade = "N/A"

    grade_color = GRADE_COLORS.get(overall_grade, "#9f9f9f")

    html = _render_embed_html(
        tool_name=tool.name,
        tool_name_jp=tool.name_jp,
        scores=scores,
        overall_score=overall_score,
        overall_grade=overall_grade,
        grade_color=grade_color,
        tool_slug=tool_slug,
    )

    return Response(
        content=html,
        media_type="text/html",
        headers={
            "X-Frame-Options": "ALLOWALL",
            "Content-Security-Policy": "frame-ancestors *",
            "Access-Control-Allow-Origin": "*",  # Intentional: embed widget is used on external sites
        },
    )


@router.get("/evaluated/{tool_slug}.svg")
async def evaluated_badge_svg(
    tool_slug: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    style: str = Query("flat", pattern="^(flat|for-the-badge)$"),
):
    """'Aixis Evaluated' SVG badge — indicates independent evaluation with published scores."""
    tool, score = await _get_tool_and_score(tool_slug, db)

    if not score:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No published scores found — badge available after evaluation",
        )

    svg = generate_evaluated_badge(
        tool_name=tool.name,
        overall_grade=score.overall_grade,
        overall_score=score.overall_score,
        style=style,
    )

    return Response(
        content=svg,
        media_type="image/svg+xml",
        headers={
            "Cache-Control": "public, max-age=3600",
            "Access-Control-Allow-Origin": "*",  # Intentional: badges are embedded on external sites
        },
    )


@router.get("/evaluated/{tool_slug}/snippet")
async def evaluated_badge_snippet(
    tool_slug: str,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    format: str = Query("html", pattern="^(html|markdown)$"),
):
    """Return an embed snippet (HTML or Markdown) for the Aixis Evaluated badge."""
    tool, score = await _get_tool_and_score(tool_slug, db)

    if not score:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No published scores found",
        )

    from ...config import settings
    base = settings.site_origin
    badge_url = f"{base}/api/public/v1/evaluated/{tool_slug}.svg"
    tool_url = f"{base}/tools/{tool_slug}"

    if format == "markdown":
        snippet = f"[![Aixis Evaluated]({badge_url})]({tool_url})"
    else:
        import html as html_mod
        badge_url_escaped = html_mod.escape(badge_url)
        tool_url_escaped = html_mod.escape(tool_url)
        snippet = (
            f'<a href="{tool_url_escaped}" target="_blank" rel="noopener">'
            f'<img src="{badge_url_escaped}" alt="Aixis Evaluated" />'
            f"</a>"
        )

    return {"snippet": snippet, "badge_url": badge_url, "tool_url": tool_url}


def _render_embed_html(
    tool_name: str,
    tool_name_jp: str,
    scores: dict[str, float],
    overall_score: float,
    overall_grade: str,
    grade_color: str,
    tool_slug: str,
) -> str:
    """Render the standalone embed widget HTML with inline SVG radar chart."""
    import html
    import math

    # Escape all user-controlled strings to prevent XSS
    tool_name = html.escape(tool_name)
    tool_name_jp = html.escape(tool_name_jp)
    overall_grade = html.escape(overall_grade)
    grade_color = html.escape(grade_color)
    tool_slug = html.escape(tool_slug)

    # Radar chart geometry
    cx, cy = 150, 150
    radius = 110
    axes = [
        ("practicality", "Practicality"),
        ("cost_performance", "Cost Perf."),
        ("localization", "Localization"),
        ("safety", "Safety"),
        ("uniqueness", "Uniqueness"),
    ]
    n = len(axes)
    angle_step = 2 * math.pi / n
    start_angle = -math.pi / 2  # Start from top

    # Grid lines (1-5 scale)
    grid_lines = ""
    for level in [1, 2, 3, 4, 5]:
        r = radius * level / 5
        points = []
        for i in range(n):
            angle = start_angle + i * angle_step
            x = cx + r * math.cos(angle)
            y = cy + r * math.sin(angle)
            points.append(f"{x:.1f},{y:.1f}")
        grid_lines += f'    <polygon points="{" ".join(points)}" fill="none" stroke="#e5e7eb" stroke-width="0.5" class="grid-line"/>\n'

    # Axis lines
    axis_lines = ""
    for i in range(n):
        angle = start_angle + i * angle_step
        x = cx + radius * math.cos(angle)
        y = cy + radius * math.sin(angle)
        axis_lines += f'    <line x1="{cx}" y1="{cy}" x2="{x:.1f}" y2="{y:.1f}" stroke="#e5e7eb" stroke-width="0.5" class="grid-line"/>\n'

    # Data polygon
    data_points = []
    for i, (key, _label) in enumerate(axes):
        val = scores.get(key, 0)
        r = radius * val / 5
        angle = start_angle + i * angle_step
        x = cx + r * math.cos(angle)
        y = cy + r * math.sin(angle)
        data_points.append(f"{x:.1f},{y:.1f}")

    # Labels
    labels_svg = ""
    for i, (key, label) in enumerate(axes):
        angle = start_angle + i * angle_step
        lx = cx + (radius + 25) * math.cos(angle)
        ly = cy + (radius + 25) * math.sin(angle)
        val = scores.get(key, 0)
        anchor = "middle"
        if math.cos(angle) < -0.1:
            anchor = "end"
        elif math.cos(angle) > 0.1:
            anchor = "start"
        labels_svg += f'    <text x="{lx:.1f}" y="{ly:.1f}" text-anchor="{anchor}" class="axis-label">{label}</text>\n'
        labels_svg += f'    <text x="{lx:.1f}" y="{ly + 14:.1f}" text-anchor="{anchor}" class="axis-score">{val:.1f}</text>\n'

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Aixis Score - {tool_name}</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans JP", sans-serif;
    background: #ffffff;
    color: #1f2937;
    display: flex;
    justify-content: center;
    align-items: center;
    min-height: 100vh;
    padding: 16px;
  }}
  @media (prefers-color-scheme: dark) {{
    body {{ background: #111827; color: #f3f4f6; }}
    .card {{ background: #1f2937; border-color: #374151; }}
    .grid-line {{ stroke: #374151 !important; }}
    .axis-label {{ fill: #d1d5db !important; }}
    .axis-score {{ fill: #9ca3af !important; }}
    .tool-name {{ color: #f9fafb; }}
    .subtitle {{ color: #9ca3af; }}
  }}
  .card {{
    background: #fff;
    border: 1px solid #e5e7eb;
    border-radius: 12px;
    padding: 24px;
    max-width: 400px;
    width: 100%;
    text-align: center;
  }}
  .tool-name {{
    font-size: 18px;
    font-weight: 700;
    margin-bottom: 4px;
    color: #111827;
  }}
  .subtitle {{
    font-size: 12px;
    color: #6b7280;
    margin-bottom: 16px;
  }}
  .grade-badge {{
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 48px;
    height: 48px;
    border-radius: 12px;
    font-size: 24px;
    font-weight: 800;
    color: #fff;
    margin-bottom: 4px;
  }}
  .overall-score {{
    font-size: 14px;
    color: #6b7280;
    margin-bottom: 16px;
  }}
  .radar-chart {{ margin: 0 auto; }}
  .axis-label {{ font-size: 11px; fill: #4b5563; font-weight: 500; }}
  .axis-score {{ font-size: 10px; fill: #9ca3af; }}
  .footer {{
    margin-top: 12px;
    font-size: 10px;
    color: #9ca3af;
  }}
  .footer a {{ color: #6366f1; text-decoration: none; }}
  .footer a:hover {{ text-decoration: underline; }}
</style>
</head>
<body>
<div class="card">
  <div class="tool-name">{tool_name}</div>
  <div class="subtitle">{tool_name_jp}</div>
  <div class="grade-badge" style="background:{grade_color}">{overall_grade}</div>
  <div class="overall-score">Overall: {overall_score:.2f} / 5.00</div>
  <svg class="radar-chart" viewBox="0 0 300 300" width="280" height="280">
{grid_lines}{axis_lines}    <polygon points="{" ".join(data_points)}" fill="{grade_color}" fill-opacity="0.25" stroke="{grade_color}" stroke-width="2"/>
{labels_svg}  </svg>
  <div class="footer">
    Powered by <a href="/tools/{tool_slug}" target="_blank">Aixis</a>
  </div>
</div>
</body>
</html>"""
