"""Aixis AI Audit Platform - FastAPI Application."""
import logging
import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
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
        return response


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
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="日本初の独立系AI監査プラットフォーム",
        lifespan=lifespan,
    )

    # Security headers middleware
    app.add_middleware(SecurityHeadersMiddleware)

    # CORS middleware — restrict origins in production
    allowed_origins = ["https://aixis.jp", "https://www.aixis.jp"]
    if settings.debug:
        allowed_origins.append("http://localhost:8000")
        allowed_origins.append("http://127.0.0.1:8000")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["*"],
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
    @app.exception_handler(404)
    async def not_found_handler(request: Request, exc: HTTPException):
        if request.url.path.startswith("/api/"):
            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=404,
                content={"detail": getattr(exc, "detail", "Not found")},
            )
        return HTMLResponse(
            content=f"""<!DOCTYPE html>
<html lang="ja"><head><meta charset="UTF-8"><title>404 - Aixis</title>
<style>body{{font-family:Inter,'Noto Sans JP',sans-serif;display:flex;justify-content:center;align-items:center;min-height:100vh;margin:0;background:#f9fafb;color:#111827}}
.box{{text-align:center}}.box h1{{font-size:4rem;font-weight:800;color:#1a365d;margin:0}}.box p{{color:#6b7280;margin:1rem 0}}
a{{color:#1a365d;text-decoration:none;font-weight:600}}a:hover{{text-decoration:underline}}</style></head>
<body><div class="box"><h1>404</h1><p>お探しのページは見つかりませんでした。</p><a href="/">ホームに戻る</a></div></body></html>""",
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
            content="""<!DOCTYPE html>
<html lang="ja"><head><meta charset="UTF-8"><title>500 - Aixis</title>
<style>body{font-family:Inter,'Noto Sans JP',sans-serif;display:flex;justify-content:center;align-items:center;min-height:100vh;margin:0;background:#f9fafb;color:#111827}
.box{text-align:center}.box h1{font-size:4rem;font-weight:800;color:#e53e3e;margin:0}.box p{color:#6b7280;margin:1rem 0}
a{color:#1a365d;text-decoration:none;font-weight:600}a:hover{text-decoration:underline}</style></head>
<body><div class="box"><h1>500</h1><p>サーバーエラーが発生しました。しばらくしてからもう一度お試しください。</p><a href="/">ホームに戻る</a></div></body></html>""",
            status_code=500,
        )

    return app


app = create_app()


def main():
    uvicorn.run(
        "aixis_web.app:app", host="0.0.0.0", port=8000, reload=settings.debug
    )
