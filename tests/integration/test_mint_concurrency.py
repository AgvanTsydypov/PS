"""
Concurrency invariants for the mint pipeline.

Each test runs N threads against shared real Postgres state and asserts the
global invariant after all threads return.  Threads synchronize on a
``threading.Barrier`` so contention is real, not sequential.

These tests target the SQL-level invariants directly — unique indexes,
the ``claims_check_caps`` trigger, ``FOR UPDATE SKIP LOCKED`` pickup, and
the per-season advisory lock that serializes ``collection_mint_number``
allocation.  Bypassing the high-level Python entry points
(``run_queue_mint_request``, ``process_mint_queue``) isolates the
concurrency contract from orchestration noise (Pinata uploads, EVM RPC,
card rendering); the orchestration paths are tested elsewhere.

Schema note: the test schema (init-db.sql + create_seasons_system.sql) only
allows status IN ('PENDING','PROCESSING','COMPLETED','FAILED').  Production
adds QUEUED at runtime via ``ensure_claims_schema_for_mint``, but the lock
semantics are identical regardless of status name, so we use PENDING.
"""

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from psycopg2 import errors

from tests.integration.conftest import make_real_connection


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _insert_claim_attempt(season_id: int, wallet: str, status: str = "PENDING"):
    """Open a fresh connection and INSERT a claim. Returns (id, error)."""
    conn = make_real_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO claims (user_wallet, season_id, phase_type, status)
                VALUES (%s, %s, 'breach', %s)
                RETURNING id
                """,
                (wallet, season_id, status),
            )
            cid = cur.fetchone()[0]
        conn.commit()
        return cid, None
    except Exception as exc:  # noqa: BLE001 — return for assertion
        conn.rollback()
        return None, exc
    finally:
        conn.close()


def _make_wallet(i: int) -> str:
    """Synthesize a deterministic 0x-prefixed EVM-shaped wallet for index i."""
    return "0x" + format(i, "x").rjust(40, "0")


# ---------------------------------------------------------------------------
# Fixture: ephemeral seasons
# ---------------------------------------------------------------------------

@pytest.fixture()
def season_factory():
    """Create active seasons with configurable total_supply; clean up on exit."""
    created: list[int] = []

    def _make(*, total_supply: int, season_num: int) -> int:
        conn = make_real_connection()
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
                            %s, %s, true)
                    RETURNING id
                    """,
                    (season_num, total_supply, total_supply),
                )
                sid = cur.fetchone()[0]
            conn.commit()
            created.append(sid)
            return sid
        finally:
            conn.close()

    yield _make

    conn = make_real_connection()
    try:
        with conn.cursor() as cur:
            for sid in created:
                cur.execute("DELETE FROM claims WHERE season_id = %s", (sid,))
                cur.execute("DELETE FROM seasons WHERE id = %s", (sid,))
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 1. Same wallet, N parallel inserts → unique constraint enforced
# ---------------------------------------------------------------------------

class TestSameWalletConcurrentInsert:
    """``unique_user_season_claim UNIQUE(user_wallet, season_id)`` is the
    backstop against double-mint per wallet per season.  Without it, two
    requests racing the application-layer eligibility check could both
    pass the check and both INSERT, billing the user twice."""

    def test_n_parallel_inserts_for_same_wallet_yield_one_success(self, season_factory):
        sid = season_factory(total_supply=0, season_num=99001)
        wallet = _make_wallet(1)
        n = 10
        barrier = threading.Barrier(n)

        def worker(_idx: int):
            barrier.wait()
            return _insert_claim_attempt(sid, wallet)

        with ThreadPoolExecutor(max_workers=n) as pool:
            results = list(pool.map(worker, range(n)))

        successes = [cid for cid, err in results if err is None]
        failures = [err for _cid, err in results if err is not None]

        assert len(successes) == 1, f"expected 1 success, got {len(successes)}: {results}"
        assert len(failures) == n - 1
        for err in failures:
            assert isinstance(err, errors.UniqueViolation), f"unexpected error: {err!r}"


# ---------------------------------------------------------------------------
# 2. Drop hour: 30 looters, 10 slots → trigger enforces cap
# ---------------------------------------------------------------------------

class TestSupplyCapEnforcedConcurrently:
    """``claims_check_caps`` trigger (create_seasons_system.sql:900) counts
    active claims (PENDING/PROCESSING/COMPLETED + QUEUED in production) and
    rejects INSERTs once total_supply is reached.  Under contention this is
    the only thing standing between a popular drop and an oversupplied
    collection."""

    def test_concurrent_inserts_capped_at_total_supply(self, season_factory):
        cap = 10
        n = 30  # 3x oversubscription, distinct wallets
        sid = season_factory(total_supply=cap, season_num=99002)
        barrier = threading.Barrier(n)

        def worker(i: int):
            barrier.wait()
            return _insert_claim_attempt(sid, _make_wallet(1000 + i))

        with ThreadPoolExecutor(max_workers=n) as pool:
            results = list(pool.map(worker, range(n)))

        successes = [cid for cid, err in results if err is None]
        failures = [err for _cid, err in results if err is not None]

        assert len(successes) == cap
        assert len(failures) == n - cap
        for err in failures:
            assert "total supply" in str(err).lower(), f"unexpected error: {err!r}"


