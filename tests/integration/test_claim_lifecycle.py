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

class _FakeMintVerifier:
    """Stand-in for ``EvmClient.fetch_mint_receipt_status`` used by recovery
    tests. Map of ``tx_hash → (status, token_id, asset_address)``; any hash
    not in the map defaults to ``"success"`` to keep the legacy auto-complete
    tests (which pre-date the on-chain verification step) working without
    explicit mapping.
    """

    def __init__(self, mapping: dict[str, tuple[str, int | None, str | None]] | None = None):
        self.mapping = mapping or {}
        self.calls: list[str] = []

    def fetch_mint_receipt_status(self, tx_hash: str):
        self.calls.append(tx_hash)
        if tx_hash in self.mapping:
            return self.mapping[tx_hash]
        # Default: pretend the tx mined successfully with a synthetic asset.
        return ("success", 1, f"0xCAFE/1")


@pytest.fixture()
def recovery_scheduler():
    """SimplifiedScheduler wired to the real test container.

    ``object.__new__`` skips heavy ``DataLoadingManager`` init and we drop in
    ``_DirectDBManager`` so ``_recover_stale_processing`` reaches the live
    container directly. ``dry_run`` is left False so recovery actually
    executes its UPDATEs.

    The receipt-verifier factory is overridden with ``_FakeMintVerifier`` so
    branch 2 (``tx_hash IS NOT NULL``) tests don't need live RPC. Tests that
    want a different on-chain status set ``scheduler._fake_verifier.mapping``
    before invoking recovery.
    """
    with _patch_scheduler_psycopg2() as sched_mod:
        scheduler = object.__new__(sched_mod.SimplifiedScheduler)
        scheduler.manager = _DirectDBManager()
        scheduler.dry_run = False
        scheduler.use_local_db = True
        verifier = _FakeMintVerifier()
        scheduler._fake_verifier = verifier
        scheduler._make_mint_receipt_verifier = lambda: verifier
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

        assert result["requeued"] == 1
        assert result["auto_completed"] == 0
        assert result["renumbered"] == 0
        assert result.get("finalized_failed", 0) == 0
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

        assert result["requeued"] == 0
        assert result["auto_completed"] == 1
        assert result["renumbered"] == 0
        assert result.get("finalized_failed", 0) == 0
        row = _read_claim(cid)
        assert row["status"] == "COMPLETED"
        # cmn must be preserved — the on-chain metadata already references it.
        assert row["collection_mint_number"] == 5
        assert row["tx_hash"] == "0xabc123"
        assert row["error_message"] is not None
        assert "auto-completed" in row["error_message"]
        # Recovery must have actually consulted the chain (no more blind flips).
        assert "0xabc123" in recovery_scheduler._fake_verifier.calls

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


# ---------------------------------------------------------------------------
# 6. Recovery — RPC verification branches (success / reverted / pending /
#    not_found). These cover the on-chain check that replaces the old
#    "blind auto-complete" flip for ``tx_hash IS NOT NULL`` rows.
# ---------------------------------------------------------------------------

