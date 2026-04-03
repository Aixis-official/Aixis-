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
from sqlalchemy.orm import selectinload

from ...db.base import get_db
from ...db.models.score import ToolPublishedScore
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
        return f"https://www.google.com/s2/favicons?domain={domain}&sz=128"
    except Exception:
        return None


@router.get("/categories", response_model=list[CategoryResponse])
async def list_categories(db: Annotated[AsyncSession, Depends(get_db)]):
    """List all tool categories."""
    result = await db.execute(
        select(ToolCategory).order_by(ToolCategory.sort_order)
    )
    return result.scalars().all()


class _CategoryCreate(BaseModel):
    slug: str
    name_jp: str
    name_en: str | None = None
    description_jp: str | None = None
    sort_order: int = 0
    audit_method_notes: str | None = None


class _CategoryUpdate(BaseModel):
    name_jp: str | None = None
    name_en: str | None = None
    description_jp: str | None = None
    sort_order: int | None = None
    audit_method_notes: str | None = None


@router.post("/categories", response_model=CategoryResponse, status_code=status.HTTP_201_CREATED)
async def create_category(
    body: _CategoryCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    _admin: Annotated[User, Depends(require_admin)],
):
    """Create a new tool category (admin only)."""
    existing = await db.execute(select(ToolCategory).where(ToolCategory.slug == body.slug))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="このスラッグは既に使用されています")
    cat = ToolCategory(slug=body.slug, name_jp=body.name_jp, name_en=body.name_en,
                       description_jp=body.description_jp, sort_order=body.sort_order,
                       audit_method_notes=body.audit_method_notes)
    db.add(cat)
    await db.commit()
    await db.refresh(cat)
    return cat


@router.put("/categories/{category_id}", response_model=CategoryResponse)
async def update_category(
    category_id: str,
    body: _CategoryUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    _admin: Annotated[User, Depends(require_admin)],
):
    """Update a tool category (admin only)."""
    result = await db.execute(select(ToolCategory).where(ToolCategory.id == category_id))
    cat = result.scalar_one_or_none()
    if not cat:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="カテゴリが見つかりません")
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(cat, key, value)
    await db.commit()
    await db.refresh(cat)
    return cat


@router.delete("/categories/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_category(
    category_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    _admin: Annotated[User, Depends(require_admin)],
):
    """Delete a tool category (admin only). Fails if tools are assigned."""
    result = await db.execute(select(ToolCategory).where(ToolCategory.id == category_id))
    cat = result.scalar_one_or_none()
    if not cat:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="カテゴリが見つかりません")
    # Check for assigned tools
    tool_count = await db.execute(
        select(func.count()).select_from(Tool).where(Tool.category_id == category_id)
    )
    if (tool_count.scalar() or 0) > 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="ツールが割り当てられているカテゴリは削除できません")
    await db.delete(cat)
    await db.commit()


@router.get("", response_model=ToolListResponse)
async def list_tools(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User | None, Depends(get_current_user)],
    page: int = Query(1, ge=1, le=1000),
    page_size: int = Query(20, ge=1, le=100),
    category_id: str | None = Query(None, max_length=50),
    q: str | None = Query(None, min_length=1, max_length=100),
    sort: str | None = Query(None, description="Sort order: name, newest, score"),
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
        # Escape SQL LIKE wildcards in user input
        safe_q = q.replace("%", r"\%").replace("_", r"\_")
        pattern = f"%{safe_q}%"
        # Search across name, name_jp, description, description_jp, and search_aliases
        # search_aliases is a JSON array — coalesce NULL to '' before cast to text
        from sqlalchemy import cast, Text as SAText
        search_filter = (
            Tool.name.ilike(pattern)
            | Tool.name_jp.ilike(pattern)
            | Tool.description.ilike(pattern)
            | Tool.description_jp.ilike(pattern)
            | cast(func.coalesce(Tool.search_aliases, ""), SAText).ilike(pattern)
        )
        query = query.where(search_filter)
        count_query = count_query.where(search_filter)

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    offset = (page - 1) * page_size
    if sort == "newest":
        query = query.options(selectinload(Tool.scores)).order_by(
            Tool.created_at.desc()
        ).offset(offset).limit(page_size)
    elif sort == "score":
        # Server-side score sorting: LEFT JOIN with latest published score
        latest_ver = (
            select(
                ToolPublishedScore.tool_id,
                func.max(ToolPublishedScore.version).label("max_ver"),
            )
            .group_by(ToolPublishedScore.tool_id)
            .subquery()
        )
        query = (
            query
            .outerjoin(latest_ver, Tool.id == latest_ver.c.tool_id)
            .outerjoin(
                ToolPublishedScore,
                (ToolPublishedScore.tool_id == Tool.id)
                & (ToolPublishedScore.version == latest_ver.c.max_ver),
            )
            .options(selectinload(Tool.scores))
            .order_by(
                ToolPublishedScore.overall_score.desc().nullslast(),
                Tool.name_jp,
            )
            .offset(offset)
            .limit(page_size)
        )
    else:
        query = query.options(selectinload(Tool.scores)).order_by(
            Tool.name_jp
        ).offset(offset).limit(page_size)
    result = await db.execute(query)
    items = result.scalars().all()

    return ToolListResponse(items=items, total=total, page=page, page_size=page_size)


