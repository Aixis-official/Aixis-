"""Risk and governance scoring service."""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.risk_governance import ToolRiskGovernance, RegulatoryFramework


# --- Grade computation (same scale as 5-axis) ---

def _grade_from_score(score: float) -> str:
    if score >= 4.5:
        return "S"
    if score >= 3.5:
        return "A"
    if score >= 2.5:
        return "B"
    if score >= 1.5:
        return "C"
    return "D"


def compute_governance_score(rg: ToolRiskGovernance) -> float:
    """Compute composite governance score from sub-components.

    Weights:
      - Regulatory compliance (JP): 35%
      - Certifications: 25%
      - Data handling transparency: 25%
      - Industry-specific compliance: 15%
    """
    scores = []
    weights = []

    # 1. Regulatory compliance (35%)
    reg_scores = []
    status_map = {"compliant": 5.0, "partial": 3.0, "non_compliant": 1.0, "unknown": 0.0}
    for field in ("ai_business_guideline_status", "appi_status", "gdpr_status"):
        val = getattr(rg, field, None)
        if val and val in status_map:
            reg_scores.append(status_map[val])
    if reg_scores:
        scores.append(sum(reg_scores) / len(reg_scores))
        weights.append(0.35)

    # 2. Certifications (25%)
    certs = rg.certifications or []
    if certs:
        cert_score = min(len(certs) * 1.25, 5.0)
        scores.append(cert_score)
        weights.append(0.25)

    # 3. Data handling transparency (25%)
    if rg.data_transparency_score is not None:
        scores.append(rg.data_transparency_score)
        weights.append(0.25)
    else:
        # Derive from boolean flags
        flags = [rg.data_deletion_available, rg.training_data_optout, rg.data_residency_japan]
        true_count = sum(1 for f in flags if f is True)
        known_count = sum(1 for f in flags if f is not None)
        if known_count > 0:
            scores.append((true_count / known_count) * 5.0)
            weights.append(0.25)

    # 4. Industry-specific compliance (15%)
    ic = rg.industry_compliance or []
    if ic:
        ic_scores = [status_map.get(item.get("status", ""), 0.0) for item in ic]
        scores.append(sum(ic_scores) / len(ic_scores))
        weights.append(0.15)

    if not scores:
        return 0.0

    total_weight = sum(weights)
    return round(sum(s * w for s, w in zip(scores, weights)) / total_weight, 2)


async def get_latest_risk_governance(
    db: AsyncSession, tool_id: str
) -> ToolRiskGovernance | None:
    """Get the latest version of risk governance for a tool."""
    result = await db.execute(
        select(ToolRiskGovernance)
        .where(ToolRiskGovernance.tool_id == tool_id)
        .order_by(ToolRiskGovernance.version.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def create_risk_governance(
    db: AsyncSession, tool_id: str, data: dict, assessed_by: str | None = None
) -> ToolRiskGovernance:
    """Create a new version of risk governance assessment."""
    # Get next version number
    latest = await get_latest_risk_governance(db, tool_id)
    version = (latest.version + 1) if latest else 1

    rg = ToolRiskGovernance(
        tool_id=tool_id,
        version=version,
        assessed_at=datetime.now(timezone.utc),
        assessed_by=assessed_by,
        **data,
    )

    # Compute governance score and grade
    rg.governance_score = compute_governance_score(rg)
    rg.governance_grade = _grade_from_score(rg.governance_score)

    db.add(rg)
    await db.commit()
    await db.refresh(rg)
    return rg


async def update_risk_governance(
    db: AsyncSession, rg_id: str, data: dict
) -> ToolRiskGovernance | None:
    """Update an existing risk governance assessment."""
    result = await db.execute(
        select(ToolRiskGovernance).where(ToolRiskGovernance.id == rg_id)
    )
    rg = result.scalar_one_or_none()
    if not rg:
        return None

    for key, value in data.items():
        if value is not None:
            setattr(rg, key, value)

    rg.governance_score = compute_governance_score(rg)
    rg.governance_grade = _grade_from_score(rg.governance_score)
    rg.updated_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(rg)
    return rg


async def list_regulatory_frameworks(db: AsyncSession) -> list[RegulatoryFramework]:
    """List all active regulatory frameworks."""
    result = await db.execute(
        select(RegulatoryFramework)
        .where(RegulatoryFramework.is_active.is_(True))
        .order_by(RegulatoryFramework.sort_order)
    )
    return list(result.scalars().all())
