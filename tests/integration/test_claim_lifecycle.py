"""
Integration tests for the claim lifecycle:
  reserve_pending_claim → finalize_completed_claim → release_reserved_claim

Verifies DB trigger behaviour that unit tests cannot reach:
- tr_claims_assign_season_mint assigns sequential collection_mint_number on INSERT
- trigger_update_season_supply decrements remaining_supply on COMPLETED transition
- When remaining_supply hits 0 the season becomes inactive + completed

Cleanup: DELETE season (cascades to claims via ON DELETE CASCADE).
"""

import pytest

from tests.integration.conftest import make_real_connection

_WALLET_A = "0x" + "d" * 40
_WALLET_B = "0x" + "e" * 40
_WALLET_C = "0x" + "f" * 40
_SOLANA_ADDR = "H1wsggroxpW3LwCCv8dVeiJW73oYPkcDGgSqhiT5Zbz3"
_SEASON_NUM = 77100


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _fetch_claim(claim_id: int) -> dict | None:
    conn = make_real_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, status, tx_hash, asset_address, collection_mint_number "
                "FROM claims WHERE id = %s",
                (claim_id,),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    keys = ("id", "status", "tx_hash", "asset_address", "collection_mint_number")
    return dict(zip(keys, row))


def _fetch_season(season_id: int) -> dict | None:
    conn = make_real_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT remaining_supply, is_active, is_completed FROM seasons WHERE id = %s",
                (season_id,),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return dict(zip(("remaining_supply", "is_active", "is_completed"), row))


# ------------------------------------------------------------------
# Fixture
# ------------------------------------------------------------------

