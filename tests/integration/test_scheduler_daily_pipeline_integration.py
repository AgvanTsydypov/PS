"""
Integration tests for ``SimplifiedScheduler.run_daily_pipeline`` (full flow).

Unit tests only exercise snapshot helpers.  Here we run the daily pipeline in
``dry_run=True`` so subprocess fetchers and heavy writes are skipped, while the
orchestration, date resolution, and ``results`` aggregation still execute against
a real ``DataLoadingManager`` connection.

``get_missing_dates`` is stubbed to ``[]`` so a dev DB with historical gaps does
not trigger ``run_catch_up`` (network + subprocess).  ``ProcessLock.is_locked`` is
forced false to avoid flaky skips from an unrelated lock file in ``/tmp``.
"""

from __future__ import annotations

from unittest import mock

import pytest

from tests.integration.conftest import (
    _patch_data_loading_manager_psycopg2,
    _patch_scheduler_psycopg2,
)


@pytest.fixture()
def dry_run_scheduler():
    with _patch_data_loading_manager_psycopg2(), _patch_scheduler_psycopg2() as sched_mod:
        yield sched_mod.SimplifiedScheduler(use_local_db=True, dry_run=True)


class TestRunDailyPipelineDryRun:

    def test_returns_success_and_results(self, dry_run_scheduler):
        sch = dry_run_scheduler
        import scripts.daily_scheduler_simple as dss

        with mock.patch.object(dss.ProcessLock, "is_locked", return_value=False):
            with mock.patch.object(sch.manager, "get_missing_dates", return_value=[]):
                out = sch.run_daily_pipeline(force=True)

        assert out.get("success") is True, out
        assert "results" in out
        res = out["results"]
        assert "events" in res
        assert res["events"].get("success") is True
        assert "resolution_polling" in res
        for key in ("redemptions", "positions", "leaderboard"):
            assert key in res
            assert res[key].get("success") is True
