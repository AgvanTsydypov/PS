"""
HTTP integration tests for admin_backend (FastAPI TestClient).

The global ``service`` on ``admin_backend.main`` is swapped for a fully wired
``SeasonWorkbenchService`` (see ``integration_full_workbench_service`` in conftest)
so routes hit real PostgreSQL without relying on whatever psycopg2 stub the
root ``tests/conftest.py`` installed at import time.
"""

from __future__ import annotations


def test_health_ok(admin_api_client):
    r = admin_api_client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body.get("status") == "ok"


def test_server_time_ok(admin_api_client):
    r = admin_api_client.get("/api/server-time")
    assert r.status_code == 200
    body = r.json()
    assert "now_utc_iso" in body and body["now_utc_iso"]


def test_config_ok(admin_api_client):
    r = admin_api_client.get("/api/config")
    assert r.status_code == 200
    assert "default_evm_recipient" in r.json()


def test_overview_has_seasons_and_logs(admin_api_client):
    r = admin_api_client.get("/api/overview")
    assert r.status_code == 200
    data = r.json()
    assert "seasons" in data and isinstance(data["seasons"], list)
    assert "logs" in data and isinstance(data["logs"], list)


def test_seasons_endpoint_list(admin_api_client):
    r = admin_api_client.get("/api/seasons")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_eligibility_empty_wallet_400(admin_api_client):
    r = admin_api_client.post("/api/eligibility", json={"wallet": "  "})
    assert r.status_code == 400


def test_eligibility_returns_streams(admin_api_client):
    wallet = "0x" + "ab" * 20
    r = admin_api_client.post("/api/eligibility", json={"wallet": wallet})
    assert r.status_code == 200
    body = r.json()
    assert body.get("wallet_address") == wallet.lower()
    assert "genesis" in body and isinstance(body["genesis"], dict)
    assert "standard" in body and isinstance(body["standard"], dict)


def test_user_web_wallet_actions_get(admin_api_client):
    r = admin_api_client.get("/api/user-web/wallet-actions")
    assert r.status_code == 200
    body = r.json()
    assert "wallet_actions_disabled" in body
    assert "database_wallet_actions_disabled" in body


# ─────────────────────────────────────────────────────────────────────────────
# /api/mint-queue/health — Overview health widget data source
#
# The widget composes three independent reads (counts, hot wallet, gas);
# each must degrade independently so a flaky RPC or missing Etherscan key
# doesn't blank the whole widget. These tests pin that contract by pointing
# the EVM env vars at obviously-broken values and verifying the endpoint
# still returns 200 with structured ``ok:false`` sub-blocks.
# ─────────────────────────────────────────────────────────────────────────────


def test_mint_queue_health_returns_200_with_required_blocks(admin_api_client):
    """Smoke: the endpoint must always return 200 with all four sub-blocks
    present (counts, last_mint_at, hot_wallet, gas, fetched_at). The widget
    relies on this shape — missing keys would render `undefined` everywhere."""
    r = admin_api_client.get("/api/mint-queue/health")
    assert r.status_code == 200
    body = r.json()
    assert "counts" in body
    assert "last_mint_at" in body
    assert "hot_wallet" in body
    assert "gas" in body
    assert "fetched_at" in body


def test_mint_queue_health_counts_have_expected_keys(admin_api_client):
    """The counts block must always carry every key the widget reads, even
    on an empty database. Default-zero is the right shape for the UI."""
    r = admin_api_client.get("/api/mint-queue/health")
    assert r.status_code == 200
    counts = r.json()["counts"]
    for key in (
        "queued", "processing", "processing_with_rbf", "stuck",
        "completed_today", "failed_today",
    ):
        assert key in counts, f"missing counts.{key}"
        assert isinstance(counts[key], int), f"counts.{key} must be int"
        assert counts[key] >= 0


