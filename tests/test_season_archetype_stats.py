"""
Unit tests for season catalog + opened-archetype stats (user_web_backend).

Data source: PostgreSQL ``claims`` (``status = 'COMPLETED'``) with archetype
read from ``card_payload_json`` or the snapshotted ``claims.archetype`` column.
"""

from __future__ import annotations

import unittest.mock as mock

import pytest
from fastapi.testclient import TestClient


def _user_web_main():
    with mock.patch("scripts.data_loading_manager.DataLoadingManager._ensure_tables"):
        import user_web_backend.main as m

    return m


@pytest.fixture(scope="module")
def uw():
    return _user_web_main()


class TestNormalizeArchetypeForStats:
    def test_empty_unknown(self, uw):
        assert uw._normalize_archetype_for_stats("") == "UNKNOWN"
        assert uw._normalize_archetype_for_stats(None) == "UNKNOWN"
        assert uw._normalize_archetype_for_stats("   ") == "UNKNOWN"

    def test_canonical_case_insensitive(self, uw):
        assert uw._normalize_archetype_for_stats("anomaly") == "ANOMALY"
        assert uw._normalize_archetype_for_stats("OPERATOR") == "OPERATOR"

    def test_the_prefix_stripped(self, uw):
        assert uw._normalize_archetype_for_stats("THE ANOMALY") == "ANOMALY"

    def test_unknown_maps_to_unknown_bucket(self, uw):
        assert uw._normalize_archetype_for_stats("UNKNOWN_LEGACY_LABEL") == "UNKNOWN"

    def test_gibberish_buckets_unknown(self, uw):
        assert uw._normalize_archetype_for_stats("not-a-real-archetype-xyz") == "UNKNOWN"


class FakeCursor:
    """Minimal cursor for ``season_opened_archetype_counts`` DB sequence."""

    def __init__(self, *, season_exists: bool, agg_rows: list):
        self._season_exists = season_exists
        self._agg_rows = agg_rows
        self._exec_idx = 0
        self.last_params: tuple | None = None

    def execute(self, sql, params=None):
        self._exec_idx += 1
        self.last_sql = sql
        self.last_params = params

    def fetchone(self):
        assert self._exec_idx == 1
        return (1,) if self._season_exists else None

    def fetchall(self):
        assert self._exec_idx == 2
        return list(self._agg_rows)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeConn:
    def __init__(self, *, season_exists: bool, agg_rows: list):
        self._cursor = FakeCursor(season_exists=season_exists, agg_rows=agg_rows)

    def cursor(self):
        return self._cursor

    def close(self):
        return None


class TestCommunityAchievementsFrozen:
    """The "Community achievements" widget is currently disabled — both
    routes return 503 without touching the database. ``FakeConn``/
    ``FakeCatalogConn`` are kept above as documentation of the previous
    contract for whoever re-enables the feature.
    """

    def test_seasons_catalog_returns_503(self, uw):
        client = TestClient(uw.app)
        res = client.get("/api/seasons/catalog")
        assert res.status_code == 503
        assert "under construction" in res.json()["detail"].lower()

    def test_opened_archetypes_returns_503(self, uw):
        client = TestClient(uw.app)
        res = client.get("/api/seasons/7/opened-archetypes")
        assert res.status_code == 503
        assert "under construction" in res.json()["detail"].lower()

    def test_opened_archetypes_does_not_touch_db(self, uw):
        """Disabled routes must not even open a DB connection."""
        sentinel = mock.MagicMock(side_effect=AssertionError("DB should not be queried"))
        with mock.patch.object(uw, "_get_connection", sentinel):
            client = TestClient(uw.app)
            res = client.get("/api/seasons/7/opened-archetypes")
        assert res.status_code == 503
        sentinel.assert_not_called()
