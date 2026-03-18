"""Tool CRUD endpoints."""
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)
from typing import Annotated
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.base import get_db
from ...db.models.tool import Tool, ToolCategory, ToolTargetConfig
from ...db.models.user import User
from ...schemas.tool import (
    CategoryResponse,
    TargetConfigCreate,
    TargetConfigResponse,
    ToolCreate,
    ToolListResponse,
    ToolResponse,
    ToolUpdate,
)
from ..deps import get_current_user, require_admin, require_analyst

router = APIRouter()


def _auto_favicon_url(url: str | None) -> str | None:
    """Generate a favicon URL from a tool's URL using Google's favicon service."""
    if not url:
        return None
    try:
        parsed = urlparse(url)
        domain = parsed.netloc or parsed.path.split("/")[0]
        if not domain:
            return None
        # Google's public favicon service — reliable and fast
        return f"https://www.google.com/s2/favicons?domain={domain}&sz=64"
    except Exception:
        return None


@router.get("/categories", response_model=list[CategoryResponse])
async def list_categories(db: Annotated[AsyncSession, Depends(get_db)]):
    """List all tool categories."""
    result = await db.execute(
        select(ToolCategory).order_by(ToolCategory.sort_order)
    )
    return result.scalars().all()


@router.get("", response_model=ToolListResponse)
async def list_tools(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User | None, Depends(get_current_user)],
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    category_id: str | None = Query(None, max_length=50),
    q: str | None = Query(None, min_length=1, max_length=100),
    all: bool = Query(False, description="Admin: include non-public tools"),
):
    """List tools with pagination, category filter, and search.

    Public access returns only public, active tools.
    Pass all=true with analyst+ auth to see all tools.
    """
    if all and (not user or user.role not in ("admin", "analyst", "auditor")):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="アナリスト以上の権限が必要です",
        )
    if all:
        query = select(Tool)
        count_query = select(func.count()).select_from(Tool)
    else:
        query = select(Tool).where(Tool.is_public.is_(True), Tool.is_active.is_(True))
        count_query = select(func.count()).select_from(Tool).where(
            Tool.is_public.is_(True), Tool.is_active.is_(True)
        )

    if category_id:
        query = query.where(Tool.category_id == category_id)
        count_query = count_query.where(Tool.category_id == category_id)

    if q:
        pattern = f"%{q}%"
        query = query.where(
            Tool.name.ilike(pattern)
            | Tool.name_jp.ilike(pattern)
            | Tool.description.ilike(pattern)
            | Tool.description_jp.ilike(pattern)
        )
        count_query = count_query.where(
            Tool.name.ilike(pattern)
            | Tool.name_jp.ilike(pattern)
            | Tool.description.ilike(pattern)
            | Tool.description_jp.ilike(pattern)
        )

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    offset = (page - 1) * page_size
    query = query.order_by(Tool.name_jp).offset(offset).limit(page_size)
    result = await db.execute(query)
    items = result.scalars().all()

    return ToolListResponse(items=items, total=total, page=page, page_size=page_size)


@router.get("/{slug}", response_model=ToolResponse)
async def get_tool(slug: str, db: Annotated[AsyncSession, Depends(get_db)]):
    """Get tool detail by slug."""
    result = await db.execute(select(Tool).where(Tool.slug == slug))
    tool = result.scalar_one_or_none()
    if not tool:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="ツールが見つかりません"
        )
    # Resolve category name
    resp = ToolResponse.model_validate(tool)
    if tool.category_id:
        cat_result = await db.execute(
            select(ToolCategory.name_jp).where(ToolCategory.id == tool.category_id)
        )
        resp.category_name_jp = cat_result.scalar_one_or_none()
    return resp


@router.post("", response_model=ToolResponse, status_code=status.HTTP_201_CREATED)
async def create_tool(
    body: ToolCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    _admin: Annotated[User, Depends(require_admin)],
):
    """Create a new tool (admin only)."""
    # Check slug uniqueness
    existing = await db.execute(select(Tool).where(Tool.slug == body.slug))
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="このスラッグは既に使用されています",
        )

    tool_data = body.model_dump()
    # Auto-set favicon if no logo_url provided and URL exists
    if not tool_data.get("logo_url") and tool_data.get("url"):
        tool_data["logo_url"] = _auto_favicon_url(tool_data["url"])
    tool = Tool(**tool_data)
    db.add(tool)
    await db.commit()
    await db.refresh(tool)

    # Emit tool.created webhook event (best-effort)
    try:
        from ...services.webhook_service import emit_event
        await emit_event("tool.created", {
            "event": "tool.created",
            "tool_id": tool.id,
            "tool_slug": tool.slug,
            "tool_name": tool.name,
        }, db)
        await db.commit()
    except Exception:
        logger.warning("Failed to emit tool.created webhook for %s", tool.slug, exc_info=True)

    return tool


