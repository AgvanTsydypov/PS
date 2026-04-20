"""
Unit tests for SeasonManager phase logic and PhaseResult.

All DB interactions are replaced by direct mock of _fetch_season so no
live PostgreSQL connection is needed.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest


def _make_season(
    season_type: str = "standard",
    total_supply: int = 100,
    remaining_supply: int = 100,
    days_since_start: float = 0.0,
) -> dict:
    """Return a minimal season dict as returned by SeasonManager._fetch_season."""
    start = datetime.now(timezone.utc) - timedelta(days=days_since_start)
    return {
        "id": 1,
        "type": season_type,
        "start_date": start,
        "total_supply": total_supply,
        "remaining_supply": remaining_supply,
        "is_active": True,
    }


def _get_phase(season_dict: dict) -> dict:
    from scripts.season_manager import SeasonManager
    sm = SeasonManager.__new__(SeasonManager)
    with patch.object(sm, "_fetch_season", return_value=season_dict):
        return sm.get_current_phase(1)


# ---------------------------------------------------------------------------
# PhaseResult
# ---------------------------------------------------------------------------

class TestPhaseResult:
    def test_to_dict_contains_all_keys(self):
        from scripts.season_manager import PhaseResult
        pr = PhaseResult(
            phase="breach",
            is_claim_open=True,
            requires_origin=False,
            reason="test",
            season_type="standard",
            supply_remaining=80,
            supply_total=100,
        )
        d = pr.to_dict()
        assert set(d.keys()) == {
            "phase", "is_claim_open", "requires_origin",
            "reason", "season_type", "supply_remaining", "supply_total",
        }

    def test_to_dict_values_match(self):
        from scripts.season_manager import PhaseResult
        pr = PhaseResult(
            phase="vault",
            is_claim_open=True,
            requires_origin=True,
            reason="vault active",
            season_type="genesis",
            supply_remaining=50,
            supply_total=200,
        )
        d = pr.to_dict()
        assert d["phase"] == "vault"
        assert d["requires_origin"] is True
        assert d["supply_remaining"] == 50


# ---------------------------------------------------------------------------
# Standard season phase transitions
# ---------------------------------------------------------------------------

class TestStandardSeasonPhases:
    def test_before_season_starts(self):
        season = _make_season(days_since_start=-1.0)  # starts in 1 day
        result = _get_phase(season)
        assert result["phase"] == "transmission"
        assert result["is_claim_open"] is False

    def test_breach_on_day_1(self):
        season = _make_season(days_since_start=0.5)
        result = _get_phase(season)
        assert result["phase"] == "breach"
        assert result["is_claim_open"] is True
        assert result["requires_origin"] is False

    def test_breach_on_day_2(self):
        season = _make_season(days_since_start=1.5)
        result = _get_phase(season)
        assert result["phase"] == "breach"

    def test_vault_on_day_4(self):
        season = _make_season(days_since_start=3.5)
        result = _get_phase(season)
        assert result["phase"] == "vault"
        assert result["requires_origin"] is True

    def test_vault_on_day_5(self):
        season = _make_season(days_since_start=4.5)
        result = _get_phase(season)
        assert result["phase"] == "vault"

    def test_scavenge_on_day_7(self):
        season = _make_season(days_since_start=6.5)
        result = _get_phase(season)
        assert result["phase"] == "scavenge"
        assert result["requires_origin"] is False

    def test_scavenge_on_day_9(self):
        season = _make_season(days_since_start=8.5)
        result = _get_phase(season)
        assert result["phase"] == "scavenge"

    def test_transmission_on_day_10(self):
        season = _make_season(days_since_start=9.5)
        result = _get_phase(season)
        assert result["phase"] == "transmission"
        assert result["is_claim_open"] is False

    def test_after_day_10_cycle_ended(self):
        season = _make_season(days_since_start=11.0)
        result = _get_phase(season)
        assert result["phase"] == "transmission"
        assert result["is_claim_open"] is False

    def test_supply_exhausted_returns_transmission(self):
        season = _make_season(days_since_start=1.0, remaining_supply=0)
        result = _get_phase(season)
        assert result["phase"] == "transmission"
        assert result["is_claim_open"] is False

    def test_breach_cap_reached_moves_to_vault(self):
        # 20% cap of 100 = 20; used = 80 (remaining 20) → cap not yet at 80 used
        # Used = total - remaining = 100 - 15 = 85 ≥ 20 (cap), so breach cap reached
        season = _make_season(
            days_since_start=1.0,
            total_supply=100,
            remaining_supply=15,  # used=85, cap=20 → cap reached
        )
        result = _get_phase(season)
        assert result["phase"] == "vault"

    def test_supply_remaining_reported_correctly(self):
        season = _make_season(remaining_supply=42, total_supply=100, days_since_start=1.0)
        result = _get_phase(season)
        assert result["supply_remaining"] == 42
        assert result["supply_total"] == 100


# ---------------------------------------------------------------------------
# Genesis season phase transitions
# ---------------------------------------------------------------------------

class TestGenesisSeasonPhases:
    def test_genesis_breach_on_day_1(self):
        season = _make_season(season_type="genesis", days_since_start=0.5)
        result = _get_phase(season)
        assert result["phase"] == "breach"
        assert result["season_type"] == "genesis"

    def test_genesis_vault_on_day_4(self):
        season = _make_season(season_type="genesis", days_since_start=3.5)
        result = _get_phase(season)
        assert result["phase"] == "vault"

    def test_genesis_scavenge_from_day_7_onwards(self):
        for day in (6.5, 10.0, 20.0, 50.0):
            season = _make_season(season_type="genesis", days_since_start=day)
            result = _get_phase(season)
            assert result["phase"] == "scavenge", f"Expected scavenge on day {day}"

    def test_genesis_no_transmission_after_day_10(self):
        season = _make_season(season_type="genesis", days_since_start=11.0)
        result = _get_phase(season)
        assert result["phase"] == "scavenge"
        assert result["is_claim_open"] is True
