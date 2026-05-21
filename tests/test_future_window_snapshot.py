"""
Unit tests for the future-window selection and the season-snapshot cap.

Covers the changes that make the admin "future window" preview and the actual
materialized season agree:

* ``SimplifiedScheduler._get_standard_filtered_event_ids`` (season_id=None)
  - bootstrap anchor projection: with no standard season yet, the window is
    anchored to the *next* season boundary strictly after now (derived from
    genesis_start + bootstrap delay, stepping by the season cycle), not to the
    very first season's start.
  - ``apply_caps`` toggles the TOP20/TAG5 trim (capped) vs the raw windowed
    candidate pool (uncapped).
* ``SimplifiedScheduler._snapshot_origin_wallets_for_season`` passes the capped
  event allowlist to ``refresh_participants_for_season`` for standard seasons,
  and NULL for genesis.
* Static guards on the schema DDL + caller source so the contract can't silently
  drift.

The DB layer is mocked: a tiny recording cursor scripts ``fetchone``/``fetchall``
and captures every ``execute(sql, params)`` so each test controls exactly what
the method sees. ``datetime.now`` is frozen by swapping the module-level
``datetime`` for a subclass.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone, timedelta
from typing import Any, List, Optional, Tuple
from unittest import mock

import pytest

import scripts.daily_scheduler_simple as sched_mod
from scripts.daily_scheduler_simple import (
    SimplifiedScheduler,
    FIRST_STANDARD_BOOTSTRAP_DELAY_DAYS,
    STANDARD_ORIGIN_SNAPSHOT_OFFSET_DAYS,
    ORIGIN_LOOKBACK_DAYS_STANDARD,
    STANDARD_SEASON_CYCLE_DAYS,
    STANDARD_SNAPSHOT_PRIMARY_TAG_CAP,
    STANDARD_SNAPSHOT_EVENT_LIMIT,
)


# ---------------------------------------------------------------------------
# These tests assume the default tuning (no env overrides). Lock that in so a
# changed constant fails loudly here rather than silently shifting the hand
# computed expected dates below.
# ---------------------------------------------------------------------------

def test_default_constants_assumed_by_this_module():
    assert FIRST_STANDARD_BOOTSTRAP_DELAY_DAYS == 10
    assert STANDARD_ORIGIN_SNAPSHOT_OFFSET_DAYS == 0
    assert ORIGIN_LOOKBACK_DAYS_STANDARD == 10
    assert STANDARD_SEASON_CYCLE_DAYS == 10
    assert STANDARD_SNAPSHOT_PRIMARY_TAG_CAP == 5
    assert STANDARD_SNAPSHOT_EVENT_LIMIT == 20


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utc(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=timezone.utc)


class _RecordingCursor:
    """Records execute(sql, params); returns scripted fetchone/fetchall."""

    def __init__(
        self,
        fetchone_results: Optional[List[Any]] = None,
        fetchall_result: Optional[List[Any]] = None,
    ) -> None:
        self.calls: List[Tuple[str, Any]] = []
        self._fetchone_results = list(fetchone_results or [])
        self._fetchall_result = list(fetchall_result or [])

    def execute(self, sql: str, params: Any = None) -> None:
        self.calls.append((sql, params))

    def fetchone(self) -> Any:
        if self._fetchone_results:
            return self._fetchone_results.pop(0)
        return None

    def fetchall(self) -> List[Any]:
        return list(self._fetchall_result)

    # Convenience accessors -------------------------------------------------
    @property
    def last(self) -> Tuple[str, Any]:
        return self.calls[-1]

    def find(self, needle: str) -> Tuple[str, Any]:
        for sql, params in self.calls:
            if needle in sql:
                return sql, params
        raise AssertionError(f"no execute call containing {needle!r}")


def _scheduler() -> SimplifiedScheduler:
    """SimplifiedScheduler bypassing its heavy DB-touching __init__."""
    return SimplifiedScheduler.__new__(SimplifiedScheduler)


class _frozen_now:
    """Context manager: freeze ``datetime.now`` in the scheduler module."""

    def __init__(self, fixed: datetime) -> None:
        self._fixed = fixed

    def __enter__(self):
        fixed = self._fixed

        class _D(datetime):
            @classmethod
            def now(cls, tz=None):  # noqa: D401 - mimic datetime.now
                return fixed if tz is None else fixed.astimezone(tz)

        self._patch = mock.patch.object(sched_mod, "datetime", _D)
        self._patch.start()
        return self

    def __exit__(self, *exc):
        self._patch.stop()
        return False


# ===========================================================================
# Bootstrap anchor projection (season_id=None)
# ===========================================================================

class TestFutureWindowAnchorProjection:
    def _run(self, fetchone_results, *, now, apply_caps=True, fetchall=None):
        cur = _RecordingCursor(
            fetchone_results=fetchone_results,
            fetchall_result=fetchall if fetchall is not None else [{"event_id": "e1"}],
        )
        sch = _scheduler()
        with _frozen_now(now):
            result = sch._get_standard_filtered_event_ids(
                cur, season_id=None, apply_caps=apply_caps
            )
        return result, cur

    def test_no_standard_now_before_first_start_anchors_at_first_season(self):
        # genesis season start May 15 -> Standard #1 starts May 25 (delay 10).
        # now (May 21) < May 25 -> the first season is the upcoming one.
        # window = [May 25 - 10, May 25) = [May 15, May 25).
        _, cur = self._run(
            [None, {"start_date": _utc(2026, 5, 15)}],
            now=_utc(2026, 5, 21),
        )
        _, params = cur.last
        assert params[0] == _utc(2026, 5, 15)
        assert params[1] == _utc(2026, 5, 25)

    def test_no_standard_mid_cycle_projects_to_next_boundary(self):
        # genesis May 5 -> Std#1 May 15. now May 28 -> 13 days in -> 1 full
        # cycle elapsed -> next boundary = May 15 + 2*10 = Jun 4.
        # window = [May 25, Jun 4).
        _, cur = self._run(
            [None, {"start_date": _utc(2026, 5, 5)}],
            now=_utc(2026, 5, 28),
        )
        _, params = cur.last
        assert params[0] == _utc(2026, 5, 25)
        assert params[1] == _utc(2026, 6, 4)

    def test_no_standard_exactly_on_boundary_projects_strictly_after(self):
        # genesis May 5 -> Std#1 May 15. now == May 25 is itself a boundary;
        # the active season just rolled, so the upcoming one is the NEXT
        # boundary -> Jun 4. window = [May 25, Jun 4).
        _, cur = self._run(
            [None, {"start_date": _utc(2026, 5, 5)}],
            now=_utc(2026, 5, 25),
        )
        _, params = cur.last
        assert params[0] == _utc(2026, 5, 25)
        assert params[1] == _utc(2026, 6, 4)

    def test_latest_standard_anchors_on_its_end_date_without_genesis_lookup(self):
        # A standard season exists: anchor on its end_date directly; the
        # genesis fallback must not be queried.
        _, cur = self._run(
            [{"end_date": _utc(2026, 6, 10)}],
            now=_utc(2026, 6, 1),
        )
        _, params = cur.last
        assert params[0] == _utc(2026, 5, 31)
        assert params[1] == _utc(2026, 6, 10)
        # No genesis SELECT was issued.
        assert all("type = 'genesis'" not in sql for sql, _ in cur.calls)

    def test_naive_end_date_is_treated_as_utc(self):
        # A timezone-naive end_date (older rows) must be coerced to UTC, not
        # raise when subtracting timedeltas.
        _, cur = self._run(
            [{"end_date": datetime(2026, 6, 10)}],  # naive
            now=_utc(2026, 6, 1),
        )
        _, params = cur.last
        assert params[1] == _utc(2026, 6, 10)

    def test_no_standard_no_genesis_returns_empty_and_skips_main_query(self):
        result, cur = self._run([None, None], now=_utc(2026, 5, 21))
        assert result == []
        # Only the two lookups ran; the working_events query was never built.
        assert len(cur.calls) == 2
        assert all("working_events" not in sql for sql, _ in cur.calls)


# ===========================================================================
# apply_caps toggle (capped TOP20/TAG5 vs raw windowed pool)
# ===========================================================================

class TestApplyCaps:
    BOOTSTRAP = [None, {"start_date": _utc(2026, 5, 15)}]
    NOW = _utc(2026, 5, 21)

    def _run(self, apply_caps):
        cur = _RecordingCursor(
            fetchone_results=list(self.BOOTSTRAP),
            fetchall_result=[{"event_id": "e1"}, {"event_id": "e2"}],
        )
        sch = _scheduler()
        with _frozen_now(self.NOW):
            result = sch._get_standard_filtered_event_ids(
                cur, season_id=None, apply_caps=apply_caps
            )
        return result, cur

    def test_capped_appends_tag_and_overall_rank_with_cap_and_limit_params(self):
        result, cur = self._run(apply_caps=True)
        sql, params = cur.last
        assert "tag_capped_events" in sql
        assert "overall_rank" in sql
        # window bounds + cap + limit
        assert params == (
            _utc(2026, 5, 15),
            _utc(2026, 5, 25),
            STANDARD_SNAPSHOT_PRIMARY_TAG_CAP,
            STANDARD_SNAPSHOT_EVENT_LIMIT,
        )
        assert result == ["e1", "e2"]

    def test_uncapped_selects_raw_working_events_with_only_window_params(self):
        result, cur = self._run(apply_caps=False)
        sql, params = cur.last
        assert "tag_capped_events" not in sql
        assert "FROM working_events" in sql
        assert params == (_utc(2026, 5, 15), _utc(2026, 5, 25))
        assert result == ["e1", "e2"]


# ===========================================================================
# _snapshot_origin_wallets_for_season passes the cap allowlist
# ===========================================================================

class TestSnapshotAllowlist:
    def _run(self, season_type: str):
        cur = _RecordingCursor(
            fetchone_results=[{"type": season_type}, {"n": 2}],
        )
        sch = _scheduler()

        seen_seasons: List[int] = []

        def fake_filter(cursor, season_id):  # matches keyword call
            seen_seasons.append(season_id)
            return ["e1", "e2"]

        sch._get_standard_filtered_event_ids = fake_filter  # type: ignore[assignment]
        n = sch._snapshot_origin_wallets_for_season(
            cur, season_id=7, season_start_date=_utc(2026, 5, 25)
        )
        return n, cur, seen_seasons

    def test_standard_passes_capped_allowlist_with_text_array_cast(self):
        n, cur, seen = self._run("standard")
        assert n == 2
        assert seen == [7]  # cap selection scoped to this season
        sql, params = cur.find("refresh_participants_for_season")
        assert "%s::text[]" in sql
        # (season_id, window_start, window_end, use_resolution_anchor, allowlist)
        assert params[4] == ["e1", "e2"]
        assert params[3] is True  # resolution-anchored for standard

    def test_genesis_passes_null_allowlist_and_skips_cap_selection(self):
        n, cur, seen = self._run("genesis")
        assert n == 2
        assert seen == []  # cap selection NOT invoked for genesis
        _, params = cur.find("refresh_participants_for_season")
        assert params[4] is None
        assert params[3] is False  # date-anchored for genesis


# ===========================================================================
# Static contract guards (DB-free)
# ===========================================================================

class TestSchemaAndCallerContract:
    @property
    def schema_sql(self) -> str:
        path = os.path.join(
            os.path.dirname(__file__), "..", "sql", "schemas", "create_seasons_system.sql"
        )
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()

    def test_refresh_function_gained_optional_event_allowlist(self):
        sql = self.schema_sql
        # Old 4-arg version is dropped so re-applying leaves no stale overload.
        assert (
            "DROP FUNCTION IF EXISTS refresh_participants_for_season(BIGINT, TIMESTAMPTZ, TIMESTAMPTZ, BOOLEAN);"
            in sql
        )
        # New optional, defaulted allowlist parameter + its filter clause.
        assert "p_event_ids" in sql
        assert "TEXT[] DEFAULT NULL" in sql
        assert "e.id = ANY(p_event_ids)" in sql
        assert "p_event_ids IS NULL OR" in sql

    def test_snapshot_caller_passes_allowlist(self):
        import inspect

        src = inspect.getsource(SimplifiedScheduler._snapshot_origin_wallets_for_season)
        assert "refresh_participants_for_season(%s, %s, %s, %s, %s::text[])" in src
        assert "event_id_allowlist" in src
        # Standard branch derives the cap selection; genesis leaves it None.
        assert "_get_standard_filtered_event_ids" in src

    def test_filtered_event_ids_supports_apply_caps_and_projection(self):
        import inspect

        src = inspect.getsource(SimplifiedScheduler._get_standard_filtered_event_ids)
        assert "apply_caps" in src
        assert "cycles_elapsed" in src
        assert "FIRST_STANDARD_BOOTSTRAP_DELAY_DAYS" in src