@router.put("/{slug}", response_model=ToolResponse)
async def update_tool(
    slug: str,
    body: ToolUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    _admin: Annotated[User, Depends(require_admin)],
):
    """Update an existing tool (admin only)."""
    result = await db.execute(select(Tool).where(Tool.slug == slug))
    tool = result.scalar_one_or_none()
    if not tool:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="ツールが見つかりません"
        )

    update_data = body.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(tool, key, value)

    # Auto-set favicon if URL was updated and no logo_url is set
    if "url" in update_data and not tool.logo_url and tool.url:
        tool.logo_url = _auto_favicon_url(tool.url)

    await db.commit()
    await db.refresh(tool)
    return tool


@router.post("/auto-favicon", status_code=status.HTTP_200_OK)
async def auto_set_favicons(
    db: Annotated[AsyncSession, Depends(get_db)],
    _admin: Annotated[User, Depends(require_admin)],
):
    """Auto-set favicon for all tools that have a URL but no logo_url."""
    result = await db.execute(
        select(Tool).where(Tool.url.isnot(None), Tool.url != "")
    )
    tools = result.scalars().all()
    updated = 0
    for tool in tools:
        if not tool.logo_url and tool.url:
            favicon = _auto_favicon_url(tool.url)
            if favicon:
                tool.logo_url = favicon
                updated += 1
    await db.commit()
    return {"updated": updated, "total": len(tools)}


@router.delete("/{slug}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tool(
    slug: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    _admin: Annotated[User, Depends(require_admin)],
):
    """Soft-delete a tool by deactivating it (admin only)."""
    result = await db.execute(select(Tool).where(Tool.slug == slug))
    tool = result.scalar_one_or_none()
    if not tool:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="ツールが見つかりません"
        )
    tool.is_active = False
    await db.commit()


# ──── Target Config Endpoints ────


@router.get("/{slug}/target-config", response_model=TargetConfigResponse | None)
async def get_target_config(
    slug: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    _user: Annotated[User, Depends(require_analyst)],
):
    """Get the active target config for a tool."""
    result = await db.execute(select(Tool).where(Tool.slug == slug))
    tool = result.scalar_one_or_none()
    if not tool:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="ツールが見つかりません"
        )

    cfg_result = await db.execute(
        select(ToolTargetConfig)
        .where(ToolTargetConfig.tool_id == tool.id, ToolTargetConfig.is_active.is_(True))
        .order_by(ToolTargetConfig.version.desc())
        .limit(1)
    )
    cfg = cfg_result.scalar_one_or_none()
    return cfg


@router.put("/{slug}/target-config", response_model=TargetConfigResponse)
async def save_target_config(
    slug: str,
    body: TargetConfigCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    _admin: Annotated[User, Depends(require_admin)],
):
    """Create or update the target config for a tool (admin only).

    Creates a new version. Previous active configs are deactivated.
    """
    result = await db.execute(select(Tool).where(Tool.slug == slug))
    tool = result.scalar_one_or_none()
    if not tool:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="ツールが見つかりません"
        )

    # Get current max version
    ver_result = await db.execute(
        select(func.max(ToolTargetConfig.version)).where(
            ToolTargetConfig.tool_id == tool.id
        )
    )
    max_ver = ver_result.scalar() or 0

    # Deactivate old configs
    old_cfgs = await db.execute(
        select(ToolTargetConfig).where(
            ToolTargetConfig.tool_id == tool.id, ToolTargetConfig.is_active.is_(True)
        )
    )
    for old in old_cfgs.scalars().all():
        old.is_active = False

    new_cfg = ToolTargetConfig(
        tool_id=tool.id,
        config_yaml=body.config_yaml,
        version=max_ver + 1,
        is_active=True,
        validated_at=datetime.now(timezone.utc),
    )
    db.add(new_cfg)
    await db.commit()
    await db.refresh(new_cfg)
    return new_cfg
