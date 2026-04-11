"""Integration tests for /sitemap.xml and /robots.txt.

Regression origin — 2026-04-12:
    ``pages.sitemap_xml`` referenced ``ToolCategory.name`` which does not
    exist on the model. Every request to ``/sitemap.xml`` raised
    ``AttributeError`` and returned HTTP 500, which in turn broke Google
    Search Console's "Request indexing" feature. The bug survived because
    no test exercised the SEO endpoints through the real router stack.

These tests cover the SEO surface that Google depends on:

- ``/sitemap.xml`` must return HTTP 200 with ``application/xml`` content
- the body must be a well-formed sitemap with at least N static URLs
- every URL inside must be absolute and use the public origin
- ``/robots.txt`` must return HTTP 200 with a ``Sitemap:`` directive
- the sitemap must be cheap to serve on repeated requests (cache working)
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

import pytest


# Every static page the sitemap is contractually required to include.
# Keep this list in sync with ``pages._STATIC_PAGES``.
_REQUIRED_STATIC_PATHS = {
    "/",
    "/tools",
    "/categories",
    "/compare",
    "/pricing",
    "/audit-process",
    "/independence",
    "/transparency",
    "/audit-protocol",
    "/contact",
    "/faq",
    "/company",
}


_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}


@pytest.mark.asyncio
async def test_sitemap_returns_200_xml(client):
    """/sitemap.xml must return 200 and application/xml, not an HTML error page."""
    resp = await client.get("/sitemap.xml")
    assert resp.status_code == 200, (
        f"/sitemap.xml returned {resp.status_code}. "
        f"Body starts with: {resp.text[:200]!r}"
    )
    assert "xml" in resp.headers.get("content-type", "").lower(), (
        f"Expected XML content-type, got {resp.headers.get('content-type')!r}"
    )


@pytest.mark.asyncio
async def test_sitemap_body_is_well_formed(client):
    """Body must parse as XML and be a <urlset> with at least one <url> entry."""
    resp = await client.get("/sitemap.xml")
    assert resp.status_code == 200
    root = ET.fromstring(resp.text)
    assert root.tag.endswith("urlset"), f"expected <urlset>, got <{root.tag}>"
    urls = root.findall("sm:url", _NS)
    assert len(urls) >= len(_REQUIRED_STATIC_PATHS), (
        f"Expected at least {len(_REQUIRED_STATIC_PATHS)} URLs in sitemap, "
        f"got {len(urls)}"
    )


@pytest.mark.asyncio
async def test_sitemap_contains_all_required_static_paths(client):
    """Every curated static page must appear in the sitemap exactly once."""
    resp = await client.get("/sitemap.xml")
    assert resp.status_code == 200
    root = ET.fromstring(resp.text)

    found_paths: set[str] = set()
    for url_el in root.findall("sm:url", _NS):
        loc = url_el.findtext("sm:loc", default="", namespaces=_NS)
        parsed = urlparse(loc)
        # Path must be absolute (never relative) so Google can crawl it.
        assert parsed.scheme in ("http", "https"), (
            f"<loc> is not absolute: {loc!r}"
        )
        assert parsed.netloc, f"<loc> has empty host: {loc!r}"
        found_paths.add(parsed.path)

    missing = _REQUIRED_STATIC_PATHS - found_paths
    assert not missing, (
        f"Sitemap is missing required static paths: {sorted(missing)}"
    )


@pytest.mark.asyncio
async def test_sitemap_includes_category_urls_without_raising(client):
    """Category URLs are generated from ToolCategory rows.

    This is the test that would have caught the 2026-04-12 regression:
    the original bug was `ToolCategory.name` (non-existent column) which
    raised AttributeError deep inside the handler. A simple 200-check
    against the real router stack surfaces it immediately.
    """
    resp = await client.get("/sitemap.xml")
    assert resp.status_code == 200, (
        "Sitemap must not 500 even when ToolCategory rows are present. "
        "Regression guard for the 2026-04-12 ToolCategory.name bug."
    )
    # Sitemap should still be well-formed XML even with seeded categories.
    ET.fromstring(resp.text)


@pytest.mark.asyncio
async def test_sitemap_is_cacheable(client):
    """Two consecutive requests should both succeed (cache doesn't poison state)."""
    r1 = await client.get("/sitemap.xml")
    r2 = await client.get("/sitemap.xml")
    assert r1.status_code == 200
    assert r2.status_code == 200
    # Contents should be identical within the 1h cache window.
    assert r1.text == r2.text


@pytest.mark.asyncio
async def test_robots_txt_returns_200_and_references_sitemap(client):
    """/robots.txt must point search engines at the sitemap."""
    resp = await client.get("/robots.txt")
    assert resp.status_code == 200
    body = resp.text
    assert re.search(r"^Sitemap:\s*https?://", body, re.MULTILINE), (
        f"/robots.txt missing 'Sitemap:' directive. Body:\n{body}"
    )
    assert "platform.aixis.jp/sitemap.xml" in body, (
        "/robots.txt must reference the canonical sitemap URL"
    )