class TestRecoveryRpcVerification:
    """A stale ``PROCESSING + tx_hash`` row is no longer assumed minted. The
    recovery pass now consults ``EvmClient.fetch_mint_receipt_status(tx_hash)``
    and routes by the answer. These tests pin each branch by swapping the
    verifier mapping before invoking recovery."""

    def test_reverted_tx_marks_failed_and_frees_cmn(
        self, recovery_scheduler, lifecycle_season
    ):
        """status=0 on receipt → no NFT exists. The row must NOT be promoted
        to COMPLETED (would lie about a successful mint that never happened).
        cmn is cleared so the slot returns to the allocator."""
        cid = _insert_claim(
            season_id=lifecycle_season,
            wallet=_wallet(601),
            status="PROCESSING",
            tx_hash="0xrev_aaa",
            cmn=42,
            age_minutes=120,
        )
        recovery_scheduler._fake_verifier.mapping = {
            "0xrev_aaa": ("reverted", None, None),
        }

        result = recovery_scheduler._recover_stale_processing()

        assert result["auto_completed"] == 0
        assert result["finalized_failed"] == 1
        row = _read_claim(cid)
        assert row["status"] == "FAILED"
        assert row["collection_mint_number"] is None
        # tx_hash is preserved as audit trail of the failed attempt.
        assert row["tx_hash"] == "0xrev_aaa"
        assert "on-chain revert" in row["error_message"]

    def test_pending_tx_is_left_in_processing_with_bumped_updated_at(
        self, recovery_scheduler, lifecycle_season
    ):
        """Receipt absent but the tx is still visible in the mempool — the
        broadcast worked, the chain just hasn't mined it yet. Recovery must
        not touch status/cmn; only bump updated_at so we back off until the
        next stale window instead of hammering the RPC every cron tick."""
        cid = _insert_claim(
            season_id=lifecycle_season,
            wallet=_wallet(602),
            status="PROCESSING",
            tx_hash="0xpend_bbb",
            cmn=7,
            age_minutes=120,
        )
        recovery_scheduler._fake_verifier.mapping = {
            "0xpend_bbb": ("pending", None, None),
        }

        result = recovery_scheduler._recover_stale_processing()

        assert result["auto_completed"] == 0
        assert result["finalized_failed"] == 0
        assert result["requeued"] == 0
        row = _read_claim(cid)
        # State and slot untouched.
        assert row["status"] == "PROCESSING"
        assert row["collection_mint_number"] == 7
        # updated_at was bumped: the row is no longer past the 30-min stale
        # threshold, so a second recovery pass on the same data is a no-op.
        recovery_scheduler._fake_verifier.calls.clear()
        again = recovery_scheduler._recover_stale_processing()
        assert again["auto_completed"] == 0
        assert recovery_scheduler._fake_verifier.calls == [], (
            "Second pass must not re-query the RPC for a row whose timer was reset"
        )

    def test_not_found_within_grace_window_is_left_alone(
        self, recovery_scheduler, lifecycle_season, monkeypatch
    ):
        """A "not_found" answer within the dropped-tx window means the tx may
        still arrive (low-tier safe gas can sit in the mempool for hours).
        Recovery must NOT requeue inside the window — that would race a tx
        that could still mine and cause a double mint."""
        import os
        monkeypatch.setenv("MINT_QUEUE_DROPPED_TX_REQUEUE_HOURS", "6")
        cid = _insert_claim(
            season_id=lifecycle_season,
            wallet=_wallet(603),
            status="PROCESSING",
            tx_hash="0xnf_ccc",
            cmn=11,
            age_minutes=120,  # past 30-min stale, well inside 6-hour grace
        )
        recovery_scheduler._fake_verifier.mapping = {
            "0xnf_ccc": ("not_found", None, None),
        }

        result = recovery_scheduler._recover_stale_processing()

        assert result["requeued"] == 0
        assert result["auto_completed"] == 0
        assert result["finalized_failed"] == 0
        row = _read_claim(cid)
        assert row["status"] == "PROCESSING"
        assert row["tx_hash"] == "0xnf_ccc"
        assert row["collection_mint_number"] == 11

    def test_not_found_past_grace_window_is_requeued(
        self, recovery_scheduler, lifecycle_season, monkeypatch
    ):
        """After the dropped-tx window expires, recovery may safely assume
        the broadcast genuinely went nowhere and requeue the row for a fresh
        attempt. cmn and tx_hash are both NULLed so the next allocator
        picks a clean slot."""
        monkeypatch.setenv("MINT_QUEUE_DROPPED_TX_REQUEUE_HOURS", "1")
        cid = _insert_claim(
            season_id=lifecycle_season,
            wallet=_wallet(604),
            status="PROCESSING",
            tx_hash="0xnf_ddd",
            cmn=13,
            age_minutes=120,  # past 1-hour grace
        )
        recovery_scheduler._fake_verifier.mapping = {
            "0xnf_ddd": ("not_found", None, None),
        }

        result = recovery_scheduler._recover_stale_processing()

        assert result["requeued"] == 1
        assert result["auto_completed"] == 0
        assert result["finalized_failed"] == 0
        row = _read_claim(cid)
        assert row["status"] == "QUEUED"
        assert row["tx_hash"] is None
        assert row["collection_mint_number"] is None
        assert "auto-requeue" in row["error_message"]

    def test_success_backfills_asset_address_when_missing(
        self, recovery_scheduler, lifecycle_season
    ):
        """The pre-broadcast hook persists only tx_hash; the on-chain
        asset_address is unknown until the receipt is parsed. Recovery
        success-path must backfill it so the claim row carries the same
        on-chain identity a normal completion would have written."""
        cid = _insert_claim(
            season_id=lifecycle_season,
            wallet=_wallet(605),
            status="PROCESSING",
            tx_hash="0xok_eee",
            cmn=21,
            age_minutes=120,
        )
        recovery_scheduler._fake_verifier.mapping = {
            "0xok_eee": ("success", 99, "0xCONTRACT/99"),
        }

        result = recovery_scheduler._recover_stale_processing()

        assert result["auto_completed"] == 1
        # The basic row reader doesn't pull asset_address; check it directly.
        conn = make_real_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT status, asset_address FROM claims WHERE id = %s",
                    (cid,),
                )
                status, asset_address = cur.fetchone()
        finally:
            conn.close()
        assert status == "COMPLETED"
        assert asset_address == "0xCONTRACT/99"


