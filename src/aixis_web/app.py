"""Aixis AI Audit Platform - FastAPI Application."""
import asyncio
import logging
import os
import secrets
import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from .config import settings
from .db.base import init_db
from .api.v1.router import api_router
from .observability import init_sentry

# Phase C-1: Sentry must be initialised BEFORE FastAPI is constructed so that
# the Starlette/FastAPI integrations can hook into the ASGI lifecycle. The
# call is a no-op when SENTRY_DSN is unset.
init_sentry()

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent


# ---------------------------------------------------------------------------
# Unified security middleware (headers + CSRF in single BaseHTTPMiddleware
# to avoid Starlette's known issue with stacking multiple BaseHTTPMiddleware)
# ---------------------------------------------------------------------------

# In production we use the `__Host-` cookie prefix for defence-in-depth:
# browsers only accept the cookie when it is (1) sent with `Secure`, (2) has
# `Path=/`, and (3) has no `Domain` attribute — which prevents sub-domain
# injection of an attacker-controlled token. In local development the app
# typically runs over plain HTTP on non-localhost hosts, so we fall back to
# the legacy name there.
_CSRF_COOKIE = "aixis_csrf" if settings.debug else "__Host-aixis_csrf"
_CSRF_HEADER = "X-CSRF-Token"
_CSRF_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _umami_origin() -> str:
    """Extract the scheme+host of the configured Umami instance, or ''.

    Used to allow-list the analytics host in CSP script-src / connect-src
    only when both UMAMI_URL and UMAMI_WEBSITE_ID are set.
    """
    if not (settings.umami_url and settings.umami_website_id):
        return ""
    from urllib.parse import urlsplit
    parts = urlsplit(settings.umami_url)
    if not parts.scheme or not parts.netloc:
        return ""
    return f"{parts.scheme}://{parts.netloc}"


_UMAMI_ORIGIN = _umami_origin()

# CSP: SHA-256 hashes of inline event handler attribute values found in
# public-facing templates. Required when using `'unsafe-hashes'` to allow
# specific inline handlers without `'unsafe-inline'`. Regenerate this list
# with scripts/csp_handler_hashes.py whenever public-template handlers change.
_PUBLIC_HANDLER_HASHES = (
    "'sha256-fN9jpWA/xW93Y41eWCy53d8smzYAlCWm/8fXBUMsmKg='"  # aixisLogout()
    " 'sha256-nyp45uB5eoqcjEacmV90ifrUqc7MMLJjYfZEx/4KGog='"  # aixisLogout(); closeMobileDrawer();
    " 'sha256-f/yQ0eBgE0fSe6eEHInB2BG60LG6DchDnZBKg6WcMbc='"  # contact form error reset
    " 'sha256-h6B3t6yWoa79lyTqNlifWCze4U+j2KkhDs1kSi+u0rY='"  # loadReports()
    " 'sha256-9gOBGqEQINNDuds+tkXbNzih6klbz+KeCyxEj4KRLeM='"  # location.reload()
    " 'sha256-8yxwzYUtemhCVZc2qptUsoPTKDfWOtRce20XRYw4JD4='"  # setPricingTab('monthly')
    " 'sha256-UxVC6nkGyq+M56T2/f5VDDG8hHGZey7bcFyI7p30qmw='"  # setPricingTab('yearly')
    " 'sha256-eIRcdTTKX99KNOtEPHEwMnz2LD/Uv/CacqAtxvTviaw='"  # this.classList.add('hero-bg-loaded')
    " 'sha256-oe4Mglfmu7V2vPjC+RomULN/Dmtj2gdBL26S0T8SWsc='"  # mouseout (rgba .04/.1)
    " 'sha256-chi+Z2cJjzQA+g+61ypkIrFza3ZXzeHAsxWPERmsHBo='"  # mouseover (rgba .08/.18)
    " 'sha256-1u/HNenE0qxoHyh3hP2+b7LWiaHDk8y1P79VtwjkhsU='"  # mouseout bg .1
    " 'sha256-l09P5LtYc8dQ2iWAH/YKETPpv/9yZ9NOeYqWq4WZDPk='"  # mouseover bg .16
    " 'sha256-Lbiqh8Ix+0Jzqj7mUD8hhtCAkMQNBhZI5vLI8gl2yLE='"  # mouseout border .25
    " 'sha256-QNIKHoiH6dIEvfWK/Z9UJsbko4lKxCgnZe99EIvmE4Q='"  # mouseover border .6
)
# Paths exempt from CSRF (API-key auth, health, login — no session to hijack)
_CSRF_EXEMPT_PREFIXES = (
    "/api/public/",
    "/api/v1/health",
    "/api/v1/auth/login",
    "/api/v1/auth/register",              # self-registration (no session yet)
    "/api/v1/auth/resend-verification",   # re-send verification email (no session yet)
    "/api/v1/auth/forgot-password",
    "/api/v1/auth/reset-password",
    "/api/v1/auth/unsubscribe",           # one-click unsubscribe from email link (HMAC-signed token)
    "/api/v1/clients/invite/",  # Public invite completion (no session to hijack)
    "/api/v1/extension/",  # Chrome extension uses API key auth, no CSRF needed
    # Note: logout requires CSRF token (state-changing operation)
)


