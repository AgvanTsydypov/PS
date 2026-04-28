"""
Integration tests for SeasonWorkbenchService.mark_winner_row_as_minted.

Verifies the double-mint guard: the UPDATE uses WHERE is_minted = FALSE so a
second call on the same row touches 0 rows and raises RuntimeError.

Also verifies that all mint artefacts (tx_hash, asset_address, minted_to_wallet,
minted_claim_id, minted_at) are persisted to the DB by the first successful call.

Cleanup: DELETE season (ON DELETE CASCADE removes winner_wallets_nft_to_claim and claims).
"""

import pytest

from tests.integration.conftest import make_real_connection

_SEASON_NUM   = 77600
_PROXY_WALLET = "0x" + "7" * 40
_EOA_WALLET   = "0x" + "8" * 40


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _fetch_winner_row(winner_id: int) -> dict | None:
    conn = make_real_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT is_minted, minted_at, minted_to_wallet,
                       minted_claim_id,
                       minted_tx_hash, minted_asset_address
                FROM winner_wallets_nft_to_claim WHERE id = %s
                """,
                (winner_id,),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return dict(zip(
        ("is_minted", "minted_at", "minted_to_wallet",
         "minted_claim_id",
         "minted_tx_hash", "minted_asset_address"),
        row,
    ))


def _make_mint_result(claim_id: int):
    from scripts.evm_service import MintedNftResult
    return MintedNftResult(
        claim_id=claim_id,
        asset_address="0x4aAd310B69B37B006Edb8D4573a4CEf7c34A5e8F/0",
        tx_hash="0x" + "b" * 64,
        nft_name="Integ NFT",
        metadata_uri="https://example.com/integ-meta.json",
        explorer_tx_url="https://sepolia.etherscan.io/tx/0x" + "b" * 64,
        explorer_asset_url="https://testnets.opensea.io/assets/sepolia/0x4aAd310B69B37B006Edb8D4573a4CEf7c34A5e8F/0",
    )


def _make_allocation(row_id: int, wallet: str):
    from admin_backend.main import WinnerClaimAllocation
    return WinnerClaimAllocation(
        row_id=row_id,
        winner_wallet_address=wallet,
        assignment_type="origin",
        pnl_value=1000.0,
        rank=1,
        snapshot={},
    )


# ------------------------------------------------------------------
# Fixture
# ------------------------------------------------------------------

@pytest.fixture()
def minted_setup():
    """
    Inserts: season → winner_wallets_nft_to_claim row + a PENDING claims row.
    Yields dict with season_id, winner_id, claim_id.
    Cleanup via season CASCADE.
    """
    conn = make_real_connection()
    season_id = winner_id = claim_id = None
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
                        100, 100, false)
                RETURNING id
                """,
                (_SEASON_NUM,),
            )
            season_id = cur.fetchone()[0]
            cur.execute(
                """
                INSERT INTO winner_wallets_nft_to_claim
                    (season_id, proxy_wallet, source, window_start, window_end)
                VALUES (%s, %s, 'integ_test_mint',
                        '2099-01-01 00:00:00+00',
                        '2099-12-31 00:00:00+00')
                RETURNING id
                """,
                (season_id, _PROXY_WALLET),
            )
            winner_id = cur.fetchone()[0]
            cur.execute(
                """
                INSERT INTO claims
                    (user_wallet, season_id, phase_type, status)
                VALUES (%s, %s, 'breach', 'PENDING')
                RETURNING id
                """,
                (_EOA_WALLET, season_id),
            )
            claim_id = cur.fetchone()[0]
        conn.commit()
        yield {
            "season_id": season_id,
            "winner_id": winner_id,
            "claim_id": claim_id,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        try:
            with conn.cursor() as cur:
                if claim_id:
                    cur.execute("DELETE FROM claims WHERE id = %s", (claim_id,))
                if winner_id:
                    cur.execute(
                        "DELETE FROM winner_wallets_nft_to_claim WHERE id = %s",
                        (winner_id,),
                    )
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

class TestMarkWinnerRowAsMinted:

    def test_is_minted_set_to_true(self, workbench, minted_setup):
        allocation = _make_allocation(minted_setup["winner_id"], _PROXY_WALLET)
        workbench.mark_winner_row_as_minted(
            allocation,
            minted_setup["claim_id"],
            _EOA_WALLET,
            _make_mint_result(minted_setup["claim_id"]),
        )
        row = _fetch_winner_row(minted_setup["winner_id"])
        assert row["is_minted"] is True

    def test_minted_at_is_set(self, workbench, minted_setup):
        allocation = _make_allocation(minted_setup["winner_id"], _PROXY_WALLET)
        workbench.mark_winner_row_as_minted(
            allocation,
            minted_setup["claim_id"],
            _EOA_WALLET,
            _make_mint_result(minted_setup["claim_id"]),
        )
        row = _fetch_winner_row(minted_setup["winner_id"])
        assert row["minted_at"] is not None

    def test_minted_to_wallet_stored(self, workbench, minted_setup):
        allocation = _make_allocation(minted_setup["winner_id"], _PROXY_WALLET)
        workbench.mark_winner_row_as_minted(
            allocation,
            minted_setup["claim_id"],
            _EOA_WALLET,
            _make_mint_result(minted_setup["claim_id"]),
        )
        row = _fetch_winner_row(minted_setup["winner_id"])
        assert row["minted_to_wallet"] == _EOA_WALLET

    def test_minted_claim_id_stored(self, workbench, minted_setup):
        allocation = _make_allocation(minted_setup["winner_id"], _PROXY_WALLET)
        workbench.mark_winner_row_as_minted(
            allocation,
            minted_setup["claim_id"],
            _EOA_WALLET,
            _make_mint_result(minted_setup["claim_id"]),
        )
        row = _fetch_winner_row(minted_setup["winner_id"])
        assert row["minted_claim_id"] == minted_setup["claim_id"]

    def test_minted_tx_hash_stored(self, workbench, minted_setup):
        allocation = _make_allocation(minted_setup["winner_id"], _PROXY_WALLET)
        mint = _make_mint_result(minted_setup["claim_id"])
        workbench.mark_winner_row_as_minted(
            allocation,
            minted_setup["claim_id"],
            _EOA_WALLET,
            mint,
        )
        row = _fetch_winner_row(minted_setup["winner_id"])
        assert row["minted_tx_hash"] == mint.tx_hash

    def test_minted_asset_address_stored(self, workbench, minted_setup):
        allocation = _make_allocation(minted_setup["winner_id"], _PROXY_WALLET)
        mint = _make_mint_result(minted_setup["claim_id"])
        workbench.mark_winner_row_as_minted(
            allocation,
            minted_setup["claim_id"],
            _EOA_WALLET,
            mint,
        )
        row = _fetch_winner_row(minted_setup["winner_id"])
        assert row["minted_asset_address"] == mint.asset_address

    def test_double_mint_guard_raises_runtime_error(self, workbench, minted_setup):
        """Second call on the same row must raise because is_minted is already TRUE."""
        allocation = _make_allocation(minted_setup["winner_id"], _PROXY_WALLET)
        mint = _make_mint_result(minted_setup["claim_id"])
        workbench.mark_winner_row_as_minted(
            allocation,
            minted_setup["claim_id"],
            _EOA_WALLET,
            mint,
        )
        with pytest.raises(RuntimeError, match="already marked as minted"):
            workbench.mark_winner_row_as_minted(
                allocation,
                minted_setup["claim_id"],
                _EOA_WALLET,
                mint,
            )
