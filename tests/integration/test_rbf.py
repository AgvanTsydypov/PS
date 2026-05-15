"""
Integration tests for the RBF (Replace-By-Fee) layer.

Covered scenarios:

1. ``_replace_stuck_transactions`` — happy path and every guard:
   * (a) stuck PROCESSING gets replaced after threshold;
   * (b) attempt cap halts further replacements (error_message badge);
   * (c) ``ReplacementCapped`` halts and badges;
   * (d) ``ReplacementUnderpriced`` halts and badges;
   * (e) ``ReplacementOriginalAlreadyMined`` does NOT badge (recovery owns it).

2. Recovery's ``tx_attempts``-fallback for the RBF race:
   * (f) latest hash returns ``not_found`` but an older attempt returns
     ``success`` → finalize with the historical winner without requeue.

The point of the suite is to lock down behaviour around the wallet's hot
nonce — getting any of these wrong can either freeze the queue (mild) or
double-mint (catastrophic, see CLAUDE.md and the original reliability
review for context).
"""

from __future__ import annotations

import json
import unittest.mock as mock

import pytest

from tests.integration.conftest import (
    _DirectDBManager,
    _patch_scheduler_psycopg2,
    make_real_connection,
)
from tests.integration.test_claim_lifecycle import (
    _FakeMintVerifier,
    _wallet,
    _read_claim,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def rbf_scheduler():
    """SimplifiedScheduler wired to the real container, ready for both
    ``_replace_stuck_transactions`` and ``_recover_stale_processing`` calls.

    Same shape as the ``recovery_scheduler`` fixture in test_claim_lifecycle
    — duplicated here so this file stays standalone and the recovery-fallback
    test (scenario f) can drive recovery directly without importing the
    sibling fixture.
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
def rbf_season():
    """Ephemeral season with cap disabled (total_supply=0)."""
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
                (98201,),
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


def _attempt(
    *,
    hash: str,
    nonce: int = 0,
    max_fee_wei: int = 5_000_000_000,         # 5 gwei
    max_priority_wei: int = 1_000_000_000,    # 1 gwei
    kind: str = "initial",
    recipient: str | None = None,
    metadata_uri: str = "ipfs://bafy_test",
) -> dict:
    """Build an attempt dict in the canonical shape ``_send_mint_tx`` writes."""
    return {
        "hash": hash,
        "nonce": nonce,
        "max_fee_wei": max_fee_wei,
        "max_priority_wei": max_priority_wei,
        "kind": kind,
        "submitted_at": "2026-05-14T12:00:00+00:00",
        "recipient": recipient or _wallet(999),
        "metadata_uri": metadata_uri,
    }


def _insert_claim_with_attempts(
    *,
    season_id: int,
    wallet: str,
    attempts: list[dict],
    age_minutes: int = 60,
    error_message: str | None = None,
) -> int:
    """Insert a PROCESSING claim with a known tx_attempts history.

    Backdates ``updated_at`` past the RBF threshold so the row is visible
    to the picker. Caller supplies the full audit so the test controls
    exactly which fees/nonce the bump will read from.
    """
    last_hash = attempts[-1]["hash"] if attempts else None
    conn = make_real_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO claims
                    (user_wallet, season_id, phase_type, status,
                     tx_hash, tx_attempts, error_message)
                VALUES (%s, %s, 'breach', 'PROCESSING', %s, %s::jsonb, %s)
                RETURNING id
                """,
                (
                    wallet, season_id, last_hash,
                    json.dumps(attempts), error_message,
                ),
            )
            cid = cur.fetchone()[0]
            cur.execute(
                "UPDATE claims SET updated_at = NOW() - (%s || ' minutes')::interval WHERE id = %s",
                (age_minutes, cid),
            )
        conn.commit()
        return cid
    finally:
        conn.close()


def _read_attempts(claim_id: int) -> list[dict]:
    conn = make_real_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT tx_attempts FROM claims WHERE id = %s", (claim_id,))
            return cur.fetchone()[0] or []
    finally:
        conn.close()


