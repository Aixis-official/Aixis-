"""Background service for trial expiration management.

Runs in a daemon thread (same pattern as scheduler_service.py).
Checks hourly for:
  1. Trials expiring in N days → send reminder email
  2. Expired trials → deactivate account + notify
"""

import logging
import threading
from datetime import datetime, timedelta, timezone

from .._time import as_aware_utc as _as_aware_utc, utc_now

logger = logging.getLogger(__name__)

_checker_thread: threading.Thread | None = None
_checker_stop = threading.Event()


def start_trial_checker():
    """Start the background trial checker. Called from app lifespan."""
    from ..config import settings
    if not settings.trial_checker_enabled:
        logger.info("Trial checker is disabled via config")
        return
    global _checker_thread
    _checker_stop.clear()
    _checker_thread = threading.Thread(
        target=_checker_loop, daemon=True, name="trial-checker"
    )
    _checker_thread.start()
    logger.info(
        "Trial checker started (interval=%ds, reminder=%dd before)",
        settings.trial_checker_interval_seconds,
        settings.trial_reminder_days_before,
    )


def stop_trial_checker():
    """Stop the background trial checker."""
    _checker_stop.set()
    if _checker_thread:
        _checker_thread.join(timeout=5)
    logger.info("Trial checker stopped")


def _checker_loop():
    """Main loop: check for expiring/expired trials at configured interval."""
    from ..config import settings
    interval = settings.trial_checker_interval_seconds
    # Run immediately on start, then every interval
    try:
        _run_trial_checks()
    except Exception:
        logger.exception("Initial trial check failed")
    while not _checker_stop.wait(interval):
        try:
            _run_trial_checks()
        except Exception:
            logger.exception("Trial check failed")


def _run_trial_checks():
    """Find and process expiring and expired trials."""
    from sqlalchemy import create_engine, text
    from ..config import settings

    sync_url = settings.database_url.replace(
        "sqlite+aiosqlite", "sqlite"
    ).replace("postgresql+asyncpg", "postgresql+psycopg2")
    engine = create_engine(sync_url, pool_pre_ping=True)

    now = utc_now()
    reminder_cutoff = now + timedelta(days=settings.trial_reminder_days_before)

    try:
        with engine.begin() as conn:
            # 1. Send reminders for trials expiring within N days
            _send_reminders(conn, now, reminder_cutoff)

            # 2. Expire overdue trials
            _expire_trials(conn, now)
    finally:
        engine.dispose()


def _send_reminders(conn, now, reminder_cutoff):
    """Send reminder emails for trials about to expire."""
    from sqlalchemy import text
    from ..config import settings
    from .email_service import send_trial_reminder_email

    rows = conn.execute(
        text(
            "SELECT id, name, email, trial_end FROM users "
            "WHERE role = 'client' "
            "AND account_status = 'active' "
            "AND trial_end IS NOT NULL "
            "AND trial_end <= :cutoff "
            "AND trial_end > :now "
            "AND (trial_reminder_sent IS NULL OR trial_reminder_sent = false)"
        ),
        {"cutoff": reminder_cutoff.isoformat(), "now": now.isoformat()},
    ).fetchall()

    for row in rows:
        user_id, name, email, trial_end_raw = row
        try:
            trial_end = _as_aware_utc(trial_end_raw)
            days_remaining = max(0, (trial_end - now).days)
            send_trial_reminder_email(name, email, days_remaining)

            conn.execute(
                text("UPDATE users SET trial_reminder_sent = true WHERE id = :id"),
                {"id": user_id},
            )
            logger.info("Trial reminder sent to %s (%d days remaining)", email, days_remaining)
        except Exception:
            logger.exception("Failed to send trial reminder to %s", email)


def _expire_trials(conn, now):
    """Deactivate expired trial accounts."""
    from sqlalchemy import text
    from .email_service import send_trial_expired_email

    rows = conn.execute(
        text(
            "SELECT id, name, email FROM users "
            "WHERE role = 'client' "
            "AND account_status = 'active' "
            "AND trial_end IS NOT NULL "
            "AND trial_end <= :now"
        ),
        {"now": now.isoformat()},
    ).fetchall()

    for row in rows:
        user_id, name, email = row
        try:
            conn.execute(
                text(
                    "UPDATE users SET is_active = false, account_status = 'expired' "
                    "WHERE id = :id"
                ),
                {"id": user_id},
            )
            send_trial_expired_email(name, email)
            logger.info("Trial expired: %s (%s) deactivated", name, email)
        except Exception:
            logger.exception("Failed to expire trial for %s", email)
