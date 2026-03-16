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
# Security headers middleware
# ---------------------------------------------------------------------------

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to all responses."""

    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        # Prevent clickjacking
        response.headers["X-Frame-Options"] = "DENY"
        # Prevent MIME-type sniffing
        response.headers["X-Content-Type-Options"] = "nosniff"
        # XSS protection (legacy browsers)
        response.headers["X-XSS-Protection"] = "1; mode=block"
        # Referrer policy
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        # Permissions policy
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        # HSTS — force HTTPS for 1 year (with subdomains + preload)
        if not settings.debug:
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains; preload"
            )
        # Prevent cross-domain policy loading (Flash/PDF)
        response.headers["X-Permitted-Cross-Domain-Policies"] = "none"
        # Content Security Policy
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
        return response


# ---------------------------------------------------------------------------
# CSRF protection middleware (double-submit cookie pattern)
# ---------------------------------------------------------------------------

_CSRF_COOKIE = "aixis_csrf"
_CSRF_HEADER = "X-CSRF-Token"
_CSRF_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
# Paths exempt from CSRF (API-key auth, health, login — no session to hijack)
_CSRF_EXEMPT_PREFIXES = ("/api/public/", "/api/v1/health", "/api/v1/auth/login")


class CSRFMiddleware(BaseHTTPMiddleware):
    """Double-submit cookie CSRF protection for state-changing requests.

    - On every response, sets an `aixis_csrf` cookie with a random token.
    - On POST/PUT/PATCH/DELETE, validates that the `X-CSRF-Token` header
      matches the cookie value. API-key-authenticated endpoints and
      Bearer-token-only requests are exempt.
    """

    async def dispatch(self, request: Request, call_next):
        # Always allow safe methods
        if request.method in _CSRF_SAFE_METHODS:
            response = await call_next(request)
            self._ensure_csrf_cookie(request, response)
            return response

        # Exempt paths (public API with X-API-Key, health checks)
        path = request.url.path
        if any(path.startswith(p) for p in _CSRF_EXEMPT_PREFIXES):
            return await call_next(request)

        # Exempt requests with Bearer token (API clients, not browser forms)
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            return await call_next(request)

        # Validate CSRF: cookie token must match header token
        cookie_token = request.cookies.get(_CSRF_COOKIE)
        header_token = request.headers.get(_CSRF_HEADER)

        if not cookie_token or not header_token or cookie_token != header_token:
            return JSONResponse(
                status_code=403,
                content={"detail": "CSRF token missing or invalid"},
            )

        response = await call_next(request)
        self._ensure_csrf_cookie(request, response)
        return response

    @staticmethod
    def _ensure_csrf_cookie(request: Request, response: Response):
        """Set CSRF cookie if not already present."""
        if _CSRF_COOKIE not in request.cookies:
            token = secrets.token_urlsafe(32)
            response.set_cookie(
                key=_CSRF_COOKIE,
                value=token,
                max_age=86400,  # 24h
                path="/",
                httponly=False,  # JS must read this to set header
                samesite="lax",
                secure=not settings.debug,
            )


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await init_db()
    # Seed master data (industry tags, use case tags, regulatory frameworks)
    from .db.base import async_session
    from .services.seed_service import seed_all
    async with async_session() as session:
        await seed_all(session)
    # Start background audit scheduler
    from .services.scheduler_service import start_scheduler, stop_scheduler
    start_scheduler()
    yield
    # Shutdown
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
    )

    # Security headers middleware
    app.add_middleware(SecurityHeadersMiddleware)

    # CSRF protection middleware
    app.add_middleware(CSRFMiddleware)

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
        logger.exception("Internal server error on %s", request.url.path)
        if request.url.path.startswith("/api/"):
            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=500,
                content={"detail": "Internal server error"},
            )
        return HTMLResponse(
            content=_error_html(
                500,
                "サーバーエラー",
                "申し訳ございません。サーバーで問題が発生しました。しばらくしてからもう一度お試しいただくか、問題が続く場合はお問い合わせください。",
            ),
            status_code=500,
        )

    return app


app = create_app()


def main():
    uvicorn.run(
        "aixis_web.app:app", host="0.0.0.0", port=8000, reload=settings.debug
    )
