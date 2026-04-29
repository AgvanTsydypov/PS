"""
Integration tests for SeasonManager eligibility and phase logic.

Each test controls the season state by inserting rows with specific
start_date offsets, then calls real SeasonManager methods against the
live database.

Phase windows (start_date = T):
  breach      T  →  T+3d   (capped at 20% of supply)
  vault       T+3d → T+6d  (Origins only)
  scavenge    T+6d → T+9d
  transmission T+9d → T+10d (closed)
  hard stop   T+10d+        (closed)

Fixture cleanup: drop the season's participants partition first (which clears
its rows), then DELETE the matching claims and the season row itself. There
is no longer a winner_wallets table to coordinate with.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tests.integration.conftest import make_real_connection

# ---------------------------------------------------------------------------
# Sentinel wallets — valid 0x+40hex format, unique enough to avoid collisions
# ---------------------------------------------------------------------------
_ORIGIN_WALLET  = "0x" + "a" * 40   # this wallet IS in winner_wallets
_LOOTER_WALLET  = "0x" + "b" * 40   # this wallet is NOT in winner_wallets
_OTHER_WALLET   = "0x" + "c" * 40   # used for "minted by different wallet"
_SEASON_NUMBER  = 88888              # sentinel season_number for test rows


# ---------------------------------------------------------------------------
# Low-level DB helpers
# ---------------------------------------------------------------------------

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _insert_season(cur, *, start_offset_days: float, season_type: str = "standard",
                   total_supply: int = 100, remaining_supply: int = 100,
                   season_number: int = _SEASON_NUMBER) -> int:
    start = _now_utc() + timedelta(days=start_offset_days)
    end   = start + timedelta(days=30)
    cur.execute(
        """
        INSERT INTO seasons
            (type, season_number, start_date, end_date,
             total_supply, remaining_supply, is_active)
        VALUES (%s, %s, %s, %s, %s, %s, TRUE)
        RETURNING id
        """,
        (season_type, season_number, start, end, total_supply, remaining_supply),
    )
    return cur.fetchone()[0]


def _insert_winner(cur, *, season_id: int, proxy_wallet: str = _ORIGIN_WALLET,
                   is_minted: bool = False, minted_to_wallet: str | None = None) -> int:
    """Insert an Origin participant for the season's partition.

    Under the queue model, ``is_minted`` / ``minted_to_wallet`` are recorded
    by inserting an active claim row keyed on the same proxy_wallet (which is
    what ``_get_origin_snapshot_mint_status`` now reads from). Returns the
    synthetic participant rowid (1).
    """
    cur.execute("SELECT participants_ensure_partition(%s)", (season_id,))
    cur.execute(
        """
        INSERT INTO participants
            (season_id, proxy_wallet, event_slug, archetype)
        VALUES (%s, %s, %s, 'OPERATOR')
        ON CONFLICT (season_id, proxy_wallet, event_slug) DO NOTHING
        """,
        (season_id, proxy_wallet, f"test-event-{season_id}-{proxy_wallet[:10]}"),
    )
    if is_minted:
        cur.execute(
            """
            INSERT INTO claims (
                user_wallet, season_id, phase_type, status, proxy_wallet
            ) VALUES (%s, %s, 'vault', 'COMPLETED', %s)
            """,
            (minted_to_wallet or proxy_wallet, season_id, proxy_wallet),
        )
    return 1


def _insert_claim(cur, *, season_id: int, wallet: str = _ORIGIN_WALLET,
                  status: str = "PENDING") -> int:
    cur.execute(
        """
        INSERT INTO claims (user_wallet, season_id, phase_type, status)
        VALUES (%s, %s, 'breach', %s)
        RETURNING id
        """,
        (wallet, season_id, status),
    )
    return cur.fetchone()[0]


def _cleanup(conn, *, claim_ids=(), winner_ids=(), season_ids=()):  # noqa: ARG001 — winner_ids kept for call-site parity
    with conn.cursor() as cur:
        for cid in claim_ids:
            cur.execute("DELETE FROM claims WHERE id = %s", (cid,))
        for sid in season_ids:
            # Drop partition first to also clear participants rows for the season.
            cur.execute("SELECT participants_drop_partition(%s)", (sid,))
            cur.execute("DELETE FROM claims WHERE season_id = %s", (sid,))
            cur.execute("DELETE FROM seasons WHERE id = %s", (sid,))
    conn.commit()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def breach_season():
    """Standard season that started 1 day ago — currently in Breach."""
    conn = make_real_connection()
    sid = wid = None
    try:
        with conn.cursor() as cur:
            sid = _insert_season(cur, start_offset_days=-1)
            wid = _insert_winner(cur, season_id=sid)
        conn.commit()
        yield {"season_id": sid, "winner_id": wid}
    finally:
        _cleanup(conn, winner_ids=[wid] if wid else [], season_ids=[sid] if sid else [])
        conn.close()


@pytest.fixture()
def vault_season():
    """Standard season that started 4 days ago — currently in Vault."""
    conn = make_real_connection()
    sid = wid = None
    try:
        with conn.cursor() as cur:
            sid = _insert_season(cur, start_offset_days=-4)
            wid = _insert_winner(cur, season_id=sid)
        conn.commit()
        yield {"season_id": sid, "winner_id": wid}
    finally:
        _cleanup(conn, winner_ids=[wid] if wid else [], season_ids=[sid] if sid else [])
        conn.close()


@pytest.fixture()
def scavenge_season():
    """Standard season that started 7 days ago — currently in Scavenge."""
    conn = make_real_connection()
    sid = wid = None
    try:
        with conn.cursor() as cur:
            sid = _insert_season(cur, start_offset_days=-7)
            wid = _insert_winner(cur, season_id=sid)
        conn.commit()
        yield {"season_id": sid, "winner_id": wid}
    finally:
        _cleanup(conn, winner_ids=[wid] if wid else [], season_ids=[sid] if sid else [])
        conn.close()


@pytest.fixture()
def transmission_season():
    """Standard season 9.5 days old — in Transmission (claims closed)."""
    conn = make_real_connection()
    sid = wid = None
    try:
        with conn.cursor() as cur:
            sid = _insert_season(cur, start_offset_days=-9.5)
            wid = _insert_winner(cur, season_id=sid)
        conn.commit()
        yield {"season_id": sid, "winner_id": wid}
    finally:
        _cleanup(conn, winner_ids=[wid] if wid else [], season_ids=[sid] if sid else [])
        conn.close()


@pytest.fixture()
def exhausted_season():
    """Season with remaining_supply = 0 — hard stop regardless of timing."""
    conn = make_real_connection()
    sid = wid = None
    try:
        with conn.cursor() as cur:
            sid = _insert_season(cur, start_offset_days=-1,
                                 total_supply=100, remaining_supply=0)
            wid = _insert_winner(cur, season_id=sid)
        conn.commit()
        yield {"season_id": sid, "winner_id": wid}
    finally:
        _cleanup(conn, winner_ids=[wid] if wid else [], season_ids=[sid] if sid else [])
        conn.close()


@pytest.fixture()
def genesis_breach_season():
    """Genesis season that started 1 day ago — in Genesis Breach."""
    conn = make_real_connection()
    sid = wid = None
    try:
        with conn.cursor() as cur:
            sid = _insert_season(cur, start_offset_days=-1, season_type="genesis",
                                 season_number=_SEASON_NUMBER + 1)
            wid = _insert_winner(cur, season_id=sid)
        conn.commit()
        yield {"season_id": sid, "winner_id": wid}
    finally:
        _cleanup(conn, winner_ids=[wid] if wid else [], season_ids=[sid] if sid else [])
        conn.close()


# ---------------------------------------------------------------------------
# Phase detection tests
# ---------------------------------------------------------------------------

class TestGetCurrentPhase:

    def test_breach_phase_returned_on_day_1(self, breach_season, real_season_manager):
        result = real_season_manager.get_current_phase(breach_season["season_id"])
        assert result["phase"] == "breach"
        assert result["is_claim_open"] is True
        assert result["requires_origin"] is False

    def test_vault_phase_returned_on_day_4(self, vault_season, real_season_manager):
        result = real_season_manager.get_current_phase(vault_season["season_id"])
        assert result["phase"] == "vault"
        assert result["is_claim_open"] is True
        assert result["requires_origin"] is True

    def test_scavenge_phase_returned_on_day_7(self, scavenge_season, real_season_manager):
        result = real_season_manager.get_current_phase(scavenge_season["season_id"])
        assert result["phase"] == "scavenge"
        assert result["is_claim_open"] is True
        assert result["requires_origin"] is False

    def test_transmission_phase_returned_on_day_9_5(self, transmission_season, real_season_manager):
        result = real_season_manager.get_current_phase(transmission_season["season_id"])
        assert result["phase"] == "transmission"
        assert result["is_claim_open"] is False

    def test_transmission_on_supply_exhausted(self, exhausted_season, real_season_manager):
        """Supply = 0 must force transmission regardless of which day it is."""
        result = real_season_manager.get_current_phase(exhausted_season["season_id"])
        assert result["phase"] == "transmission"
        assert result["is_claim_open"] is False
        assert "exhausted" in result["reason"].lower()

    def test_genesis_breach_phase_on_day_1(self, genesis_breach_season, real_season_manager):
        result = real_season_manager.get_current_phase(genesis_breach_season["season_id"])
        assert result["phase"] == "breach"
        assert result["season_type"] == "genesis"
        assert result["is_claim_open"] is True

    def test_phase_result_includes_supply_fields(self, breach_season, real_season_manager):
        result = real_season_manager.get_current_phase(breach_season["season_id"])
        assert result["supply_total"] == 100
        assert result["supply_remaining"] == 100

    def test_breach_cap_reached_early_moves_to_vault(self, real_season_manager):
        """When used_supply >= 20% of total_supply during day 1-3, phase becomes vault."""
        conn = make_real_connection()
        sid = wid = None
        try:
            with conn.cursor() as cur:
                # 80 remaining out of 100 = 20 used = exactly at breach cap (20%)
                sid = _insert_season(cur, start_offset_days=-1,
                                     total_supply=100, remaining_supply=80,
                                     season_number=_SEASON_NUMBER + 2)
                wid = _insert_winner(cur, season_id=sid)
            conn.commit()

            result = real_season_manager.get_current_phase(sid)
            assert result["phase"] == "vault"
            assert result["requires_origin"] is True
        finally:
            _cleanup(conn, winner_ids=[wid] if wid else [],
                     season_ids=[sid] if sid else [])
            conn.close()


# ---------------------------------------------------------------------------
# is_origin_wallet_for_season tests
# ---------------------------------------------------------------------------

class TestIsOriginWalletForSeason:

    def test_origin_wallet_returns_true(self, breach_season, real_season_manager):
        assert real_season_manager.is_origin_wallet_for_season(
            _ORIGIN_WALLET, breach_season["season_id"]
        ) is True

    def test_unknown_wallet_returns_false(self, breach_season, real_season_manager):
        assert real_season_manager.is_origin_wallet_for_season(
            _LOOTER_WALLET, breach_season["season_id"]
        ) is False

    def test_case_insensitive_match(self, breach_season, real_season_manager):
        """Uppercase wallet must still match the stored lowercase row."""
        upper = _ORIGIN_WALLET.upper()
        # validator requires '0x' prefix lowercase — vary body only
        body_upper = "0x" + _ORIGIN_WALLET[2:].upper()
        assert real_season_manager.is_origin_wallet_for_season(
            body_upper, breach_season["season_id"]
        ) is True

    def test_wrong_season_returns_false(self, breach_season, real_season_manager):
        """A valid origin wallet for season X must return False for season X+9999."""
        assert real_season_manager.is_origin_wallet_for_season(
            _ORIGIN_WALLET, breach_season["season_id"] + 9999
        ) is False


# ---------------------------------------------------------------------------
# check_user_eligibility_for_season — combines phase + origin + claim checks
# ---------------------------------------------------------------------------

class TestCheckUserEligibilityForSeason:

    def test_origin_wallet_eligible_in_breach(self, breach_season, real_season_manager):
        result = real_season_manager.check_user_eligibility_for_season(
            _ORIGIN_WALLET, breach_season["season_id"]
        )
        assert result["eligible_now"] is True
        assert result["already_claimed"] is False
        assert result["is_origin_wallet"] is True

    def test_looter_wallet_eligible_in_breach(self, breach_season, real_season_manager):
        """Non-origin wallets can still claim during Breach."""
        result = real_season_manager.check_user_eligibility_for_season(
            _LOOTER_WALLET, breach_season["season_id"]
        )
        assert result["eligible_now"] is True
        assert result["is_origin_wallet"] is False

    def test_origin_wallet_eligible_in_vault(self, vault_season, real_season_manager):
        result = real_season_manager.check_user_eligibility_for_season(
            _ORIGIN_WALLET, vault_season["season_id"]
        )
        assert result["eligible_now"] is True
        assert result["requires_origin"] is True

    def test_looter_wallet_blocked_in_vault(self, vault_season, real_season_manager):
        """Vault phase must reject non-origin wallets."""
        result = real_season_manager.check_user_eligibility_for_season(
            _LOOTER_WALLET, vault_season["season_id"]
        )
        assert result["eligible_now"] is False
        assert "vault" in result["ineligible_reason"].lower() or \
               "origin" in result["ineligible_reason"].lower()

    def test_both_wallets_eligible_in_scavenge(self, scavenge_season, real_season_manager):
        """Scavenge is open to all wallets."""
        for wallet in (_ORIGIN_WALLET, _LOOTER_WALLET):
            result = real_season_manager.check_user_eligibility_for_season(
                wallet, scavenge_season["season_id"]
            )
            assert result["eligible_now"] is True, f"{wallet} should be eligible in scavenge"

    def test_nobody_eligible_in_transmission(self, transmission_season, real_season_manager):
        for wallet in (_ORIGIN_WALLET, _LOOTER_WALLET):
            result = real_season_manager.check_user_eligibility_for_season(
                wallet, transmission_season["season_id"]
            )
            assert result["eligible_now"] is False
            assert result["is_claim_open"] is False

    def test_already_claimed_blocks_eligibility(self, breach_season, real_season_manager):
        """A wallet with an existing PENDING claim must be marked ineligible."""
        conn = make_real_connection()
        cid = None
        try:
            with conn.cursor() as cur:
                cid = _insert_claim(cur, season_id=breach_season["season_id"],
                                    wallet=_ORIGIN_WALLET, status="PENDING")
            conn.commit()

            result = real_season_manager.check_user_eligibility_for_season(
                _ORIGIN_WALLET, breach_season["season_id"]
            )
            assert result["eligible_now"] is False
            assert result["already_claimed"] is True
            assert "already claimed" in result["ineligible_reason"].lower()
        finally:
            _cleanup(conn, claim_ids=[cid] if cid else [])
            conn.close()

    def test_failed_claim_does_not_block_eligibility(self, breach_season, real_season_manager):
        """A FAILED claim must NOT be treated as already claimed."""
        conn = make_real_connection()
        cid = None
        try:
            with conn.cursor() as cur:
                cid = _insert_claim(cur, season_id=breach_season["season_id"],
                                    wallet=_ORIGIN_WALLET, status="FAILED")
            conn.commit()

            result = real_season_manager.check_user_eligibility_for_season(
                _ORIGIN_WALLET, breach_season["season_id"]
            )
            assert result["already_claimed"] is False
            assert result["eligible_now"] is True
        finally:
            _cleanup(conn, claim_ids=[cid] if cid else [])
            conn.close()

    def test_origin_allocation_minted_by_same_wallet_blocks(self, breach_season, real_season_manager):
        """If the origin's slot already has an active claim, even the original wallet can't claim again."""
        conn = make_real_connection()
        cid = None
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO claims (
                        user_wallet, season_id, phase_type, status, proxy_wallet
                    ) VALUES (%s, %s, 'vault', 'COMPLETED', %s)
                    RETURNING id
                    """,
                    (_ORIGIN_WALLET, breach_season["season_id"], _ORIGIN_WALLET),
                )
                cid = cur.fetchone()[0]
            conn.commit()

            result = real_season_manager.check_user_eligibility_for_season(
                _ORIGIN_WALLET, breach_season["season_id"]
            )
            assert result["eligible_now"] is False
            assert result["origin_snapshot_is_minted"] is True
            assert "minted" in (result["ineligible_reason"] or "").lower()
        finally:
            if cid:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM claims WHERE id = %s", (cid,))
                conn.commit()
            conn.close()

    def test_origin_allocation_minted_by_different_wallet_blocks(self, breach_season, real_season_manager):
        """Origin slot consumed by a *different* claimer → ineligible with that wallet mentioned."""
        conn = make_real_connection()
        cid = None
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO claims (
                        user_wallet, season_id, phase_type, status, proxy_wallet
                    ) VALUES (%s, %s, 'vault', 'COMPLETED', %s)
                    RETURNING id
                    """,
                    (_OTHER_WALLET, breach_season["season_id"], _ORIGIN_WALLET),
                )
                cid = cur.fetchone()[0]
            conn.commit()

            result = real_season_manager.check_user_eligibility_for_season(
                _ORIGIN_WALLET, breach_season["season_id"]
            )
            assert result["eligible_now"] is False
            assert _OTHER_WALLET in (result["ineligible_reason"] or "")
        finally:
            if cid:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM claims WHERE id = %s", (cid,))
                conn.commit()
            conn.close()

    def test_response_shape_contains_all_expected_keys(self, breach_season, real_season_manager):
        result = real_season_manager.check_user_eligibility_for_season(
            _ORIGIN_WALLET, breach_season["season_id"]
        )
        for key in (
            "season_id", "season_type", "phase", "phase_reason",
            "already_claimed", "eligible_now", "ineligible_reason",
            "requires_origin", "is_claim_open",
            "is_origin_wallet", "origin_snapshot_is_minted",
            "origin_snapshot_minted_to_wallet",
        ):
            assert key in result, f"missing key {key!r} in eligibility response"
