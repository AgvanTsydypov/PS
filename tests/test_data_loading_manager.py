"""
Unit tests for data_loading_manager module-level helpers and DataLoadingManager.get_loading_dates.
No database connection is required.
"""

import os
from datetime import date, timedelta
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Module-level pure helpers
# ---------------------------------------------------------------------------

class TestEnvDate:
    def test_returns_default_when_env_not_set(self):
        from scripts.data_loading_manager import _env_date
        default = date(2024, 1, 1)
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("__NONEXISTENT__", None)
            result = _env_date("__NONEXISTENT__", default)
        assert result == default

    def test_parses_valid_iso_date(self):
        from scripts.data_loading_manager import _env_date
        with patch.dict(os.environ, {"TEST_DATE": "2025-06-15"}):
            result = _env_date("TEST_DATE", date(2000, 1, 1))
        assert result == date(2025, 6, 15)

    def test_raises_on_invalid_format(self):
        from scripts.data_loading_manager import _env_date
        with patch.dict(os.environ, {"TEST_DATE": "15-06-2025"}):
            with pytest.raises(ValueError, match="YYYY-MM-DD"):
                _env_date("TEST_DATE", date(2000, 1, 1))

    def test_returns_default_for_empty_string(self):
        from scripts.data_loading_manager import _env_date
        default = date(2024, 3, 1)
        with patch.dict(os.environ, {"TEST_DATE": "   "}):
            result = _env_date("TEST_DATE", default)
        assert result == default


class TestEnvInt:
    def test_returns_default_when_not_set(self):
        from scripts.data_loading_manager import _env_int
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("__NONEXISTENT_INT__", None)
            result = _env_int("__NONEXISTENT_INT__", 42)
        assert result == 42

    def test_parses_integer(self):
        from scripts.data_loading_manager import _env_int
        with patch.dict(os.environ, {"TEST_INT": "100"}):
            assert _env_int("TEST_INT", 0) == 100

    def test_strips_whitespace(self):
        from scripts.data_loading_manager import _env_int
        with patch.dict(os.environ, {"TEST_INT": "  7  "}):
            assert _env_int("TEST_INT", 0) == 7


class TestEnvIntOrNone:
    def test_returns_default_when_not_set(self):
        from scripts.data_loading_manager import _env_int_or_none
        os.environ.pop("__NI__", None)
        assert _env_int_or_none("__NI__", 5) == 5

    def test_returns_none_for_none_string(self):
        from scripts.data_loading_manager import _env_int_or_none
        for value in ("none", "None", "null", "NULL", ""):
            with patch.dict(os.environ, {"TEST_NI": value}):
                assert _env_int_or_none("TEST_NI", 99) is None

    def test_parses_integer(self):
        from scripts.data_loading_manager import _env_int_or_none
        with patch.dict(os.environ, {"TEST_NI": "250"}):
            assert _env_int_or_none("TEST_NI", None) == 250


# ---------------------------------------------------------------------------
# DataLoadingManager.get_loading_dates (pure arithmetic, no DB call)
# ---------------------------------------------------------------------------

class TestGetLoadingDates:
    def setup_method(self):
        from scripts.data_loading_manager import DataLoadingManager, EVENTS_LAG_DAYS, DATA_LAG_DAYS
        self.manager = DataLoadingManager.__new__(DataLoadingManager)
        self.events_lag = EVENTS_LAG_DAYS
        self.data_lag = DATA_LAG_DAYS

    def test_reference_date_is_returned(self):
        from scripts.data_loading_manager import DataLoadingManager
        manager = DataLoadingManager.__new__(DataLoadingManager)
        ref = date(2025, 4, 15)
        result = manager.get_loading_dates(ref)
        assert result["reference_date"] == ref

    def test_events_date_is_lag_days_before_reference(self):
        from scripts.data_loading_manager import DataLoadingManager, EVENTS_LAG_DAYS
        manager = DataLoadingManager.__new__(DataLoadingManager)
        ref = date(2025, 4, 15)
        result = manager.get_loading_dates(ref)
        assert result["events_date"] == ref - timedelta(days=EVENTS_LAG_DAYS)

    def test_redemptions_date_is_data_lag_before_reference(self):
        from scripts.data_loading_manager import DataLoadingManager, DATA_LAG_DAYS
        manager = DataLoadingManager.__new__(DataLoadingManager)
        ref = date(2025, 4, 15)
        result = manager.get_loading_dates(ref)
        assert result["redemptions_date"] == ref - timedelta(days=DATA_LAG_DAYS)

    def test_uses_today_when_no_reference_given(self):
        from scripts.data_loading_manager import DataLoadingManager
        manager = DataLoadingManager.__new__(DataLoadingManager)
        result = manager.get_loading_dates()
        assert result["reference_date"] == date.today()