class _FakeRbfClient:
    """Stand-in for ``EvmClient`` that records replace_stuck_tx invocations
    and can be primed to raise specific RBF exceptions.

    The fake mirrors the production interface exactly — same kwargs, same
    return shape (an attempt dict). Anything else would let test bugs slip
    past us by drifting from the real signature.
    """

    def __init__(self, *, raise_exc: Exception | None = None,
                 next_hash: str = "0xreplaced01"):
        self._raise = raise_exc
        self._next_hash = next_hash
        self.calls: list[dict] = []

    def replace_stuck_tx(
        self, *, previous_attempt, current_rapid_max_fee_wei,
        max_fee_wei_ceiling, on_pre_broadcast,
    ):
        self.calls.append({
            "previous_attempt": previous_attempt,
            "current_rapid_max_fee_wei": current_rapid_max_fee_wei,
            "max_fee_wei_ceiling": max_fee_wei_ceiling,
        })
        if self._raise is not None:
            raise self._raise
        bumped = _attempt(
            hash=self._next_hash,
            nonce=int(previous_attempt["nonce"]),
            max_fee_wei=int(previous_attempt["max_fee_wei"] * 1.20),
            max_priority_wei=int(previous_attempt["max_priority_wei"] * 1.20),
            kind="replacement",
            recipient=str(previous_attempt["recipient"]),
            metadata_uri=str(previous_attempt["metadata_uri"]),
        )
        on_pre_broadcast(bumped)
        return bumped


def _patch_evm_client(scheduler_module, fake: _FakeRbfClient):
    """Patch ``EvmClient`` symbol so ``_replace_stuck_transactions`` picks
    up the fake on its lazy import. Returns the mock context manager."""
    return mock.patch.object(
        scheduler_module.__dict__.get("EvmClient", None) or
        __import__("scripts.evm_service", fromlist=["EvmClient"]).EvmClient,
        "__new__", lambda *a, **kw: fake,
    )


# ---------------------------------------------------------------------------
# (a) Happy path: stuck claim gets replaced
# ---------------------------------------------------------------------------


