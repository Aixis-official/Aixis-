"""Lead-scoring service — behavior-based activity tracking.

Introduced with the 2026-04-15 free-registration pivot. The platform DB
is now a lead-acquisition funnel feeding Aixis Advisory Audit. This module
records the meaningful actions a visitor takes and accumulates a
per-user ``lead_score`` used by the in-app Leads Dashboard and by
sales follow-up automation.

Event types and their score deltas (canonical — keep in sync with the
``LeadActivity`` model docstring):

    tool_view          +5
    tool_compare       +10
    safety_axis_view   +15
    governance_view    +15
    pricing_view       +20
    advisory_cta_click +25
    pdf_download       +10
    onboarding_done    +5

Anonymous events are recorded against a session-cookie ID (``aixis_sid``)
and reattached to the user on registration — that way a visitor who
spent a week browsing before signing up starts with a meaningful score.
A running total of 50 or more qualifies as a "hot lead".
"""

import hashlib
import json
import logging
import secrets
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.lead_activity import LeadActivity
from ..db.models.user import User

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Event catalog
# ---------------------------------------------------------------------------

EVENT_TOOL_VIEW = "tool_view"
EVENT_TOOL_COMPARE = "tool_compare"
EVENT_SAFETY_AXIS_VIEW = "safety_axis_view"
EVENT_GOVERNANCE_VIEW = "governance_view"
EVENT_PRICING_VIEW = "pricing_view"
EVENT_ADVISORY_CTA_CLICK = "advisory_cta_click"
EVENT_PDF_DOWNLOAD = "pdf_download"
EVENT_ONBOARDING_DONE = "onboarding_done"

SCORE_DELTAS: dict[str, int] = {
    EVENT_TOOL_VIEW: 5,
    EVENT_TOOL_COMPARE: 10,
    EVENT_SAFETY_AXIS_VIEW: 15,
    EVENT_GOVERNANCE_VIEW: 15,
    EVENT_PRICING_VIEW: 20,
    EVENT_ADVISORY_CTA_CLICK: 25,
    EVENT_PDF_DOWNLOAD: 10,
    EVENT_ONBOARDING_DONE: 5,
}

# A running lead_score at or above this threshold qualifies the user
# as a "hot lead" — shown first in the Leads Dashboard and eligible for
# proactive sales outreach.
HOT_LEAD_THRESHOLD = 50

# Debounce window for tool_view / page-view style events so a single
# visitor hammering refresh does not inflate their score. Anything shorter
# than this gap against the same (user_or_session, event, tool_slug) is
# skipped silently.
_DEDUP_WINDOW_SECONDS = 300  # 5 minutes

# Events that should be debounced — anything that happens on page load.
# Intentional actions (compare, pdf download, advisory click) are not
# debounced because each invocation is meaningful.
_DEDUPED_EVENTS: frozenset[str] = frozenset(
    {
        EVENT_TOOL_VIEW,
        EVENT_SAFETY_AXIS_VIEW,
        EVENT_GOVERNANCE_VIEW,
        EVENT_PRICING_VIEW,
    }
)


# ---------------------------------------------------------------------------
# Session ID (anonymous cookie)
# ---------------------------------------------------------------------------

ANONYMOUS_SESSION_COOKIE = "aixis_sid"
_SESSION_ID_TTL_DAYS = 90


def generate_session_id() -> str:
    """Generate a new random session id for an anonymous visitor."""
    return secrets.token_urlsafe(32)


