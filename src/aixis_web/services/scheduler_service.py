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
    _drip_counter = 0
    while not _scheduler_stop.wait(interval):
        try:
            _check_due_schedules()
        except Exception:
            logger.exception("Scheduler check failed")

        # Drip campaign: check at a lower cadence than audit schedules
        # (every ~10 cycles; at default 60s interval that's every 10 min).
        # Drip emails are day-granularity so polling any more often wastes DB work.
        _drip_counter += 1
        if _drip_counter % 10 == 0:
            try:
                _check_due_drip_emails()
            except Exception:
                logger.exception("Drip email check failed")

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


# ---------------------------------------------------------------------------
# Drip campaign (Phase 5 — 2026-04-15 free-registration pivot)
# ---------------------------------------------------------------------------

# Maps the next drip_stage transition to its elapsed-days threshold.
# drip_stage=1 means "day 0 welcome sent" (fires on verification).
# drip_stage=2 means "day 3 industry-top email sent", etc.
_DRIP_STAGE_DAYS = {
    2: 3,   # Day 3: industry top-tools
    3: 7,   # Day 7: advisory intro
    4: 14,  # Day 14: free consult
    5: 30,  # Day 30: benchmark pitch
}

# Max users processed per scheduler tick — keeps a single cycle bounded even if
# a long-running backlog has built up.
_DRIP_BATCH_LIMIT = 20


def _check_due_drip_emails():
    """Find registered users due for their next drip email and send it.

    Uses the existing Tool/User model read path — drip stages are tracked
    on `users.drip_stage` (integer 0-5). The scheduler advances one stage
    per user per tick and commits after each send so a partial failure
    can't lose progress.
    """
    from sqlalchemy import create_engine, text
    from ..config import settings
    from .email_service import (
        send_drip_industry_top5,
        send_drip_advisory_intro,
        send_drip_free_consult,
        send_drip_benchmark_pitch,
    )

    sync_url = settings.database_url.replace(
        "sqlite+aiosqlite", "sqlite"
    ).replace("postgresql+asyncpg", "postgresql+psycopg2")
    engine = create_engine(sync_url, pool_pre_ping=True)

    now = datetime.now(timezone.utc)
    sent_counts: dict[int, int] = {stage: 0 for stage in _DRIP_STAGE_DAYS}

    try:
        for next_stage, days in _DRIP_STAGE_DAYS.items():
            cutoff = now - timedelta(days=days)
            # Read eligible users in one transaction; release before we
            # spend time rendering emails and hitting SMTP.
            with engine.begin() as read_conn:
                rows = read_conn.execute(
                    text(
                        "SELECT id, email, name, industry "
                        "FROM users "
                        "WHERE email_verified_at IS NOT NULL "
                        "  AND marketing_opt_in = :opt_in "
                        "  AND is_active = :active "
                        "  AND drip_stage = :prev_stage "
                        "  AND email_verified_at <= :cutoff "
                        "  AND role = 'client' "
                        "ORDER BY email_verified_at ASC "
                        "LIMIT :batch"
                    ),
                    {
                        "opt_in": True,
                        "active": True,
                        "prev_stage": next_stage - 1,
                        "cutoff": cutoff.isoformat(),
                        "batch": _DRIP_BATCH_LIMIT,
                    },
                ).fetchall()

            for row in rows:
                user_id, email, name, industry = row
                # Stage dispatch may run a read query (day-3 top-5 lookup),
                # so give it its own short-lived tx.
                try:
                    with engine.begin() as lookup_conn:
                        _send_drip_for_stage(
                            next_stage, name or "", email, industry,
                            send_drip_industry_top5=send_drip_industry_top5,
                            send_drip_advisory_intro=send_drip_advisory_intro,
                            send_drip_free_consult=send_drip_free_consult,
                            send_drip_benchmark_pitch=send_drip_benchmark_pitch,
                            conn=lookup_conn,
                        )
                except Exception:
                    logger.exception(
                        "Drip email failed for user %s stage=%s — leaving drip_stage unchanged",
                        email, next_stage,
                    )
                    continue

                # Advance stage + stamp last-sent timestamp in its own tx
                # so a crash after the send doesn't cause a re-send next cycle.
                try:
                    with engine.begin() as write_conn:
                        write_conn.execute(
                            text(
                                "UPDATE users SET drip_stage = :stage, "
                                "drip_last_sent_at = :now WHERE id = :id"
                            ),
                            {
                                "stage": next_stage,
                                "now": now.isoformat(),
                                "id": user_id,
                            },
                        )
                    sent_counts[next_stage] += 1
                except Exception:
                    logger.exception(
                        "Drip email sent but stage bump failed for user %s "
                        "(will retry next cycle)",
                        email,
                    )
    finally:
        engine.dispose()

    total = sum(sent_counts.values())
    if total > 0:
        logger.info("Drip campaign: sent %d emails this cycle (%s)", total, sent_counts)


