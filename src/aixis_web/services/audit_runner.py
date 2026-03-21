"""Audit runner service — provides DB sync helpers and webhook/notification utilities.

After the Chrome extension migration, browser automation is no longer run server-side.
This module retains:
  - In-memory scoring job tracking
  - Webhook & notification event emission
  - DB sync helpers used by the extension API and LLM scorer
"""

import asyncio
import logging
import threading
from datetime import datetime, timezone
from typing import Any

from ..config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# In-memory registry of scoring jobs (lightweight tracking)
# ---------------------------------------------------------------------------

_running_jobs: dict[str, dict[str, Any]] = {}
_lock = threading.RLock()


def get_running_job(session_id: str) -> dict[str, Any] | None:
    with _lock:
        return _running_jobs.get(session_id)


def register_job(session_id: str, **kwargs: Any) -> None:
    with _lock:
        _running_jobs[session_id] = {
            "session_id": session_id,
            "status": "running",
            "started_at": datetime.now(timezone.utc).isoformat(),
            **kwargs,
        }


def update_job(session_id: str, **kwargs: Any) -> None:
    with _lock:
        if session_id in _running_jobs:
            _running_jobs[session_id].update(kwargs)


def cleanup_job(session_id: str, delay_seconds: int = 300) -> None:
    """Remove job from registry after a delay (so status can still be polled)."""
    def _cleanup():
        import time
        time.sleep(delay_seconds)
        with _lock:
            _running_jobs.pop(session_id, None)

    threading.Thread(target=_cleanup, daemon=True).start()


# ---------------------------------------------------------------------------
# Webhook & notification event emission
# ---------------------------------------------------------------------------

def emit_audit_events_sync(
    db_session_id: str,
    tool_name: str,
    event_type: str,
    total_planned: int = 0,
    total_executed: int = 0,
    error: str | None = None,
) -> None:
    """Emit webhook and notification events for audit lifecycle (sync context)."""
    payload = {
        "event": event_type,
        "db_session_id": db_session_id,
        "tool_name": tool_name,
        "total_planned": total_planned,
        "total_executed": total_executed,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if error:
        payload["error"] = error

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(
            _emit_audit_events_async(settings.database_url, db_session_id, payload, event_type, tool_name)
        )
    except Exception:
        logger.warning("Failed to emit async audit events for %s", db_session_id)
    finally:
        loop.close()


async def _emit_audit_events_async(
    db_url: str,
    db_session_id: str,
    payload: dict,
    event_type: str,
    tool_name: str,
) -> None:
    """Async helper to emit webhook and notification events."""
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession as _AsyncSession
    from sqlalchemy.orm import sessionmaker
    from .webhook_service import emit_event
    from .notification_service import dispatch_notification

    engine = create_async_engine(db_url, echo=False)
    async_session = sessionmaker(engine, class_=_AsyncSession, expire_on_commit=False)

    async with async_session() as db:
        try:
            await emit_event(event_type, payload, db)
            await db.commit()
        except Exception:
            logger.warning("Webhook emit failed for %s", event_type)
            await db.rollback()

        try:
            from sqlalchemy import text
            result = await db.execute(
                text("SELECT initiated_by FROM audit_sessions WHERE id = :sid"),
                {"sid": db_session_id},
            )
            row = result.fetchone()
            if row and row[0]:
                user_id = row[0]
                if "completed" in event_type:
                    title_jp = f"監査完了: {tool_name}"
                    title_en = f"Audit completed: {tool_name}"
                elif "failed" in event_type:
                    title_jp = f"監査失敗: {tool_name}"
                    title_en = f"Audit failed: {tool_name}"
                else:
                    title_jp = f"監査更新: {tool_name}"
                    title_en = f"Audit update: {tool_name}"

                await dispatch_notification(
                    db=db,
                    user_id=user_id,
                    event_type=event_type,
                    title=title_en,
                    title_jp=title_jp,
                    link=f"/dashboard/audits/{db_session_id}",
                )
                await db.commit()
        except Exception:
            logger.warning("Notification dispatch failed for %s", event_type)
            await db.rollback()

    await engine.dispose()