def test_mint_queue_health_hot_wallet_degrades_gracefully_when_evm_unset(
    admin_api_client, monkeypatch,
):
    """If EVM env vars are missing/broken, the hot_wallet sub-block must
    surface ``ok:false`` with an error message — NOT 500. Pre-launch the
    operator may not have configured a hot wallet yet; the widget should
    still render the counts and gas blocks."""
    # Force EvmClient init failure by clearing the private key.
    monkeypatch.setenv("EVM_PRIVATE_KEY", "")
    # Clear the cached balance so the next call actually re-fetches.
    from admin_backend.main import (
        _WALLET_BALANCE_CACHE, _WALLET_BALANCE_LOCK,
    )
    with _WALLET_BALANCE_LOCK:
        _WALLET_BALANCE_CACHE["value"] = None
        _WALLET_BALANCE_CACHE["expires_at"] = 0.0

    r = admin_api_client.get("/api/mint-queue/health")
    assert r.status_code == 200
    hw = r.json()["hot_wallet"]
    assert hw.get("ok") is False
    assert "error" in hw


def test_mint_queue_health_gas_block_present_even_when_cache_cold(admin_api_client):
    """When the gas-tracker cache is cold (no recent /api/gas-tracker call),
    the gas sub-block must still be present with ``ok:false`` and an
    explanatory error rather than missing entirely. Widget shows ``--``."""
    # Force cold cache.
    from admin_backend.main import _GAS_TRACKER_CACHE, _GAS_TRACKER_LOCK
    with _GAS_TRACKER_LOCK:
        _GAS_TRACKER_CACHE["value"] = None
        _GAS_TRACKER_CACHE["expires_at"] = 0.0

    r = admin_api_client.get("/api/mint-queue/health")
    assert r.status_code == 200
    gas = r.json()["gas"]
    # We don't assert ok:false here because if /api/gas-tracker was hit by
    # an earlier test in the same session, the cache might be warm. What we
    # require is just that the block is structured (has ok or has rapid_gwei).
    assert "ok" in gas
    if gas["ok"] is False:
        assert "error" in gas
    else:
        assert "rapid_gwei" in gas


def test_mint_queue_health_counts_reflect_inserted_claims(admin_api_client):
    """Insert a stuck claim directly and verify the stuck counter increments.
    Defends against the SQL drifting away from the runbook's badge format."""
    from tests.integration.conftest import make_real_connection

    # Snapshot pre-state.
    pre = admin_api_client.get("/api/mint-queue/health").json()["counts"]

    # Need a season FK first.
    conn = make_real_connection()
    season_id: int | None = None
    claim_id: int | None = None
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO seasons
                    (type, season_number, start_date, end_date,
                     total_supply, remaining_supply, is_active)
                VALUES ('standard', 98301,
                        '2099-01-01 00:00:00+00', '2099-12-31 00:00:00+00',
                        0, 0, true)
                RETURNING id
                """,
            )
            season_id = cur.fetchone()[0]
            cur.execute(
                """
                INSERT INTO claims
                    (user_wallet, season_id, phase_type, status,
                     tx_hash, error_message)
                VALUES (%s, %s, 'breach', 'PROCESSING', %s, %s)
                RETURNING id
                """,
                (
                    "0x" + "ab" * 20, season_id, "0xdead",
                    "[stuck: integration test marker]",
                ),
            )
            claim_id = cur.fetchone()[0]
        conn.commit()
    finally:
        conn.close()

    try:
        post = admin_api_client.get("/api/mint-queue/health").json()["counts"]
        assert post["stuck"] == pre["stuck"] + 1
        # Stuck rows are NOT counted in the active "processing" bucket
        # (the widget would otherwise double-count).
        assert post["processing"] == pre["processing"]
    finally:
        # Cleanup.
        conn = make_real_connection()
        try:
            with conn.cursor() as cur:
                if claim_id is not None:
                    cur.execute("DELETE FROM claims WHERE id = %s", (claim_id,))
                if season_id is not None:
                    cur.execute("DELETE FROM seasons WHERE id = %s", (season_id,))
            conn.commit()
        finally:
            conn.close()
