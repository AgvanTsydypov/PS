"""
Unit tests for the admin-selected mint gas tier
(``polystars_mint_runtime_settings``).

Three layers exercised:

* ``SeasonWorkbenchService.get_mint_speed_tier`` / ``set_mint_speed_tier``
* ``SimplifiedScheduler._read_mint_speed_tier`` (cron worker reader, must
  fall back to 'safe' on any DB hiccup so a missing migration cannot stall
  the mint queue)
* ``GET / PUT /api/mint-settings/speed-tier`` HTTP layer via FastAPI's
  ``TestClient`` — verifies validation rejects unknown tiers and that the
  exposed ``allowed`` list matches the DB CHECK constraint.

The DB layer is fully mocked via the project-wide ``conftest.py`` stub
for ``psycopg2``; tests build their own ``MagicMock`` connections per case
and bolt them onto the service / scheduler so each assertion controls
exactly what ``cursor.execute`` sees and ``fetchone`` returns.
"""

from __future__ import annotations

from typing import Any, List, Tuple
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_conn(fetchone_returns: Any) -> Tuple[MagicMock, MagicMock]:
    """Build a (conn, cursor) MagicMock pair whose ``cursor.fetchone``
    returns the given value. Tracks every ``execute(sql, params)`` call.
    """
    cursor = MagicMock()
    cursor.fetchone.return_value = fetchone_returns
    cursor.__enter__ = lambda s: s
    cursor.__exit__ = MagicMock(return_value=False)

    conn = MagicMock()
    conn.cursor.return_value = cursor
    return conn, cursor


def _make_service():
    """Instantiate ``SeasonWorkbenchService`` without touching the DB.

    Mirrors the pattern in ``tests/test_service_statics.py`` — patches the
    three collaborators that the constructor reaches for, then bolts on a
    fresh ``MagicMock`` manager whose ``get_connection`` the test will set.
    """
    import unittest.mock as mock

    with (
        mock.patch("scripts.data_loading_manager.DataLoadingManager._ensure_tables"),
        mock.patch("scripts.season_manager.SeasonManager.__init__", return_value=None),
        mock.patch(
            "scripts.daily_scheduler_simple.SimplifiedScheduler.__init__",
            return_value=None,
        ),
    ):
        from admin_backend.main import SeasonWorkbenchService

        svc = SeasonWorkbenchService.__new__(SeasonWorkbenchService)
        svc.manager = MagicMock()
        return svc


def _make_scheduler():
    """Instantiate ``SimplifiedScheduler`` bypassing its heavy ``__init__``.

    Only ``self.manager`` is needed for ``_read_mint_speed_tier``; everything
    else stays unset, mirroring how the function is actually invoked.
    """
    from scripts.daily_scheduler_simple import SimplifiedScheduler

    sch = SimplifiedScheduler.__new__(SimplifiedScheduler)
    sch.manager = MagicMock()
    return sch


# ===========================================================================
# SeasonWorkbenchService.get_mint_speed_tier
# ===========================================================================


class TestServiceGetMintSpeedTier:
    def test_returns_stored_value_when_valid(self):
        svc = _make_service()
        conn, cursor = _make_conn(fetchone_returns=("rapid",))
        svc.manager.get_connection.return_value = conn

        assert svc.get_mint_speed_tier() == "rapid"
        # Query targets the singleton row.
        sql_arg = cursor.execute.call_args[0][0]
        assert "polystars_mint_runtime_settings" in sql_arg
        assert "singleton_id = 1" in sql_arg

    def test_returns_safe_when_row_missing(self):
        svc = _make_service()
        conn, _ = _make_conn(fetchone_returns=None)
        svc.manager.get_connection.return_value = conn
        assert svc.get_mint_speed_tier() == "safe"

    def test_returns_safe_when_row_value_invalid(self):
        # A DB that somehow holds a value not in the CHECK constraint (e.g.
        # the constraint was dropped, or a manual UPDATE bypassed it) must
        # not crash the caller; safe is the documented fallback.
        svc = _make_service()
        conn, _ = _make_conn(fetchone_returns=("turbo",))
        svc.manager.get_connection.return_value = conn
        assert svc.get_mint_speed_tier() == "safe"

    def test_each_valid_tier_round_trips(self):
        svc = _make_service()
        for tier in ("safe", "propose", "rapid"):
            conn, _ = _make_conn(fetchone_returns=(tier,))
            svc.manager.get_connection.return_value = conn
            assert svc.get_mint_speed_tier() == tier

    def test_closes_connection_even_on_error(self):
        svc = _make_service()
        conn, cursor = _make_conn(fetchone_returns=("safe",))
        cursor.execute.side_effect = RuntimeError("boom")
        svc.manager.get_connection.return_value = conn
        with pytest.raises(RuntimeError):
            svc.get_mint_speed_tier()
        conn.close.assert_called_once()


