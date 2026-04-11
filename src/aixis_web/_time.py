"""Datetime helpers shared across services.

Historical context
------------------
SQLAlchemy's ``DateTime(timezone=True)`` column type is honored by Postgres
but not by SQLite (which has no native tz type). Depending on the driver
round-trip we may get back either a naive ``datetime`` or an ISO-8601 string
without offset. Python code that then compares the value against
``datetime.now(timezone.utc)`` trips on

    TypeError: can't subtract offset-naive and offset-aware datetimes

This module provides a single normaliser so every service can speak
offset-aware UTC without repeating the pattern.
"""
from __future__ import annotations

from datetime import datetime, timezone


def as_aware_utc(value: datetime | str | None) -> datetime | None:
    """Normalize a datetime / ISO-8601 string to an offset-aware UTC datetime.

    Accepts:
      * ``None`` → returns ``None`` unchanged (caller decides how to handle)
      * ``str``  → parsed via ``datetime.fromisoformat``
      * naive ``datetime``  → assumed to be UTC, tz attached
      * aware ``datetime``  → converted to UTC if needed

    Returns a ``datetime`` whose ``tzinfo`` is always ``timezone.utc``
    (or ``None`` if the input was ``None``).
    """
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def utc_now() -> datetime:
    """Current UTC time, always offset-aware.

    A thin wrapper around ``datetime.now(timezone.utc)`` so that callers can
    import from one place and make intent explicit at the call site.
    """
    return datetime.now(timezone.utc)