@router.get("/{slug}", response_model=ToolResponse)
async def get_tool(
    slug: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User | None, Depends(get_current_user)],
):
    """Get tool detail by slug. Non-auth users only see public+active tools."""
    query = select(Tool).where(Tool.slug == slug).options(selectinload(Tool.scores))
    # Non-authenticated or non-analyst users can only see public active tools
    if not user or user.role not in ("admin", "analyst", "auditor"):
        query = query.where(Tool.is_public.is_(True), Tool.is_active.is_(True))
    result = await db.execute(query)
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

    # Re-fetch with scores eagerly loaded for proper serialization
    refreshed = await db.execute(
        select(Tool).where(Tool.id == tool.id).options(selectinload(Tool.scores))
    )
    return refreshed.scalar_one()


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

    # Server-side merge for auth_storage_state: never lose cookies or localStorage
    if "auth_storage_state" in update_data and update_data["auth_storage_state"] is not None:
        new_state = update_data["auth_storage_state"]
        existing_state = tool.auth_storage_state if isinstance(tool.auth_storage_state, dict) else {}
        new_cookies = new_state.get("cookies", [])
        new_origins = new_state.get("origins", [])
        exist_cookies = existing_state.get("cookies", [])
        exist_origins = existing_state.get("origins", [])
        # Merge cookies: keep existing if new is empty, otherwise use new
        merged_cookies = new_cookies if new_cookies else exist_cookies
        # Merge origins by origin URL (additive)
        origin_map = {}
        for o in exist_origins:
            origin_map[o.get("origin", "")] = o
        for o in new_origins:
            origin_map[o.get("origin", "")] = o
        merged_origins = list(origin_map.values()) if origin_map else []
        update_data["auth_storage_state"] = {"cookies": merged_cookies, "origins": merged_origins}

    for key, value in update_data.items():
        setattr(tool, key, value)

    # Auto-set favicon if URL was updated and no logo_url is set
    if "url" in update_data and not tool.logo_url and tool.url:
        tool.logo_url = _auto_favicon_url(tool.url)

    await db.commit()

    # Re-fetch with scores eagerly loaded (same pattern as GET endpoint)
    refreshed = await db.execute(
        select(Tool).where(Tool.id == tool.id).options(selectinload(Tool.scores))
    )
    tool = refreshed.scalar_one()

    # Resolve category name (same as GET endpoint)
    resp = ToolResponse.model_validate(tool)
    if tool.category_id:
        cat_result = await db.execute(
            select(ToolCategory.name_jp).where(ToolCategory.id == tool.category_id)
        )
        resp.category_name_jp = cat_result.scalar_one_or_none()
    return resp


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


