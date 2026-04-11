"""Phase C-2: template-level smoke tests.

These tests catch render-time errors that the type checker can't see — missing
keys, format-string failures, undefined-attribute access. They are deliberately
narrow: they don't spin up the full FastAPI app or hit a database, they just
render the public templates against a stubbed `base.html` that exposes the
same blocks the real one does.

The motivating regression is the 2026-04-11 hotfix where
`tool_detail.html` referenced `ssr.score.overall_score` / `ssr.score.overall_grade`
even though `pages.py` only ships `overall` / `grade` in the SSR dict — that
caused `'%.1f'|format(undefined)` to raise inside Jinja and turned every
`/tools/{slug}` page into a 500 in production.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader, StrictUndefined

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = REPO_ROOT / "src" / "aixis_web" / "templates"


# A minimal stub of base.html that defines every block tool_detail.html (and
# its peers) extend. We render the real public template against this stub so
# that the test does not depend on base.html's actual contents — which carry
# their own asset references and would otherwise dominate the test surface.
_STUB_BASE = """\
<!doctype html><html><head>
{% block preload %}{% endblock %}
{% block meta_robots %}{% endblock %}
{% block google_verification %}{% endblock %}
{% block meta_description %}{% endblock %}
{% block meta_keywords %}{% endblock %}
{% block page_title_tag %}{% endblock %}
{% block canonical %}{% endblock %}
{% block ogp %}{% endblock %}
{% block structured_data %}{% endblock %}
{% block head %}{% endblock %}
</head><body>
{% block content %}{% endblock %}
{% block scripts %}{% endblock %}
</body></html>
"""


def _make_env() -> Environment:
    return Environment(
        loader=ChoiceLoader(
            [
                DictLoader({"base.html": _STUB_BASE}),
                FileSystemLoader(str(TEMPLATES_DIR)),
            ]
        ),
        autoescape=True,
        # StrictUndefined would surface key typos at render time, but the
        # public templates intentionally rely on `default()` filters in many
        # spots, so we keep the default Undefined behaviour and rely on the
        # specific assertions below.
        undefined=StrictUndefined,
    )


def _ssr_with_score() -> dict:
    """Mirror exactly what `pages.tool_detail_page` builds when a tool exists.

    Keep this in sync with `src/aixis_web/pages.py::tool_detail_page`.
    """
    return {
        "name": "テストツール",
        "vendor": "テスト株式会社",
        "description": "テスト用のAIツール説明",
        "logo_url": "https://example.com/logo.png",
        "category": "汎用AI",
        "pricing_model": "paid",
        "executive_summary": "テストサマリー",
        "pros": ["長所1", "長所2"],
        "cons": ["短所1"],
        "features": ["機能1"],
        "url": "https://example.com",
        "score": {
            "overall": 4.2,
            "grade": "A",
            "practicality": 4.0,
            "cost_performance": 4.5,
            "localization": 4.1,
            "safety": 4.3,
            "uniqueness": 4.0,
            "version": "v1",
            "published_at": "2026年04月",
        },
    }


def _tool_data() -> dict:
    return {
        "name": "Test Tool",
        "name_jp": "テストツール",
        "vendor": "テスト株式会社",
        "description_jp": "テスト用のAIツール説明",
        "logo_url": "https://example.com/logo.png",
        "url": "https://example.com",
        "category_id": "cat-1",
    }


def _base_ctx(**overrides):
    ctx = {
        "request": None,
        "user": None,
        "title": "テストツール",
        "active_page": "tools",
        "csp_nonce": "test-nonce",
        "seo_description": "テスト用説明",
        "seo_keywords": ["AI", "テスト"],
        "slug": "test-tool",
        "tool_data": None,
        "ssr": None,
    }
    ctx.update(overrides)
    return ctx


def test_tool_detail_renders_with_score():
    """Regression: rendering with ssr.score.overall set must not raise.

    The original bug raised ``TypeError: must be real number, not Undefined``
    inside ``'%.1f'|format(ssr.score.overall_score)``. This test fails fast
    if anyone reintroduces a key mismatch between pages.py and the template.
    """
    env = _make_env()
    tpl = env.get_template("public/tool_detail.html")
    out = tpl.render(_base_ctx(tool_data=_tool_data(), ssr=_ssr_with_score()))
    # Sanity: the rendered ImageObject JSON-LD should contain the formatted score.
    assert "総合4.2/5.0" in out
    assert "グレードA" in out


def test_tool_detail_renders_without_score():
    """The 404-style render path (tool not found) must also work."""
    env = _make_env()
    tpl = env.get_template("public/tool_detail.html")
    # No tool_data, no ssr — same shape as the 404 branch in pages.py.
    out = tpl.render(_base_ctx())
    assert "ツール詳細" in out or "test-tool" in out


def test_tool_detail_renders_with_score_overall_none():
    """Edge case: ssr.score exists but overall is None (no published score yet)."""
    env = _make_env()
    tpl = env.get_template("public/tool_detail.html")
    ssr = _ssr_with_score()
    ssr["score"]["overall"] = None
    ssr["score"]["grade"] = None
    out = tpl.render(_base_ctx(tool_data=_tool_data(), ssr=ssr))
    # When overall is None, the ImageObject block is skipped entirely.
    assert "ImageObject" not in out


@pytest.mark.parametrize(
    "template_name",
    [
        "public/landing.html",
        "public/tools.html",
        "public/tool_detail.html",
        "public/categories.html",
        "public/compare.html",
        "public/about.html",
        "public/contact.html",
        "public/pricing.html",
        "public/audit_protocol.html",
        "public/score_changelog.html",
        "public/faq.html",
    ],
)
def test_public_template_parses(template_name):
    """All public templates must at least parse without Jinja syntax errors."""
    env = _make_env()
    tpl_path = TEMPLATES_DIR / template_name
    if not tpl_path.exists():
        pytest.skip(f"{template_name} does not exist in this checkout")
    src = tpl_path.read_text(encoding="utf-8")
    # parse() raises TemplateSyntaxError on bad syntax.
    env.parse(src)
