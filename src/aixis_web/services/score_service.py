"""Score merging service - combines automated and manual scores."""
from datetime import datetime
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import yaml

from ..db.models.score import AxisScoreRecord, ToolPublishedScore, ScoreHistory, ManualChecklistRecord
from ..db.models.audit import AuditSession
from ..config import settings
from aixis_agent.core.enums import OverallGrade, ScoreAxis

# Auto/manual mix ratios per axis
AXIS_MIX = {
    "practicality":     {"auto": 0.4, "manual": 0.6},
    "cost_performance": {"auto": 0.0, "manual": 1.0},
    "localization":     {"auto": 0.7, "manual": 0.3},
    "safety":           {"auto": 0.35, "manual": 0.65},
    "uniqueness":       {"auto": 0.0, "manual": 1.0},
}


async def get_auto_scores(db: AsyncSession, session_id: str) -> dict[str, float]:
    """Get automated scores for all axes from a session."""
    result = await db.execute(
        select(AxisScoreRecord).where(
            AxisScoreRecord.session_id == session_id,
            AxisScoreRecord.source == "auto"
        )
    )
    scores = {}
    for record in result.scalars():
        scores[record.axis] = record.score
    return scores


async def get_manual_scores(db: AsyncSession, session_id: str) -> dict[str, float]:
    """Calculate manual scores from checklist entries per axis."""
    result = await db.execute(
        select(ManualChecklistRecord).where(ManualChecklistRecord.session_id == session_id)
    )
    entries_by_axis: dict[str, list] = {}
    for entry in result.scalars():
        entries_by_axis.setdefault(entry.axis, []).append(entry)

    scores = {}
    for axis, entries in entries_by_axis.items():
        total_weight = sum(e.weight for e in entries if e.score is not None)
        if total_weight > 0:
            weighted_sum = sum(e.score * e.weight for e in entries if e.score is not None)
            scores[axis] = min(5.0, weighted_sum / total_weight)
    return scores


async def merge_and_publish(db: AsyncSession, session_id: str, tool_id: str, published_by: str | None = None) -> ToolPublishedScore:
    """Merge auto + manual scores and publish to tool_scores."""
    auto_scores = await get_auto_scores(db, session_id)
    manual_scores = await get_manual_scores(db, session_id)

    final = {}
    for axis, mix in AXIS_MIX.items():
        auto = auto_scores.get(axis, 0.0) if mix["auto"] > 0 else 0.0
        manual = manual_scores.get(axis, 0.0) if mix["manual"] > 0 else 0.0

        # If manual portion is required but not provided, use auto only
        if mix["manual"] > 0 and axis not in manual_scores:
            final[axis] = auto  # Partial score
        elif mix["auto"] > 0 and axis not in auto_scores:
            final[axis] = manual
        else:
            final[axis] = auto * mix["auto"] + manual * mix["manual"]

        final[axis] = max(0.0, min(5.0, round(final[axis], 1)))

    # Overall score: equal-weight average of all 5 axes
    overall = round(sum(final.values()) / len(final), 1) if final else 0.0
    grade = OverallGrade.from_score(overall)

    # Determine next version
    existing = await db.execute(
        select(ToolPublishedScore).where(ToolPublishedScore.tool_id == tool_id).order_by(ToolPublishedScore.version.desc())
    )
    latest = existing.scalar_one_or_none()
    next_version = (latest.version + 1) if latest else 1

    published = ToolPublishedScore(
        tool_id=tool_id,
        practicality=final.get("practicality", 0.0),
        cost_performance=final.get("cost_performance", 0.0),
        localization=final.get("localization", 0.0),
        safety=final.get("safety", 0.0),
        uniqueness=final.get("uniqueness", 0.0),
        overall_score=overall,
        overall_grade=grade.value,
        source_session_id=session_id,
        version=next_version,
        published_at=datetime.utcnow(),
        published_by=published_by,
    )
    db.add(published)

    # Record score history
    for axis, score in final.items():
        db.add(ScoreHistory(
            tool_id=tool_id,
            axis=axis,
            score=score,
            source_session_id=session_id,
        ))

    # Update session status
    session = await db.execute(select(AuditSession).where(AuditSession.id == session_id))
    session_obj = session.scalar_one_or_none()
    if session_obj:
        session_obj.status = "completed"
        session_obj.completed_at = datetime.utcnow()

    await db.commit()
    await db.refresh(published)

    # Emit score.published webhook event (best-effort)
    try:
        from .webhook_service import emit_event
        await emit_event("score.published", {
            "event": "score.published",
            "tool_id": tool_id,
            "session_id": session_id,
            "version": next_version,
            "overall_score": overall,
            "overall_grade": grade.value,
            "scores": final,
        }, db)
        await db.commit()
    except Exception:
        import logging
        logging.getLogger(__name__).warning("Failed to emit score.published webhook")

    return published


def load_checklist_template(axis: str, checklists_dir: Path | None = None) -> list[dict]:
    """Load manual checklist YAML template for an axis."""
    if checklists_dir is None:
        checklists_dir = Path(settings.config_dir) / "scoring" / "checklists"

    file_map = {
        "cost_performance": "cost_performance.yaml",
        "safety": "safety_manual.yaml",
        "uniqueness": "uniqueness.yaml",
        "practicality": "practicality_manual.yaml",
    }

    filename = file_map.get(axis)
    if not filename:
        return []

    path = checklists_dir / filename
    if not path.exists():
        return []

    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data.get("items", [])