# ===========================================================================
# SeasonWorkbenchService.set_mint_speed_tier
# ===========================================================================


class TestServiceSetMintSpeedTier:
    @pytest.mark.parametrize("tier", ["safe", "propose", "rapid"])
    def test_accepts_each_allowed_tier(self, tier):
        svc = _make_service()
        conn, cursor = _make_conn(fetchone_returns=None)
        svc.manager.get_connection.return_value = conn

        svc.set_mint_speed_tier(tier)

        sql, params = cursor.execute.call_args[0]
        assert "INSERT INTO polystars_mint_runtime_settings" in sql
        assert "ON CONFLICT (singleton_id) DO UPDATE" in sql
        assert params == (tier,)
        conn.commit.assert_called_once()
        conn.close.assert_called_once()

    @pytest.mark.parametrize(
        "bad_tier",
        ["", "RAPID", "Standard", "fast", "turbo", None, 0, "safe "],
    )
    def test_rejects_invalid_tier(self, bad_tier):
        svc = _make_service()
        conn, _ = _make_conn(fetchone_returns=None)
        svc.manager.get_connection.return_value = conn

        with pytest.raises(ValueError, match="Invalid mint speed tier"):
            svc.set_mint_speed_tier(bad_tier)

        # No DB writes attempted on validation failure — important so a
        # buggy admin call cannot silently mutate the row.
        conn.cursor.assert_not_called()
        conn.commit.assert_not_called()

    def test_rollback_on_db_failure(self):
        svc = _make_service()
        conn, cursor = _make_conn(fetchone_returns=None)
        cursor.execute.side_effect = RuntimeError("boom")
        svc.manager.get_connection.return_value = conn

        with pytest.raises(RuntimeError):
            svc.set_mint_speed_tier("rapid")

        conn.rollback.assert_called_once()
        conn.commit.assert_not_called()
        conn.close.assert_called_once()


# ===========================================================================
# SimplifiedScheduler._read_mint_speed_tier (cron worker)
# ===========================================================================


class TestSchedulerReadMintSpeedTier:
    """The cron worker reads the tier on every loop iteration. A missing
    migration, a missing row, or a transient DB blip must NEVER raise —
    the worker silently falls back to 'safe', which is also the historical
    broadcast tier (so no surprise in behaviour).
    """

    def test_returns_stored_value(self):
        sch = _make_scheduler()
        conn, _ = _make_conn(fetchone_returns=("propose",))
        sch.manager.get_connection.return_value = conn
        assert sch._read_mint_speed_tier() == "propose"

    def test_falls_back_when_row_missing(self):
        sch = _make_scheduler()
        conn, _ = _make_conn(fetchone_returns=None)
        sch.manager.get_connection.return_value = conn
        assert sch._read_mint_speed_tier() == "safe"

    def test_falls_back_when_value_unknown(self):
        sch = _make_scheduler()
        conn, _ = _make_conn(fetchone_returns=("hyperspeed",))
        sch.manager.get_connection.return_value = conn
        assert sch._read_mint_speed_tier() == "safe"

    def test_falls_back_when_get_connection_raises(self):
        sch = _make_scheduler()
        sch.manager.get_connection.side_effect = RuntimeError("db down")
        assert sch._read_mint_speed_tier() == "safe"

    def test_falls_back_when_execute_raises(self):
        # e.g. table does not exist on a DB that never ran the migration.
        sch = _make_scheduler()
        conn, cursor = _make_conn(fetchone_returns=None)
        cursor.execute.side_effect = RuntimeError("relation does not exist")
        sch.manager.get_connection.return_value = conn

        assert sch._read_mint_speed_tier() == "safe"
        # The connection is still closed in the finally branch so the pool
        # does not bleed handles when the table is missing.
        conn.close.assert_called_once()

    def test_swallows_close_errors(self):
        # Some pool implementations raise on double-close. The helper must
        # not surface that.
        sch = _make_scheduler()
        conn, _ = _make_conn(fetchone_returns=("rapid",))
        conn.close.side_effect = RuntimeError("already closed")
        sch.manager.get_connection.return_value = conn
        assert sch._read_mint_speed_tier() == "rapid"