# ---------------------------------------------------------------------------
# Request timeout middleware — prevents any single request from hanging
# ---------------------------------------------------------------------------
_REQUEST_TIMEOUT = 60  # seconds (generous for LLM-related endpoints)


class RequestTimeoutMiddleware(BaseHTTPMiddleware):
    """Abort requests that exceed the timeout to prevent thread pool starvation."""

    async def dispatch(self, request: Request, call_next):
        try:
            return await asyncio.wait_for(
                call_next(request), timeout=_REQUEST_TIMEOUT
            )
        except asyncio.TimeoutError:
            logger.warning("Request timeout (%ds): %s %s", _REQUEST_TIMEOUT, request.method, request.url.path)
            return JSONResponse(
                status_code=504,
                content={"detail": "リクエストがタイムアウトしました。しばらくしてからもう一度お試しください。"},
            )


class SecurityMiddleware(BaseHTTPMiddleware):
    """Combined security headers + CSRF protection middleware.

    Security headers: X-Frame-Options, CSP, HSTS, etc.
    CSRF: Double-submit cookie — sets `__Host-aixis_csrf` cookie (prod) or
    `aixis_csrf` (dev), validates X-CSRF-Token header on state-changing
    requests. Bearer-token and API-key-authenticated requests are exempt.
    """

    async def dispatch(self, request: Request, call_next):
        # --- Per-request CSP nonce (must be set BEFORE the route runs so
        # that templates can render `nonce="{{ csp_nonce }}"` attributes
        # matching the eventual Content-Security-Policy header) ---
        request.state.csp_nonce = secrets.token_urlsafe(16)

        # --- Anonymous session ID (Phase 3 lead-gen) ---
        # Visitors get a long-lived `aixis_sid` cookie so anonymous browsing
        # activity can be reattached to a user_id at registration time. Routes
        # read `request.state.session_id`; the cookie is (re)issued below on
        # the response.
        existing_sid = request.cookies.get("aixis_sid")
        if existing_sid and 16 <= len(existing_sid) <= 128:
            request.state.session_id = existing_sid
            request.state.session_id_is_new = False
        else:
            request.state.session_id = secrets.token_urlsafe(32)
            request.state.session_id_is_new = True

        # --- CSRF check (before calling route) ---
        if request.method not in _CSRF_SAFE_METHODS:
            path = request.url.path
            is_exempt = any(path.startswith(p) for p in _CSRF_EXEMPT_PREFIXES)
            auth_header = request.headers.get("Authorization", "")
            has_bearer = auth_header.startswith("Bearer ")

            if not is_exempt and not has_bearer:
                cookie_token = request.cookies.get(_CSRF_COOKIE)
                header_token = request.headers.get(_CSRF_HEADER)
                if not cookie_token or not header_token or not secrets.compare_digest(cookie_token, header_token):
                    return JSONResponse(
                        status_code=403,
                        content={"detail": "CSRF token missing or invalid"},
                    )

        # --- Call the actual route ---
        response: Response = await call_next(request)

        # --- Security headers ---
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-XSS-Protection"] = "0"
        response.headers["Permissions-Policy"] = (
            "accelerometer=(), ambient-light-sensor=(), autoplay=(), battery=(), "
            "camera=(), cross-origin-isolated=(), display-capture=(), "
            "document-domain=(), encrypted-media=(), fullscreen=(), "
            "geolocation=(), gyroscope=(), keyboard-map=(), magnetometer=(), "
            "microphone=(), midi=(), navigation-override=(), payment=(), "
            "picture-in-picture=(), publickey-credentials-get=(), "
            "screen-wake-lock=(), sync-xhr=(), usb=(), web-share=(), "
            "xr-spatial-tracking=()"
        )
        response.headers["X-Permitted-Cross-Domain-Policies"] = "none"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        response.headers["X-DNS-Prefetch-Control"] = "off"
        if not settings.debug:
            response.headers["Strict-Transport-Security"] = (
                "max-age=63072000; includeSubDomains; preload"
            )
        # --- CSP: relax for admin pages (Tailwind Play CDN needs unsafe-eval) ---
        admin_path = request.url.path.startswith("/admin") or request.url.path.startswith("/dashboard")
        if admin_path:
            csp_directives = [
                "default-src 'self'",
                "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.tailwindcss.com https://cdn.plot.ly",
                "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
                "font-src 'self' https://fonts.gstatic.com",
                "img-src 'self' data: https:",
                "connect-src 'self'",
                "frame-ancestors 'none'",
                "base-uri 'self'",
                "form-action 'self'",
                "object-src 'none'",
            ]
        else:
            # Public site CSP: nonce-based, no `'unsafe-inline'` for scripts.
            # Inline event handlers are allow-listed via `'unsafe-hashes'`
            # plus their SHA-256 hashes (see _PUBLIC_HANDLER_HASHES). Inline
            # `style="..."` attributes are still common, so style-src keeps
            # `'unsafe-inline'` for now (lower XSS impact than scripts).
            nonce = request.state.csp_nonce
            extra_script = f" {_UMAMI_ORIGIN}" if _UMAMI_ORIGIN else ""
            extra_connect = f" {_UMAMI_ORIGIN}" if _UMAMI_ORIGIN else ""
            # Cloudflare Turnstile (opt-in via env). Only allow-listed when the
            # site key is configured — keeps the prod CSP minimal otherwise.
            turnstile_enabled = bool(
                getattr(settings, "turnstile_site_key", "")
                and getattr(settings, "turnstile_secret_key", "")
            )
            extra_script_turnstile = " https://challenges.cloudflare.com" if turnstile_enabled else ""
            extra_frame_turnstile = "frame-src https://challenges.cloudflare.com" if turnstile_enabled else "frame-src 'self'"
            csp_directives = [
                "default-src 'self'",
                (
                    f"script-src 'self' 'nonce-{nonce}' 'unsafe-hashes' "
                    f"{_PUBLIC_HANDLER_HASHES} "
                    f"https://cdn.plot.ly{extra_script}{extra_script_turnstile}"
                ),
                "style-src 'self' 'unsafe-inline'",
                "font-src 'self'",
                "img-src 'self' data: https:",
                f"connect-src 'self'{extra_connect}",
                extra_frame_turnstile,
                "frame-ancestors 'none'",
                "base-uri 'self'",
                "form-action 'self'",
                "object-src 'none'",
            ]
        response.headers["Content-Security-Policy"] = "; ".join(csp_directives)

        # --- Referrer-Policy: stricter for sensitive pages ---
        path = request.url.path
        if path in (
            "/login",
            "/register",
            "/reset-password",
            "/forgot-password",
            "/invite",
            "/verify-email-success",
            "/verify-email-failed",
            "/register-pending",
        ):
            response.headers["Referrer-Policy"] = "no-referrer"
        else:
            response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        # --- Cache-Control ---
        if path.startswith("/static/"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"  # 1 year (fingerprinted via ?v=)
        elif path.startswith(("/screenshots/", "/uploads/")):
            response.headers["Cache-Control"] = "public, max-age=86400"  # 1 day
        elif path.startswith(("/admin", "/dashboard", "/mypage")):
            # Authenticated HTML pages — never cache to ensure fresh templates after deploys
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
            response.headers["Pragma"] = "no-cache"
        elif path.startswith("/api/") and not path.startswith("/api/public/"):
            # Prevent browser caching of authenticated API responses
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
            response.headers["Pragma"] = "no-cache"
        elif not path.startswith(("/login", "/logout", "/reset-password", "/forgot-password", "/invite", "/api")):
            # Public HTML pages — short CDN cache with must-revalidate so Googlebot
            # always re-validates freshness on re-indexing requests, but human users
            # still benefit from 1-hour edge caching.
            response.headers.setdefault(
                "Cache-Control", "public, max-age=3600, must-revalidate"
            )

        # --- CSRF cookie (set on every response if not already present) ---
        if _CSRF_COOKIE not in request.cookies:
            response.set_cookie(
                key=_CSRF_COOKIE,
                value=secrets.token_urlsafe(32),
                max_age=86400,
                path="/",
                httponly=False,  # JS must read this to set header
                samesite="lax",
                secure=not settings.debug,
            )

        # --- Anonymous session cookie (aixis_sid) ---
        # 90-day TTL is long enough to span a typical research cycle. HttpOnly
        # so page scripts cannot read it; same-site=lax keeps it flowing on
        # normal cross-site navigation while blocking CSRF use.
        if getattr(request.state, "session_id_is_new", False):
            response.set_cookie(
                key="aixis_sid",
                value=request.state.session_id,
                max_age=90 * 86400,
                path="/",
                httponly=True,
                samesite="lax",
                secure=not settings.debug,
            )
            # A response that mints a fresh session ID must not be
            # served from a shared cache — otherwise the next visitor
            # inherits the cookie and we cross-contaminate anonymous
            # activity. Subsequent visits (with the cookie already set)
            # fall through to the normal public cache policy.
            response.headers["Cache-Control"] = "private, no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"

        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup — data safety first
    # 1. Create pre-migration backup (best-effort)
    try:
        from .services.backup_service import create_backup
        backup_result = create_backup(reason="pre_deploy")
        if "error" not in backup_result:
            logger.info("Pre-deploy backup: %s", backup_result.get("filename"))
        else:
            logger.warning("Pre-deploy backup skipped: %s", backup_result.get("error"))
    except Exception as e:
        logger.warning("Pre-deploy backup failed (non-critical): %s", e)

    # 2. Initialize database (create tables + add columns — never drops)
    await init_db()

    # 3. Data integrity check — log user/client counts for monitoring
    try:
        from .db.base import async_session as _session_factory
        from sqlalchemy import func, select, text
        from .db.models.user import User
        async with _session_factory() as session:
            user_count = (await session.execute(select(func.count()).select_from(User))).scalar() or 0
            client_count = (await session.execute(
                select(func.count()).select_from(User).where(User.role == "client")
            )).scalar() or 0
            logger.info(
                "DATA INTEGRITY CHECK — users: %d, clients: %d",
                user_count, client_count,
            )
    except Exception as e:
        logger.warning("Data integrity check failed: %s", e)

    # 4. Seed master data (industry tags, use case tags, regulatory frameworks)
    from .db.base import async_session
    from .services.seed_service import seed_all
    async with async_session() as session:
        await seed_all(session)

    # 5. Restore persisted settings from PostgreSQL (whitelisted keys only)
    _RESTORE_WHITELIST = frozenset({
        "AIXIS_ANTHROPIC_API_KEY",
        "AIXIS_AI_BUDGET_MAX_COST_JPY",
        "AIXIS_AI_BUDGET_MAX_CALLS",
        "AIXIS_GDRIVE_CREDENTIALS_JSON",
        "AIXIS_GDRIVE_FOLDER_ID",
        "AIXIS_GDRIVE_EXPORT_INTERVAL_HOURS",
        "AIXIS_GDRIVE_ENABLED",
    })
    try:
        from sqlalchemy import select as _select
        from .db.models.app_setting import AppSetting
        async with async_session() as session:
            result = await session.execute(_select(AppSetting))
            for row in result.scalars():
                import os
                if row.key not in _RESTORE_WHITELIST:
                    logger.warning("Skipping non-whitelisted setting: %s", row.key)
                    continue
                os.environ[row.key] = row.value
                # Also update runtime settings object
                if row.key == "AIXIS_ANTHROPIC_API_KEY" and row.value:
                    settings.anthropic_api_key = row.value
                    logger.info("Restored API key from database")
                elif row.key == "AIXIS_AI_BUDGET_MAX_COST_JPY" and row.value:
                    try:
                        settings.ai_budget_max_cost_jpy = int(row.value)
                        logger.info("Restored cost limit: %s JPY", row.value)
                    except (ValueError, TypeError):
                        logger.warning("Invalid ai_budget_max_cost_jpy value: %r", row.value)
                elif row.key == "AIXIS_AI_BUDGET_MAX_CALLS" and row.value:
                    try:
                        settings.ai_budget_max_calls = int(row.value)
                        logger.info("Restored call limit: %s", row.value)
                    except (ValueError, TypeError):
                        logger.warning("Invalid ai_budget_max_calls value: %r", row.value)
                elif row.key == "AIXIS_GDRIVE_CREDENTIALS_JSON" and row.value:
                    settings.gdrive_credentials_json = row.value
                    logger.info("Restored GDrive credentials from database")
                elif row.key == "AIXIS_GDRIVE_FOLDER_ID" and row.value:
                    settings.gdrive_folder_id = row.value
                    logger.info("Restored GDrive folder ID: %s", row.value[:10] + "...")
                elif row.key == "AIXIS_GDRIVE_EXPORT_INTERVAL_HOURS" and row.value:
                    try:
                        settings.gdrive_export_interval_hours = int(row.value)
                        logger.info("Restored GDrive interval: %sh", row.value)
                    except (ValueError, TypeError):
                        logger.warning("Invalid gdrive_export_interval_hours value: %r", row.value)
                elif row.key == "AIXIS_GDRIVE_ENABLED" and row.value:
                    settings.gdrive_enabled = row.value.lower() in ("true", "1", "yes")
                    logger.info("Restored GDrive enabled: %s", settings.gdrive_enabled)
    except Exception as e:
        logger.warning("Failed to restore settings from DB: %s", e)

    # 6. Start background services
    from .services.scheduler_service import start_scheduler, stop_scheduler
    start_scheduler()
    from .services.gdrive_export_service import start_gdrive_export, stop_gdrive_export
    start_gdrive_export()
    from .services.trial_service import start_trial_checker, stop_trial_checker
    start_trial_checker()

    # 7. Start automatic backup scheduler (hourly + daily + weekly)
    from .services.backup_service import start_backup_scheduler, stop_backup_scheduler
    start_backup_scheduler()

    yield

    # Shutdown
    stop_backup_scheduler()
    stop_trial_checker()
    stop_gdrive_export()
    stop_scheduler()


def create_app() -> FastAPI:
    # Disable OpenAPI docs in production to prevent info disclosure
    docs_url = "/docs" if settings.debug else None
    redoc_url = "/redoc" if settings.debug else None
    openapi_url = "/openapi.json" if settings.debug else None

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="日本初の独立系AI監査プラットフォーム",
        lifespan=lifespan,
        docs_url=docs_url,
        redoc_url=redoc_url,
        openapi_url=openapi_url,
        redirect_slashes=False,
    )

    # GZip compression (text assets: HTML, CSS, JS, JSON, SVG)
    app.add_middleware(GZipMiddleware, minimum_size=500)

    # Request timeout middleware (outermost — catches hanging requests)
    app.add_middleware(RequestTimeoutMiddleware)

    # Security middleware (headers + CSRF — single BaseHTTPMiddleware)
    app.add_middleware(SecurityMiddleware)

    # CORS middleware — restrict origins in production
    allowed_origins = [
        "https://aixis.jp",
        "https://www.aixis.jp",
        "https://platform.aixis.jp",
    ]
    if settings.debug:
        allowed_origins.append("http://localhost:8000")
        allowed_origins.append("http://127.0.0.1:8000")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=[
            "Authorization", "Content-Type", "X-API-Key",
            "X-Requested-With", "X-CSRF-Token",
        ],
    )

    # Mount screenshots from persistent volume (before /static to avoid shadowing)
    screenshots_dir = Path(settings.screenshots_dir)
    screenshots_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/screenshots", StaticFiles(directory=str(screenshots_dir)), name="screenshots")

    # Mount uploaded files from persistent volume (before /static)
    uploads_dir = Path(os.environ.get("UPLOADS_DIR", "/data/uploads"))
    uploads_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/uploads", StaticFiles(directory=str(uploads_dir)), name="uploads")

    # Mount static files (app assets — bundled in container image, OK to be ephemeral)
    static_dir = BASE_DIR / "static"
    static_dir.mkdir(exist_ok=True)
    # Mount /.well-known (security.txt etc) before /static
    well_known_dir = static_dir / ".well-known"
    if well_known_dir.exists():
        app.mount("/.well-known", StaticFiles(directory=str(well_known_dir)), name="well-known")
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # Include API router
    app.include_router(api_router, prefix="/api/v1")

    # Include public API router
    from .api.public_v1 import public_router

    app.include_router(public_router, prefix="/api/public/v1")

    # Include page routes (SSR)
    from .pages import page_router

    app.include_router(page_router)

    # Custom error handlers for HTML pages (API paths still return JSON)
    def _error_html(code: int, title: str, message: str) -> str:
        return f"""<!DOCTYPE html>
<html lang="ja"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title} - Aixis AI監査プラットフォーム</title>
<meta name="robots" content="noindex, follow">
<link rel="canonical" href="https://platform.aixis.jp/">
<link rel="preload" href="/static/fonts/NotoSerifJP-Regular.woff2" as="font" type="font/woff2" crossorigin>
<link rel="stylesheet" href="/static/css/style.min.css">
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:'Noto Serif JP',serif;display:flex;flex-direction:column;justify-content:center;align-items:center;min-height:100vh;background:#fafafa;color:#111827}}
.error-container{{text-align:center;max-width:520px;padding:2rem}}
.error-code{{font-size:6rem;font-weight:800;color:#e2e8f0;line-height:1;letter-spacing:-0.04em}}
.error-title{{font-size:1.125rem;font-weight:700;color:#1e293b;margin-top:1rem}}
.error-message{{font-size:0.875rem;color:#64748b;margin-top:0.75rem;line-height:1.6}}
.error-actions{{margin-top:2rem;display:flex;gap:1rem;justify-content:center;flex-wrap:wrap}}
.error-actions a{{display:inline-flex;align-items:center;gap:0.5rem;padding:0.625rem 1.5rem;font-size:0.875rem;font-weight:600;text-decoration:none;border-radius:4px;transition:all 0.2s}}
.btn-primary{{background:#0f172a;color:#fff}}
.btn-primary:hover{{background:#1e293b}}
.btn-secondary{{border:1px solid #d1d5db;color:#374151}}
.btn-secondary:hover{{background:#f9fafb}}
.section-line{{width:24px;height:2px;background:#cbd5e1;margin:0 auto 1rem}}
.nav-links{{margin-top:2.5rem;padding-top:1.5rem;border-top:1px solid #e5e7eb}}
.nav-links p{{font-size:0.75rem;color:#9ca3af;margin-bottom:0.75rem}}
.nav-links ul{{list-style:none;display:flex;gap:1.25rem;justify-content:center;flex-wrap:wrap}}
.nav-links a{{font-size:0.8125rem;color:#6366f1;text-decoration:none;transition:color 0.2s}}
.nav-links a:hover{{color:#4338ca;text-decoration:underline}}
</style></head>
<body>
<div class="error-container">
<div class="error-code">{code}</div>
<div class="section-line"></div>
<h1 class="error-title">{title}</h1>
<p class="error-message">{message}</p>
<div class="error-actions">
<a href="/" class="btn-primary">ホームに戻る</a>
<a href="/contact" class="btn-secondary">お問い合わせ</a>
</div>
<nav class="nav-links">
<p>お探しのページが見つからない場合</p>
<ul>
<li><a href="/tools">AIツール一覧</a></li>
<li><a href="/categories">カテゴリ</a></li>
<li><a href="/compare">ツール比較</a></li>
<li><a href="/pricing">料金プラン</a></li>
</ul>
</nav>
</div>
</body></html>"""

    _404_audit_html = """<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta name="robots" content="noindex">
    <title>監査失敗 - 404 | Aixis</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="preload" href="/static/fonts/NotoSerifJP-Regular.woff2" as="font" type="font/woff2" crossorigin>
    <link rel="stylesheet" href="/static/css/style.min.css">
    <style>
        @keyframes stamp-appear {
            0% { transform: scale(2) rotate(-15deg); opacity: 0; }
            60% { transform: scale(0.95) rotate(3deg); opacity: 0.9; }
            100% { transform: scale(1) rotate(-3deg); opacity: 1; }
        }
        @keyframes scan-line {
            0% { top: 0; }
            100% { top: 100%; }
        }
        .stamp-animation {
            animation: stamp-appear 0.5s cubic-bezier(0.34, 1.56, 0.64, 1) forwards;
            animation-delay: 0.3s;
            opacity: 0;
        }
        .scan-effect::after {
            content: '';
            position: absolute;
            left: 0;
            width: 100%;
            height: 2px;
            background: linear-gradient(90deg, transparent, rgba(59,130,246,0.3), transparent);
            animation: scan-line 2s ease-in-out infinite;
        }
    </style>
</head>
<body class="bg-gray-50 min-h-screen flex items-center justify-center p-4" style="font-family: 'Noto Serif JP', serif;">
    <div class="max-w-lg w-full">
        <div class="bg-white border border-gray-200 shadow-sm relative scan-effect overflow-hidden">
            <div class="border-b border-gray-200 px-8 py-5 flex items-center justify-between">
                <div>
                    <div class="text-[10px] font-semibold text-gray-400 uppercase tracking-widest font-sans">Aixis Audit Report</div>
                    <div class="text-xs text-gray-400 mt-0.5">Report ID: 404-NOT-FOUND</div>
                </div>
                <a href="/" class="text-sm font-bold text-gray-800 hover:text-blue-600 transition-colors" style="font-family: 'Noto Serif JP', serif;">Aixis</a>
            </div>
            <div class="px-8 py-10 text-center relative">
                <div class="text-[120px] font-black text-gray-100 leading-none select-none" style="font-family: 'Noto Serif JP', serif;">404</div>
                <div class="absolute inset-0 flex items-center justify-center">
                    <div class="stamp-animation border-4 border-red-400 rounded-sm px-6 py-3 transform -rotate-3">
                        <div class="text-red-400 font-black text-2xl tracking-wider" style="font-family: 'Noto Serif JP', serif;">NOT FOUND</div>
                    </div>
                </div>
                <h1 class="mt-6 text-lg font-bold text-gray-900">ページの監査に失敗しました</h1>
                <p class="mt-2 text-sm text-gray-500 leading-relaxed">
                    リクエストされたURLの監査を試みましたが、<br>該当するデータが見つかりませんでした。
                </p>
            </div>
            <div class="border-t border-gray-100 px-8 py-4">
                <table class="w-full text-xs">
                    <tr>
                        <td class="py-1.5 text-gray-400 w-1/3">ステータス</td>
                        <td class="py-1.5 text-red-500 font-semibold">404 — 未検出</td>
                    </tr>
                    <tr>
                        <td class="py-1.5 text-gray-400">監査対象</td>
                        <td class="py-1.5 text-gray-600 font-mono text-[11px]" id="requested-url"></td>
                    </tr>
                    <tr>
                        <td class="py-1.5 text-gray-400">監査日時</td>
                        <td class="py-1.5 text-gray-600" id="audit-datetime"></td>
                    </tr>
                    <tr>
                        <td class="py-1.5 text-gray-400">判定</td>
                        <td class="py-1.5"><span class="inline-flex items-center justify-center w-6 h-6 rounded-full text-white text-[10px] font-black" style="background: #ef4444;">D</span> <span class="text-gray-500 ml-1">要注意</span></td>
                    </tr>
                </table>
            </div>
            <div class="border-t border-gray-200 px-8 py-5 flex flex-wrap gap-3 justify-center">
                <a href="/" class="inline-flex items-center gap-2 px-5 py-2.5 text-sm font-bold bg-gray-900 text-white hover:bg-gray-800 rounded-md transition-colors">ホームに戻る</a>
                <a href="/tools" class="inline-flex items-center gap-2 px-5 py-2.5 text-sm font-bold border border-gray-300 text-gray-700 hover:bg-gray-50 rounded-md transition-colors">監査データベース</a>
                <a href="/contact" class="inline-flex items-center gap-2 px-5 py-2.5 text-sm font-bold border border-gray-300 text-gray-700 hover:bg-gray-50 rounded-md transition-colors">お問い合わせ</a>
            </div>
        </div>
        <p class="text-center text-xs text-gray-400 mt-4">&copy; Aixis. Independent AI Audit Platform.</p>
    </div>
    <script>
        document.getElementById('requested-url').textContent = window.location.pathname;
        document.getElementById('audit-datetime').textContent = new Date().toLocaleString('ja-JP');
    </script>
</body>
</html>"""

    @app.exception_handler(404)
    async def not_found_handler(request: Request, exc: HTTPException):
        if request.url.path.startswith("/api/"):
            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=404,
                content={"detail": getattr(exc, "detail", "Not found")},
            )
        return HTMLResponse(
            content=_404_audit_html,
            status_code=404,
        )

    @app.exception_handler(500)
    async def server_error_handler(request: Request, exc: Exception):
        import traceback
        tb_str = traceback.format_exception(type(exc), exc, exc.__traceback__)
        tb_text = "".join(tb_str)
        logger.error("500 on %s: %s\n%s", request.url.path, exc, tb_text)
        if request.url.path.startswith("/api/"):
            from fastapi.responses import JSONResponse
            if settings.debug:
                return JSONResponse(
                    status_code=500,
                    content={"detail": f"Internal server error: {type(exc).__name__}: {exc}"},
                )
            return JSONResponse(
                status_code=500,
                content={"detail": "Internal server error"},
            )
        if settings.debug:
            import html as _html
            error_detail = _html.escape(f"{type(exc).__name__}: {exc}")
            tb_escaped = _html.escape(tb_text)
            return HTMLResponse(
                content=_error_html(
                    500,
                    "サーバーエラー",
                    f"サーバーで問題が発生しました。<br><code style='font-size:0.75rem;color:#ef4444;word-break:break-all'>{error_detail}</code>"
                    f"<br><pre style='font-size:0.65rem;color:#666;text-align:left;max-height:400px;overflow:auto;margin-top:1rem;padding:1rem;background:#f1f5f9;border-radius:0.5rem'>{tb_escaped}</pre>",
                ),
                status_code=500,
            )
        return HTMLResponse(
            content=_error_html(
                500,
                "サーバーエラー",
                "サーバーで問題が発生しました。しばらくしてからもう一度お試しください。",
            ),
            status_code=500,
        )

    return app


app = create_app()


def main():
    uvicorn.run(
        "aixis_web.app:app", host="0.0.0.0", port=8000, reload=settings.debug
    )
