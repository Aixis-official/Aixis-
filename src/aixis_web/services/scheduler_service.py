"""Background scheduler for periodic audit re-runs."""

import logging
import threading
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

_scheduler_thread: threading.Thread | None = None
_scheduler_stop = threading.Event()


def start_scheduler():
    """Start the background scheduler. Called from app lifespan."""
    from ..config import settings
    if not settings.scheduler_enabled:
        logger.info("Audit scheduler is disabled via config")
        return
    global _scheduler_thread
    _scheduler_stop.clear()
    _scheduler_thread = threading.Thread(
        target=_scheduler_loop, daemon=True, name="audit-scheduler"
    )
    _scheduler_thread.start()
    logger.info("Audit scheduler started (interval=%ds)", settings.scheduler_check_interval_seconds)


def stop_scheduler():
    """Stop the background scheduler."""
    _scheduler_stop.set()
    if _scheduler_thread:
        _scheduler_thread.join(timeout=5)
    logger.info("Audit scheduler stopped")


def _scheduler_loop():
    """Main loop: check for due schedules at configured interval."""
    from ..config import settings
    interval = settings.scheduler_check_interval_seconds
    _cleanup_counter = 0
    while not _scheduler_stop.wait(interval):
        try:
            _check_due_schedules()
        except Exception:
            logger.exception("Scheduler check failed")

        # Periodic DB cleanup: run every ~12 cycles (1 hour at 300s interval)
        _cleanup_counter += 1
        if _cleanup_counter % 12 == 0:
            try:
                _periodic_db_cleanup()
            except Exception:
                logger.debug("Periodic cleanup failed", exc_info=True)


def _periodic_db_cleanup():
    """Clean up expired rate limit entries and revoked tokens."""
    from sqlalchemy import create_engine, text
    from ..config import settings

    sync_url = settings.database_url.replace(
        "sqlite+aiosqlite", "sqlite"
    ).replace("postgresql+asyncpg", "postgresql+psycopg2")
    engine = create_engine(sync_url, pool_pre_ping=True)

    now = datetime.now(timezone.utc)
    try:
        with engine.begin() as conn:
            # Clean up expired rate limit entries (older than 24h)
            cutoff = now - timedelta(hours=24)
            rl_result = conn.execute(
                text("DELETE FROM rate_limit_entries WHERE created_at < :cutoff"),
                {"cutoff": cutoff.isoformat()},
            )
            rl_count = rl_result.rowcount or 0

            # Clean up expired revoked tokens
            rt_result = conn.execute(
                text("DELETE FROM revoked_tokens WHERE expires_at < :now"),
                {"now": now.isoformat()},
            )
            rt_count = rt_result.rowcount or 0

            if rl_count > 0 or rt_count > 0:
                logger.info(
                    "Periodic cleanup: deleted %d rate_limit_entries, %d revoked_tokens",
                    rl_count, rt_count,
                )
    except Exception:
        logger.debug("Periodic DB cleanup tables may not exist yet", exc_info=True)
    finally:
        engine.dispose()


def _check_due_schedules():
    """Find and trigger all due audit schedules."""
    from sqlalchemy import create_engine, text

    from ..config import settings

    sync_url = settings.database_url.replace(
        "sqlite+aiosqlite", "sqlite"
    ).replace("postgresql+asyncpg", "postgresql+psycopg2")
    engine = create_engine(sync_url, pool_pre_ping=True)

    now = datetime.now(timezone.utc)
    try:
        with engine.begin() as conn:
            rows = conn.execute(
                text(
                    "SELECT s.id, s.tool_id, s.profile_id, s.categories, s.cron_expression, "
                    "t.name as tool_name "
                    "FROM audit_schedules s JOIN tools t ON s.tool_id = t.id "
                    "WHERE s.is_active = true AND s.next_run_at <= :now"
                ),
                {"now": now.isoformat()},
            ).fetchall()

            for row in rows:
                try:
                    _trigger_scheduled_audit(conn, row, now)
                except Exception:
                    logger.exception("Failed to trigger schedule %s", row[0])
    finally:
        engine.dispose()


def _trigger_scheduled_audit(conn, row, now):
    """Trigger a single scheduled audit (now a no-op with logging)."""
    from sqlalchemy import text

    schedule_id, tool_id, profile_id, categories_json, cron_expr, tool_name = row

    # Server-side audit runner has been removed; audits now run via Chrome extension.
    logger.error(
        "Scheduled audit %s for tool %s skipped: server-side audit runner has been "
        "removed. Audits now run via Chrome extension. Disable this schedule or "
        "migrate to the extension-based workflow.",
        schedule_id,
        tool_name,
    )

    # Still update next_run so the scheduler doesn't re-trigger every cycle
    next_run = _calculate_next_run(cron_expr, now)
    conn.execute(
        text(
            """
        UPDATE audit_schedules
        SET last_run_at = :now, next_run_at = :next
        WHERE id = :id
    """
        ),
        {
            "now": now.isoformat(),
            "next": next_run.isoformat() if next_run else None,
            "id": schedule_id,
        },
    )


def _calculate_next_run(
    cron_expression: str, from_time: datetime
) -> datetime | None:
    """Calculate next run time from cron expression. Simple parser for common patterns."""
    # Simple cron parser without external dependencies
    # Supports: minute hour day_of_month month day_of_week
    try:
        parts = cron_expression.strip().split()
        if len(parts) != 5:
            return from_time + timedelta(days=1)  # fallback: daily

        minute, hour, dom, month, dow = parts

        # Simple daily pattern: "M H * * *"
        if dom == "*" and month == "*" and dow == "*":
            target_hour = int(hour) if hour != "*" else 0
            target_minute = int(minute) if minute != "*" else 0
            next_dt = from_time.replace(
                hour=target_hour, minute=target_minute, second=0, microsecond=0
            )
            if next_dt <= from_time:
                next_dt += timedelta(days=1)
            return next_dt

        # Weekly pattern: "M H * * D" (D = 0-6, 0=Sun)
        if dom == "*" and month == "*" and dow != "*":
            cron_dow = int(dow.split("-")[0].split(",")[0])  # take first value
            # Convert cron dow (0=Sun, 6=Sat) to Python weekday (0=Mon, 6=Sun)
            target_dow = (cron_dow + 6) % 7  # Sun(0)->6, Mon(1)->0, Sat(6)->5
            target_hour = int(hour) if hour != "*" else 0
            target_minute = int(minute) if minute != "*" else 0
            days_ahead = target_dow - from_time.weekday()
            if days_ahead < 0:
                days_ahead += 7
            next_dt = from_time + timedelta(days=days_ahead)
            next_dt = next_dt.replace(
                hour=target_hour, minute=target_minute, second=0, microsecond=0
            )
            # If same day but time already passed, jump to next week
            if next_dt <= from_time:
                next_dt += timedelta(days=7)
            return next_dt

        # Monthly: "M H D * *"
        if dom != "*" and month == "*" and dow == "*":
            target_day = int(dom)
            target_hour = int(hour) if hour != "*" else 0
            target_minute = int(minute) if minute != "*" else 0
            next_dt = from_time.replace(
                day=min(target_day, 28),
                hour=target_hour,
                minute=target_minute,
                second=0,
                microsecond=0,
            )
            if next_dt <= from_time:
                if from_time.month == 12:
                    next_dt = next_dt.replace(year=from_time.year + 1, month=1)
                else:
                    next_dt = next_dt.replace(month=from_time.month + 1)
            return next_dt

        # Fallback: daily
        return from_time + timedelta(days=1)
    except Exception:
        return from_time + timedelta(days=1)