def _send_drip_for_stage(
    stage: int,
    user_name: str,
    user_email: str,
    industry_slug: str | None,
    *,
    send_drip_industry_top5,
    send_drip_advisory_intro,
    send_drip_free_consult,
    send_drip_benchmark_pitch,
    conn,
) -> None:
    """Dispatch the appropriate drip email for a given stage."""
    from sqlalchemy import text

    if stage == 2:
        # Day 3: industry top-5
        industry_label = None
        top_tools: list[dict] = []
        if industry_slug:
            try:
                industry_row = conn.execute(
                    text("SELECT id, name_jp FROM industry_tags WHERE slug = :slug"),
                    {"slug": industry_slug},
                ).fetchone()
                if industry_row:
                    industry_id, industry_label = industry_row
                    rows = conn.execute(
                        text(
                            "SELECT t.name_jp, t.vendor, t.slug, ts.overall_grade "
                            "FROM tools t "
                            "JOIN tool_industry_mappings m ON m.tool_id = t.id "
                            "LEFT JOIN tool_scores ts ON ts.tool_id = t.id AND ts.version = ("
                            "  SELECT MAX(version) FROM tool_scores WHERE tool_id = t.id"
                            ") "
                            "WHERE m.industry_id = :iid AND m.fit_level = 'recommended' "
                            "  AND t.is_public = :pub AND t.is_active = :act "
                            "ORDER BY ts.overall_score DESC NULLS LAST LIMIT 5"
                        ),
                        {"iid": industry_id, "pub": True, "act": True},
                    ).fetchall()
                    top_tools = [
                        {
                            "name_jp": r[0],
                            "vendor": r[1] or "",
                            "slug": r[2],
                            "overall_grade": r[3] or "-",
                        }
                        for r in rows
                    ]
            except Exception:
                logger.debug("Drip day-3 lookup failed (sqlite NULLS LAST may not be supported)", exc_info=True)
                # SQLite doesn't support NULLS LAST — retry without it.
                try:
                    industry_row = conn.execute(
                        text("SELECT id, name_jp FROM industry_tags WHERE slug = :slug"),
                        {"slug": industry_slug},
                    ).fetchone()
                    if industry_row:
                        industry_id, industry_label = industry_row
                        rows = conn.execute(
                            text(
                                "SELECT t.name_jp, t.vendor, t.slug, ts.overall_grade "
                                "FROM tools t "
                                "JOIN tool_industry_mappings m ON m.tool_id = t.id "
                                "LEFT JOIN tool_scores ts ON ts.tool_id = t.id AND ts.version = ("
                                "  SELECT MAX(version) FROM tool_scores WHERE tool_id = t.id"
                                ") "
                                "WHERE m.industry_id = :iid AND m.fit_level = 'recommended' "
                                "  AND t.is_public = :pub AND t.is_active = :act "
                                "ORDER BY COALESCE(ts.overall_score, 0) DESC LIMIT 5"
                            ),
                            {"iid": industry_id, "pub": True, "act": True},
                        ).fetchall()
                        top_tools = [
                            {
                                "name_jp": r[0],
                                "vendor": r[1] or "",
                                "slug": r[2],
                                "overall_grade": r[3] or "-",
                            }
                            for r in rows
                        ]
                except Exception:
                    logger.warning("Drip day-3 fallback lookup also failed", exc_info=True)
        send_drip_industry_top5(user_name, user_email, industry_label, top_tools)
    elif stage == 3:
        send_drip_advisory_intro(user_name, user_email)
    elif stage == 4:
        send_drip_free_consult(user_name, user_email)
    elif stage == 5:
        send_drip_benchmark_pitch(user_name, user_email)
    else:
        raise ValueError(f"Unknown drip stage: {stage}")


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
