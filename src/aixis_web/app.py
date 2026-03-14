"""Aixis AI Audit Platform - FastAPI Application."""
import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from .config import settings
from .db.base import init_db
from .api.v1.router import api_router

BASE_DIR = Path(__file__).parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await init_db()
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

    return app


app = create_app()


def main():
    uvicorn.run(
        "aixis_web.app:app", host="0.0.0.0", port=8000, reload=settings.debug
    )
