"""Industry tags, use-case tags, and adoption pattern endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.base import get_db
from ...db.models.user import User
from ...schemas.industry import (
    IndustryTagResponse,
    UseCaseTagResponse,
    ToolIndustryMappingCreate,
    ToolIndustryMappingResponse,
    ToolUseCaseMappingCreate,
    ToolUseCaseMappingResponse,
    IndustryAdoptionPatternCreate,
    IndustryAdoptionPatternResponse,
)
from ...services import adoption_service
from ..deps import require_admin

router = APIRouter()


# --- Industry Tags ---

@router.get("/tags", response_model=list[IndustryTagResponse])
async def list_industry_tags(db: Annotated[AsyncSession, Depends(get_db)]):
    """List all industry tags."""
    return await adoption_service.list_industry_tags(db)


# --- Use Case Tags ---

@router.get("/use-cases", response_model=list[UseCaseTagResponse])
async def list_use_case_tags(db: Annotated[AsyncSession, Depends(get_db)]):
    """List all use case tags."""
    return await adoption_service.list_use_case_tags(db)


# --- Tool Industry Mappings ---

@router.get(
    "/tools/{tool_id}/industries",
    response_model=list[ToolIndustryMappingResponse],
)
async def get_tool_industries(
    tool_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    return await adoption_service.get_tool_industries(db, tool_id)


@router.post(
    "/tools/{tool_id}/industries",
    response_model=ToolIndustryMappingResponse,
)
async def add_tool_industry(
    tool_id: str,
    body: ToolIndustryMappingCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(require_admin)],
):
    return await adoption_service.add_tool_industry(
        db, tool_id, body.industry_id, body.fit_level, body.use_case_summary_jp
    )


@router.delete("/tools/{tool_id}/industries/{industry_id}")
async def remove_tool_industry(
    tool_id: str,
    industry_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(require_admin)],
):
    ok = await adoption_service.remove_tool_industry(db, tool_id, industry_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Mapping not found")
    return {"ok": True}


# --- Tool Use Case Mappings ---

@router.get(
    "/tools/{tool_id}/use-cases",
    response_model=list[ToolUseCaseMappingResponse],
)
async def get_tool_use_cases(
    tool_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    return await adoption_service.get_tool_use_cases(db, tool_id)


@router.post(
    "/tools/{tool_id}/use-cases",
    response_model=ToolUseCaseMappingResponse,
)
async def add_tool_use_case(
    tool_id: str,
    body: ToolUseCaseMappingCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(require_admin)],
):
    return await adoption_service.add_tool_use_case(
        db, tool_id, body.use_case_id, body.relevance, body.description_jp
    )


@router.delete("/tools/{tool_id}/use-cases/{use_case_id}")
async def remove_tool_use_case(
    tool_id: str,
    use_case_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(require_admin)],
):
    ok = await adoption_service.remove_tool_use_case(db, tool_id, use_case_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Mapping not found")
    return {"ok": True}


# --- Adoption Patterns ---

@router.get(
    "/tools/{tool_id}/adoption",
    response_model=list[IndustryAdoptionPatternResponse],
)
async def get_tool_adoption(
    tool_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    return await adoption_service.get_tool_adoption_patterns(db, tool_id)


@router.post(
    "/tools/{tool_id}/adoption",
    response_model=IndustryAdoptionPatternResponse,
)
async def create_adoption_pattern(
    tool_id: str,
    body: IndustryAdoptionPatternCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(require_admin)],
):
    return await adoption_service.create_adoption_pattern(
        db, tool_id, body.model_dump()
    )


# --- Benchmark: tools by industry ---

@router.get("/benchmark/{industry_slug}")
async def get_benchmark_by_industry(
    industry_slug: str,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Get all tools mapped to an industry (benchmark view)."""
    mappings = await adoption_service.get_tools_by_industry(db, industry_slug)
    return [
        {
            "tool_id": m.tool_id,
            "fit_level": m.fit_level,
            "use_case_summary_jp": m.use_case_summary_jp,
        }
        for m in mappings
    ]
