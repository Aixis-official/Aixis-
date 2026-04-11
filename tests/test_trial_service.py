"""Regression coverage for aixis_web.services.trial_service.

Historical bug (Sentry 2026-04-11): ``_send_reminders`` subtracted an
offset-naive ``trial_end`` from an offset-aware ``now`` and raised
``TypeError: can't subtract offset-naive and offset-aware datetimes``.
The ``_as_aware_utc`` normaliser exists precisely to prevent that
regression — these tests pin its contract.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytest

from aixis_web.services.trial_service import _as_aware_utc


class TestAsAwareUTC:
    def test_naive_datetime_is_assumed_utc(self) -> None:
        naive = datetime(2026, 4, 20, 12, 0, 0)
        result = _as_aware_utc(naive)
        assert result.tzinfo is not None
        assert result.utcoffset() == timedelta(0)
        assert result == datetime(2026, 4, 20, 12, 0, 0, tzinfo=timezone.utc)

    def test_aware_datetime_is_converted_to_utc(self) -> None:
        jst = timezone(timedelta(hours=9))
        aware = datetime(2026, 4, 20, 21, 0, 0, tzinfo=jst)
        result = _as_aware_utc(aware)
        assert result.tzinfo is not None
        assert result.utcoffset() == timedelta(0)
        assert result == datetime(2026, 4, 20, 12, 0, 0, tzinfo=timezone.utc)

    def test_iso_string_without_offset(self) -> None:
        result = _as_aware_utc("2026-04-20T12:00:00")
        assert result == datetime(2026, 4, 20, 12, 0, 0, tzinfo=timezone.utc)

    def test_iso_string_with_offset(self) -> None:
        result = _as_aware_utc("2026-04-20T21:00:00+09:00")
        assert result == datetime(2026, 4, 20, 12, 0, 0, tzinfo=timezone.utc)

    def test_subtraction_against_now_never_raises(self) -> None:
        """The original Sentry error — pin that this path is safe now."""
        now = datetime.now(timezone.utc)
        # SQLite-style naive round-trip
        trial_end = _as_aware_utc(datetime(2026, 4, 20, 12, 0, 0))
        # The line that used to raise TypeError:
        days = (trial_end - now).days
        assert isinstance(days, int)
