"""
Tests for the mint-time IP sybil gate in ``user_web_backend.main``.

The gate refuses a *token-holder*-path mint when another wallet has already
minted in the same season from the same client IP. Trader-rank wallets are
intentionally exempt: their mint is already gated by real Polymarket trading
history, and an IP-collision check would falsely block CGNAT / coffee-shop /
family scenarios with no defensive payoff.

Coverage here:
  * ``_hash_client_ip``                       — pure HMAC determinism + None paths
  * ``_count_other_wallets_minted_from_ip``    — collision counting against mocked DB
  * ``_record_mint_ip_hash``                   — UPDATE issued, swallows DB errors
  * ``/api/me/mint`` integration               — full gate decision tree
  * ``/api/auth/wallet/verify`` UPSERT         — writes + COALESCE-protects last_ip_hash
"""

from __future__ import annotations

import unittest.mock as mock
from typing import Any, Dict, Optional

import pytest
from fastapi.testclient import TestClient


def _user_web_module():
    """Import user_web_backend.main with the DataLoadingManager DB hop stubbed
    out — same trick used by tests/test_token_gate.py."""
    with mock.patch("scripts.data_loading_manager.DataLoadingManager._ensure_tables"):
        import user_web_backend.main as m
    return m


@pytest.fixture()
def m():
    return _user_web_module()


# ─────────────────────────────────────────────────────────────────────────────
# 1. _hash_client_ip — pure HMAC layer
# ─────────────────────────────────────────────────────────────────────────────
class TestHashClientIp:
    """The hash is HMAC-SHA256(salt, ip). Without a secret salt a raw IPv4
    sha256 would be brute-forceable in seconds (only 2^32 inputs), so every
    code path that decides "no salt" must return None and let the caller
    skip the gate rather than hash with a degenerate key."""

    def test_deterministic_for_same_ip_and_salt(self, m):
        with mock.patch.object(m, "_mint_ip_hash_salt", return_value=b"x" * 32):
            assert m._hash_client_ip("1.2.3.4") == m._hash_client_ip("1.2.3.4")

    def test_different_ips_produce_different_hashes(self, m):
        with mock.patch.object(m, "_mint_ip_hash_salt", return_value=b"x" * 32):
            assert m._hash_client_ip("1.2.3.4") != m._hash_client_ip("1.2.3.5")

    def test_different_salts_produce_different_hashes(self, m):
        with mock.patch.object(m, "_mint_ip_hash_salt", return_value=b"a" * 32):
            ha = m._hash_client_ip("1.2.3.4")
        with mock.patch.object(m, "_mint_ip_hash_salt", return_value=b"b" * 32):
            hb = m._hash_client_ip("1.2.3.4")
        assert ha != hb

    def test_hex_digest_length_is_64(self, m):
        with mock.patch.object(m, "_mint_ip_hash_salt", return_value=b"x" * 32):
            h = m._hash_client_ip("1.2.3.4")
        assert isinstance(h, str) and len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_none_ip_returns_none(self, m):
        with mock.patch.object(m, "_mint_ip_hash_salt", return_value=b"x" * 32):
            assert m._hash_client_ip(None) is None

    def test_empty_string_ip_returns_none(self, m):
        with mock.patch.object(m, "_mint_ip_hash_salt", return_value=b"x" * 32):
            assert m._hash_client_ip("") is None

    def test_unknown_sentinel_returns_none(self, m):
        # ``_rate_limit_client_ip`` returns the literal "unknown" when no
        # client host can be resolved. The gate must treat that as
        # "untrackable" rather than hashing the literal string and getting a
        # collision basket of every untrackable request.
        with mock.patch.object(m, "_mint_ip_hash_salt", return_value=b"x" * 32):
            assert m._hash_client_ip("unknown") is None

    def test_missing_salt_returns_none(self, m):
        with mock.patch.object(m, "_mint_ip_hash_salt", return_value=None):
            assert m._hash_client_ip("1.2.3.4") is None

    def test_dev_fallback_salt_is_used_when_env_empty_in_dev(self, m, monkeypatch):
        # In dev mode (NODE_ENV=development, MINT_IP_HASH_SALT unset)
        # ``_mint_ip_hash_salt`` returns a deterministic dev constant so the
        # feature can be exercised locally without extra config. In prod the
        # boot validator refuses to start under that condition.
        monkeypatch.delenv("MINT_IP_HASH_SALT", raising=False)
        monkeypatch.setenv("NODE_ENV", "development")
        salt = m._mint_ip_hash_salt()
        assert salt is not None and len(salt) >= 32

    def test_missing_salt_in_non_dev_returns_none(self, m, monkeypatch):
        monkeypatch.delenv("MINT_IP_HASH_SALT", raising=False)
        monkeypatch.setenv("NODE_ENV", "production")
        assert m._mint_ip_hash_salt() is None


