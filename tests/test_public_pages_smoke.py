"""Smoke tests for every public SSR page.

Rationale:
    The 2026-04-12 GSC incident revealed that there was no integration
    coverage on the public SSR endpoints. A single parametrized test
    that simply asks "does each page return 200 and contain a <title>"
    is enough to catch the vast majority of production regressions
    (AttributeError in a handler, Jinja KeyError, broken import, etc.)
    without needing a full browser-based e2e suite.

The test hits every page through the real FastAPI router stack, so it
exercises middleware, dependency injection, Jinja rendering, and any
DB queries the handler performs. Anything that raises becomes a 500
and the assertion fires immediately.
"""

from __future__ import annotations

import pytest


# (url, min_body_size_bytes, expected_substring)
# expected_substring is a cheap "did SSR really produce content" signal.
# Keep this list in sync with pages.py — every new public route should
# land here in the same PR.
_PUBLIC_PAGES: list[tuple[str, int, str]] = [
    ("/", 5000, "<title"),
    ("/tools", 3000, "<title"),
    ("/categories", 2000, "<title"),
    ("/compare", 2000, "<title"),
    ("/pricing", 2000, "アドバイザリー監査"),  # naming regression guard
    ("/audit-process", 2000, "<title"),
    ("/audit-protocol", 2000, "<title"),
    ("/independence", 2000, "アドバイザリー監査"),  # naming regression guard
    ("/transparency", 2000, "アドバイザリー監査"),  # naming regression guard
    ("/score-changelog", 2000, "<title"),
    ("/faq", 2000, "<title"),
    ("/contact", 2000, "<title"),
]


# Pages that return HTTP 301 redirects rather than 200 HTML.
# These are legal/policy pages that were consolidated on aixis.jp.
_REDIRECT_PAGES: list[tuple[str, str]] = [
    ("/terms", "https://aixis.jp/terms"),
    ("/tokushoho", "https://aixis.jp/tokushoho"),
    ("/accessibility", "https://aixis.jp/accessibility"),
    ("/company", "https://aixis.jp/company"),
    ("/whitepaper", "/static/pdf/"),  # prefix match — filename may version-bump
]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,min_size,expected_substring",
    _PUBLIC_PAGES,
    ids=[p[0] for p in _PUBLIC_PAGES],
)
async def test_public_page_renders_successfully(
    client, path: str, min_size: int, expected_substring: str
):
    """Every public SSR page must return 200 with a non-trivial HTML body."""
    resp = await client.get(path)
    assert resp.status_code == 200, (
        f"GET {path} returned {resp.status_code}. "
        f"Body starts with: {resp.text[:300]!r}"
    )
    assert "text/html" in resp.headers.get("content-type", "").lower(), (
        f"{path} did not return HTML. content-type="
        f"{resp.headers.get('content-type')!r}"
    )
    body = resp.text
    assert len(body) >= min_size, (
        f"{path} body is suspiciously small: {len(body)} bytes "
        f"(expected >={min_size})"
    )
    assert expected_substring in body, (
        f"{path} body is missing expected marker {expected_substring!r}. "
        f"This often means the template silently failed to render key content."
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,expected_location",
    _REDIRECT_PAGES,
    ids=[p[0] for p in _REDIRECT_PAGES],
)
async def test_redirect_pages(client, path: str, expected_location: str):
    """Legal/policy redirects must resolve to their canonical host."""
    resp = await client.get(path, follow_redirects=False)
    assert resp.status_code in (301, 302, 307, 308), (
        f"GET {path} expected a redirect, got {resp.status_code}"
    )
    location = resp.headers.get("location", "")
    assert expected_location in location, (
        f"GET {path} redirected to {location!r}, "
        f"expected {expected_location!r}"
    )


@pytest.mark.asyncio
async def test_health_endpoint(client):
    """/healthz must respond cheaply so Railway + uptime monitors can poll it."""
    resp = await client.get("/healthz")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_404_page_returns_html_not_traceback(client):
    """Unknown routes should serve a branded 404 page, not a raw traceback."""
    resp = await client.get("/this-page-does-not-exist-12345")
    assert resp.status_code == 404
    assert "text/html" in resp.headers.get("content-type", "").lower()
    # Must not leak stack traces.
    assert "Traceback" not in resp.text
    assert "Exception" not in resp.text


@pytest.mark.asyncio
async def test_language_switcher_ui_is_hidden(client):
    """Phase D-1 EN switcher is temporarily disabled — UI should not appear.

    We keep the backend routes alive (/en, /en/pricing, /en/audit-process)
    but the JA/EN toggle should not be visible on any JA page until the
    English localization phase ships.
    """
    resp = await client.get("/")
    assert resp.status_code == 200
    # The switcher button has a distinctive aria-label.
    assert 'aria-label="View this page in English"' not in resp.text
    # And its URL pattern should not be linked anywhere above-the-fold.
    # (Sitemap still includes /en — that's intentional for the future.)


@pytest.mark.asyncio
async def test_en_pages_are_noindex(client):
    """English pages are behind noindex until the full English phase ships."""
    resp = await client.get("/en")
    assert resp.status_code == 200
    assert "noindex" in resp.text.lower(), (
        "/en must include <meta name=robots content=noindex,nofollow> "
        "until the full English localization phase is ready"
    )


@pytest.mark.asyncio
async def test_public_pages_have_canonical_link(client):
    """Every public page must declare a canonical URL so Google dedupes correctly."""
    for path, _size, _marker in _PUBLIC_PAGES:
        resp = await client.get(path)
        assert resp.status_code == 200
        assert 'rel="canonical"' in resp.text, (
            f"{path} is missing <link rel=canonical>"
        )


@pytest.mark.asyncio
async def test_public_pages_have_ogp_image(client):
    """Every public page must declare og:image for social/search previews."""
    for path, _size, _marker in _PUBLIC_PAGES:
        resp = await client.get(path)
        assert resp.status_code == 200
        assert 'property="og:image"' in resp.text, (
            f"{path} is missing <meta property=og:image>"
        )


@pytest.mark.asyncio
async def test_cache_control_on_public_html(client):
    """Public HTML pages must send Cache-Control so Googlebot can revalidate."""
    resp = await client.get("/")
    assert resp.status_code == 200
    cc = resp.headers.get("cache-control", "")
    assert "max-age" in cc, (
        f"Public HTML must set Cache-Control max-age=. Got: {cc!r}"
    )
    assert "must-revalidate" in cc, (
        "Public HTML must set must-revalidate so Googlebot re-fetches on "
        f"indexing requests. Got: {cc!r}"
    )
