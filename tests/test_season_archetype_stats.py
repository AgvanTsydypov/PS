"""
Unit tests for season catalog + opened-archetype stats (user_web_backend).

Data source: PostgreSQL ``claims`` (``status = 'COMPLETED'``) with archetype
from ``card_payload_json`` or ``winner_wallets_nft_to_claim.archetype``.
"""

from __future__ import annotations

import unittest.mock as mock

import pytest
from fastapi import HTTPException
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


class TestSeasonOpenedArchetypeCounts:
    def test_merges_raw_rows_and_unknowns(self, uw):
        agg_rows = [
            ("", 2),
            ("ANOMALY", 1),
            ("the icarus", 3),
            ("GRAVITON", 1),
            ("nonsense", 5),
        ]
        fake = FakeConn(season_exists=True, agg_rows=agg_rows)

        with mock.patch.object(uw, "_get_connection", return_value=fake):
            out = uw.season_opened_archetype_counts(42)

        assert out["season_id"] == 42
        assert out["unknown"] == 2 + 5
        assert out["by_archetype"]["ANOMALY"] == 1
        assert out["by_archetype"]["ICARUS"] == 3
        assert out["by_archetype"]["GRAVITON"] == 1
        assert out["total_opened"] == sum(out["by_archetype"].values()) + out["unknown"]
        assert fake._cursor.last_params == (42,)

    def test_404_when_season_missing(self, uw):
        fake = FakeConn(season_exists=False, agg_rows=[])

        with mock.patch.object(uw, "_get_connection", return_value=fake):
            with pytest.raises(HTTPException) as exc:
                uw.season_opened_archetype_counts(99)
            assert exc.value.status_code == 404


class FakeCatalogCursor:
    def __init__(self, rows):
        self._rows = rows
        self.last_sql = ""

    def execute(self, sql, params=None):
        self.last_sql = sql

    def fetchall(self):
        return list(self._rows)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeCatalogConn:
    def __init__(self, rows):
        self._cursor = FakeCatalogCursor(rows)

    def cursor(self):
        return self._cursor

    def close(self):
        return None


class TestSeasonsCatalog:
    def test_builds_titles(self, uw):
        rows = [
            (10, "genesis", 1, True),
            (11, "standard", 2, False),
        ]
        with mock.patch.object(uw, "_get_connection", return_value=FakeCatalogConn(rows)):
            payload = uw.seasons_catalog()

        assert payload["seasons"][0]["title"] == "Genesis"
        assert payload["seasons"][1]["title"] == "Standard #2"
        assert payload["seasons"][1]["is_active"] is False


class TestSeasonArchetypeHttpClient:
    def test_invalid_season_id_400(self, uw):
        client = TestClient(uw.app)
        res = client.get("/api/seasons/0/opened-archetypes")
        assert res.status_code == 400

    def test_opened_archetypes_parameterized_season_id(self, uw):
        """Guardrail: season id must be bound as a query parameter, not interpolated."""
        captured: list[tuple[str, tuple | None]] = []

        class CapCursor(FakeCursor):
            def execute(self, sql, params=None):
                captured.append((sql, params))
                super().execute(sql, params)

        class CapConn:
            def __init__(self):
                self._c = CapCursor(season_exists=True, agg_rows=[("BOT", 1)])

            def cursor(self):
                return self._c

            def close(self):
                return None

        with mock.patch.object(uw, "_get_connection", return_value=CapConn()):
            client = TestClient(uw.app)
            res = client.get("/api/seasons/7/opened-archetypes")

        assert res.status_code == 200
        assert res.json()["season_id"] == 7
        assert res.json()["by_archetype"]["BOT"] == 1
        assert len(captured) == 2
        _exists_sql, exists_params = captured[0]
        _agg_sql, agg_params = captured[1]
        assert exists_params == (7,)
        assert agg_params == (7,)
        assert "%s" in _exists_sql
        assert "%s" in _agg_sql