@pytest.fixture()
def claim_season():
    """Insert a test season with 5 supply units and yield its ID.  Always cleans up."""
    conn = make_real_connection()
    season_id = None
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO seasons
                    (type, season_number, start_date, end_date,
                     total_supply, remaining_supply, is_active)
                VALUES ('standard', %s,
                        '2099-01-01 00:00:00+00',
                        '2099-12-31 00:00:00+00',
                        5, 5, true)
                RETURNING id
                """,
                (_SEASON_NUM,),
            )
            season_id = cur.fetchone()[0]
        conn.commit()
        yield season_id
    except Exception:
        conn.rollback()
        raise
    finally:
        try:
            with conn.cursor() as cur:
                if season_id:
                    cur.execute("DELETE FROM seasons WHERE id = %s", (season_id,))
            conn.commit()
        except Exception:
            conn.rollback()
        finally:
            conn.close()


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------

class TestReservePendingClaim:

    def test_inserts_pending_row(self, workbench, claim_season):
        result = workbench.reserve_pending_claim(
            wallet=_WALLET_A,
            recipient_wallet=_SOLANA_ADDR,
            season_id=claim_season,
            phase="breach",
            mint_chain="solana",
        )
        claim = _fetch_claim(result["claim_id"])
        assert claim is not None
        assert claim["status"] == "PENDING"

    def test_trigger_assigns_collection_mint_number(self, workbench, claim_season):
        result = workbench.reserve_pending_claim(
            wallet=_WALLET_A,
            recipient_wallet=_SOLANA_ADDR,
            season_id=claim_season,
            phase="breach",
            mint_chain="solana",
        )
        assert result["collection_mint_number"] is not None
        assert result["collection_mint_number"] >= 1

    def test_sequential_mint_numbers_within_season(self, workbench, claim_season):
        r1 = workbench.reserve_pending_claim(
            wallet=_WALLET_A,
            recipient_wallet=_SOLANA_ADDR,
            season_id=claim_season,
            phase="breach",
            mint_chain="solana",
        )
        r2 = workbench.reserve_pending_claim(
            wallet=_WALLET_B,
            recipient_wallet=_SOLANA_ADDR,
            season_id=claim_season,
            phase="breach",
            mint_chain="solana",
        )
        n1 = r1["collection_mint_number"]
        n2 = r2["collection_mint_number"]
        assert n2 == n1 + 1, f"expected sequential numbers, got {n1} then {n2}"


class TestFinalizeCompletedClaim:

    def _make_result(self, claim_id: int):
        from scripts.solana_service import MintedNftResult
        return MintedNftResult(
            claim_id=claim_id,
            asset_address="FAKE_ASSET_ADDRESS",
            tx_hash="FAKE_TX_HASH_" + "a" * 40,
            nft_name="Test NFT",
            metadata_uri="https://example.com/meta.json",
            explorer_tx_url="https://explorer.solana.com/tx/fake",
            explorer_asset_url="https://explorer.solana.com/address/fake",
        )

    def test_status_becomes_completed(self, workbench, claim_season):
        r = workbench.reserve_pending_claim(
            wallet=_WALLET_A,
            recipient_wallet=_SOLANA_ADDR,
            season_id=claim_season,
            phase="breach",
            mint_chain="solana",
        )
        workbench.finalize_completed_claim(
            claim_id=r["claim_id"],
            mint_result=self._make_result(r["claim_id"]),
            mint_chain="solana",
        )
        claim = _fetch_claim(r["claim_id"])
        assert claim["status"] == "COMPLETED"

    def test_tx_hash_and_asset_address_written(self, workbench, claim_season):
        r = workbench.reserve_pending_claim(
            wallet=_WALLET_A,
            recipient_wallet=_SOLANA_ADDR,
            season_id=claim_season,
            phase="breach",
            mint_chain="solana",
        )
        mint = self._make_result(r["claim_id"])
        workbench.finalize_completed_claim(
            claim_id=r["claim_id"],
            mint_result=mint,
            mint_chain="solana",
        )
        claim = _fetch_claim(r["claim_id"])
        assert claim["tx_hash"] == mint.tx_hash
        assert claim["asset_address"] == mint.asset_address

    def test_trigger_decrements_season_remaining_supply(self, workbench, claim_season):
        before = _fetch_season(claim_season)["remaining_supply"]
        r = workbench.reserve_pending_claim(
            wallet=_WALLET_A,
            recipient_wallet=_SOLANA_ADDR,
            season_id=claim_season,
            phase="breach",
            mint_chain="solana",
        )
        workbench.finalize_completed_claim(
            claim_id=r["claim_id"],
            mint_result=self._make_result(r["claim_id"]),
            mint_chain="solana",
        )
        after = _fetch_season(claim_season)["remaining_supply"]
        assert after == before - 1

    def test_supply_exhaustion_deactivates_season(self, workbench, claim_season):
        """Completing 5 claims on a 5-supply season triggers is_active→False."""
        wallets = [_WALLET_A, _WALLET_B, _WALLET_C,
                   "0x" + "1" * 40, "0x" + "2" * 40]
        for w in wallets:
            r = workbench.reserve_pending_claim(
                wallet=w,
                recipient_wallet=_SOLANA_ADDR,
                season_id=claim_season,
                phase="breach",
                mint_chain="solana",
            )
            workbench.finalize_completed_claim(
                claim_id=r["claim_id"],
                mint_result=self._make_result(r["claim_id"]),
                mint_chain="solana",
            )
        season = _fetch_season(claim_season)
        assert season["remaining_supply"] == 0
        assert season["is_active"] is False
        assert season["is_completed"] is True


class TestReleaseReservedClaim:

    def test_release_pending_deletes_row_and_returns_true(self, workbench, claim_season):
        r = workbench.reserve_pending_claim(
            wallet=_WALLET_A,
            recipient_wallet=_SOLANA_ADDR,
            season_id=claim_season,
            phase="breach",
            mint_chain="solana",
        )
        released = workbench.release_reserved_claim(r["claim_id"])
        assert released is True
        assert _fetch_claim(r["claim_id"]) is None

    def test_release_completed_claim_does_not_delete(self, workbench, claim_season):
        from scripts.solana_service import MintedNftResult
        r = workbench.reserve_pending_claim(
            wallet=_WALLET_A,
            recipient_wallet=_SOLANA_ADDR,
            season_id=claim_season,
            phase="breach",
            mint_chain="solana",
        )
        workbench.finalize_completed_claim(
            claim_id=r["claim_id"],
            mint_result=MintedNftResult(
                claim_id=r["claim_id"],
                asset_address="ASSET",
                tx_hash="TXHASH",
                nft_name="N",
                metadata_uri="https://x",
                explorer_tx_url="https://x",
                explorer_asset_url="https://x",
            ),
            mint_chain="solana",
        )
        released = workbench.release_reserved_claim(r["claim_id"])
        assert released is False
        assert _fetch_claim(r["claim_id"]) is not None

    def test_release_nonexistent_claim_returns_false(self, workbench, claim_season):
        released = workbench.release_reserved_claim(999_999_999)
        assert released is False
