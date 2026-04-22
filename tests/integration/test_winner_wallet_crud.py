"""
Integration tests for winner wallet CRUD operations
(SeasonWorkbenchService.create/update/delete_winner_wallet_row).

Verifies:
- Wallet address is normalised to lowercase on insert and update
- The (season_id, proxy_wallet) UNIQUE constraint is enforced
- Returned dict contains expected keys
- update_winner_wallet_row raises ValueError on unknown row_id
- delete_winner_wallet_row removes the row; raises ValueError on unknown row_id

Cleanup: DELETE season (ON DELETE CASCADE removes winner_wallets_nft_to_claim rows).
"""

import pytest

from tests.integration.conftest import make_real_connection

_SEASON_NUM = 77300
_WALLET_1   = "0x" + "4" * 40   # all lowercase hex — valid
_WALLET_2   = "0x" + "5" * 40
_WINDOW_START = "2099-01-01T00:00:00+00:00"
_WINDOW_END   = "2099-12-31T00:00:00+00:00"


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _make_req(season_id: int, wallet: str, source: str = "integ_test"):
    from admin_backend.main import WinnerWalletsUpsertRequest
    return WinnerWalletsUpsertRequest(
        season_id=season_id,
        wallet_address=wallet,
        source=source,
        window_start_iso=_WINDOW_START,
        window_end_iso=_WINDOW_END,
    )


def _row_exists(winner_id: int) -> bool:
    conn = make_real_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM winner_wallets_nft_to_claim WHERE id = %s", (winner_id,)
            )
            return cur.fetchone() is not None
    finally:
        conn.close()


# ------------------------------------------------------------------
# Fixture
# ------------------------------------------------------------------

@pytest.fixture()
def crud_season():
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
                        100, 100, false)
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

class TestCreateWinnerWalletRow:

    def test_row_inserted_in_db(self, workbench, crud_season):
        req = _make_req(crud_season, _WALLET_1)
        result = workbench.create_winner_wallet_row(req)
        assert _row_exists(result["id"])

    def test_wallet_normalised_to_lowercase(self, workbench, crud_season):
        mixed_case = "0x" + "4" * 38 + "AB"  # ends in AB (upper)
        req = _make_req(crud_season, mixed_case)
        result = workbench.create_winner_wallet_row(req)
        assert result["wallet_address"] == mixed_case.lower()

    def test_returned_dict_has_expected_keys(self, workbench, crud_season):
        req = _make_req(crud_season, _WALLET_1)
        result = workbench.create_winner_wallet_row(req)
        for key in ("id", "season_id", "wallet_address", "source", "is_minted"):
            assert key in result, f"missing key: {key}"

    def test_is_minted_defaults_to_false(self, workbench, crud_season):
        req = _make_req(crud_season, _WALLET_1)
        result = workbench.create_winner_wallet_row(req)
        assert result["is_minted"] is False

    def test_duplicate_wallet_same_season_raises(self, workbench, crud_season):
        req = _make_req(crud_season, _WALLET_1)
        workbench.create_winner_wallet_row(req)
        with pytest.raises(Exception):  # psycopg2.IntegrityError or wrapped
            workbench.create_winner_wallet_row(req)

    def test_invalid_wallet_format_raises_value_error(self, workbench, crud_season):
        req = _make_req(crud_season, "not-a-wallet")
        with pytest.raises(ValueError, match="EVM address"):
            workbench.create_winner_wallet_row(req)


class TestUpdateWinnerWalletRow:

    def test_update_changes_stored_fields(self, workbench, crud_season):
        created = workbench.create_winner_wallet_row(_make_req(crud_season, _WALLET_1))
        row_id = created["id"]
        updated_req = _make_req(crud_season, _WALLET_1, source="updated_source")
        result = workbench.update_winner_wallet_row(row_id, updated_req)
        assert result["source"] == "updated_source"

    def test_update_returns_current_row_dict(self, workbench, crud_season):
        created = workbench.create_winner_wallet_row(_make_req(crud_season, _WALLET_1))
        result = workbench.update_winner_wallet_row(
            created["id"], _make_req(crud_season, _WALLET_2)
        )
        assert result["wallet_address"] == _WALLET_2

    def test_update_nonexistent_row_raises_value_error(self, workbench, crud_season):
        with pytest.raises(ValueError):
            workbench.update_winner_wallet_row(
                999_999_999, _make_req(crud_season, _WALLET_1)
            )


class TestDeleteWinnerWalletRow:

    def test_delete_removes_row(self, workbench, crud_season):
        created = workbench.create_winner_wallet_row(_make_req(crud_season, _WALLET_1))
        row_id = created["id"]
        workbench.delete_winner_wallet_row(row_id)
        assert not _row_exists(row_id)

    def test_delete_nonexistent_row_raises_value_error(self, workbench, crud_season):
        with pytest.raises(ValueError):
            workbench.delete_winner_wallet_row(999_999_999)