# ---------------------------------------------------------------------------
# 7. Pre-broadcast hook — the closure that closes the double-mint window.
# ---------------------------------------------------------------------------

class TestPreBroadcastHook:
    """``EvmClient.mint_user_nft`` invokes ``on_pre_broadcast(tx_hash)`` BEFORE
    ``send_raw_transaction``. The cron worker uses this to persist the
    deterministic ``signed.hash`` so a crash anywhere between broadcast and
    the worker's own DB write still leaves recovery enough information to
    avoid a second mintTo. These tests verify the contract of that hook by
    driving the same UPDATE the production closure runs against the real
    container, then asserting recovery now routes the row through RPC
    verification rather than the old "no tx_hash → requeue" branch."""

    def test_hook_writes_tx_hash_and_blocks_requeue_branch(
        self, recovery_scheduler, lifecycle_season
    ):
        # Simulate the pre-broadcast hook firing: row in PROCESSING with
        # cmn already allocated, tx_hash filled in BEFORE the broadcast,
        # then a crash. The row sits stale.
        cid = _insert_claim(
            season_id=lifecycle_season,
            wallet=_wallet(701),
            status="PROCESSING",
            tx_hash="0xprehook_abc",
            cmn=8,
            age_minutes=120,
        )
        # Crash window: imagine wait_for_transaction_receipt timed out, the
        # tx is actually in the mempool. Verifier reports "pending".
        recovery_scheduler._fake_verifier.mapping = {
            "0xprehook_abc": ("pending", None, None),
        }

        result = recovery_scheduler._recover_stale_processing()

        # Critical: the row must NOT be requeued (would cause a double mint
        # when the original tx eventually confirms).
        assert result["requeued"] == 0
        row = _read_claim(cid)
        assert row["status"] == "PROCESSING"
        assert row["tx_hash"] == "0xprehook_abc"
        assert row["collection_mint_number"] == 8

    def test_hook_followed_by_successful_confirmation_completes_row(
        self, recovery_scheduler, lifecycle_season
    ):
        """End-to-end of the new pre-hook flow: the worker crashes after
        broadcast but before the durability UPDATE. On the next run the tx
        has mined, recovery looks it up and finalizes the row to COMPLETED
        with the on-chain asset_address (the worker's own pre-hook only
        recorded tx_hash; asset_address comes from the receipt)."""
        cid = _insert_claim(
            season_id=lifecycle_season,
            wallet=_wallet(702),
            status="PROCESSING",
            tx_hash="0xprehook_def",
            cmn=9,
            age_minutes=120,
        )
        recovery_scheduler._fake_verifier.mapping = {
            "0xprehook_def": ("success", 17, "0xCONTRACT/17"),
        }

        result = recovery_scheduler._recover_stale_processing()

        assert result["auto_completed"] == 1
        row = _read_claim(cid)
        assert row["status"] == "COMPLETED"
        # cmn must be preserved — the rendered card and on-chain metadata
        # are immutably keyed to the original number.
        assert row["collection_mint_number"] == 9