def hash_ip(ip: str | None) -> str | None:
    """Return a SHA-256 hash of the IP address for privacy-preserving logging.

    We never persist raw IPs — the hash is sufficient for uniqueness and
    abuse detection, and avoids the compliance hit of treating the IP as
    personal data at rest.
    """
    if not ip:
        return None
    return hashlib.sha256(ip.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Tracking
# ---------------------------------------------------------------------------


async def track_activity(
    db: AsyncSession,
    *,
    event_type: str,
    user_id: str | None = None,
    session_id: str | None = None,
    tool_slug: str | None = None,
    page_path: str | None = None,
    metadata: dict[str, Any] | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    score_delta: int | None = None,
) -> LeadActivity | None:
    """Record a lead activity event.

    Returns the newly-created ``LeadActivity`` row, or ``None`` if the
    event was debounced away (same user/session + event + tool_slug
    inside the dedup window).

    The caller must commit — the service only stages writes so the HTTP
    handler can group them into a single transaction.

    At least one of ``user_id`` or ``session_id`` must be supplied, or
    the event is dropped (no one to attribute it to).

    If ``score_delta`` is omitted, the canonical delta for ``event_type``
    is used. Unknown event types default to 0 (recorded but no scoring).
    """
    if not user_id and not session_id:
        logger.debug("track_activity: dropping event with no subject")
        return None

    if score_delta is None:
        score_delta = SCORE_DELTAS.get(event_type, 0)

    now = datetime.now(timezone.utc)

    # Debounce — skip near-duplicate passive events so refresh-mashing
    # does not inflate lead scores.
    if event_type in _DEDUPED_EVENTS:
        if await _recent_duplicate_exists(
            db,
            event_type=event_type,
            user_id=user_id,
            session_id=session_id,
            tool_slug=tool_slug,
            now=now,
        ):
            return None

    metadata_json: str | None = None
    if metadata:
        try:
            metadata_json = json.dumps(metadata, ensure_ascii=False)
        except (TypeError, ValueError):
            metadata_json = None

    row = LeadActivity(
        user_id=user_id,
        session_id=session_id,
        event_type=event_type,
        score_delta=score_delta,
        tool_slug=tool_slug,
        page_path=page_path,
        metadata_json=metadata_json,
        ip_hash=hash_ip(ip),
        user_agent=(user_agent or "")[:500] or None,
        created_at=now,
    )
    db.add(row)
    await db.flush()

    # Attributed events bump the user's running score and refresh
    # last_active_at for the dashboard "recently active" filter.
    if user_id and score_delta:
        await db.execute(
            update(User)
            .where(User.id == user_id)
            .values(
                lead_score=(User.lead_score + score_delta),
                last_active_at=now,
            )
        )
    elif user_id:
        # Still update last_active_at even for zero-score events
        await db.execute(
            update(User).where(User.id == user_id).values(last_active_at=now)
        )

    return row


async def _recent_duplicate_exists(
    db: AsyncSession,
    *,
    event_type: str,
    user_id: str | None,
    session_id: str | None,
    tool_slug: str | None,
    now: datetime,
) -> bool:
    """Return True if an identical event was recorded within the dedup window."""
    from datetime import timedelta

    cutoff = now - timedelta(seconds=_DEDUP_WINDOW_SECONDS)

    conditions = [
        LeadActivity.event_type == event_type,
        LeadActivity.created_at >= cutoff,
    ]

    # Prefer user_id attribution if available; otherwise fall back to session_id.
    if user_id:
        conditions.append(LeadActivity.user_id == user_id)
    else:
        conditions.append(LeadActivity.session_id == session_id)

    if tool_slug is None:
        conditions.append(LeadActivity.tool_slug.is_(None))
    else:
        conditions.append(LeadActivity.tool_slug == tool_slug)

    result = await db.execute(select(LeadActivity.id).where(*conditions).limit(1))
    return result.first() is not None


# ---------------------------------------------------------------------------
# Anonymous → registered reattachment
# ---------------------------------------------------------------------------


async def reattach_anonymous_activities(
    db: AsyncSession,
    *,
    user_id: str,
    session_id: str | None,
) -> int:
    """Assign anonymous LeadActivity rows to a newly-registered user.

    Called from the registration flow once the new ``User`` row exists.
    Rows with a matching ``session_id`` and no ``user_id`` get their
    ``user_id`` set, and the user's ``lead_score`` is recalculated from
    the full (now-attributed) activity history.

    Returns the number of rows reattached.
    """
    if not session_id:
        return 0

    # 1. Find unattributed activity for this session
    result = await db.execute(
        select(LeadActivity).where(
            LeadActivity.session_id == session_id,
            LeadActivity.user_id.is_(None),
        )
    )
    rows = result.scalars().all()
    if not rows:
        return 0

    # 2. Assign them to the user
    await db.execute(
        update(LeadActivity)
        .where(
            LeadActivity.session_id == session_id,
            LeadActivity.user_id.is_(None),
        )
        .values(user_id=user_id)
    )

    # 3. Recompute lead_score from the full attributed set (safer than adding
    # deltas — guarantees consistency even if some rows were already attributed).
    total_result = await db.execute(
        select(func.coalesce(func.sum(LeadActivity.score_delta), 0)).where(
            LeadActivity.user_id == user_id
        )
    )
    total_score = int(total_result.scalar() or 0)

    now = datetime.now(timezone.utc)
    await db.execute(
        update(User)
        .where(User.id == user_id)
        .values(lead_score=total_score, last_active_at=now)
    )

    return len(rows)


# ---------------------------------------------------------------------------
# Queries for the Leads Dashboard
# ---------------------------------------------------------------------------


async def get_recent_activities_for_user(
    db: AsyncSession,
    user_id: str,
    limit: int = 50,
) -> list[LeadActivity]:
    """Return the most-recent activities for a single user, newest first."""
    result = await db.execute(
        select(LeadActivity)
        .where(LeadActivity.user_id == user_id)
        .order_by(LeadActivity.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


def is_hot_lead(user: User) -> bool:
    """Return True if the user's accumulated score clears the hot threshold."""
    return (user.lead_score or 0) >= HOT_LEAD_THRESHOLD
