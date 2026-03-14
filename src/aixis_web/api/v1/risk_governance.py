"""Risk and governance assessment endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.base import get_db
from ...db.models.user import User
from ...schemas.risk_governance import (
    RiskGovernanceCreate,
    RiskGovernanceUpdate,
    RiskGovernanceResponse,
    RegulatoryFrameworkResponse,
)
from ...services import risk_governance_service
from ..deps import require_admin

router = APIRouter()


@router.get(
    "/tools/{tool_id}",
    response_model=RiskGovernanceResponse | None,
)
async def get_risk_governance(
    tool_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Get latest risk/governance assessment for a tool."""
    return await risk_governance_service.get_latest_risk_governance(db, tool_id)


@router.post(
    "/tools/{tool_id}",
    response_model=RiskGovernanceResponse,
)
async def create_risk_governance(
    tool_id: str,
    body: RiskGovernanceCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(require_admin)],
):
    """Create a new risk/governance assessment (creates a new version)."""
    return await risk_governance_service.create_risk_governance(
        db, tool_id, body.model_dump(exclude_unset=True), assessed_by=user.id
    )


@router.put(
    "/{rg_id}",
    response_model=RiskGovernanceResponse,
)
async def update_risk_governance(
    rg_id: str,
    body: RiskGovernanceUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(require_admin)],
):
    """Update an existing risk/governance assessment."""
    rg = await risk_governance_service.update_risk_governance(
        db, rg_id, body.model_dump(exclude_unset=True)
    )
    if not rg:
        raise HTTPException(status_code=404, detail="Assessment not found")
    return rg


@router.get(
    "/regulatory-frameworks",
    response_model=list[RegulatoryFrameworkResponse],
)
async def list_regulatory_frameworks(
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """List all regulatory frameworks."""
    return await risk_governance_service.list_regulatory_frameworks(db)
