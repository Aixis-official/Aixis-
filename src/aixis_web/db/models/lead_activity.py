"""Lead activity tracking for behavior-based lead scoring."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Text

from ..base import Base


def new_uuid():
    return str(uuid.uuid4())


class LeadActivity(Base):
    """Records meaningful actions by registered users for lead-scoring.

    Event types (canonical slugs):
      - tool_view          +5
      - tool_compare       +10
      - safety_axis_view   +15
      - governance_view    +15
      - pricing_view       +20
      - advisory_cta_click +25
      - pdf_download       +10
      - onboarding_done    +5

    Score accumulates into ``users.lead_score``; 50+ marks a hot lead.
    Both user-attributed and anonymous (session_id) events are recorded —
    anonymous rows are reattached on registration if session_id matches.
    """

    __tablename__ = "lead_activities"

    id = Column(String(36), primary_key=True, default=new_uuid)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=True)  # nullable for pre-register events
    session_id = Column(String(64), nullable=True)  # anonymous tracking id (cookie)
    event_type = Column(String(50), nullable=False)
    score_delta = Column(Integer, nullable=False, default=0)

    # Context
    tool_slug = Column(String(200), nullable=True)
    page_path = Column(String(500), nullable=True)
    metadata_json = Column(Text, nullable=True)  # arbitrary JSON payload

    # Request metadata
    ip_hash = Column(String(64), nullable=True)  # SHA-256 of IP (privacy)
    user_agent = Column(String(500), nullable=True)

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_lead_activities_user_id", "user_id"),
        Index("ix_lead_activities_session_id", "session_id"),
        Index("ix_lead_activities_event_type", "event_type"),
        Index("ix_lead_activities_created_at", "created_at"),
    )
