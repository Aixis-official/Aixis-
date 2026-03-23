"""Aixis AI Audit Platform - FastAPI Application."""
import logging
import secrets
import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from .config import settings
from .db.base import init_db
from .api.v1.router import api_router

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent


# ---------------------------------------------------------------------------
# Unified security middleware (headers + CSRF in single BaseHTTPMiddleware
# to avoid Starlette's known issue with stacking multiple BaseHTTPMiddleware)
# ---------------------------------------------------------------------------

_CSRF_COOKIE = "aixis_csrf"
_CSRF_HEADER = "X-CSRF-Token"
_CSRF_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
# Paths exempt from CSRF (API-key auth, health, login — no session to hijack)
_CSRF_EXEMPT_PREFIXES = (
    "/api/public/",
    "/api/v1/health",
    "/api/v1/auth/login",
    "/api/v1/auth/forgot-password",
    "/api/v1/auth/reset-password",
    "/api/v1/clients/invite/",  # Public invite completion (no session to hijack)
    "/api/v1/extension/",  # Chrome extension uses API key auth, no CSRF needed
)


class SecurityMiddleware(BaseHTTPMiddleware):
    """Combined security headers + CSRF protection middleware.

    Security headers: X-Frame-Options, CSP, HSTS, etc.
    CSRF: Double-submit cookie — sets `aixis_csrf` cookie, validates
    X-CSRF-Token header on state-changing requests. Bearer-token and
    API-key-authenticated requests are exempt.
    """

    async def dispatch(self, request: Request, call_next):
        # --- CSRF check (before calling route) ---
        if request.method not in _CSRF_SAFE_METHODS:
            path = request.url.path
            is_exempt = any(path.startswith(p) for p in _CSRF_EXEMPT_PREFIXES)
            auth_header = request.headers.get("Authorization", "")
            has_bearer = auth_header.startswith("Bearer ")

            if not is_exempt and not has_bearer:
                cookie_token = request.cookies.get(_CSRF_COOKIE)
                header_token = request.headers.get(_CSRF_HEADER)
                if not cookie_token or not header_token or cookie_token != header_token:
                    return JSONResponse(
                        status_code=403,
                        content={"detail": "CSRF token missing or invalid"},
                    )

        # --- Call the actual route ---
        response: Response = await call_next(request)

        # --- Security headers ---
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["X-Permitted-Cross-Domain-Policies"] = "none"
        if not settings.debug:
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains; preload"
            )
        csp_directives = [
            "default-src 'self'",
            "script-src 'self' 'unsafe-inline' https://www.googletagmanager.com https://cdn.tailwindcss.com https://cdn.plot.ly https://unpkg.com",
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.tailwindcss.com",
            "font-src 'self' https://fonts.gstatic.com",
            "img-src 'self' data: https:",
            "connect-src 'self' https://www.google-analytics.com https://www.googletagmanager.com",
            "frame-ancestors 'none'",
            "base-uri 'self'",
            "form-action 'self'",
        ]
        response.headers["Content-Security-Policy"] = "; ".join(csp_directives)

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

    # 5. Restore persisted settings from PostgreSQL
    try:
        from sqlalchemy import select as _select
        from .db.models.app_setting import AppSetting
        async with async_session() as session:
            result = await session.execute(_select(AppSetting))
            for row in result.scalars():
                import os
                os.environ[row.key] = row.value
                # Also update runtime settings object
                if row.key == "AIXIS_ANTHROPIC_API_KEY" and row.value:
                    settings.anthropic_api_key = row.value
                    logger.info("Restored API key from database")
                elif row.key == "AIXIS_AI_BUDGET_MAX_COST_JPY" and row.value:
                    settings.ai_budget_max_cost_jpy = int(row.value)
                    logger.info("Restored cost limit: %s JPY", row.value)
                elif row.key == "AIXIS_AI_BUDGET_MAX_CALLS" and row.value:
                    settings.ai_budget_max_calls = int(row.value)
                    logger.info("Restored call limit: %s", row.value)
    except Exception as e:
        logger.warning("Failed to restore settings from DB: %s", e)

    # 6. Start background services
    from .services.scheduler_service import start_scheduler, stop_scheduler
    start_scheduler()
    from .services.gdrive_export_service import start_gdrive_export, stop_gdrive_export
    start_gdrive_export()
    from .services.trial_service import start_trial_checker, stop_trial_checker
    start_trial_checker()

    yield

    # Shutdown
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

    # Mount static files
    static_dir = BASE_DIR / "static"
    static_dir.mkdir(exist_ok=True)
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
<title>{code} - Aixis</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;800&family=Noto+Sans+JP:wght@400;600;700&display=swap" rel="stylesheet">
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:Inter,'Noto Sans JP',sans-serif;display:flex;flex-direction:column;justify-content:center;align-items:center;min-height:100vh;background:#fafafa;color:#111827}}
.error-container{{text-align:center;max-width:480px;padding:2rem}}
.error-code{{font-size:6rem;font-weight:800;color:#e2e8f0;line-height:1;letter-spacing:-0.04em}}
.error-title{{font-size:1.125rem;font-weight:700;color:#1e293b;margin-top:1rem}}
.error-message{{font-size:0.875rem;color:#64748b;margin-top:0.75rem;line-height:1.6}}
.error-actions{{margin-top:2rem;display:flex;gap:1rem;justify-content:center;flex-wrap:wrap}}
.error-actions a{{display:inline-flex;align-items:center;gap:0.5rem;padding:0.625rem 1.5rem;font-size:0.875rem;font-weight:600;text-decoration:none;transition:all 0.2s}}
.btn-primary{{background:#0f172a;color:#fff}}
.btn-primary:hover{{background:#1e293b}}
.btn-secondary{{border:1px solid #d1d5db;color:#374151}}
.btn-secondary:hover{{background:#f9fafb}}
.section-line{{width:24px;height:2px;background:#cbd5e1;margin:0 auto 1rem}}
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
</div>
</body></html>"""

    @app.exception_handler(404)
    async def not_found_handler(request: Request, exc: HTTPException):
        if request.url.path.startswith("/api/"):
            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=404,
                content={"detail": getattr(exc, "detail", "Not found")},
            )
        return HTMLResponse(
            content=_error_html(
                404,
                "ページが見つかりません",
                "お探しのページは移動または削除された可能性があります。URLをご確認のうえ、もう一度お試しください。",
            ),
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
            return JSONResponse(
                status_code=500,
                content={"detail": f"Internal server error: {type(exc).__name__}: {exc}"},
            )
        # Include error details in HTML for debugging (safe: admin-only pages)
        import html as _html
        error_detail = _html.escape(f"{type(exc).__name__}: {exc}")
        return HTMLResponse(
            content=_error_html(
                500,
                "サーバーエラー",
                f"サーバーで問題が発生しました。<br><code style='font-size:0.75rem;color:#ef4444;word-break:break-all'>{error_detail}</code>",
            ),
            status_code=500,
        )

    return app


app = create_app()


def main():
    uvicorn.run(
        "aixis_web.app:app", host="0.0.0.0", port=8000, reload=settings.debug
    )