# ---------------------------------------------------------------------------
# 3. process_mint_queue race: two workers grab the same row
# ---------------------------------------------------------------------------

# Mirrors scripts/daily_scheduler_simple.py:2692 — the production query targets
# status='QUEUED', but FOR UPDATE SKIP LOCKED semantics are status-agnostic.
_PICKUP_SQL = """
UPDATE claims
SET    status = 'PROCESSING', updated_at = NOW()
WHERE  id = (
    SELECT id FROM claims
    WHERE  status = 'PENDING'
      AND  season_id = %s
    ORDER  BY created_at ASC, id ASC
    FOR UPDATE SKIP LOCKED
    LIMIT 1
)
RETURNING id
"""


class TestWorkerPickupRace:
    """``FOR UPDATE SKIP LOCKED`` is what lets multiple worker replicas drain
    the queue without coordinating.  If two workers ever picked up the same
    row, one would attempt to mint an NFT for a row the other already
    transitioned to PROCESSING — at best a duplicate Pinata upload, at worst
    two on-chain mints from the same claim row."""

    def test_two_workers_cannot_pick_same_row(self, season_factory):
        sid = season_factory(total_supply=0, season_num=99003)
        cid, err = _insert_claim_attempt(sid, _make_wallet(99), status="PENDING")
        assert err is None and cid is not None

        results: list[int | None] = []
        results_lock = threading.Lock()
        barrier = threading.Barrier(2)

        def pickup():
            barrier.wait()
            conn = make_real_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute(_PICKUP_SQL, (sid,))
                    row = cur.fetchone()
                conn.commit()
                with results_lock:
                    results.append(row[0] if row else None)
            finally:
                conn.close()

        t1 = threading.Thread(target=pickup)
        t2 = threading.Thread(target=pickup)
        t1.start(); t2.start()
        t1.join(); t2.join()

        non_null = [r for r in results if r is not None]
        null = [r for r in results if r is None]
        assert len(non_null) == 1, f"expected exactly one pickup, got {results}"
        assert len(null) == 1
        assert non_null[0] == cid


# ---------------------------------------------------------------------------
# 4. cmn allocation under parallel PROCESSING — no duplicates, no gaps
# ---------------------------------------------------------------------------

# Mirrors scripts/daily_scheduler_simple.py:2754 — read MAX over PROCESSING
# ∪ COMPLETED with non-null cmn, then claim the next number under
# ``pg_advisory_xact_lock(9283742, season_id)``.
_CMN_ALLOC_SQL = """
UPDATE claims
SET    collection_mint_number = (
           SELECT COALESCE(MAX(collection_mint_number), 0) + 1
           FROM   claims
           WHERE  season_id = %s
             AND  status    IN ('PROCESSING', 'COMPLETED')
             AND  collection_mint_number IS NOT NULL
       ),
       updated_at = NOW()
WHERE  id = %s
  AND  collection_mint_number IS NULL
RETURNING collection_mint_number
"""


class TestCmnAllocationNoDuplicates:
    """The collection mint number is baked onto the rendered card image — once
    two cards share a number, the divergence is permanent (the PNG is on
    Pinata, the number is on chain).  The per-season advisory lock
    ``pg_advisory_xact_lock(9283742, season_id)`` is what makes the
    MAX(...)+1 read-modify-write atomic across workers."""

    def test_n_parallel_cmn_allocations_yield_distinct_numbers(self, season_factory):
        n = 10
        sid = season_factory(total_supply=0, season_num=99004)

        ids: list[int] = []
        for i in range(n):
            cid, err = _insert_claim_attempt(sid, _make_wallet(2000 + i), status="PROCESSING")
            assert err is None
            ids.append(cid)

        results: list[int] = []
        results_lock = threading.Lock()
        barrier = threading.Barrier(n)

        def allocate(claim_id: int):
            barrier.wait()
            conn = make_real_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT pg_advisory_xact_lock(9283742, %s)", (sid,))
                    cur.execute(_CMN_ALLOC_SQL, (sid, claim_id))
                    row = cur.fetchone()
                conn.commit()
                with results_lock:
                    if row is not None:
                        results.append(row[0])
            finally:
                conn.close()

        threads = [threading.Thread(target=allocate, args=(cid,)) for cid in ids]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert sorted(results) == list(range(1, n + 1)), (
            f"expected contiguous 1..{n}, got {sorted(results)}"
        )