@router.post("/{slug}/auto-research", status_code=status.HTTP_200_OK)
async def auto_research_tool_info(
    slug: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    _admin: Annotated[User, Depends(require_admin)],
    preview: bool = Query(False, description="Preview only — do not save to DB"),
):
    """Use LLM to research and auto-populate tool info from official sources.

    When preview=true, returns research data without saving (for human review).
    When preview=false (default), also includes SEO/article fields.
    """
    result = await db.execute(select(Tool).where(Tool.slug == slug))
    tool = result.scalar_one_or_none()
    if not tool:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="ツールが見つかりません"
        )

    import anthropic
    import json
    from ...config import settings as app_settings

    api_key = app_settings.anthropic_api_key
    if not api_key:
        return {
            "status": "error",
            "message": "Anthropic APIキーが設定されていません。設定画面からAPIキーを登録してください。",
        }

    try:
        client = anthropic.Anthropic(api_key=api_key)
    except Exception as e:
        return {
            "status": "error",
            "message": f"Anthropic クライアントの初期化に失敗しました: {e}",
        }
    tool_name = tool.name_jp or tool.name or slug
    tool_url = tool.url or ""
    tool_vendor = tool.vendor or ""

    research_prompt = f"""以下のAIツールについて、公式サイト・公式ドキュメントから確認できる情報のみを使用して、JSONフォーマットで回答してください。

重要なルール:
- 公式情報（公式サイト、公式ドキュメント、公式ブログ）のみを使用すること
- 確認できない情報はnullとすること
- 推測や憶測は一切含めないこと

ツール名: {tool_name}
公式URL: {tool_url}
ベンダー: {tool_vendor}

以下のJSON形式で回答してください（説明文不要、JSONのみ）:
{{
  "name": "英語正式名称（公式サイトの表記通り）",
  "name_jp": "日本語名称（公式に日本語名がある場合。ない場合は英語名そのまま）",
  "vendor": "ベンダー/開発元の正式名称",
  "description_jp": "ツールの概要説明（2-3文、公式情報に基づく）",
  "supported_languages": ["ja", "en"],
  "features": ["主要機能1", "主要機能2", "主要機能3"],
  "pricing_model": "free|freemium|paid|enterprise のいずれか",
  "free_trial_available": true,
  "free_trial_days": null,
  "executive_summary_jp": "企業担当者向けの概要。ツールの本質・強み・注意点を3-4行で。数値は使わず本質を伝える。",
  "pros_jp": ["メリット1", "メリット2", "メリット3"],
  "cons_jp": ["デメリット1", "デメリット2"],
  "risks_jp": "データの第三者提供リスクなど、公式情報から確認できるリスク・注意点",
  "pricing_detail_jp": "料金プランの詳細。無料/有料プランの内容と価格。"
}}

supported_languagesは実際にUI/インターフェースが対応している言語のISO 639-1コードのリストを返してください。
featuresは主要な機能を最大5つまで、簡潔な日本語で。
executive_summary_jpは企業の意思決定者向けに、このツールの本質を伝える概要。"""

    try:
        import asyncio
        loop = asyncio.get_event_loop()
        try:
            response = await loop.run_in_executor(None, lambda: client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=2000,
                messages=[{"role": "user", "content": research_prompt}],
            ))
        except anthropic.AuthenticationError as e:
            return {"status": "error", "message": f"APIキー認証エラー: {e}。ANTHROPIC_API_KEYの値を確認してください。"}
        except anthropic.RateLimitError as e:
            return {"status": "error", "message": f"APIレート制限に達しました。しばらく待ってから再試行してください: {e}"}
        except anthropic.APIConnectionError as e:
            return {"status": "error", "message": f"Anthropic APIへの接続に失敗しました: {e}"}
        except anthropic.BadRequestError as e:
            return {"status": "error", "message": f"APIリクエストエラー: {e}"}

        content = response.content[0].text if response.content else ""

        # Extract JSON from response
        import re
        json_match = re.search(r'\{[\s\S]*\}', content)
        if not json_match:
            return {"status": "error", "message": "LLMからJSON応答を取得できませんでした", "raw": content}

        data = json.loads(json_match.group())

        # Preview mode: return research data without saving
        if preview:
            return {
                "status": "preview",
                "research_data": data,
                "message": "プレビューモードです。内容を確認・編集してから保存してください。",
            }

        updated_fields = []

        # Update fields only if LLM provides non-null values
        if data.get("name"):
            tool.name = data["name"]
            updated_fields.append("name")
        if data.get("name_jp"):
            tool.name_jp = data["name_jp"]
            updated_fields.append("name_jp")
        if data.get("vendor"):
            tool.vendor = data["vendor"]
            updated_fields.append("vendor")
        if data.get("description_jp"):
            tool.description_jp = data["description_jp"]
            updated_fields.append("description_jp")
        if data.get("supported_languages") and isinstance(data["supported_languages"], list):
            tool.supported_languages = data["supported_languages"]
            updated_fields.append("supported_languages")
        if data.get("features") and isinstance(data["features"], list):
            tool.features = data["features"]
            updated_fields.append("features")
        if data.get("pricing_model"):
            tool.pricing_model = data["pricing_model"]
            updated_fields.append("pricing_model")
        if data.get("free_trial_available") is not None:
            tool.free_trial_available = data["free_trial_available"]
            updated_fields.append("free_trial_available")
        if data.get("free_trial_days") is not None:
            tool.free_trial_days = data["free_trial_days"]
            updated_fields.append("free_trial_days")
        if data.get("executive_summary_jp"):
            tool.executive_summary_jp = data["executive_summary_jp"]
            updated_fields.append("executive_summary_jp")
        if data.get("pros_jp") and isinstance(data["pros_jp"], list):
            tool.pros_jp = data["pros_jp"]
            updated_fields.append("pros_jp")
        if data.get("cons_jp") and isinstance(data["cons_jp"], list):
            tool.cons_jp = data["cons_jp"]
            updated_fields.append("cons_jp")
        if data.get("risks_jp"):
            tool.risks_jp = data["risks_jp"]
            updated_fields.append("risks_jp")
        if data.get("pricing_detail_jp"):
            tool.pricing_detail_jp = data["pricing_detail_jp"]
            updated_fields.append("pricing_detail_jp")

        tool.updated_at = datetime.now(timezone.utc)
        await db.commit()

        return {
            "status": "success",
            "updated_fields": updated_fields,
            "research_data": data,
        }

    except Exception as e:
        logger.exception("Auto-research failed for %s: %s", slug, e)
        return {"status": "error", "message": str(e)}


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
    # Validate YAML before saving
    import yaml as _yaml
    try:
        parsed = _yaml.safe_load(body.config_yaml)
        if not isinstance(parsed, dict):
            raise HTTPException(status_code=400, detail="YAML形式が不正です。辞書形式である必要があります。")
        if "start_url" not in parsed:
            raise HTTPException(status_code=400, detail="start_url が必須です。有効なターゲット設定YAMLを入力してください。")
    except _yaml.YAMLError as e:
        raise HTTPException(status_code=400, detail=f"YAML解析エラー: {e}")

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
