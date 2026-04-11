"""Shared test fixtures.

This conftest intentionally sets environment variables at module-import
time so that the aixis_web package (which reads ``settings`` at import)
uses a test-safe configuration: an ephemeral SQLite database, debug
mode, and tmp-dir mounts for ``/screenshots`` and ``/uploads``. Tests
that simply parse templates or exercise pure helpers continue to work;
the integration tests additionally spin up the full FastAPI app through
``httpx.ASGITransport``.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

# ---------------------------------------------------------------------------
# IMPORTANT: set env vars before any aixis_web module is imported. pytest
# loads this file before collecting test modules, so the imports inside the
# test modules will see these values.
# ---------------------------------------------------------------------------
_TEST_TMP = Path(tempfile.mkdtemp(prefix="aixis_test_"))
(_TEST_TMP / "screenshots").mkdir(exist_ok=True)
(_TEST_TMP / "uploads").mkdir(exist_ok=True)
(_TEST_TMP / "db").mkdir(exist_ok=True)

os.environ.setdefault("DEBUG", "true")
os.environ.setdefault("SECRET_KEY", "test_secret_key_that_is_long_enough_for_tests_ok")
os.environ.setdefault("ADMIN_PASSWORD", "test_admin_password_not_default")
os.environ.setdefault(
    "DATABASE_URL", f"sqlite+aiosqlite:///{_TEST_TMP / 'db' / 'test.db'}"
)
os.environ.setdefault("SCREENSHOTS_DIR", str(_TEST_TMP / "screenshots"))
os.environ.setdefault("UPLOADS_DIR", str(_TEST_TMP / "uploads"))

import pytest  # noqa: E402


@pytest.fixture
def config_dir() -> Path:
    return Path(__file__).parent.parent / "config"


@pytest.fixture
def patterns_dir(config_dir) -> Path:
    return config_dir / "patterns"


@pytest.fixture
def targets_dir(config_dir) -> Path:
    return config_dir / "targets"


# ---------------------------------------------------------------------------
# Integration-test fixtures (FastAPI app + in-memory DB)
# ---------------------------------------------------------------------------
#
# These are used by ``test_sitemap_integration.py`` and
# ``test_public_pages_smoke.py`` to exercise the real router stack through
# ``httpx.ASGITransport`` — which would have caught the 2026-04-12 sitemap
# regression (ToolCategory.name → ToolCategory.name_en) in CI instead of
# production.


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="session")
async def _initialized_app():
    """Build the FastAPI app with schema created and minimal seed data.

    Scope is ``session`` because init_db is slow and the tests only read.
    """
    from aixis_web.app import app  # noqa: PLC0415
    from aixis_web.db.base import init_db  # noqa: PLC0415

    await init_db()
    return app


@pytest.fixture
async def client(_initialized_app):
    """Async HTTP client for SSR + sitemap integration tests."""
    from httpx import ASGITransport, AsyncClient  # noqa: PLC0415

    transport = ASGITransport(app=_initialized_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c