# ─────────────────────────────────────────────────────────────────────────────
# 2. _count_other_wallets_minted_from_ip — DB join logic
# ─────────────────────────────────────────────────────────────────────────────
def _stub_connection_returning(value: Any):
    """Build a fake psycopg2 connection whose cursor.fetchone() returns a
    single-column row containing ``value``. The cursor doubles as its own
    context manager so ``with conn.cursor() as cursor:`` works."""
    cursor = mock.MagicMock()
    cursor.fetchone.return_value = (value,)
    cursor.__enter__ = lambda self: self
    cursor.__exit__ = mock.MagicMock(return_value=False)
    conn = mock.MagicMock()
    conn.cursor.return_value = cursor
    return conn, cursor


class TestCountOtherWalletsMintedFromIp:
    def test_returns_zero_when_no_collision(self, m):
        conn, _ = _stub_connection_returning(0)
        with mock.patch.object(m, "_get_connection", return_value=conn):
            assert m._count_other_wallets_minted_from_ip("hash", 1, "0xabc") == 0

    def test_returns_count_when_collision(self, m):
        conn, _ = _stub_connection_returning(3)
        with mock.patch.object(m, "_get_connection", return_value=conn):
            assert m._count_other_wallets_minted_from_ip("hash", 1, "0xabc") == 3

    def test_returns_zero_when_fetchone_returns_none(self, m):
        # Defensive: COUNT() always returns one row, but if a future schema
        # change ever made fetchone() return None we should fall through to 0
        # rather than crash on a tuple-index error.
        cursor = mock.MagicMock()
        cursor.fetchone.return_value = None
        cursor.__enter__ = lambda self: self
        cursor.__exit__ = mock.MagicMock(return_value=False)
        conn = mock.MagicMock()
        conn.cursor.return_value = cursor
        with mock.patch.object(m, "_get_connection", return_value=conn):
            assert m._count_other_wallets_minted_from_ip("hash", 1, "0xabc") == 0

    def test_query_filters_out_self_wallet(self, m):
        conn, cursor = _stub_connection_returning(0)
        with mock.patch.object(m, "_get_connection", return_value=conn):
            m._count_other_wallets_minted_from_ip("hash_xyz", 42, "0xMyWallet")
        sql, params = cursor.execute.call_args.args
        assert "user_wallet_signins" in sql
        assert "claims" in sql
        assert "last_ip_hash" in sql
        # Self-wallet must be excluded so a legitimate user re-clicking mint
        # for their own pending claim doesn't trip the gate against themselves.
        assert "<>" in sql or "!=" in sql
        assert params == ("hash_xyz", 42, "0xMyWallet")

    def test_query_only_counts_active_statuses(self, m):
        conn, cursor = _stub_connection_returning(0)
        with mock.patch.object(m, "_get_connection", return_value=conn):
            m._count_other_wallets_minted_from_ip("hash", 1, "0xabc")
        sql, _ = cursor.execute.call_args.args
        # If we counted only COMPLETED, an attacker could spam N parallel
        # QUEUED mints before any flips on-chain and bypass the gate.
        for status in ("QUEUED", "PENDING", "PROCESSING", "COMPLETED"):
            assert status in sql

    def test_connection_is_closed_even_on_error(self, m):
        conn = mock.MagicMock()
        conn.cursor.side_effect = RuntimeError("boom")
        with mock.patch.object(m, "_get_connection", return_value=conn):
            with pytest.raises(RuntimeError):
                m._count_other_wallets_minted_from_ip("hash", 1, "0xabc")
        conn.close.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# 3. _record_mint_ip_hash — best-effort UPDATE after mint
