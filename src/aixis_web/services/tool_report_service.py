"""Per-tool audit report PDF generator.

Renders a tool's public audit data — 5-axis scores, grade, executive summary,
pros/cons, and risk-governance highlights — into a branded PDF suitable for
distribution in稟議 / board packs.

Gated behind login (registered role or above) to prevent anonymous scraping
into PDFs, but otherwise the data matches the public tool detail page.
"""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..config import settings
from ..db.models.risk_governance import ToolRiskGovernance
from ..db.models.tool import Tool

logger = logging.getLogger(__name__)

_TEMPLATE_NAME = "pdf/tool_report.html"


async def generate_tool_report_pdf(db: AsyncSession, slug: str) -> tuple[bytes, str] | None:
    """Generate a PDF audit report for the tool identified by ``slug``.

    Returns (pdf_bytes, filename) or None if the tool isn't found / not public.
    """
    result = await db.execute(
        select(Tool)
        .where(Tool.slug == slug, Tool.is_public == True)  # noqa: E712
        .options(
            selectinload(Tool.scores),
            selectinload(Tool.category),
            selectinload(Tool.risk_governance),
        )
    )
    tool = result.scalar_one_or_none()
    if not tool:
        return None

    latest_score = tool.scores[0] if tool.scores else None

    # ToolRiskGovernance is a relationship; pick the latest by version.
    rg_list = tool.risk_governance or []
    latest_rg: ToolRiskGovernance | None = (
        sorted(rg_list, key=lambda r: r.version or 0, reverse=True)[0] if rg_list else None
    )

    ctx = {
        "tool": tool,
        "score": latest_score,
        "rg": latest_rg,
        "published_at": latest_score.published_at if latest_score else None,
        "category_name": tool.category.name_jp if tool.category else "",
        "site_origin": settings.site_origin,
    }

    html = _render_template(ctx)

    # WeasyPrint may not be importable at module-import time (optional dep),
    # and it's heavy — keep the import inside the call.
    try:
        from weasyprint import HTML
    except ImportError:
        logger.error("weasyprint is not installed — cannot render tool report PDF")
        return None

    try:
        base_url = str(Path(__file__).resolve().parents[1])
        pdf_bytes = HTML(string=html, base_url=base_url).write_pdf()
    except Exception as exc:
        logger.exception("WeasyPrint failed rendering tool report for %s: %s", slug, exc)
        return None

    filename = f"aixis-audit-{tool.slug}.pdf"
    return pdf_bytes, filename


def _render_template(ctx: dict) -> str:
    """Render the tool_report Jinja template to an HTML string."""
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    templates_dir = Path(__file__).resolve().parents[1] / "templates"
    env = Environment(
        loader=FileSystemLoader(str(templates_dir)),
        autoescape=select_autoescape(["html", "xml"]),
    )
    env.filters["grade_color"] = _grade_color
    env.filters["score_bar"] = _score_bar
    template = env.get_template(_TEMPLATE_NAME)
    return template.render(**ctx)


def _grade_color(grade: str | None) -> str:
    if not grade:
        return "#94a3b8"
    grade = grade.strip().upper()
    return {
        "S": "#10b981",   # emerald-500
        "A": "#22c55e",   # green-500
        "B": "#3b82f6",   # blue-500
        "C": "#f59e0b",   # amber-500
        "D": "#f97316",   # orange-500
        "F": "#ef4444",   # red-500
    }.get(grade, "#94a3b8")


def _score_bar(score: float | None, max_value: float = 5.0) -> int:
    """Return an integer 0-100 percentage for rendering a horizontal bar."""
    if score is None:
        return 0
    try:
        pct = int(round(float(score) / max_value * 100))
        return max(0, min(100, pct))
    except (TypeError, ValueError):
        return 0
