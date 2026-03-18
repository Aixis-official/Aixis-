"""Audit preset endpoints."""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.base import get_db
from ...db.models.preset import AuditPreset
from ...db.models.user import User
from ..deps import require_analyst

router = APIRouter()


class PresetCreate(BaseModel):
    name: str
    name_jp: str | None = None
    description: str | None = None
    tool_id: str | None = None
    profile_id: str | None = None
    categories: list[str] | None = None
    budget_max_calls: int = 200
    budget_max_cost_jpy: int = 20


class PresetResponse(BaseModel):
    id: str
    name: str
    name_jp: str | None
    description: str | None
    tool_id: str | None
    profile_id: str | None
    categories: list[str] | None
    budget_max_calls: int
    budget_max_cost_jpy: int
    is_default: bool
    model_config = {"from_attributes": True}


@router.get("", response_model=list[PresetResponse])
async def list_presets(
    db: Annotated[AsyncSession, Depends(get_db)],
    _user: Annotated[User, Depends(require_analyst)],
):
    result = await db.execute(select(AuditPreset).order_by(AuditPreset.created_at.desc()))
    return result.scalars().all()


@router.post("", response_model=PresetResponse, status_code=status.HTTP_201_CREATED)
async def create_preset(
    body: PresetCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(require_analyst)],
):
    preset = AuditPreset(
        name=body.name,
        name_jp=body.name_jp,
        description=body.description,
        tool_id=body.tool_id,
        profile_id=body.profile_id,
        categories=body.categories or [],
        budget_max_calls=body.budget_max_calls,
        budget_max_cost_jpy=body.budget_max_cost_jpy,
        created_by=user.id,
    )
    db.add(preset)
    await db.commit()
    await db.refresh(preset)
    return preset


@router.delete("/{preset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_preset(
    preset_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    _user: Annotated[User, Depends(require_analyst)],
):
    result = await db.execute(select(AuditPreset).where(AuditPreset.id == preset_id))
    preset = result.scalar_one_or_none()
    if not preset:
        raise HTTPException(status_code=404, detail="プリセットが見つかりません")
    await db.delete(preset)
    await db.commit()