# ─────────────────────────────────────────────────────────────────────────────
class TestRecordMintIpHash:
    def test_issues_update_with_lowercased_wallet_match(self, m):
        conn, cursor = _stub_connection_returning(None)
        with mock.patch.object(m, "_get_connection", return_value=conn):
            m._record_mint_ip_hash("0xMixedCase", "newhash")
        sql, params = cursor.execute.call_args.args
        assert "UPDATE user_wallet_signins" in sql
        assert "last_ip_hash" in sql
        assert "LOWER(wallet_address)" in sql
        assert params == ("newhash", "0xMixedCase")
        conn.commit.assert_called_once()

    def test_db_error_is_swallowed_so_mint_response_succeeds(self, m):
        # The mint already enqueued by this point; if the IP-hash bind fails
        # (DB hiccup, network blip), we must not 500 the user — we just lose
        # the chance to bind their next sybil-check correlation. Logged, not
        # raised.
        conn = mock.MagicMock()
        conn.cursor.side_effect = RuntimeError("db down")
        with mock.patch.object(m, "_get_connection", return_value=conn):
            m._record_mint_ip_hash("0xabc", "hash")  # must not raise
        conn.rollback.assert_called_once()
        conn.close.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# 4. /api/me/mint integration — gate decision tree under TestClient
