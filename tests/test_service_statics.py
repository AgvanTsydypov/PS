"""
Unit tests for pure static methods on SeasonWorkbenchService
(admin_backend/main.py).

These are imported directly from the class without instantiating it,
so no DB connection is made.
"""

from datetime import datetime, timezone
import pytest


def _svc():
    """Return the SeasonWorkbenchService class without instantiating it."""
    import sys
    # admin_backend.main instantiates service at module level — import only the class
    # by reaching into the module after it has been imported via conftest mocks.
    import importlib
    import unittest.mock as mock

    # Patch the three __init__ helpers that reach out to DB so import succeeds.
    with (
        mock.patch("scripts.data_loading_manager.DataLoadingManager._ensure_tables"),
        mock.patch("scripts.season_manager.SeasonManager.__init__", return_value=None),
        mock.patch("scripts.daily_scheduler_simple.SimplifiedScheduler.__init__", return_value=None),
    ):
        import admin_backend.main as m
    return m.SeasonWorkbenchService


# ---------------------------------------------------------------------------
# fmt_remaining
# ---------------------------------------------------------------------------

class TestFmtRemaining:
    @property
    def fmt(self):
        return _svc().fmt_remaining

    def test_zero_or_negative(self):
        assert self.fmt(0) == "0s"
        assert self.fmt(-10) == "0s"

    def test_seconds_only(self):
        assert self.fmt(45) == "45s"

    def test_one_minute(self):
        assert self.fmt(60) == "1m 0s"

    def test_minutes_and_seconds(self):
        assert self.fmt(90) == "1m 30s"

    def test_one_hour(self):
        assert self.fmt(3600) == "1h 0m 0s"

    def test_hours_minutes_seconds(self):
        assert self.fmt(3661) == "1h 1m 1s"

    def test_one_day(self):
        assert self.fmt(86400) == "1d 0m 0s"

    def test_full_combination(self):
        # 1d + 1h + 1m + 1s = 86400 + 3600 + 60 + 1 = 90061
        assert self.fmt(90061) == "1d 1h 1m 1s"

    def test_fractional_seconds_truncated(self):
        # float input — should truncate, not round
        assert self.fmt(90.9) == "1m 30s"


# ---------------------------------------------------------------------------
# parse_iso_datetime_utc
# ---------------------------------------------------------------------------

class TestParseIsoDatetimeUtc:
    @property
    def parse(self):
        return _svc().parse_iso_datetime_utc

    def test_z_suffix_treated_as_utc(self):
        dt = self.parse("2025-06-15T12:00:00Z")
        assert dt.tzinfo == timezone.utc
        assert dt.hour == 12

    def test_explicit_utc_offset(self):
        dt = self.parse("2025-06-15T12:00:00+00:00")
        assert dt.tzinfo == timezone.utc

    def test_positive_offset_converted_to_utc(self):
        dt = self.parse("2025-06-15T14:00:00+02:00")
        assert dt.hour == 12  # 14:00+02 → 12:00 UTC

    def test_naive_datetime_assumed_utc(self):
        dt = self.parse("2025-06-15T12:00:00")
        assert dt.tzinfo == timezone.utc
        assert dt.hour == 12

    def test_empty_string_raises(self):
        with pytest.raises(ValueError, match="empty"):
            self.parse("")

    def test_whitespace_only_raises(self):
        with pytest.raises(ValueError):
            self.parse("   ")

    def test_result_is_always_utc(self):
        for s in (
            "2025-01-01T00:00:00Z",
            "2025-01-01T00:00:00+00:00",
            "2025-01-01T05:00:00+05:00",
        ):
            dt = self.parse(s)
            assert dt.tzinfo == timezone.utc


# ---------------------------------------------------------------------------
# fmt_dt
# ---------------------------------------------------------------------------

class TestFmtDt:
    @property
    def fmt(self):
        return _svc().fmt_dt

    def test_none_returns_na(self):
        assert self.fmt(None) == "n/a"

    def test_utc_datetime_formatted(self):
        dt = datetime(2025, 6, 15, 12, 30, 0, tzinfo=timezone.utc)
        assert self.fmt(dt) == "2025-06-15 12:30:00 UTC"

    def test_naive_datetime_treated_as_utc(self):
        dt = datetime(2025, 6, 15, 12, 30, 0)
        result = self.fmt(dt)
        assert "2025-06-15 12:30:00 UTC" == result


# ---------------------------------------------------------------------------
# _resolve_stream_for_season_id
# ---------------------------------------------------------------------------

class TestResolveStreamForSeasonId:
    @property
    def resolve(self):
        return _svc()._resolve_stream_for_season_id

    def test_finds_genesis_stream(self):
        eligibility = {"genesis": {"season_id": 5, "type": "genesis"}}
        result = self.resolve(eligibility, 5)
        assert result == {"season_id": 5, "type": "genesis"}

    def test_finds_standard_stream(self):
        eligibility = {
            "genesis": {"season_id": 1},
            "standard": {"season_id": 7},
        }
        result = self.resolve(eligibility, 7)
        assert result == {"season_id": 7}

    def test_returns_none_when_not_found(self):
        eligibility = {"genesis": {"season_id": 1}, "standard": {"season_id": 2}}
        assert self.resolve(eligibility, 99) is None

    def test_returns_none_for_empty_eligibility(self):
        assert self.resolve({}, 1) is None

    def test_genesis_takes_precedence_when_both_match(self):
        # Both streams have same season_id — genesis found first
        eligibility = {
            "genesis": {"season_id": 3},
            "standard": {"season_id": 3},
        }
        result = self.resolve(eligibility, 3)
        assert result == {"season_id": 3}

    def test_non_dict_stream_ignored(self):
        eligibility = {"genesis": None, "standard": {"season_id": 2}}
        result = self.resolve(eligibility, 2)
        assert result == {"season_id": 2}
