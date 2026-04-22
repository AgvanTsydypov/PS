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
    assert "default_solana_recipient" in r.json()


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
    assert "double_mint" in body


def test_user_web_wallet_actions_get(admin_api_client):
    r = admin_api_client.get("/api/user-web/wallet-actions")
    assert r.status_code == 200
    body = r.json()
    assert "wallet_actions_disabled" in body
    assert "database_wallet_actions_disabled" in body
