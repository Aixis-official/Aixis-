"""Tool catalog management service."""
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from ..db.models.tool import Tool, ToolCategory


async def list_tools(db: AsyncSession, category_slug: str | None = None, search: str | None = None,
                     is_public: bool | None = None, offset: int = 0, limit: int = 20):
    """List tools with optional filtering."""
    query = select(Tool).where(Tool.is_active.is_(True))
    if category_slug:
        cat = await db.execute(select(ToolCategory).where(ToolCategory.slug == category_slug))
        cat_obj = cat.scalar_one_or_none()
        if cat_obj:
            query = query.where(Tool.category_id == cat_obj.id)
    if search:
        from sqlalchemy import cast, Text as SAText
        pattern = f"%{search}%"
        query = query.where(
            (Tool.name_jp.ilike(pattern))
            | (Tool.name.ilike(pattern))
            | (Tool.vendor.ilike(pattern))
            | (cast(Tool.search_aliases, SAText).ilike(pattern))
        )
    if is_public is not None:
        query = query.where(Tool.is_public == is_public)
    total = await db.scalar(select(func.count()).select_from(query.subquery()))
    results = await db.execute(query.offset(offset).limit(limit).order_by(Tool.name_jp))
    return results.scalars().all(), total or 0


async def get_tool_by_slug(db: AsyncSession, slug: str) -> Tool | None:
    result = await db.execute(select(Tool).where(Tool.slug == slug))
    return result.scalar_one_or_none()


async def create_tool(db: AsyncSession, **kwargs) -> Tool:
    tool = Tool(**kwargs)
    db.add(tool)
    await db.commit()
    await db.refresh(tool)
    return tool


async def update_tool(db: AsyncSession, tool: Tool, **kwargs) -> Tool:
    for key, value in kwargs.items():
        if hasattr(tool, key):
            setattr(tool, key, value)
    await db.commit()
    await db.refresh(tool)
    return tool


async def list_categories(db: AsyncSession):
    result = await db.execute(select(ToolCategory).order_by(ToolCategory.sort_order))
    return result.scalars().all()


async def get_tool_count(db: AsyncSession) -> int:
    return await db.scalar(select(func.count()).select_from(Tool)) or 0
