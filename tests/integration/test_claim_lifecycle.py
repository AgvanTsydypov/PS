"""
State-machine tests for the claim lifecycle.

Covers two surfaces:

1. The forward path through the queue worker — the SQL transitions
   ``QUEUED → PROCESSING → COMPLETED/FAILED`` that ``process_mint_queue``
   issues directly. Bypassing the Python orchestrator (Pinata/EVM/cardgen)
   isolates the lifecycle invariants from external services.

2. The crash-recovery branches in ``_recover_stale_processing`` —
   re-entered at the start of every worker run to heal ``PROCESSING``
   rows abandoned by a previous crash:

     * Branch 1: ``tx_hash IS NULL`` and aged → requeue back to
       ``QUEUED`` with ``collection_mint_number`` nulled out.
     * Branch 2: ``tx_hash IS NOT NULL`` and aged → auto-finalize to
       ``COMPLETED`` (mint already broadcast on-chain; only the final
       flip didn't land).
     * Branch 3: as Branch 2 but the simple flip violates
       ``ux_claims_season_collection_mint`` because a sibling already
       owns that ``cmn`` in COMPLETED → renumber to ``MAX(cmn)+1`` over
       PROCESSING ∪ COMPLETED for the season; on-chain metadata keeps
       the original number, divergence captured in ``error_message``.

The recovery threshold (``MINT_QUEUE_STALE_PROCESSING_MINUTES``,
default 30) gates every branch; in-flight rows from a sibling worker
are never touched. The "no-op when fresh" test guards that gate.
"""

from __future__ import annotations

import pytest

from tests.integration.conftest import (
    _DirectDBManager,
    _patch_scheduler_psycopg2,
    make_real_connection,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def recovery_scheduler():
    """SimplifiedScheduler wired to the real test container.

    ``object.__new__`` skips heavy ``DataLoadingManager`` init and we drop in
    ``_DirectDBManager`` so ``_recover_stale_processing`` reaches the live
    container directly. ``dry_run`` is left False so recovery actually
    executes its UPDATEs.
    """
    with _patch_scheduler_psycopg2() as sched_mod:
        scheduler = object.__new__(sched_mod.SimplifiedScheduler)
        scheduler.manager = _DirectDBManager()
        scheduler.dry_run = False
        scheduler.use_local_db = True
        yield scheduler


@pytest.fixture()
def lifecycle_season():
    """Create one ephemeral season with total_supply=0 (cap disabled)."""
    conn = make_real_connection()
    season_id: int | None = None
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
                        0, 0, true)
                RETURNING id
                """,
                # Use a high season_number so we don't clash with other tests
                # that share the same DB session (testcontainers is per-session).
                (98101,),
            )
            season_id = cur.fetchone()[0]
        conn.commit()
    finally:
        conn.close()

    yield season_id

    conn = make_real_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM claims  WHERE season_id = %s", (season_id,))
            cur.execute("DELETE FROM seasons WHERE id        = %s", (season_id,))
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _wallet(i: int) -> str:
    return "0x" + format(i, "x").rjust(40, "0")


def _insert_claim(
    *,
    season_id: int,
    wallet: str,
    status: str,
    tx_hash: str | None = None,
    cmn: int | None = None,
    age_minutes: int | None = None,
) -> int:
    """Insert a claim and (optionally) backdate ``updated_at``.

    The ``BEFORE INSERT`` trigger keeps ``updated_at = NOW()``, so the
    backdate has to be a separate UPDATE — we set it to the requested
    age past the recovery threshold.
    """
    conn = make_real_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO claims
                    (user_wallet, season_id, phase_type, status,
                     tx_hash, collection_mint_number)
                VALUES (%s, %s, 'breach', %s, %s, %s)
                RETURNING id
                """,
                (wallet, season_id, status, tx_hash, cmn),
            )
            cid = cur.fetchone()[0]
            if age_minutes is not None:
                cur.execute(
                    "UPDATE claims SET updated_at = NOW() - (%s || ' minutes')::interval WHERE id = %s",
                    (age_minutes, cid),
                )
        conn.commit()
        return cid
    finally:
        conn.close()


