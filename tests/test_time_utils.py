"""Contract tests for aixis_web._time shared helpers."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from aixis_web._time import as_aware_utc, utc_now


class TestAsAwareUTC:
    def test_none_passthrough(self) -> None:
        assert as_aware_utc(None) is None

    def test_naive_datetime_is_tagged_utc(self) -> None:
        naive = datetime(2026, 4, 20, 12, 0, 0)
        result = as_aware_utc(naive)
        assert result is not None
        assert result.utcoffset() == timedelta(0)
        assert result == datetime(2026, 4, 20, 12, 0, 0, tzinfo=timezone.utc)

    def test_aware_utc_passthrough(self) -> None:
        aware = datetime(2026, 4, 20, 12, 0, 0, tzinfo=timezone.utc)
        assert as_aware_utc(aware) == aware

    def test_aware_other_tz_converted(self) -> None:
        jst = timezone(timedelta(hours=9))
        aware = datetime(2026, 4, 20, 21, 0, 0, tzinfo=jst)
        result = as_aware_utc(aware)
        assert result == datetime(2026, 4, 20, 12, 0, 0, tzinfo=timezone.utc)

    def test_iso_string_without_offset(self) -> None:
        assert as_aware_utc("2026-04-20T12:00:00") == datetime(
            2026, 4, 20, 12, 0, 0, tzinfo=timezone.utc
        )

    def test_iso_string_with_offset(self) -> None:
        assert as_aware_utc("2026-04-20T21:00:00+09:00") == datetime(
            2026, 4, 20, 12, 0, 0, tzinfo=timezone.utc
        )

    def test_subtraction_against_utc_now_never_raises(self) -> None:
        """Pin the original Sentry crash line."""
        now = utc_now()
        trial_end = as_aware_utc(datetime(2026, 4, 20, 12, 0, 0))
        assert trial_end is not None
        _ = (trial_end - now).days  # must not raise TypeError


class TestUtcNow:
    def test_returns_aware_utc(self) -> None:
        now = utc_now()
        assert now.tzinfo is not None
        assert now.utcoffset() == timedelta(0)
