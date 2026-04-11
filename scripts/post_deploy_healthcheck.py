#!/usr/bin/env python3
"""Post-deploy synthetic healthcheck for platform.aixis.jp.

Purpose:
    The 2026-04-12 GSC indexing outage showed that a single broken
    handler (``/sitemap.xml`` → HTTP 500) could sit in production for
    hours without being noticed, because there was no end-to-end smoke
    test running against the live host. This script is meant to run
    immediately after every deploy — locally, in CI, or from a
    scheduled uptime monitor — and fail loudly if any SEO-critical
    endpoint is broken.

What it checks:
    - ``/healthz``      — liveness probe, must return 200
    - ``/``             — landing page, must return 200 HTML with a <title>
    - ``/tools``        — dynamic DB-backed page, guards against query errors
    - ``/pricing``      — must mention "アドバイザリー監査" (naming guard)
    - ``/sitemap.xml``  — SEO-critical, must be 200 application/xml
    - ``/robots.txt``   — must reference the canonical sitemap URL

Exit codes:
    0  — all checks passed
    1  — at least one check failed (script prints the failing URL + reason)

Usage:
    python scripts/post_deploy_healthcheck.py
    python scripts/post_deploy_healthcheck.py --base-url https://platform.aixis.jp
"""
from __future__ import annotations

import argparse
import sys
import urllib.request
from dataclasses import dataclass
from urllib.error import HTTPError, URLError

DEFAULT_BASE_URL = "https://platform.aixis.jp"
TIMEOUT = 15  # seconds per request


@dataclass
class Check:
    path: str
    expected_status: int = 200
    required_substring: str | None = None
    required_content_type: str | None = None
    min_body_size: int = 0
    description: str = ""


CHECKS: list[Check] = [
    Check(
        path="/healthz",
        description="Liveness probe",
    ),
    Check(
        path="/",
        required_substring="<title",
        required_content_type="text/html",
        min_body_size=5000,
        description="Landing page renders",
    ),
    Check(
        path="/tools",
        required_substring="<title",
        required_content_type="text/html",
        min_body_size=3000,
        description="Tools listing (DB-backed) renders",
    ),
    Check(
        path="/pricing",
        required_substring="アドバイザリー監査",
        required_content_type="text/html",
        min_body_size=2000,
        description="Pricing page + Phase B naming guard",
    ),
    Check(
        path="/sitemap.xml",
        required_content_type="xml",
        min_body_size=500,
        description="Sitemap (SEO-critical, GSC regression guard)",
    ),
    Check(
        path="/robots.txt",
        required_substring="platform.aixis.jp/sitemap.xml",
        min_body_size=20,
        description="robots.txt references canonical sitemap",
    ),
]


def run_check(base_url: str, check: Check) -> tuple[bool, str]:
    url = base_url.rstrip("/") + check.path
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "aixis-post-deploy-healthcheck/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            status = resp.status
            content_type = resp.headers.get("content-type", "")
            body = resp.read()
    except HTTPError as e:
        return False, f"HTTP {e.code}: {e.reason}"
    except URLError as e:
        return False, f"URL error: {e.reason}"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"

    if status != check.expected_status:
        return False, f"expected status {check.expected_status}, got {status}"

    if check.required_content_type and check.required_content_type not in content_type.lower():
        return False, (
            f"expected content-type containing {check.required_content_type!r}, "
            f"got {content_type!r}"
        )

    if len(body) < check.min_body_size:
        return False, (
            f"body is suspiciously small: {len(body)} bytes "
            f"(expected >={check.min_body_size})"
        )

    if check.required_substring:
        try:
            text = body.decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            text = ""
        if check.required_substring not in text:
            return False, (
                f"body is missing required substring {check.required_substring!r}"
            )

    return True, "ok"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"Base URL to check (default: {DEFAULT_BASE_URL})",
    )
    args = parser.parse_args()

    print(f"post-deploy healthcheck: base={args.base_url}")
    print("-" * 72)

    failures: list[tuple[Check, str]] = []
    for check in CHECKS:
        ok, msg = run_check(args.base_url, check)
        marker = "PASS" if ok else "FAIL"
        label = check.description or check.path
        print(f"[{marker}] {check.path:18s} — {label}")
        if not ok:
            print(f"        reason: {msg}")
            failures.append((check, msg))

    print("-" * 72)
    if failures:
        print(f"FAILED: {len(failures)}/{len(CHECKS)} checks failed on {args.base_url}")
        for check, msg in failures:
            print(f"  - {check.path}: {msg}")
        return 1

    print(f"OK: all {len(CHECKS)} checks passed on {args.base_url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