def _read_claim(claim_id: int) -> dict:
    import psycopg2.extras

    conn = make_real_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, status, tx_hash, collection_mint_number, error_message
                FROM   claims
                WHERE  id = %s
                """,
                (claim_id,),
            )
            return dict(cur.fetchone())
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 1. Forward transitions — QUEUED → PROCESSING → COMPLETED / FAILED
# ---------------------------------------------------------------------------

# Mirrors the production pickup query (daily_scheduler_simple.py:2594) — the
# atomic ``UPDATE … WHERE id = (SELECT … FOR UPDATE SKIP LOCKED)`` flip.
_PICKUP_SQL = """
UPDATE claims
SET    status = 'PROCESSING', updated_at = NOW()
WHERE  id = (
    SELECT id FROM claims
    WHERE  status   = 'QUEUED'
      AND  season_id = %s
    ORDER  BY created_at ASC, id ASC
    FOR UPDATE SKIP LOCKED
    LIMIT 1
)
RETURNING id, status
"""


class TestForwardTransitions:
    """The full happy-path lifecycle. Each transition is a single SQL
    UPDATE in production; here we drive them by hand and assert the
    observable state after each step."""

    def test_queued_to_processing_to_completed(self, lifecycle_season):
        cid = _insert_claim(
            season_id=lifecycle_season,
            wallet=_wallet(101),
            status="QUEUED",
        )
        assert _read_claim(cid)["status"] == "QUEUED"

        # QUEUED → PROCESSING (pickup)
        conn = make_real_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(_PICKUP_SQL, (lifecycle_season,))
                picked_id, picked_status = cur.fetchone()
            conn.commit()
        finally:
            conn.close()
        assert picked_id == cid
        assert picked_status == "PROCESSING"
        assert _read_claim(cid)["status"] == "PROCESSING"

        # PROCESSING → COMPLETED (final flip after on-chain mint)
        conn = make_real_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE claims
                    SET    status                 = 'COMPLETED',
                           tx_hash                = %s,
                           collection_mint_number = 1,
                           updated_at             = NOW()
                    WHERE  id = %s
                      AND  status = 'PROCESSING'
                    """,
                    ("0xdeadbeef", cid),
                )
                assert cur.rowcount == 1
            conn.commit()
        finally:
            conn.close()

        row = _read_claim(cid)
        assert row["status"] == "COMPLETED"
        assert row["tx_hash"] == "0xdeadbeef"
        assert row["collection_mint_number"] == 1

    def test_queued_to_processing_to_failed(self, lifecycle_season):
        cid = _insert_claim(
            season_id=lifecycle_season,
            wallet=_wallet(102),
            status="QUEUED",
        )

        conn = make_real_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(_PICKUP_SQL, (lifecycle_season,))
                cur.fetchone()
                cur.execute(
                    """
                    UPDATE claims
                    SET    status        = 'FAILED',
                           error_message = %s,
                           updated_at    = NOW()
                    WHERE  id = %s
                      AND  status = 'PROCESSING'
                    """,
                    ("pinata upload failed", cid),
                )
                assert cur.rowcount == 1
            conn.commit()
        finally:
            conn.close()

        row = _read_claim(cid)
        assert row["status"] == "FAILED"
        assert row["error_message"] == "pinata upload failed"

    def test_pickup_skips_non_queued_rows(self, lifecycle_season):
        """A row already in PROCESSING must not be re-picked by the
        QUEUED-only pickup query; otherwise a worker could grab a row
        another worker is mid-mint on."""
        _insert_claim(
            season_id=lifecycle_season,
            wallet=_wallet(103),
            status="PROCESSING",
        )

        conn = make_real_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(_PICKUP_SQL, (lifecycle_season,))
                row = cur.fetchone()
            conn.commit()
        finally:
            conn.close()

        assert row is None


# ---------------------------------------------------------------------------
# 2. Recovery — Branch 1: stuck PROCESSING with no tx_hash → requeue
# ---------------------------------------------------------------------------