# ===========================================================================
# HTTP layer — FastAPI endpoints
# ===========================================================================


@pytest.fixture
def http_client(monkeypatch):
    """Boot a TestClient against admin_backend.main with the singleton
    ``service`` swapped for a MagicMock so the routes hit no real DB.
    """
    import unittest.mock as mock

    with (
        mock.patch("scripts.data_loading_manager.DataLoadingManager._ensure_tables"),
        mock.patch("scripts.season_manager.SeasonManager.__init__", return_value=None),
        mock.patch(
            "scripts.daily_scheduler_simple.SimplifiedScheduler.__init__",
            return_value=None,
        ),
    ):
        import admin_backend.main as m

    from fastapi.testclient import TestClient

    fake_service = MagicMock()
    fake_service.get_mint_speed_tier.return_value = "safe"
    monkeypatch.setattr(m, "service", fake_service)

    return TestClient(m.app), fake_service, m


class TestHttpGetMintSpeedTier:
    def test_returns_current_tier_and_allowed_list(self, http_client):
        client, svc, m = http_client
        svc.get_mint_speed_tier.return_value = "rapid"

        resp = client.get("/api/mint-settings/speed-tier")
        assert resp.status_code == 200
        body = resp.json()
        assert body["tier"] == "rapid"
        # The allowed list must mirror the DB CHECK constraint exactly —
        # the UI relies on this to populate its dropdown options.
        assert body["allowed"] == ["safe", "propose", "rapid"]
        assert body["allowed"] == list(m.MINT_SPEED_TIERS)


class TestHttpPutMintSpeedTier:
    @pytest.mark.parametrize("tier", ["safe", "propose", "rapid"])
    def test_accepts_each_allowed_tier(self, http_client, tier):
        client, svc, _ = http_client
        # GET after PUT reflects the new value.
        svc.get_mint_speed_tier.return_value = tier

        resp = client.put("/api/mint-settings/speed-tier", json={"tier": tier})
        assert resp.status_code == 200
        assert resp.json()["tier"] == tier
        svc.set_mint_speed_tier.assert_called_once_with(tier)

    def test_rejects_unknown_tier_with_400(self, http_client):
        client, svc, _ = http_client
        # ServiceMock will raise ValueError exactly as the real service does.
        svc.set_mint_speed_tier.side_effect = ValueError(
            "Invalid mint speed tier 'turbo' (expected one of: safe, propose, rapid)"
        )

        resp = client.put(
            "/api/mint-settings/speed-tier", json={"tier": "turbo"}
        )
        assert resp.status_code == 400
        assert "Invalid mint speed tier" in resp.json()["detail"]

    def test_rejects_missing_tier_field(self, http_client):
        client, svc, _ = http_client
        # Pydantic returns 422 for missing required fields — the body never
        # reaches the service.
        resp = client.put("/api/mint-settings/speed-tier", json={})
        assert resp.status_code == 422
        svc.set_mint_speed_tier.assert_not_called()


# ===========================================================================
# Constants
# ===========================================================================


class TestMintSpeedTiersConstant:
    """The single source of truth for allowed tiers is
    ``admin_backend.main.MINT_SPEED_TIERS``. The DB CHECK constraint, the
    service validator, the worker fallback and the UI dropdown all assume
    the same ordering and the same values.
    """

    def test_constant_matches_db_check_constraint(self):
        import unittest.mock as mock

        with (
            mock.patch("scripts.data_loading_manager.DataLoadingManager._ensure_tables"),
            mock.patch("scripts.season_manager.SeasonManager.__init__", return_value=None),
            mock.patch(
                "scripts.daily_scheduler_simple.SimplifiedScheduler.__init__",
                return_value=None,
            ),
        ):
            from admin_backend.main import MINT_SPEED_TIERS

        assert MINT_SPEED_TIERS == ("safe", "propose", "rapid")
