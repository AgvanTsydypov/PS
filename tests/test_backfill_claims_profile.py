"""
Unit tests for scripts/backfill_claims_profile.py (Path 2 — backfilling
``claims.x_username`` / ``claims.profile_name`` on rows that predate the
insert-time stamping).

DB access is exercised through a MagicMock connection so these stay hermetic;
the live UPDATE against real PostgreSQL is covered by the integration test in
tests/integration/test_claims_profile_enrichment.py. Network access is stubbed
the same way as the insert-path lookup.
"""

from __future__ import annotations

import io
import json
import urllib.error
from unittest.mock import MagicMock

import pytest

import scripts.backfill_claims_profile as backfill


class _FakeResponse:
    def __init__(self, body: str):
        self._buf = io.BytesIO(body.encode("utf-8"))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._buf.read()


def _patch_urlopen(monkeypatch, *, body=None, exc=None):
    def fake_urlopen(req, timeout=None):
        if exc is not None:
            raise exc
        return _FakeResponse(body)

    monkeypatch.setattr(backfill.urllib.request, "urlopen", fake_urlopen)


def _mock_conn_with_cursor():
    """A MagicMock psycopg2 connection whose ``with conn.cursor() as cur``
    yields a controllable cursor mock."""
    cur = MagicMock()
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur
    conn.cursor.return_value.__exit__.return_value = False
    return conn, cur


# ---------------------------------------------------------------------------
# _profile_identity
# ---------------------------------------------------------------------------

def test_profile_identity_extracts_both():
    assert backfill._profile_identity({"xUsername": "ace", "name": "Ada"}) == ("ace", "Ada")


def test_profile_identity_strips_and_nulls_blanks():
    assert backfill._profile_identity({"xUsername": " ", "name": "  Bob "}) == (None, "Bob")


def test_profile_identity_empty():
    assert backfill._profile_identity({}) == (None, None)


# ---------------------------------------------------------------------------
# _fetch_public_profile
# ---------------------------------------------------------------------------

def test_fetch_public_profile_ok(monkeypatch):
    _patch_urlopen(monkeypatch, body=json.dumps({"xUsername": "x", "name": "n"}))
    assert backfill._fetch_public_profile("0xabc") == {"xUsername": "x", "name": "n"}


@pytest.mark.parametrize("code", [403, 404, 500])
def test_fetch_public_profile_http_error_returns_none(monkeypatch, code):
    err = urllib.error.HTTPError("http://x", code, "msg", None, io.BytesIO(b""))
    _patch_urlopen(monkeypatch, exc=err)
    assert backfill._fetch_public_profile("0xabc") is None


def test_fetch_public_profile_network_error_returns_none(monkeypatch):
    _patch_urlopen(monkeypatch, exc=urllib.error.URLError("boom"))
    assert backfill._fetch_public_profile("0xabc") is None


def test_fetch_public_profile_bad_json_returns_none(monkeypatch):
    _patch_urlopen(monkeypatch, body="not json")
    assert backfill._fetch_public_profile("0xabc") is None


def test_fetch_public_profile_non_dict_returns_none(monkeypatch):
    _patch_urlopen(monkeypatch, body=json.dumps([1, 2, 3]))
    assert backfill._fetch_public_profile("0xabc") is None


# ---------------------------------------------------------------------------
# _fetch_unenriched_wallets
# ---------------------------------------------------------------------------

def test_fetch_unenriched_wallets_returns_first_column():
    conn, cur = _mock_conn_with_cursor()
    cur.fetchall.return_value = [("0xaaa",), ("0xbbb",)]
    out = backfill._fetch_unenriched_wallets(conn, limit=None)
    assert out == ["0xaaa", "0xbbb"]
    # No LIMIT clause and no params when limit is None.
    sql, params = cur.execute.call_args[0]
    assert "x_username IS NULL" in sql and "profile_name IS NULL" in sql
    assert "LIMIT" not in sql
    assert params == []


def test_fetch_unenriched_wallets_applies_limit():
    conn, cur = _mock_conn_with_cursor()
    cur.fetchall.return_value = []
    backfill._fetch_unenriched_wallets(conn, limit=5)
    sql, params = cur.execute.call_args[0]
    assert "LIMIT %s" in sql
    assert params == [5]


# ---------------------------------------------------------------------------
# _update_wallet
# ---------------------------------------------------------------------------

def test_update_wallet_binds_params_and_returns_rowcount():
    conn, cur = _mock_conn_with_cursor()
    cur.rowcount = 3
    n = backfill._update_wallet(conn, "0xAAA", "ace", "Ada")
    assert n == 3
    sql, params = cur.execute.call_args[0]
    # Only-still-NULL guard prevents clobbering insert-time values.
    assert "x_username IS NULL" in sql and "profile_name IS NULL" in sql
    assert "LOWER(proxy_wallet) = LOWER(%s)" in sql
    assert params == ("ace", "Ada", "0xAAA")