class TestRecoveryRequeueBranch:
    """No ``tx_hash`` means the EVM mint never went out, so the row is
    safe to flip back to QUEUED. The cmn is nulled so the next pickup
    re-allocates from MAX+1 — the freed number becomes a permanent gap
    rather than a risk of two cards sharing it."""

    def test_stale_processing_without_tx_hash_is_requeued(
        self, recovery_scheduler, lifecycle_season
    ):
        cid = _insert_claim(
            season_id=lifecycle_season,
            wallet=_wallet(201),
            status="PROCESSING",
            tx_hash=None,
            cmn=7,
            age_minutes=120,  # well past the 30-min default threshold
        )

        result = recovery_scheduler._recover_stale_processing()

        assert result == {"requeued": 1, "auto_completed": 0, "renumbered": 0}
        row = _read_claim(cid)
        assert row["status"] == "QUEUED"
        assert row["collection_mint_number"] is None
        assert row["error_message"] is not None
        assert "auto-reset" in row["error_message"]

    def test_fresh_processing_without_tx_hash_is_left_alone(
        self, recovery_scheduler, lifecycle_season
    ):
        """Threshold gate: a row updated within the last few minutes is
        an active sibling worker, not a corpse — recovery must not touch it."""
        cid = _insert_claim(
            season_id=lifecycle_season,
            wallet=_wallet(202),
            status="PROCESSING",
            tx_hash=None,
            cmn=3,
            age_minutes=1,  # well inside the threshold
        )

        result = recovery_scheduler._recover_stale_processing()

        assert result["requeued"] == 0
        row = _read_claim(cid)
        assert row["status"] == "PROCESSING"
        assert row["collection_mint_number"] == 3


# ---------------------------------------------------------------------------
# 3. Recovery — Branch 2: stuck PROCESSING with tx_hash → auto-complete
# ---------------------------------------------------------------------------

class TestRecoveryAutoCompleteBranch:
    """``tx_hash IS NOT NULL`` means the mint already broadcast on-chain.
    Re-running the worker would be a double-spend; recovery just lands
    the COMPLETED flip that the crashed prior run never got to."""

    def test_stale_processing_with_tx_hash_is_auto_completed(
        self, recovery_scheduler, lifecycle_season
    ):
        cid = _insert_claim(
            season_id=lifecycle_season,
            wallet=_wallet(301),
            status="PROCESSING",
            tx_hash="0xabc123",
            cmn=5,
            age_minutes=120,
        )

        result = recovery_scheduler._recover_stale_processing()

        assert result == {"requeued": 0, "auto_completed": 1, "renumbered": 0}
        row = _read_claim(cid)
        assert row["status"] == "COMPLETED"
        # cmn must be preserved — the on-chain metadata already references it.
        assert row["collection_mint_number"] == 5
        assert row["tx_hash"] == "0xabc123"
        assert row["error_message"] is not None
        assert "auto-completed" in row["error_message"]

    def test_fresh_processing_with_tx_hash_is_left_alone(
        self, recovery_scheduler, lifecycle_season
    ):
        cid = _insert_claim(
            season_id=lifecycle_season,
            wallet=_wallet(302),
            status="PROCESSING",
            tx_hash="0xfeedface",
            cmn=11,
            age_minutes=1,
        )

        result = recovery_scheduler._recover_stale_processing()

        assert result["auto_completed"] == 0
        row = _read_claim(cid)
        assert row["status"] == "PROCESSING"


# ---------------------------------------------------------------------------
# 4. Recovery — Branch 3: stuck PROCESSING + duplicate cmn → renumber
# ---------------------------------------------------------------------------

