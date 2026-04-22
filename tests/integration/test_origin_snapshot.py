"""
Integration tests for SimplifiedScheduler._snapshot_origin_wallets_for_season.

The function freezes a winner/origin snapshot into winner_wallets_nft_to_claim.
It is called with a cursor inside a transaction.

These tests work regardless of what production participant data is in the DB:
1. No exception — the function always completes cleanly.
2. Idempotency — calling twice produces the same row count (DELETE then INSERT).
3. Correctness — all inserted rows belong to the target season_id.
4. Uniqueness — no duplicate (season_id, proxy_wallet) pairs after snapshot.
5. UNIQUE DB constraint — attempting a duplicate wallet insert manually errors.
6. Wallet format constraint — invalid proxy_wallet addresses are rejected.

Cleanup: DELETE season (cascades to winner_wallets_nft_to_claim).
"""

from datetime import datetime, timezone

import psycopg2
import pytest

from tests.integration.conftest import make_real_connection

_SEASON_NUM  = 77500
_SNAP_WALLET = "0x" + "6" * 40


# ------------------------------------------------------------------
# Fixture
# ------------------------------------------------------------------

@pytest.fixture()
def snap_season():
    """Standard season — uses real participant data from the production DB."""
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
                        333, 333, true)
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
# Helpers
# ------------------------------------------------------------------

def _winner_row_count(season_id: int) -> int:
    conn = make_real_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM winner_wallets_nft_to_claim WHERE season_id = %s",
                (season_id,),
            )
            return cur.fetchone()[0]
    finally:
        conn.close()


def _winner_rows(season_id: int) -> list[dict]:
    conn = make_real_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, season_id, proxy_wallet "
                "FROM winner_wallets_nft_to_claim WHERE season_id = %s",
                (season_id,),
            )
            rows = cur.fetchall()
    finally:
        conn.close()
    return [dict(zip(("id", "season_id", "proxy_wallet"), r)) for r in rows]


def _run_snapshot(scheduler, season_id: int) -> int:
    conn = make_real_connection()
    try:
        with conn.cursor() as cur:
            count = scheduler._snapshot_origin_wallets_for_season(
                cur,
                season_id,
                datetime(2099, 1, 1, tzinfo=timezone.utc),
            )
        conn.commit()
        return count
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------

class TestOriginSnapshotRuns:

    def test_does_not_raise(self, real_scheduler, snap_season):
        _run_snapshot(real_scheduler, snap_season)  # must not raise

    def test_returns_integer(self, real_scheduler, snap_season):
        result = _run_snapshot(real_scheduler, snap_season)
        assert isinstance(result, int) and result >= 0

    def test_row_count_matches_return_value(self, real_scheduler, snap_season):
        n = _run_snapshot(real_scheduler, snap_season)
        assert _winner_row_count(snap_season) == n


class TestOriginSnapshotIdempotency:

    def test_second_call_does_not_raise(self, real_scheduler, snap_season):
        _run_snapshot(real_scheduler, snap_season)
        _run_snapshot(real_scheduler, snap_season)  # must not raise

    def test_second_call_produces_same_row_count(self, real_scheduler, snap_season):
        n1 = _run_snapshot(real_scheduler, snap_season)
        n2 = _run_snapshot(real_scheduler, snap_season)
        assert n1 == n2

    def test_stale_manually_inserted_row_cleared_on_second_call(
        self, real_scheduler, snap_season
    ):
        """The DELETE-before-INSERT guarantee must remove rows not in latest snapshot."""
        _run_snapshot(real_scheduler, snap_season)
        count_after_first = _winner_row_count(snap_season)

        # Manually insert an extra row that wouldn't be in a clean snapshot
        conn = make_real_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO winner_wallets_nft_to_claim
                        (season_id, proxy_wallet, source, window_start, window_end)
                    VALUES (%s, %s, 'test_stale',
                            '2098-01-01+00', '2099-01-01+00')
                    ON CONFLICT DO NOTHING
                    """,
                    (snap_season, _SNAP_WALLET),
                )
            conn.commit()
        finally:
            conn.close()

        # After the second snapshot, the table must match the second run's output
        n2 = _run_snapshot(real_scheduler, snap_season)
        assert _winner_row_count(snap_season) == n2
        assert _winner_row_count(snap_season) == count_after_first


class TestOriginSnapshotCorrectness:

    def test_all_rows_have_correct_season_id(self, real_scheduler, snap_season):
        _run_snapshot(real_scheduler, snap_season)
        for row in _winner_rows(snap_season):
            assert row["season_id"] == snap_season

    def test_no_duplicate_proxy_wallets_per_season(self, real_scheduler, snap_season):
        _run_snapshot(real_scheduler, snap_season)
        rows = _winner_rows(snap_season)
        wallets = [r["proxy_wallet"] for r in rows]
        assert len(wallets) == len(set(wallets)), "duplicate proxy_wallet found after snapshot"


class TestOriginSnapshotConstraints:

    def test_db_rejects_duplicate_wallet_per_season(self, snap_season):
        """The UNIQUE (season_id, proxy_wallet) constraint must be enforced."""
        conn = make_real_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO winner_wallets_nft_to_claim
                        (season_id, proxy_wallet, source, window_start, window_end)
                    VALUES (%s, %s, 'test_dup_1', '2098-01-01+00', '2099-01-01+00')
                    """,
                    (snap_season, _SNAP_WALLET),
                )
            conn.commit()
        finally:
            conn.close()

        conn2 = make_real_connection()
        try:
            with pytest.raises(psycopg2.IntegrityError):
                with conn2.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO winner_wallets_nft_to_claim
                            (season_id, proxy_wallet, source, window_start, window_end)
                        VALUES (%s, %s, 'test_dup_2', '2098-01-01+00', '2099-01-01+00')
                        """,
                        (snap_season, _SNAP_WALLET),
                    )
                conn2.commit()
        finally:
            conn2.rollback()
            conn2.close()

    def test_wallet_format_constraint_rejects_invalid_address(self, snap_season):
        conn = make_real_connection()
        try:
            with pytest.raises(psycopg2.IntegrityError):
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO winner_wallets_nft_to_claim
                            (season_id, proxy_wallet, source, window_start, window_end)
                        VALUES (%s, %s, 'test_bad', '2098-01-01+00', '2099-01-01+00')
                        """,
                        (snap_season, "not-a-wallet"),
                    )
                conn.commit()
        finally:
            conn.rollback()
            conn.close()
