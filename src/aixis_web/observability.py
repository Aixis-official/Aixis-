"""Sentry initialization — opt-in via SENTRY_DSN env var.

Phase C-1: integrate Sentry for error tracking and performance monitoring.

The init is a strict no-op when:
  * the `sentry_sdk` package is not installed, OR
  * `SENTRY_DSN` is unset / empty.

This lets us land the wiring before the DSN is provisioned without forcing
the dependency on every dev environment.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def init_sentry() -> bool:
    """Initialise Sentry if `SENTRY_DSN` is set. Returns True on success."""
    dsn = os.environ.get("SENTRY_DSN", "").strip()
    if not dsn:
        return False

    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.starlette import StarletteIntegration
        from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
        from sentry_sdk.integrations.logging import LoggingIntegration
    except ImportError:
        logger.info("SENTRY_DSN is set but sentry-sdk is not installed; skipping init")
        return False

    environment = os.environ.get("SENTRY_ENVIRONMENT", "production")
    release = os.environ.get("SENTRY_RELEASE") or os.environ.get("RAILWAY_GIT_COMMIT_SHA", "")
    traces_sample_rate = float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0.05"))
    profiles_sample_rate = float(os.environ.get("SENTRY_PROFILES_SAMPLE_RATE", "0.0"))

    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        release=release or None,
        # Privacy: never ship user PII to Sentry. Send_default_pii=False is the
        # default but we set it explicitly so a future SDK upgrade can't change it.
        send_default_pii=False,
        # Performance: sample a small fraction of traces. Override via env.
        traces_sample_rate=traces_sample_rate,
        profiles_sample_rate=profiles_sample_rate,
        integrations=[
            StarletteIntegration(transaction_style="endpoint"),
            FastApiIntegration(transaction_style="endpoint"),
            SqlalchemyIntegration(),
            LoggingIntegration(level=logging.INFO, event_level=logging.ERROR),
        ],
        before_send=_scrub_event,
    )
    logger.info("Sentry initialised (env=%s, traces=%.2f)", environment, traces_sample_rate)
    return True


# Header / cookie keys whose values must never reach Sentry.
_SENSITIVE_KEYS = frozenset(
    k.lower()
    for k in (
        "authorization",
        "cookie",
        "set-cookie",
        "x-api-key",
        "x-csrf-token",
        "proxy-authorization",
    )
)


def _scrub_event(event, hint):
    """Strip auth headers / cookies before sending to Sentry."""
    request = event.get("request") if isinstance(event, dict) else None
    if isinstance(request, dict):
        headers = request.get("headers")
        if isinstance(headers, dict):
            for k in list(headers.keys()):
                if k.lower() in _SENSITIVE_KEYS:
                    headers[k] = "[redacted]"
        # Cookies are also serialised separately by some SDK versions.
        if "cookies" in request:
            request["cookies"] = "[redacted]"
    return event