class TestRecoveryRenumberBranch:
    """Triggered when the simple auto-complete flip would violate
    ``ux_claims_season_collection_mint`` (a sibling already owns the cmn
    in COMPLETED). Recovery rolls back to the SAVEPOINT, allocates the
    next free MAX(cmn)+1 over PROCESSING ∪ COMPLETED under the per-season
    advisory lock, and lands COMPLETED with the new number. The on-chain
    NFT still references the original cmn — the divergence is recorded
    in error_message."""

    def test_duplicate_cmn_triggers_renumber_and_completion(
        self, recovery_scheduler, lifecycle_season
    ):
        # Sibling COMPLETED row that already owns cmn=4. The unique index
        # is partial (only enforced for COMPLETED rows), so this is what
        # forces the conflict on the recovery flip.
        sibling = _insert_claim(
            season_id=lifecycle_season,
            wallet=_wallet(401),
            status="COMPLETED",
            tx_hash="0xsibling",
            cmn=4,
        )

        # Pre-populate a few more COMPLETED rows so MAX(cmn)+1 is a
        # non-trivial number we can assert on.
        for i, num in enumerate((1, 2, 3), start=402):
            _insert_claim(
                season_id=lifecycle_season,
                wallet=_wallet(i),
                status="COMPLETED",
                tx_hash=f"0xc{num:x}",
                cmn=num,
            )

        # Stuck PROCESSING row whose cmn collides with the sibling's 4.
        stuck = _insert_claim(
            season_id=lifecycle_season,
            wallet=_wallet(410),
            status="PROCESSING",
            tx_hash="0xstuck",
            cmn=4,
            age_minutes=120,
        )

        result = recovery_scheduler._recover_stale_processing()

        assert result["requeued"] == 0
        assert result["auto_completed"] == 1
        assert result["renumbered"] == 1

        stuck_row = _read_claim(stuck)
        assert stuck_row["status"] == "COMPLETED"
        assert stuck_row["tx_hash"] == "0xstuck"
        # Allocator picks MAX(cmn)+1 over PROCESSING ∪ COMPLETED with non-null cmn.
        # Existing cmns at the time of the renumber UPDATE: 1, 2, 3, 4 (sibling),
        # plus the stuck row itself still holding 4 → MAX = 4, new cmn = 5.
        assert stuck_row["collection_mint_number"] == 5
        assert stuck_row["error_message"] is not None
        assert "auto-renumbered" in stuck_row["error_message"]
        assert "4 -> 5" in stuck_row["error_message"]

        # Sibling untouched.
        sibling_row = _read_claim(sibling)
        assert sibling_row["status"] == "COMPLETED"
        assert sibling_row["collection_mint_number"] == 4


# ---------------------------------------------------------------------------
# 5. Recovery — multiple branches in a single pass
# ---------------------------------------------------------------------------

class TestRecoveryMixedBatch:
    """The recovery pass iterates internally; one invocation must heal
    every aged corpse regardless of which branch each one falls into.
    This guards against a regression where a per-row exception in one
    branch could short-circuit the rest of the batch."""

    def test_single_pass_heals_all_three_branch_kinds(
        self, recovery_scheduler, lifecycle_season
    ):
        # Branch 1 candidate
        requeue_cid = _insert_claim(
            season_id=lifecycle_season,
            wallet=_wallet(501),
            status="PROCESSING",
            tx_hash=None,
            age_minutes=120,
        )
        # Branch 2 candidate
        autocomp_cid = _insert_claim(
            season_id=lifecycle_season,
            wallet=_wallet(502),
            status="PROCESSING",
            tx_hash="0xokay",
            cmn=10,
            age_minutes=120,
        )
        # Branch 3 setup: COMPLETED sibling + colliding stuck row
        _insert_claim(
            season_id=lifecycle_season,
            wallet=_wallet(503),
            status="COMPLETED",
            tx_hash="0xsib",
            cmn=20,
        )
        renum_cid = _insert_claim(
            season_id=lifecycle_season,
            wallet=_wallet(504),
            status="PROCESSING",
            tx_hash="0xstk",
            cmn=20,
            age_minutes=120,
        )

        result = recovery_scheduler._recover_stale_processing()

        assert result["requeued"] == 1
        assert result["auto_completed"] == 2  # Branch 2 + Branch 3 both count
        assert result["renumbered"] == 1

        assert _read_claim(requeue_cid)["status"] == "QUEUED"
        assert _read_claim(autocomp_cid)["status"] == "COMPLETED"
        renum_row = _read_claim(renum_cid)
        assert renum_row["status"] == "COMPLETED"
        assert renum_row["collection_mint_number"] != 20
