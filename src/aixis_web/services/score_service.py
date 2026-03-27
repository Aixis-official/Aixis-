"""Score merging service - combines automated and manual scores."""
from datetime import datetime, timezone
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
    "cost_performance": {"auto": 0.3, "manual": 0.7},
    "localization":     {"auto": 0.7, "manual": 0.3},
    "safety":           {"auto": 0.35, "manual": 0.65},
    "uniqueness":       {"auto": 0.4, "manual": 0.6},
}


async def get_auto_scores(db: AsyncSession, session_id: str) -> dict[str, float]:
    """Get automated scores for all axes from a session.

    The LLM scorer stores scores with source='llm' or 'hybrid', while the
    legacy agent scorer uses source='auto'. The manual editor uses 'manual_edit'.
    Accept all non-checklist-manual sources.
    """
    result = await db.execute(
        select(AxisScoreRecord).where(
            AxisScoreRecord.session_id == session_id,
            AxisScoreRecord.source.in_(["auto", "llm", "hybrid", "manual_edit"]),
        )
    )
    scores = {}
    for record in result.scalars():
        # Use the raw auto_score if available in details (stored by LLM scorer),
        # otherwise use the record score directly
        auto_score = record.score
        if record.details:
            import json as _json
            try:
                details = record.details if isinstance(record.details, dict) else _json.loads(record.details)
                if "auto_score" in details:
                    auto_score = float(details["auto_score"])
            except (TypeError, ValueError, _json.JSONDecodeError):
                pass
        scores[record.axis] = auto_score
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
        has_auto = axis in auto_scores and mix["auto"] > 0
        has_manual = axis in manual_scores and mix["manual"] > 0

        if has_auto and has_manual:
            # Both available: weighted blend
            final[axis] = auto_scores[axis] * mix["auto"] + manual_scores[axis] * mix["manual"]
        elif has_auto and not has_manual:
            # Only auto available: use auto score directly (not scaled down)
            final[axis] = auto_scores[axis]
        elif has_manual and not has_auto:
            # Only manual available: use manual score directly
            final[axis] = manual_scores[axis]
        else:
            # Neither available
            final[axis] = 0.0

        final[axis] = max(0.0, min(5.0, round(final[axis], 1)))

    # Overall score: equal-weight average of all 5 axes
    overall = round(sum(final.values()) / len(final), 1) if final else 0.0

    # Check completion rate — override grade if insufficient
    session_obj_q = await db.execute(select(AuditSession).where(AuditSession.id == session_id))
    session_for_completion = session_obj_q.scalar_one_or_none()
    _total_planned = session_for_completion.total_planned if session_for_completion else 0
    _total_executed = session_for_completion.total_executed if session_for_completion else 0
    _completion_rate = _total_executed / _total_planned if _total_planned > 0 else 1.0

    if _completion_rate < 0.3 and _total_planned > 0:
        # Insufficient completion: override grade to D (lowest valid grade)
        grade_value = "D"
    else:
        grade = OverallGrade.from_score(overall)
        grade_value = grade.value

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
        overall_grade=grade_value,
        source_session_id=session_id,
        version=next_version,
        published_at=datetime.now(timezone.utc),
        published_by=published_by,
    )
    db.add(published)

    # Record score history (per-axis + overall snapshot for time-series)
    for axis, score in final.items():
        db.add(ScoreHistory(
            tool_id=tool_id,
            axis=axis,
            score=score,
            overall_score=overall,
            overall_grade=grade_value,
            source_session_id=session_id,
        ))

    # Update session status
    session = await db.execute(select(AuditSession).where(AuditSession.id == session_id))
    session_obj = session.scalar_one_or_none()
    if session_obj:
        session_obj.status = "completed"
        session_obj.completed_at = datetime.now(timezone.utc)

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
            "overall_grade": grade_value,
            "scores": final,
        }, db)
        await db.commit()
    except Exception:
        import logging
        logging.getLogger(__name__).warning("Failed to emit score.published webhook")

    return published


AXIS_NAMES_JP = {
    "practicality": "実務適性",
    "cost_performance": "費用対効果",
    "localization": "日本語能力",
    "safety": "信頼性・安全性",
    "uniqueness": "革新性",
}


async def generate_score_diff(db: AsyncSession, tool_id: str, current_session_id: str) -> dict | None:
    """Compare current audit scores against the previous version for the same tool.

    Returns a diff report dict or None if no previous audit exists.
    """
    # Get current session's auto scores
    current_scores = await get_auto_scores(db, current_session_id)
    if not current_scores:
        return None

    # Find the previous completed audit for this tool (excluding current)
    prev_sessions = await db.execute(
        select(AuditSession)
        .where(
            AuditSession.tool_id == tool_id,
            AuditSession.id != current_session_id,
            AuditSession.status.in_(["completed", "awaiting_manual"]),
            AuditSession.deleted_at.is_(None),
        )
        .order_by(AuditSession.completed_at.desc())
        .limit(1)
    )
    prev_session = prev_sessions.scalar_one_or_none()
    if not prev_session:
        return None

    prev_scores = await get_auto_scores(db, prev_session.id)
    if not prev_scores:
        return None

    # Build diff
    axes_diff = []
    all_axes = set(list(current_scores.keys()) + list(prev_scores.keys()))
    for axis in sorted(all_axes):
        curr = current_scores.get(axis, 0.0)
        prev = prev_scores.get(axis, 0.0)
        delta = round(curr - prev, 2)
        axes_diff.append({
            "axis": axis,
            "axis_name_jp": AXIS_NAMES_JP.get(axis, axis),
            "current": curr,
            "previous": prev,
            "delta": delta,
            "direction": "up" if delta > 0 else ("down" if delta < 0 else "same"),
        })

    # Previous reliability scores
    prev_reliability = prev_session.reliability_scores
    if isinstance(prev_reliability, str):
        import json
        try:
            prev_reliability = json.loads(prev_reliability)
        except Exception:
            prev_reliability = None

    # Current reliability
    curr_session_result = await db.execute(
        select(AuditSession).where(AuditSession.id == current_session_id)
    )
    curr_session = curr_session_result.scalar_one_or_none()
    curr_reliability = curr_session.reliability_scores if curr_session else None
    if isinstance(curr_reliability, str):
        import json
        try:
            curr_reliability = json.loads(curr_reliability)
        except Exception:
            curr_reliability = None

    # Calculate overall change
    curr_avg = sum(current_scores.values()) / len(current_scores) if current_scores else 0
    prev_avg = sum(prev_scores.values()) / len(prev_scores) if prev_scores else 0

    return {
        "has_previous": True,
        "previous_session_id": prev_session.id,
        "previous_session_code": prev_session.session_code,
        "previous_completed_at": prev_session.completed_at.isoformat() if prev_session.completed_at else None,
        "axes": axes_diff,
        "overall_current": round(curr_avg, 2),
        "overall_previous": round(prev_avg, 2),
        "overall_delta": round(curr_avg - prev_avg, 2),
        "reliability_current": curr_reliability,
        "reliability_previous": prev_reliability,
    }


def load_checklist_template(axis: str, checklists_dir: Path | None = None) -> list[dict]:
    """Load manual checklist YAML template for an axis."""
    if checklists_dir is None:
        checklists_dir = Path(settings.config_dir) / "scoring" / "checklists"

    file_map = {
        "cost_performance": "cost_performance.yaml",
        "safety": "safety_manual.yaml",
        "uniqueness": "uniqueness.yaml",
        "practicality": "practicality_manual.yaml",
        "localization": "localization.yaml",
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