# ─────────────────────────────────────────────────────────────────────────────
@pytest.fixture()
def mint_test_client(m, monkeypatch):
    """Return a (TestClient, m) wired so the IP gate path is reachable.

    Wallet auth is forced to return a known address; the Polymarket profile
    + trader-rank lookups, the token-holder check, and the underlying mint
    service are all monkey-patched per test. The client carries no real
    cookie — auth bypass happens at the wallet-extraction layer so we don't
    have to mint a JWT for every test.
    """
    monkeypatch.setattr(m, "_require_wallet_actions_enabled", lambda: None)
    monkeypatch.setattr(
        m,
        "_extract_wallet_from_request",
        lambda request: "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    )
    # Real client IP — TestClient defaults to "testclient" which is not a
    # valid IP but still hashable; we override _rate_limit_client_ip per test
    # group to exercise both "resolved IP" and "unknown" paths.
    monkeypatch.setattr(m, "_rate_limit_client_ip", lambda request: "203.0.113.7")
    # Hash function is exercised in its own block; here we bypass the salt
    # config and return a deterministic stand-in so collision asserts can
    # match a known string.
    monkeypatch.setattr(
        m,
        "_hash_client_ip",
        lambda ip: "deadbeef" if ip and ip != "unknown" else None,
    )
    monkeypatch.setattr(m, "MINT_IP_GATE_ENABLED", True)
    monkeypatch.setattr(m, "MINT_IP_GATE_MAX_PER_SEASON", 1)
    return TestClient(m.app), m


class TestMeMintIpGate:
    def _stub_polymarket(self, m, monkeypatch, has_rank: bool):
        """Wire the trader-rank branch deterministically."""
        monkeypatch.setattr(
            m,
            "_load_wallet_signin_snapshot",
            lambda wallet: ("0xproxy", "1" if has_rank else "No trades yet"),
        )
        monkeypatch.setattr(
            m, "_fetch_polymarket_public_profile", lambda wallet: {"proxyWallet": "0xproxy"}
        )
        monkeypatch.setattr(m, "_proxy_wallet_from_profile", lambda profile: "0xproxy")
        monkeypatch.setattr(m, "_is_registered_on_polymarket", lambda proxy: True)
        monkeypatch.setattr(
            m,
            "_fetch_polymarket_trader_rank",
            lambda proxy: (("1", True) if has_rank else ("No trades yet", True)),
        )

    def test_trader_rank_wallet_skips_ip_gate_entirely(self, mint_test_client, monkeypatch):
        # A wallet with a real Polymarket rank must mint without the IP gate
        # ever being consulted. We assert by spying on the collision counter:
        # if it was called, the gate ran where it shouldn't have.
        client, m = mint_test_client
        self._stub_polymarket(m, monkeypatch, has_rank=True)
        spy = mock.MagicMock(return_value=99)
        monkeypatch.setattr(m, "_count_other_wallets_minted_from_ip", spy)
        monkeypatch.setattr(m, "_record_mint_ip_hash", mock.MagicMock())
        monkeypatch.setattr(
            m.mint_service, "run_queue_mint_request", lambda req: {"status": "queued"}
        )
        r = client.post("/api/me/mint", json={"season_id": 1})
        assert r.status_code == 200
        spy.assert_not_called()

    def test_token_holder_no_collision_passes_and_records_ip(
        self, mint_test_client, monkeypatch
    ):
        client, m = mint_test_client
        self._stub_polymarket(m, monkeypatch, has_rank=False)
        monkeypatch.setattr(m, "_wallet_holds_gate_token", lambda wallet: True)
        monkeypatch.setattr(
            m, "_count_other_wallets_minted_from_ip", lambda h, s, w: 0
        )
        record_spy = mock.MagicMock()
        monkeypatch.setattr(m, "_record_mint_ip_hash", record_spy)
        monkeypatch.setattr(
            m.mint_service,
            "run_queue_mint_request",
            lambda req: {"status": "queued", "claim_id": 7},
        )
        r = client.post("/api/me/mint", json={"season_id": 1})
        assert r.status_code == 200
        # Bind the wallet to its mint-time IP so the next mint from this IP
        # by some other wallet collides correctly.
        record_spy.assert_called_once_with(
            "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "deadbeef"
        )

    def test_token_holder_collision_returns_429_and_does_not_mint(
        self, mint_test_client, monkeypatch
    ):
        client, m = mint_test_client
        self._stub_polymarket(m, monkeypatch, has_rank=False)
        monkeypatch.setattr(m, "_wallet_holds_gate_token", lambda wallet: True)
        monkeypatch.setattr(
            m, "_count_other_wallets_minted_from_ip", lambda h, s, w: 1
        )
        mint_spy = mock.MagicMock()
        monkeypatch.setattr(m.mint_service, "run_queue_mint_request", mint_spy)
        record_spy = mock.MagicMock()
        monkeypatch.setattr(m, "_record_mint_ip_hash", record_spy)
        r = client.post("/api/me/mint", json={"season_id": 1})
        assert r.status_code == 429
        assert "network" in r.json()["detail"].lower()
        # Critical: the mint service must NOT be called on a gate rejection,
        # otherwise the slot is already burned by the time we say "no".
        mint_spy.assert_not_called()
        record_spy.assert_not_called()

    def test_gate_disabled_lets_collision_through(self, mint_test_client, monkeypatch):
        client, m = mint_test_client
        monkeypatch.setattr(m, "MINT_IP_GATE_ENABLED", False)
        self._stub_polymarket(m, monkeypatch, has_rank=False)
        monkeypatch.setattr(m, "_wallet_holds_gate_token", lambda wallet: True)
        # Even with a fat collision, the gate-disabled flag must short-circuit
        # the check entirely (we should not even spend a DB roundtrip on it).
        spy = mock.MagicMock(return_value=99)
        monkeypatch.setattr(m, "_count_other_wallets_minted_from_ip", spy)
        record_spy = mock.MagicMock()
        monkeypatch.setattr(m, "_record_mint_ip_hash", record_spy)
        monkeypatch.setattr(
            m.mint_service, "run_queue_mint_request", lambda req: {"status": "queued"}
        )
        r = client.post("/api/me/mint", json={"season_id": 1})
        assert r.status_code == 200
        spy.assert_not_called()
        record_spy.assert_not_called()

    def test_unknown_ip_skips_gate_fail_open(self, mint_test_client, monkeypatch):
        # When _hash_client_ip returns None (no salt OR no resolvable IP) we
        # must fail open. Failing closed would let a single proxy misconfig
        # lock every minter out instantly — unacceptable for prod incidents.
        client, m = mint_test_client
        monkeypatch.setattr(m, "_hash_client_ip", lambda ip: None)
        self._stub_polymarket(m, monkeypatch, has_rank=False)
        monkeypatch.setattr(m, "_wallet_holds_gate_token", lambda wallet: True)
        spy = mock.MagicMock(return_value=99)
        monkeypatch.setattr(m, "_count_other_wallets_minted_from_ip", spy)
        record_spy = mock.MagicMock()
        monkeypatch.setattr(m, "_record_mint_ip_hash", record_spy)
        monkeypatch.setattr(
            m.mint_service, "run_queue_mint_request", lambda req: {"status": "queued"}
        )
        r = client.post("/api/me/mint", json={"season_id": 1})
        assert r.status_code == 200
        spy.assert_not_called()
        record_spy.assert_not_called()

    def test_token_holder_max_per_season_two_allows_one_collision(
        self, mint_test_client, monkeypatch
    ):
        # If an operator raises the cap to 2, a single prior mint from the
        # same IP must NOT trip the gate — only the third one does.
        client, m = mint_test_client
        monkeypatch.setattr(m, "MINT_IP_GATE_MAX_PER_SEASON", 2)
        self._stub_polymarket(m, monkeypatch, has_rank=False)
        monkeypatch.setattr(m, "_wallet_holds_gate_token", lambda wallet: True)
        monkeypatch.setattr(
            m, "_count_other_wallets_minted_from_ip", lambda h, s, w: 1
        )
        monkeypatch.setattr(m, "_record_mint_ip_hash", mock.MagicMock())
        monkeypatch.setattr(
            m.mint_service, "run_queue_mint_request", lambda req: {"status": "queued"}
        )
        r = client.post("/api/me/mint", json={"season_id": 1})
        assert r.status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
# 5. /api/auth/wallet/verify — UPSERT writes last_ip_hash and COALESCE-protects it
# ─────────────────────────────────────────────────────────────────────────────
class TestVerifyPersistsIpHash:
    """The sign-in handler hashes the request IP and writes it into
    ``user_wallet_signins.last_ip_hash``. The UPSERT must use ``COALESCE`` so
    a sign-in from an unresolvable IP (returns NULL hash) does not blank a
    previously-recorded hash — otherwise an attacker could wipe their own
    correlation by signing in once via Tor.
    """

    @pytest.fixture()
    def verify_client(self, m, monkeypatch):
        from datetime import datetime, timezone

        monkeypatch.setattr(m, "_require_wallet_actions_enabled", lambda: None)

        wallet = m.Web3.to_checksum_address("0x" + "ab" * 20)
        challenge_msg = "domain wants you to sign in"
        # Bypass the SIWE signature recovery + atomic consume layer.
        monkeypatch.setattr(
            m,
            "_peek_siwe_challenge",
            lambda cid: (
                m.ChallengeRecord(
                    wallet_address=wallet.lower(),
                    message=challenge_msg,
                    expires_at=datetime.now(timezone.utc).replace(year=2099),
                ),
                "ok",
            ),
        )
        monkeypatch.setattr(m, "_consume_siwe_challenge", lambda cid: True)
        monkeypatch.setattr(
            m.Account, "recover_message", staticmethod(lambda enc, signature: wallet)
        )
        # Profile / leaderboard lookups: no network.
        monkeypatch.setattr(
            m, "_load_wallet_signin_snapshot", lambda w: ("0xproxy", "1")
        )
        monkeypatch.setattr(
            m, "_fetch_polymarket_public_profile", lambda w: {"proxyWallet": "0xproxy"}
        )
        monkeypatch.setattr(m, "_proxy_wallet_from_profile", lambda p: "0xproxy")
        monkeypatch.setattr(m, "_fetch_polymarket_trader_rank", lambda p: ("1", True))
        return TestClient(m.app), m, wallet

    def _capture_upsert(self, m, monkeypatch, ip_hash_returned):
        cursor = mock.MagicMock()
        cursor.__enter__ = lambda self: self
        cursor.__exit__ = mock.MagicMock(return_value=False)
        cursor.fetchone.return_value = (
            "0x" + "ab" * 20,
            __import__("datetime").datetime.now(),
            __import__("datetime").datetime.now(),
            1,
            "0xproxy",
            "1",
        )
        conn = mock.MagicMock()
        conn.cursor.return_value = cursor
        monkeypatch.setattr(m, "_get_connection", lambda: conn)
        monkeypatch.setattr(m, "_hash_client_ip", lambda ip: ip_hash_returned)
        return cursor

    def test_verify_includes_last_ip_hash_in_upsert(
        self, verify_client, monkeypatch
    ):
        client, m, wallet = verify_client
        cursor = self._capture_upsert(m, monkeypatch, "0xfeedface")
        r = client.post(
            "/api/auth/wallet/verify",
            json={"challenge_id": "cid-1", "signature": "0x" + "00" * 65},
        )
        assert r.status_code == 200
        sql, params = cursor.execute.call_args.args
        assert "INSERT INTO user_wallet_signins" in sql
        assert "last_ip_hash" in sql
        # COALESCE on update is what prevents an unresolvable-IP sign-in
        # from blanking an earlier recorded hash.
        assert "COALESCE" in sql
        assert "0xfeedface" in params

    def test_verify_passes_none_when_ip_unresolvable(
        self, verify_client, monkeypatch
    ):
        client, m, wallet = verify_client
        cursor = self._capture_upsert(m, monkeypatch, None)
        r = client.post(
            "/api/auth/wallet/verify",
            json={"challenge_id": "cid-2", "signature": "0x" + "00" * 65},
        )
        assert r.status_code == 200
        _, params = cursor.execute.call_args.args
        # NULL is the signal for "don't overwrite" via COALESCE; the column
        # stays NULL only on a brand-new insert from an unknown IP.
        assert None in params


# ─────────────────────────────────────────────────────────────────────────────
# 6. _rate_limit_client_ip — proxy-header precedence
# ─────────────────────────────────────────────────────────────────────────────
class TestRateLimitClientIpProxyHeaders:
    """The mint-time IP gate hashes whatever ``_rate_limit_client_ip`` returns.
    When the app is deployed behind Cloudflare → nginx → uvicorn, the TCP peer
    is the nearest proxy and every request collapses to one IP if we don't
    consult proxy headers — so the gate would block every legit user after the
    first. These tests pin the precedence:

        CF-Connecting-IP  >  True-Client-IP  >  X-Real-IP  >  X-Forwarded-For

    and assert that proxy headers are ONLY honored when the operator has
    explicitly opted in via ``USER_WEB_TRUST_PROXY_HEADERS`` (or its legacy
    alias). With trust off, untrusted clients can't spoof their IP.
    """

    def _fake_request(self, headers: Dict[str, str], peer: Optional[str] = "10.0.0.1"):
        req = mock.MagicMock()
        req.headers = headers
        if peer:
            req.client = mock.MagicMock(host=peer)
        else:
            req.client = None
        return req

    def test_trust_off_ignores_proxy_headers(self, m, monkeypatch):
        monkeypatch.delenv("USER_WEB_TRUST_PROXY_HEADERS", raising=False)
        monkeypatch.delenv("USER_WEB_TRUST_X_FORWARDED_FOR", raising=False)
        req = self._fake_request(
            {
                "cf-connecting-ip": "1.2.3.4",
                "x-forwarded-for": "5.6.7.8",
                "x-real-ip": "9.10.11.12",
            },
            peer="10.0.0.1",
        )
        # All those spoofable headers must be ignored — the only safe IP is
        # the TCP peer, since we're not promised a proxy in front.
        assert m._rate_limit_client_ip(req) == "10.0.0.1"

    def test_trust_on_prefers_cf_connecting_ip(self, m, monkeypatch):
        # When traffic is Cloudflare → nginx → app, only CF-Connecting-IP
        # carries the original client; the other headers were stamped by
        # downstream hops and would point at a Cloudflare edge.
        monkeypatch.setenv("USER_WEB_TRUST_PROXY_HEADERS", "1")
        req = self._fake_request(
            {
                "cf-connecting-ip": "203.0.113.7",
                "true-client-ip": "198.51.100.4",
                "x-real-ip": "192.0.2.5",
                "x-forwarded-for": "192.0.2.6, 10.0.0.1",
            }
        )
        assert m._rate_limit_client_ip(req) == "203.0.113.7"

    def test_trust_on_falls_back_to_true_client_ip(self, m, monkeypatch):
        monkeypatch.setenv("USER_WEB_TRUST_PROXY_HEADERS", "1")
        req = self._fake_request(
            {
                "true-client-ip": "198.51.100.4",
                "x-real-ip": "192.0.2.5",
                "x-forwarded-for": "192.0.2.6",
            }
        )
        assert m._rate_limit_client_ip(req) == "198.51.100.4"

    def test_trust_on_falls_back_to_x_real_ip(self, m, monkeypatch):
        # Single-hop nginx (no Cloudflare in front) typically sets only
        # X-Real-IP. The fall-through must reach it.
        monkeypatch.setenv("USER_WEB_TRUST_PROXY_HEADERS", "1")
        req = self._fake_request(
            {"x-real-ip": "192.0.2.5", "x-forwarded-for": "10.0.0.1"}
        )
        assert m._rate_limit_client_ip(req) == "192.0.2.5"

    def test_trust_on_takes_leftmost_xff_hop(self, m, monkeypatch):
        # X-Forwarded-For is a chain; the leftmost token is the original
        # client, the rest are intermediate proxies. Trusting the rightmost
        # would always pin to our own ingress IP.
        monkeypatch.setenv("USER_WEB_TRUST_PROXY_HEADERS", "1")
        req = self._fake_request(
            {"x-forwarded-for": "203.0.113.9, 198.51.100.1, 10.0.0.1"}
        )
        assert m._rate_limit_client_ip(req) == "203.0.113.9"

    def test_trust_on_with_no_proxy_headers_uses_peer(self, m, monkeypatch):
        monkeypatch.setenv("USER_WEB_TRUST_PROXY_HEADERS", "1")
        req = self._fake_request({}, peer="10.0.0.1")
        assert m._rate_limit_client_ip(req) == "10.0.0.1"

    def test_legacy_env_alias_still_enables_trust(self, m, monkeypatch):
        # USER_WEB_TRUST_X_FORWARDED_FOR shipped first; deployments that
        # already set it must keep working without a config change.
        monkeypatch.delenv("USER_WEB_TRUST_PROXY_HEADERS", raising=False)
        monkeypatch.setenv("USER_WEB_TRUST_X_FORWARDED_FOR", "1")
        req = self._fake_request({"cf-connecting-ip": "203.0.113.7"})
        assert m._rate_limit_client_ip(req) == "203.0.113.7"

    def test_unresolvable_returns_unknown_sentinel(self, m, monkeypatch):
        monkeypatch.delenv("USER_WEB_TRUST_PROXY_HEADERS", raising=False)
        monkeypatch.delenv("USER_WEB_TRUST_X_FORWARDED_FOR", raising=False)
        req = self._fake_request({}, peer=None)
        assert m._rate_limit_client_ip(req) == "unknown"

    def test_blank_proxy_header_values_are_skipped(self, m, monkeypatch):
        # An empty / whitespace-only header value must not be treated as a
        # valid IP — otherwise we'd hash the empty string for every request
        # behind a misconfigured proxy.
        monkeypatch.setenv("USER_WEB_TRUST_PROXY_HEADERS", "1")
        req = self._fake_request(
            {
                "cf-connecting-ip": "   ",
                "x-real-ip": "192.0.2.5",
            }
        )
        assert m._rate_limit_client_ip(req) == "192.0.2.5"