class TestReplaceStuckTransactions:
    """Direct exercise of ``_replace_stuck_transactions`` with a fake
    EvmClient. Each test inserts one claim, runs the method, and asserts
    on the post-state of ``claims`` (status, tx_hash, tx_attempts,
    error_message)."""

    def test_stuck_claim_gets_replaced(self, rbf_scheduler, rbf_season):
        """The headline scenario: one PROCESSING claim has been pending
        20+ minutes with one initial attempt. RBF picks it, bumps fees,
        the new hash lands in ``tx_hash`` and the new attempt is appended
        to the audit array."""
        cid = _insert_claim_with_attempts(
            season_id=rbf_season,
            wallet=_wallet(201),
            attempts=[_attempt(hash="0xinitial01")],
            age_minutes=25,  # past the 20-min threshold
        )

        fake = _FakeRbfClient(next_hash="0xreplaced01")
        from scripts import evm_service
        with mock.patch.object(evm_service, "EvmClient", return_value=fake):
            result = rbf_scheduler._replace_stuck_transactions(
                rapid_max_fee_wei=10_000_000_000,  # 10 gwei
                eth_usd=4000.0,
            )

        assert result["replaced"] == 1
        assert result["capped"] == 0
        assert result["underpriced"] == 0

        row = _read_claim(cid)
        assert row["status"] == "PROCESSING"
        assert row["tx_hash"] == "0xreplaced01"
        attempts = _read_attempts(cid)
        assert len(attempts) == 2
        assert attempts[0]["hash"] == "0xinitial01"
        assert attempts[1]["hash"] == "0xreplaced01"
        assert attempts[1]["kind"] == "replacement"

        # Replacement was called with the previous attempt's fees as input
        # and the live rapid as the floor.
        call = fake.calls[0]
        assert call["previous_attempt"]["hash"] == "0xinitial01"
        assert call["current_rapid_max_fee_wei"] == 10_000_000_000

    def test_attempt_cap_halts_further_replacements(self, rbf_scheduler, rbf_season):
        """A claim already at the attempt cap (5 entries) must not be
        bumped again; instead it gets the stuck badge in error_message
        and the EvmClient is never called."""
        attempts = [
            _attempt(hash=f"0xattempt{i:02d}", kind="initial" if i == 0 else "replacement")
            for i in range(5)
        ]
        cid = _insert_claim_with_attempts(
            season_id=rbf_season,
            wallet=_wallet(202),
            attempts=attempts,
            age_minutes=25,
        )

        fake = _FakeRbfClient()
        from scripts import evm_service
        with mock.patch.object(evm_service, "EvmClient", return_value=fake):
            result = rbf_scheduler._replace_stuck_transactions(
                rapid_max_fee_wei=10_000_000_000,
                eth_usd=4000.0,
            )

        # No replacement attempted — picker filtered it out via the
        # ``jsonb_array_length(tx_attempts) < max_attempts`` clause.
        assert fake.calls == []
        # But the cap-marker pass at the end of the method tagged it.
        row = _read_claim(cid)
        assert row["status"] == "PROCESSING"
        assert row["error_message"] is not None
        assert "[stuck:" in row["error_message"]
        assert "max RBF attempts" in row["error_message"]

    def test_already_stuck_rows_are_not_re_picked(self, rbf_scheduler, rbf_season):
        """A claim that already carries an ``[stuck: …]`` error_message
        must be skipped — operator manually clears the badge to re-enable
        retries."""
        cid = _insert_claim_with_attempts(
            season_id=rbf_season,
            wallet=_wallet(203),
            attempts=[_attempt(hash="0xinitial03")],
            age_minutes=25,
            error_message="[stuck: cleared by operator after wallet refill]",
        )

        fake = _FakeRbfClient()
        from scripts import evm_service
        with mock.patch.object(evm_service, "EvmClient", return_value=fake):
            result = rbf_scheduler._replace_stuck_transactions(
                rapid_max_fee_wei=10_000_000_000, eth_usd=4000.0,
            )

        assert fake.calls == []
        assert result["replaced"] == 0
        # Error message and tx_hash unchanged.
        row = _read_claim(cid)
        assert row["error_message"].startswith("[stuck:")
        assert row["tx_hash"] == "0xinitial03"

    def test_capped_exception_marks_claim_stuck(self, rbf_scheduler, rbf_season):
        """If the bumped fee exceeds the per-tx ceiling, EvmClient raises
        ReplacementCapped. The scheduler must badge the claim and stop
        attempting it without crashing the whole pass."""
        from scripts.evm_service import ReplacementCapped

        cid = _insert_claim_with_attempts(
            season_id=rbf_season,
            wallet=_wallet(204),
            attempts=[_attempt(hash="0xinitial04")],
            age_minutes=25,
        )

        fake = _FakeRbfClient(raise_exc=ReplacementCapped("ceiling 1 wei"))
        from scripts import evm_service
        with mock.patch.object(evm_service, "EvmClient", return_value=fake):
            result = rbf_scheduler._replace_stuck_transactions(
                rapid_max_fee_wei=10_000_000_000, eth_usd=4000.0,
            )

        assert result["replaced"] == 0
        assert result["capped"] >= 1
        row = _read_claim(cid)
        assert row["status"] == "PROCESSING"
        assert row["error_message"] is not None
        assert "[stuck:" in row["error_message"]
        assert "max RBF fee cap" in row["error_message"]

    def test_underpriced_exception_marks_claim_stuck(self, rbf_scheduler, rbf_season):
        """``ReplacementUnderpriced`` from the RPC means the bump wasn't
        big enough (mempool already has a higher bid for this nonce, or
        the node enforces stricter rules). Same outcome as capped:
        badge + stop, no further auto-retries."""
        from scripts.evm_service import ReplacementUnderpriced

        cid = _insert_claim_with_attempts(
            season_id=rbf_season,
            wallet=_wallet(205),
            attempts=[_attempt(hash="0xinitial05")],
            age_minutes=25,
        )

        fake = _FakeRbfClient(raise_exc=ReplacementUnderpriced("replacement underpriced"))
        from scripts import evm_service
        with mock.patch.object(evm_service, "EvmClient", return_value=fake):
            result = rbf_scheduler._replace_stuck_transactions(
                rapid_max_fee_wei=10_000_000_000, eth_usd=4000.0,
            )

        assert result["underpriced"] >= 1
        row = _read_claim(cid)
        assert row["error_message"] is not None
        assert "underpriced" in row["error_message"].lower()

    def test_original_already_mined_does_not_badge(self, rbf_scheduler, rbf_season):
        """``ReplacementOriginalAlreadyMined`` means the original tx mined
        while we were preparing the bump. The row must NOT be badged —
        recovery on the next tick will verify the original hash and
        finalize naturally. Badging here would block recovery."""
        from scripts.evm_service import ReplacementOriginalAlreadyMined

        cid = _insert_claim_with_attempts(
            season_id=rbf_season,
            wallet=_wallet(206),
            attempts=[_attempt(hash="0xinitial06")],
            age_minutes=25,
        )

        fake = _FakeRbfClient(raise_exc=ReplacementOriginalAlreadyMined("nonce too low"))
        from scripts import evm_service
        with mock.patch.object(evm_service, "EvmClient", return_value=fake):
            result = rbf_scheduler._replace_stuck_transactions(
                rapid_max_fee_wei=10_000_000_000, eth_usd=4000.0,
            )

        assert result["original_already_mined"] >= 1
        row = _read_claim(cid)
        # Critical: error_message stays clean, recovery owns the row now.
        assert row["error_message"] is None or "[stuck:" not in (row["error_message"] or "")

    def test_legacy_backfilled_attempt_is_marked_stuck(self, rbf_scheduler, rbf_season):
        """Backfilled rows have a synthesized attempt with no fee data;
        we can't safely bump them. Mark stuck so the operator knows to
        intervene manually."""
        cid = _insert_claim_with_attempts(
            season_id=rbf_season,
            wallet=_wallet(207),
            attempts=[{
                "hash": "0xlegacy01",
                "kind": "initial",
                "submitted_at": "2026-01-01T00:00:00+00:00",
                "backfilled": True,
            }],
            age_minutes=25,
        )

        fake = _FakeRbfClient()
        from scripts import evm_service
        with mock.patch.object(evm_service, "EvmClient", return_value=fake):
            rbf_scheduler._replace_stuck_transactions(
                rapid_max_fee_wei=10_000_000_000, eth_usd=4000.0,
            )

        # No bump happened (we don't know the old fees).
        assert fake.calls == []
        row = _read_claim(cid)
        assert "[stuck:" in (row["error_message"] or "")
        assert "legacy attempt" in (row["error_message"] or "")

    def test_fresh_claim_below_threshold_is_skipped(self, rbf_scheduler, rbf_season):
        """A row updated 5 minutes ago is younger than the 20-min RBF
        threshold and must not be touched — bumping fresh sends would burn
        gas on tx that would have included anyway."""
        cid = _insert_claim_with_attempts(
            season_id=rbf_season,
            wallet=_wallet(208),
            attempts=[_attempt(hash="0xinitial08")],
            age_minutes=5,
        )

        fake = _FakeRbfClient()
        from scripts import evm_service
        with mock.patch.object(evm_service, "EvmClient", return_value=fake):
            result = rbf_scheduler._replace_stuck_transactions(
                rapid_max_fee_wei=10_000_000_000, eth_usd=4000.0,
            )

        assert fake.calls == []
        assert result["replaced"] == 0
        row = _read_claim(cid)
        assert row["tx_hash"] == "0xinitial08"


