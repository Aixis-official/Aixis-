"""Public API v1 -- accessible via API keys."""

from fastapi import APIRouter

from .tools import router as tools_router
from .badges import router as badges_router

public_router = APIRouter()
public_router.include_router(tools_router, tags=["Public Tools & Scores"])
public_router.include_router(badges_router, tags=["Badges & Widgets"])