# ---------------------------------------------------------------------------
# 8. EvmClient.fetch_mint_receipt_status — RPC-shape contract test.
#    No DB involved; just verifies the verifier translates web3 responses
#    into the four discrete states recovery branches on.
# ---------------------------------------------------------------------------

class TestFetchMintReceiptStatus:
    """Pin the public contract of the receipt verifier so future refactors
    of EvmClient don't silently change what recovery sees. Each test stubs
    ``self.w3`` on a real EvmClient instance with hand-built fakes."""

    def _make_client(self):
        from scripts.evm_service import EvmClient
        # object.__new__ skips __init__ so we don't need EVM_PRIVATE_KEY etc.
        client = object.__new__(EvmClient)
        client._contract_address = "0xCONTRACT"
        return client

    def test_success_path_returns_status_and_asset(self):
        """Receipt found, status==1, Transfer(mint) parses → ("success", token_id, asset)."""
        import unittest.mock as mock

        client = self._make_client()
        client.w3 = mock.MagicMock()
        client.w3.eth.get_transaction_receipt.return_value = {
            "status": 1, "transactionHash": b"\x00" * 32,
        }
        # _extract_token_id walks Transfer events; bypass by patching it.
        client._extract_token_id = lambda receipt: 42  # type: ignore[assignment]

        status, token_id, asset = client.fetch_mint_receipt_status("0xanything")

        assert status == "success"
        assert token_id == 42
        assert asset == "0xCONTRACT/42"

    def test_reverted_status_zero_returns_reverted(self):
        import unittest.mock as mock

        client = self._make_client()
        client.w3 = mock.MagicMock()
        client.w3.eth.get_transaction_receipt.return_value = {
            "status": 0, "transactionHash": b"\x00" * 32,
        }

        status, token_id, asset = client.fetch_mint_receipt_status("0xrev")

        assert status == "reverted"
        assert token_id is None
        assert asset is None

    def test_pending_when_receipt_absent_but_tx_in_mempool(self):
        import unittest.mock as mock
        from web3.exceptions import TransactionNotFound

        client = self._make_client()
        client.w3 = mock.MagicMock()
        client.w3.eth.get_transaction_receipt.side_effect = TransactionNotFound("not found")
        # get_transaction returns a tx object → still pending in mempool.
        client.w3.eth.get_transaction.return_value = {"hash": b"\x00" * 32}

        status, _, _ = client.fetch_mint_receipt_status("0xpending")

        assert status == "pending"

    def test_not_found_when_neither_receipt_nor_pending_tx(self):
        import unittest.mock as mock
        from web3.exceptions import TransactionNotFound

        client = self._make_client()
        client.w3 = mock.MagicMock()
        client.w3.eth.get_transaction_receipt.side_effect = TransactionNotFound("not found")
        client.w3.eth.get_transaction.side_effect = TransactionNotFound("not found")

        status, _, _ = client.fetch_mint_receipt_status("0xghost")

        assert status == "not_found"

    def test_rpc_exception_treated_as_pending_to_avoid_premature_requeue(self):
        """A flaky node returning a 500 must NOT be interpreted as
        "tx dropped" — that would trigger a requeue (and a possible double
        mint) for a tx the chain may have already mined."""
        import unittest.mock as mock

        client = self._make_client()
        client.w3 = mock.MagicMock()
        client.w3.eth.get_transaction_receipt.side_effect = RuntimeError("RPC 500")

        status, _, _ = client.fetch_mint_receipt_status("0xrpc_err")

        assert status == "pending"


# ---------------------------------------------------------------------------
# 9. End-to-end pre_broadcast wiring — verifies the cron closure persists
#    tx_hash BEFORE EvmClient.send_raw_transaction is invoked. Uses a real
#    EvmClient with stubbed web3, but a real DB UPDATE through the
#    scheduler's closure logic.
# ---------------------------------------------------------------------------

