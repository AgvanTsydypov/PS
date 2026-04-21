"""
Unit tests for the Scenarios tab service logic and Pydantic models.

Covers:
  - SimulateGeneratedCardsBatchRequest field validation
  - SeasonWorkbenchService.parse_iso_datetime_utc
  - SeasonWorkbenchService.apply_advanced_scenario (validation only, DB mocked)
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError


# ---------------------------------------------------------------------------
# SimulateGeneratedCardsBatchRequest
# ---------------------------------------------------------------------------

class TestSimulateGeneratedCardsBatchRequest:
    @pytest.fixture(autouse=True)
    def _import(self):
        from admin_backend.main import SimulateGeneratedCardsBatchRequest
        self.Model = SimulateGeneratedCardsBatchRequest

    def test_defaults(self):
        req = self.Model()
        assert req.max_count == 50
        assert req.origin_match_fraction == pytest.approx(0.1)
        assert req.maximum_diversity is True
        assert req.request_id is None

    def test_max_count_min_boundary(self):
        req = self.Model(max_count=1)
        assert req.max_count == 1

    def test_max_count_max_boundary(self):
        req = self.Model(max_count=200)
        assert req.max_count == 200

    def test_max_count_zero_rejected(self):
        with pytest.raises(ValidationError):
            self.Model(max_count=0)

    def test_max_count_above_200_rejected(self):
        with pytest.raises(ValidationError):
            self.Model(max_count=201)

    def test_origin_match_fraction_zero_valid(self):
        req = self.Model(origin_match_fraction=0.0)
        assert req.origin_match_fraction == pytest.approx(0.0)

    def test_origin_match_fraction_one_valid(self):
        req = self.Model(origin_match_fraction=1.0)
        assert req.origin_match_fraction == pytest.approx(1.0)

    def test_origin_match_fraction_negative_rejected(self):
        with pytest.raises(ValidationError):
            self.Model(origin_match_fraction=-0.01)

    def test_origin_match_fraction_above_one_rejected(self):
        with pytest.raises(ValidationError):
            self.Model(origin_match_fraction=1.01)

    def test_maximum_diversity_false_accepted(self):
        req = self.Model(maximum_diversity=False)
        assert req.maximum_diversity is False

    def test_request_id_accepted(self):
        req = self.Model(request_id="abc-123")
        assert req.request_id == "abc-123"

    def test_all_fields_together(self):
        req = self.Model(
            max_count=100,
            origin_match_fraction=0.5,
            maximum_diversity=False,
            request_id="run-42",
        )
        assert req.max_count == 100
        assert req.origin_match_fraction == pytest.approx(0.5)
        assert req.maximum_diversity is False
        assert req.request_id == "run-42"


# ---------------------------------------------------------------------------
# SeasonWorkbenchService.parse_iso_datetime_utc
# ---------------------------------------------------------------------------

class TestParseIsoDt:
    @pytest.fixture(autouse=True)
    def _import(self):
        from admin_backend.main import SeasonWorkbenchService
        self.fn = SeasonWorkbenchService.parse_iso_datetime_utc

    def test_empty_string_raises(self):
        with pytest.raises(ValueError):
            self.fn("")

    def test_whitespace_only_raises(self):
        with pytest.raises(ValueError):
            self.fn("   ")

    def test_valid_iso_date_only(self):
        dt = self.fn("2025-01-15")
        assert dt.year == 2025
        assert dt.month == 1
        assert dt.day == 15

    def test_z_suffix_becomes_utc(self):
        dt = self.fn("2025-06-01T12:00:00Z")
        assert dt.tzinfo is not None
        assert dt.utcoffset().total_seconds() == 0

    def test_offset_aware_converted_to_utc(self):
        # parse_iso_datetime_utc always returns UTC via .astimezone(utc)
        dt = self.fn("2025-06-01T12:00:00+03:00")
        assert dt.tzinfo == timezone.utc
        assert dt.hour == 9  # 12:00+03 → 09:00 UTC

    def test_naive_datetime_gets_utc(self):
        dt = self.fn("2025-06-01T12:00:00")
        assert dt.tzinfo == timezone.utc

    def test_returns_datetime_instance(self):
        dt = self.fn("2025-01-01")
        assert isinstance(dt, datetime)


# ---------------------------------------------------------------------------
# SeasonWorkbenchService.apply_advanced_scenario
# ---------------------------------------------------------------------------

def _make_service():
    """Return a SeasonWorkbenchService instance without calling __init__."""
    from admin_backend.main import SeasonWorkbenchService
    svc = SeasonWorkbenchService.__new__(SeasonWorkbenchService)
    svc.manager = MagicMock()
    return svc


def _make_req(**overrides):
    from admin_backend.main import AdvancedScenarioRequest
    defaults = {
        "season_id": 1,
        "season_number": 1,
        "total_supply": 100,
        "remaining_supply": 50,
        "start_date_iso": "2025-01-01T00:00:00Z",
        "end_date_iso": "2025-04-01T00:00:00Z",
        "is_active": True,
        "is_completed": False,
    }
    defaults.update(overrides)
    return AdvancedScenarioRequest(**defaults)


class TestApplyAdvancedScenarioValidation:
    def test_season_number_zero_raises(self):
        svc = _make_service()
        with pytest.raises(ValueError, match="season_number"):
            svc.apply_advanced_scenario(_make_req(season_number=0))

    def test_season_number_negative_raises(self):
        svc = _make_service()
        with pytest.raises(ValueError, match="season_number"):
            svc.apply_advanced_scenario(_make_req(season_number=-5))

    def test_total_supply_zero_raises(self):
        svc = _make_service()
        with pytest.raises(ValueError, match="total_supply"):
            svc.apply_advanced_scenario(_make_req(total_supply=0, remaining_supply=0))

    def test_total_supply_negative_raises(self):
        svc = _make_service()
        with pytest.raises(ValueError, match="total_supply"):
            svc.apply_advanced_scenario(_make_req(total_supply=-1, remaining_supply=0))

    def test_remaining_supply_negative_raises(self):
        svc = _make_service()
        with pytest.raises(ValueError, match="remaining_supply"):
            svc.apply_advanced_scenario(_make_req(remaining_supply=-1))

    def test_remaining_supply_exceeds_total_raises(self):
        svc = _make_service()
        with pytest.raises(ValueError, match="remaining_supply"):
            svc.apply_advanced_scenario(_make_req(total_supply=100, remaining_supply=101))

    def test_remaining_supply_equals_total_is_valid(self):
        svc = _make_service()
        mock_cursor = MagicMock()
        mock_cursor.__enter__ = lambda s: s
        mock_cursor.__exit__ = MagicMock(return_value=False)
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        svc.manager.get_connection.return_value = mock_conn
        # Should not raise
        svc.apply_advanced_scenario(_make_req(total_supply=100, remaining_supply=100))

    def test_remaining_supply_zero_is_valid(self):
        svc = _make_service()
        mock_cursor = MagicMock()
        mock_cursor.__enter__ = lambda s: s
        mock_cursor.__exit__ = MagicMock(return_value=False)
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        svc.manager.get_connection.return_value = mock_conn
        svc.apply_advanced_scenario(_make_req(total_supply=100, remaining_supply=0))

    def test_end_date_before_start_raises(self):
        svc = _make_service()
        with pytest.raises(ValueError, match="end_date"):
            svc.apply_advanced_scenario(
                _make_req(
                    start_date_iso="2025-06-01T00:00:00Z",
                    end_date_iso="2025-01-01T00:00:00Z",
                )
            )

    def test_end_date_equal_to_start_raises(self):
        svc = _make_service()
        with pytest.raises(ValueError, match="end_date"):
            svc.apply_advanced_scenario(
                _make_req(
                    start_date_iso="2025-01-01T00:00:00Z",
                    end_date_iso="2025-01-01T00:00:00Z",
                )
            )

    def test_valid_request_calls_db_update(self):
        svc = _make_service()
        mock_cursor = MagicMock()
        mock_cursor.__enter__ = lambda s: s
        mock_cursor.__exit__ = MagicMock(return_value=False)
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        svc.manager.get_connection.return_value = mock_conn

        svc.apply_advanced_scenario(_make_req())

        mock_cursor.execute.assert_called_once()
        mock_conn.commit.assert_called_once()

    def test_valid_request_passes_season_id_to_db(self):
        svc = _make_service()
        mock_cursor = MagicMock()
        mock_cursor.__enter__ = lambda s: s
        mock_cursor.__exit__ = MagicMock(return_value=False)
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        svc.manager.get_connection.return_value = mock_conn

        svc.apply_advanced_scenario(_make_req(season_id=99))

        call_args = mock_cursor.execute.call_args[0]
        params = call_args[1]
        assert 99 in params

    def test_season_number_one_is_valid(self):
        svc = _make_service()
        mock_cursor = MagicMock()
        mock_cursor.__enter__ = lambda s: s
        mock_cursor.__exit__ = MagicMock(return_value=False)
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        svc.manager.get_connection.return_value = mock_conn
        # Should not raise
        svc.apply_advanced_scenario(_make_req(season_number=1))
