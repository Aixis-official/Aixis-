"""Score merging service - combines automated and manual scores."""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import yaml

from ..db.models.score import AxisScoreRecord, ToolPublishedScore, ScoreHistory, ManualChecklistRecord
from ..db.models.audit import AuditSession
from ..db.models.tool import Tool
from ..config import settings
from aixis_agent.core.enums import OverallGrade, ScoreAxis

logger = logging.getLogger(__name__)

# Auto/manual mix ratios per axis
AXIS_MIX = {
    "practicality":     {"auto": 0.4, "manual": 0.6},
    "cost_performance": {"auto": 0.3, "manual": 0.7},
    "localization":     {"auto": 0.7, "manual": 0.3},
    "safety":           {"auto": 0.35, "manual": 0.65},
    "uniqueness":       {"auto": 0.4, "manual": 0.6},
}


async def get_auto_scores(db: AsyncSession, session_id: str) -> tuple[dict[str, float], set[str]]:
    """Get automated scores for all axes from a session.

    Returns (scores_dict, manual_edit_axes) where manual_edit_axes contains
    axes that were directly edited (should bypass AXIS_MIX blending).

    The LLM scorer stores scores with source='llm' or 'hybrid', while the
    legacy agent scorer uses source='auto'. The manual editor uses 'manual_edit'.
    """
    result = await db.execute(
        select(AxisScoreRecord).where(
            AxisScoreRecord.session_id == session_id,
            AxisScoreRecord.source.in_(["auto", "llm", "hybrid", "manual_edit"]),
        )
    )
    scores = {}
    manual_edit_axes = set()
    for record in result.scalars():
        if record.source == "manual_edit":
            # Manual edits are final scores — no blending needed
            scores[record.axis] = record.score
            manual_edit_axes.add(record.axis)
            continue

        # Use the raw auto_score if available in details (stored by LLM scorer),
        # otherwise use the record score directly.
        # Guard: if details.auto_score is 0 but record.score > 0, prefer record.score
        # (handles legacy data where error scores corrupted the details JSON).
        auto_score = record.score
        if record.details:
            import json as _json
            try:
                details = record.details if isinstance(record.details, dict) else _json.loads(record.details)
                if "auto_score" in details:
                    detail_score = float(details["auto_score"])
                    # Only use details.auto_score if it's non-zero or record.score is also zero
                    if detail_score > 0 or record.score == 0:
                        auto_score = detail_score
            except (TypeError, ValueError, _json.JSONDecodeError):
                pass
        scores[record.axis] = auto_score
    return scores, manual_edit_axes


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
    auto_scores, manual_edit_axes = await get_auto_scores(db, session_id)
    manual_scores = await get_manual_scores(db, session_id)

    final = {}
    for axis, mix in AXIS_MIX.items():
        # Manual edits are final — bypass AXIS_MIX blending entirely
        if axis in manual_edit_axes:
            final[axis] = auto_scores.get(axis, 0.0)
            final[axis] = max(0.0, min(5.0, round(final[axis], 1)))
            continue

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

    # Overall score: always average across all 5 axes (even if some are 0)
    all_axis_count = len(AXIS_MIX)  # Always 5
    overall = round(sum(final.get(a, 0.0) for a in AXIS_MIX) / all_axis_count, 1)

    # Check completion rate — apply grade cap if insufficient
    session_obj_q = await db.execute(select(AuditSession).where(AuditSession.id == session_id))
    session_for_completion = session_obj_q.scalar_one_or_none()
    _total_planned = session_for_completion.total_planned if session_for_completion else 0
    _total_executed = session_for_completion.total_executed if session_for_completion else 0
    _completion_rate = _total_executed / _total_planned if _total_planned > 0 else 1.0

    grade = OverallGrade.from_score(overall)
    grade_value = grade.value

    if _total_planned > 0 and _completion_rate < 0.5:
        # Low completion: cap grade based on completion rate
        # < 30% → cap at C, 30-50% → cap at B (prevents inflated grades from few tests)
        if _completion_rate < 0.3:
            max_grade = "C"
        else:
            max_grade = "B"
        grade_order = ["S", "A", "B", "C", "D"]
        if grade_order.index(grade_value) < grade_order.index(max_grade):
            grade_value = max_grade

    # Determine next version
    existing = await db.execute(
        select(ToolPublishedScore).where(ToolPublishedScore.tool_id == tool_id).order_by(ToolPublishedScore.version.desc()).limit(1)
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

    # Mark tool as public so it appears in the public database
    tool_result = await db.execute(select(Tool).where(Tool.id == tool_id))
    tool_obj = tool_result.scalar_one_or_none()
    if tool_obj and not tool_obj.is_public:
        tool_obj.is_public = True

    await db.commit()
    await db.refresh(published)

    # Generate public-facing analysis summaries using LLM (best-effort)
    try:
        await generate_public_summaries(db, session_id)
    except Exception:
        logger.warning("Failed to generate public summaries for session %s", session_id)

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
    current_scores, _ = await get_auto_scores(db, current_session_id)
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

    prev_scores, _ = await get_auto_scores(db, prev_session.id)
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


async def generate_public_summaries(db: AsyncSession, session_id: str) -> None:
    """Generate public-facing analysis summaries using LLM.

    Takes raw audit strengths/risks (which may contain meta observations)
    and rewrites them as objective, user-facing insights. Stores results
    back in AxisScoreRecord.details['public_highlights'] and ['public_concerns'].
    """
    try:
        import anthropic
    except ImportError:
        logger.warning("anthropic package not available, skipping public summary generation")
        return

    api_key = settings.anthropic_api_key
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY not set, skipping public summary generation")
        return

    result = await db.execute(
        select(AxisScoreRecord).where(AxisScoreRecord.session_id == session_id)
    )
    records = list(result.scalars())
    if not records:
        return

    client = anthropic.Anthropic(api_key=api_key)
    model = settings.ai_scoring_model or "claude-haiku-4-5-20251001"

    for record in records:
        axis_name = AXIS_NAMES_JP.get(record.axis, record.axis)
        strengths = record.strengths or []
        risks = record.risks or []
        if isinstance(strengths, str):
            try:
                strengths = json.loads(strengths)
            except Exception:
                strengths = []
        if isinstance(risks, str):
            try:
                risks = json.loads(risks)
            except Exception:
                risks = []

        if not strengths and not risks:
            continue

        raw_strengths = "\n".join(f"- {s}" if isinstance(s, str) else f"- {s.get('text', str(s))}" for s in strengths)
        raw_risks = "\n".join(f"- {r}" if isinstance(r, str) else f"- {r.get('text', str(r))}" for r in risks)

        prompt = f"""あなたはAIツール比較メディアのプロライターです。以下は「{axis_name}」軸の監査で得られた生データです。これを顧客（企業の導入検討者）が読むレポートに書き換えます。

## 強み（生データ）
{raw_strengths or "（なし）"}

## リスク・懸念（生データ）
{raw_risks or "（なし）"}

## 書き換えルール（厳守）
1. **顧客目線で書く**: 「導入すると〜できる」「〜に便利」など、利用者が得る価値として表現
2. **禁止ワード**: 以下を含む記述は完全に除外すること
   - 「画像から判断不可」「確認できない」「テストでは」「観察N」「スクリーンショット」
   - 「未計測」「不十分」「情報不足」「判断できない」「計測データ」
   - その他、監査プロセスや評価手法に言及する表現すべて
3. **確認できたことだけ書く**: 不明・未確認の事項は記載しない（「〜は不明」とは書かない）
4. **簡潔に**: 1項目20〜40文字。体言止めまたは「〜に対応」「〜が可能」形式
5. **具体的に**: 「高品質」のような曖昧語ではなく、何がどう良い/悪いかを示す

JSONのみ回答（説明不要）:
{{"highlights": ["強み1", "強み2"], "concerns": ["懸念1", "懸念2"]}}
各2〜3項目。確認できた事実がない場合は空配列。"""

        try:
            response = client.messages.create(
                model=model,
                max_tokens=500,
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text.strip()
            # Extract JSON from response
            if "```" in text:
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
                text = text.strip()
            parsed = json.loads(text)

            # Store in details
            details = record.details or {}
            if isinstance(details, str):
                try:
                    details = json.loads(details)
                except Exception:
                    details = {}
            details["public_highlights"] = parsed.get("highlights", [])
            details["public_concerns"] = parsed.get("concerns", [])
            record.details = details

        except Exception as e:
            logger.warning("Failed to generate public summary for axis %s: %s", record.axis, e)
            continue

    await db.commit()


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