class TestPreBroadcastHookOrdering:
    """The single most important invariant in this whole refactor: the DB
    UPDATE that records ``tx_hash`` MUST commit before ``send_raw_transaction``
    is called. Otherwise a crash between the two would leave the row without
    tx_hash and recovery would happily requeue an already-broadcast tx."""

    def test_hook_commits_before_send_raw_transaction(
        self, recovery_scheduler, lifecycle_season
    ):
        import unittest.mock as mock

        cid = _insert_claim(
            season_id=lifecycle_season,
            wallet=_wallet(801),
            status="PROCESSING",
            tx_hash=None,
            cmn=1,
        )

        observed: dict[str, object] = {"db_tx_hash_at_send": None}

        def fake_send_raw_transaction(*_args, **_kwargs):
            # Capture what's in the DB at the exact moment the broadcast
            # would have hit the wire. If the hook fired BEFORE us, tx_hash
            # is already persisted.
            conn = make_real_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT tx_hash FROM claims WHERE id = %s", (cid,))
                    observed["db_tx_hash_at_send"] = cur.fetchone()[0]
            finally:
                conn.close()
            # Return a 32-byte hash to satisfy the caller's downstream parse.
            return b"\x00" * 32

        # Build a minimal EvmClient and inject fakes.
        from scripts.evm_service import EvmClient
        client = object.__new__(EvmClient)
        client._account = mock.MagicMock()
        client.max_retries = 1
        client.retry_delay_seconds = 0.01

        fake_signed = mock.MagicMock()
        # signed.hash.hex() → "deadbeef..." in tests; the prefix-fixup logic
        # in _send_mint_tx will add 0x.
        fake_signed.hash.hex.return_value = "deadbeef" + "0" * 56
        fake_signed.raw_transaction = b"\x01\x02\x03"
        client._account.sign_transaction.return_value = fake_signed

        client.w3 = mock.MagicMock()
        client.w3.eth.chain_id = 1
        client.w3.eth.get_transaction_count.return_value = 0
        client.w3.eth.send_raw_transaction.side_effect = fake_send_raw_transaction
        # Receipt must look like a successful mint so _extract_token_id is the
        # only thing that could fail; bypass it via patch.
        client.w3.eth.wait_for_transaction_receipt.return_value = {
            "status": 1, "transactionHash": b"\x00" * 32,
        }
        client._extract_token_id = lambda receipt: 1  # type: ignore[assignment]
        client._chain_id = 1
        client._contract_address = "0xC"

        # fn.estimate_gas + fn.build_transaction need to look real enough.
        fake_fn = mock.MagicMock()
        fake_fn.estimate_gas.return_value = 100_000
        fake_fn.build_transaction.return_value = {"foo": "bar"}
        client._contract = mock.MagicMock()
        client._contract.functions.mintTo.return_value = fake_fn
        client.GAS_MULTIPLIER = 1.3

        # Drive _send_mint_tx with the production-style hook closure.
        # Hook signature now takes the full attempt dict (RBF makes this
        # the canonical way to capture nonce + fees alongside the hash).
        captured_attempts: list[dict] = []

        def on_pre_broadcast(attempt: dict) -> None:
            captured_attempts.append(attempt)
            conn = make_real_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE claims SET tx_hash = %s, updated_at = NOW() WHERE id = %s",
                        (attempt["hash"], cid),
                    )
                conn.commit()
            finally:
                conn.close()

        client._send_mint_tx(
            recipient="0x" + "0" * 40,
            metadata_uri="ipfs://test",
            on_pre_broadcast=on_pre_broadcast,
        )

        # The invariant: at the moment send_raw_transaction was called, the
        # claim row already had the deterministic tx_hash written. If this
        # is None, the hook fired AFTER broadcast — exactly the bug this
        # refactor exists to eliminate.
        assert observed["db_tx_hash_at_send"] is not None
        assert observed["db_tx_hash_at_send"].startswith("0xdeadbeef")

        # Attempt dict carries everything RBF needs to bump later.
        assert len(captured_attempts) == 1
        attempt = captured_attempts[0]
        assert attempt["hash"].startswith("0xdeadbeef")
        assert attempt["kind"] == "initial"
        assert attempt["nonce"] == 0  # from get_transaction_count mock
        assert attempt["recipient"] == "0x" + "0" * 40
        assert attempt["metadata_uri"] == "ipfs://test"
        # Fees come from the build_transaction mock returning {"foo":"bar"}
        # (no max_fee field), so the helper falls back to 0 — that's fine
        # for this test, what matters is the keys exist for RBF to read.
        assert "max_fee_wei" in attempt
        assert "max_priority_wei" in attempt
        assert "submitted_at" in attempt
