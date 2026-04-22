"""
Integration tests for season supply and date management:
  SeasonWorkbenchService.apply_remaining_supply
  SeasonWorkbenchService.update_season_dates

apply_remaining_supply writes remaining_supply to the DB and also flips
is_active/is_completed based on the value (0 → deactivate + complete).

update_season_dates writes start_date / end_date as TIMESTAMPTZ.

Cleanup: DELETE season (no FK dependents in these tests).
"""

from datetime import datetime, timezone

import pytest

from tests.integration.conftest import make_real_connection

_SEASON_NUM = 77400


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _fetch_season(season_id: int) -> dict:
    conn = make_real_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT remaining_supply, is_active, is_completed,
                       start_date, end_date
                FROM seasons WHERE id = %s
                """,
                (season_id,),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    assert row is not None, f"season {season_id} not found"
    return dict(zip(
        ("remaining_supply", "is_active", "is_completed", "start_date", "end_date"),
        row,
    ))


# ------------------------------------------------------------------
# Fixture
# ------------------------------------------------------------------

@pytest.fixture()
def supply_season():
    """Active season with total_supply=10, remaining_supply=10."""
    conn = make_real_connection()
    season_id = None
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO seasons
                    (type, season_number, start_date, end_date,
                     total_supply, remaining_supply, is_active)
                VALUES ('standard', %s,
                        '2099-01-01 00:00:00+00',
                        '2099-12-31 00:00:00+00',
                        10, 10, true)
                RETURNING id
                """,
                (_SEASON_NUM,),
            )
            season_id = cur.fetchone()[0]
        conn.commit()
        yield season_id
    except Exception:
        conn.rollback()
        raise
    finally:
        try:
            with conn.cursor() as cur:
                if season_id:
                    cur.execute("DELETE FROM seasons WHERE id = %s", (season_id,))
            conn.commit()
        except Exception:
            conn.rollback()
        finally:
            conn.close()


# ------------------------------------------------------------------
# Tests: apply_remaining_supply
# ------------------------------------------------------------------

class TestApplyRemainingSupply:

    def test_sets_remaining_supply_in_db(self, workbench, supply_season):
        workbench.apply_remaining_supply(supply_season, 7)
        assert _fetch_season(supply_season)["remaining_supply"] == 7

    def test_zero_supply_sets_is_active_false(self, workbench, supply_season):
        workbench.apply_remaining_supply(supply_season, 0)
        assert _fetch_season(supply_season)["is_active"] is False

    def test_zero_supply_sets_is_completed_true(self, workbench, supply_season):
        workbench.apply_remaining_supply(supply_season, 0)
        assert _fetch_season(supply_season)["is_completed"] is True

    def test_positive_supply_leaves_is_active_true(self, workbench, supply_season):
        workbench.apply_remaining_supply(supply_season, 5)
        assert _fetch_season(supply_season)["is_active"] is True

    def test_positive_supply_leaves_is_completed_false(self, workbench, supply_season):
        workbench.apply_remaining_supply(supply_season, 5)
        assert _fetch_season(supply_season)["is_completed"] is False

    def test_supply_update_persisted_across_connections(self, workbench, supply_season):
        workbench.apply_remaining_supply(supply_season, 3)
        # Verify with an independent connection (not the one workbench used)
        season = _fetch_season(supply_season)
        assert season["remaining_supply"] == 3


# ------------------------------------------------------------------
# Tests: update_season_dates
# ------------------------------------------------------------------

class TestUpdateSeasonDates:

    _NEW_START = datetime(2088, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    _NEW_END   = datetime(2088, 6, 15, 12, 0, 0, tzinfo=timezone.utc)

    def test_start_date_updated_in_db(self, workbench, supply_season):
        workbench.update_season_dates(supply_season, self._NEW_START, self._NEW_END)
        row = _fetch_season(supply_season)
        # DB returns timezone-aware datetime; compare as UTC
        stored = row["start_date"].astimezone(timezone.utc)
        assert stored == self._NEW_START

    def test_end_date_updated_in_db(self, workbench, supply_season):
        workbench.update_season_dates(supply_season, self._NEW_START, self._NEW_END)
        row = _fetch_season(supply_season)
        stored = row["end_date"].astimezone(timezone.utc)
        assert stored == self._NEW_END

    def test_dates_stored_as_timezone_aware(self, workbench, supply_season):
        workbench.update_season_dates(supply_season, self._NEW_START, self._NEW_END)
        row = _fetch_season(supply_season)
        assert row["start_date"].tzinfo is not None
        assert row["end_date"].tzinfo is not None

    def test_other_season_fields_unaffected(self, workbench, supply_season):
        before = _fetch_season(supply_season)
        workbench.update_season_dates(supply_season, self._NEW_START, self._NEW_END)
        after = _fetch_season(supply_season)
        assert after["remaining_supply"] == before["remaining_supply"]
        assert after["is_active"] == before["is_active"]