# ---------------------------------------------------------------------------
# (f) Recovery fallback through tx_attempts
# ---------------------------------------------------------------------------


class TestRecoveryAttemptsFallback:
    """The race we're defending against: an RBF replacement is broadcast,
    the original mines anyway (private-mempool / MEV-Share rebroadcast),
    the replacement returns ``not_found`` because nonce is consumed by the
    original. Without the audit fallback, recovery would requeue (after
    the 6h fuse) and we'd double-mint when the regular pickup loop
    re-broadcasts."""

    def test_not_found_with_historical_winner_finalizes_via_old_hash(
        self, rbf_scheduler, rbf_season,
    ):
        # Two attempts on file: the original (mined privately) and the
        # replacement (lost the race, RPC reports not_found).
        cid = _insert_claim_with_attempts(
            season_id=rbf_season,
            wallet=_wallet(301),
            attempts=[
                _attempt(hash="0xoriginal_mined"),
                _attempt(hash="0xreplacement_lost", kind="replacement",
                         max_fee_wei=6_000_000_000),
            ],
            age_minutes=120,  # past stale threshold
        )

        rbf_scheduler._fake_verifier.mapping = {
            "0xreplacement_lost": ("not_found", None, None),
            "0xoriginal_mined":   ("success", 42, "0xCAFE/42"),
        }

        result = rbf_scheduler._recover_stale_processing()

        # Critical invariants:
        # 1. NOT requeued — that's the bug we're preventing.
        assert result["requeued"] == 0
        # 2. Auto-completed via the historical winner.
        assert result["auto_completed"] >= 1
        # 3. The live tx_hash is now the one that actually mined, so
        #    /api/cards and explorer links resolve correctly.
        row = _read_claim(cid)
        assert row["status"] == "COMPLETED"
        assert row["tx_hash"] == "0xoriginal_mined"

    def test_not_found_with_no_historical_winner_falls_back_to_requeue_logic(
        self, rbf_scheduler, rbf_season,
    ):
        """If neither the latest nor any older attempt mined, recovery
        should respect the dropped-tx fuse just like before — not_found
        for ALL hashes means the tx genuinely never landed."""
        cid = _insert_claim_with_attempts(
            season_id=rbf_season,
            wallet=_wallet(302),
            attempts=[
                _attempt(hash="0xfirst_lost"),
                _attempt(hash="0xsecond_lost", kind="replacement"),
            ],
            age_minutes=120,  # past stale threshold but well under 6h fuse
        )

        rbf_scheduler._fake_verifier.mapping = {
            "0xfirst_lost":  ("not_found", None, None),
            "0xsecond_lost": ("not_found", None, None),
        }

        result = rbf_scheduler._recover_stale_processing()

        # Under the 6h dropped-tx fuse: leave in PROCESSING, just bump
        # updated_at to back off until the next stale window.
        assert result["requeued"] == 0
        assert result["auto_completed"] == 0
        row = _read_claim(cid)
        assert row["status"] == "PROCESSING"

    def test_single_attempt_history_uses_original_path(
        self, rbf_scheduler, rbf_season,
    ):
        """Backwards-compat sanity: a row with exactly one attempt
        (no RBF history) goes through the unchanged not_found logic.
        The new fallback only kicks in when len(tx_attempts) > 1."""
        cid = _insert_claim_with_attempts(
            season_id=rbf_season,
            wallet=_wallet(303),
            attempts=[_attempt(hash="0xsolo_lost")],
            age_minutes=120,
        )

        rbf_scheduler._fake_verifier.mapping = {
            "0xsolo_lost": ("not_found", None, None),
        }

        result = rbf_scheduler._recover_stale_processing()

        # Same as the multi-attempt no-winner case: under the 6h fuse,
        # leave in PROCESSING.
        assert result["requeued"] == 0
        row = _read_claim(cid)
        assert row["status"] == "PROCESSING"
