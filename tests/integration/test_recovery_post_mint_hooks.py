"""
Integration tests for the recovery-path post-mint hooks.

When a stuck PROCESSING row is auto-completed by ``_recover_stale_processing``
(simple flip, historical-winner promotion, or renumber path), the normal
QUEUE pickup loop's three post-mint side-effects don't fire by default:

  1. ``claims.token_id`` backfill from the on-chain receipt.
  2. ``denormalize_card_onto_claim`` — writes ``card_slug``, ``card_title``,
     ``front/back_image_url``, ``primary/secondary_tag``, ``pattern``,
     ``card_payload_json`` so ``/api/cards/{slug}`` resolves the STAR.
  3. Telegram "NEW CLAIM" announcement.

``_run_post_recovery_hooks`` plugs that gap by re-deriving the
``polystars_card`` payload from the on-chain ``metadata_uri`` (the
``card_display_data`` block we always publish) and dispatching the three
side-effects. This suite locks down the contract:

* Each recovery branch (simple flip / historical winner / renumber) fires
  all three hooks.
* The hooks are best-effort — any failure inside them MUST NOT undo the
  COMPLETED flip that already committed.
* Legacy metadata without ``card_display_data`` is handled gracefully
  (token_id still gets written; the denormalize + TG steps skip cleanly).
"""

from __future__ import annotations

import json
import unittest.mock as mock
from typing import Any, Dict, List, Optional

import pytest

from tests.integration.conftest import (
    _DirectDBManager,
    _patch_scheduler_psycopg2,
    make_real_connection,
)
from tests.integration.test_claim_lifecycle import (
    _FakeMintVerifier,
    _insert_claim,
    _read_claim,
    _wallet,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def hooks_scheduler():
    """SimplifiedScheduler wired to the test container, with the verifier
    factory swapped out for a recording fake. Tests inject the per-tx_hash
    receipt mapping via ``scheduler._fake_verifier.mapping`` before driving
    recovery."""
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
def hooks_season():
    """Ephemeral season for these tests. ``total_supply=0`` keeps the cap
    trigger out of the picture — we only care about recovery's flip side-
    effects, not supply enforcement."""
    conn = make_real_connection()
    season_id: Optional[int] = None
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
                # High season_number so this row doesn't clash with sibling
                # season fixtures sharing the same testcontainers session.
                (98901,),
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


def _insert_processing_with_metadata(
    *,
    season_id: int,
    wallet: str,
    tx_hash: str,
    metadata_uri: str,
    age_minutes: int = 60,
    cmn: Optional[int] = None,
    tx_attempts: Optional[List[Dict[str, Any]]] = None,
) -> int:
    """Insert a stuck PROCESSING row populated with the fields recovery
    needs to drive the post-mint hooks (``metadata_uri``, ``tx_hash``,
    optionally ``tx_attempts`` for the historical-winner branch)."""
    conn = make_real_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO claims
                    (user_wallet, season_id, phase_type, status,
                     tx_hash, metadata_uri, collection_mint_number,
                     tx_attempts)
                VALUES (%s, %s, 'breach', 'PROCESSING', %s, %s, %s, %s::jsonb)
                RETURNING id
                """,
                (
                    wallet, season_id, tx_hash, metadata_uri, cmn,
                    json.dumps(tx_attempts or []),
                ),
            )
            cid = cur.fetchone()[0]
            cur.execute(
                "UPDATE claims SET updated_at = NOW() - (%s || ' minutes')::interval"
                " WHERE id = %s",
                (age_minutes, cid),
            )
        conn.commit()
        return cid
    finally:
        conn.close()


def _read_claim_full(claim_id: int) -> dict:
    import psycopg2.extras

    conn = make_real_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, status, tx_hash, metadata_uri, token_id, asset_address,
                       collection_mint_number, error_message,
                       card_slug, card_title, front_image_url, back_image_url,
                       primary_tag, secondary_tag, pattern, card_payload_json
                FROM   claims
                WHERE  id = %s
                """,
                (claim_id,),
            )
            return dict(cur.fetchone())
    finally:
        conn.close()


def _make_card_display_data(
    *,
    slug: str = "test-recovery-slug",
    season_type: str = "standard",
    season_size: int = 256,
    cmn: int = 7,
    archetype: str = "SUBSTRATE",
    card_title: str = "Test Recovery Star",
    primary_tag: str = "Economy",
    secondary_tag: str = "Macro",
    front_cid: str = "bafkreifrontxxx",
    back_cid: str = "bafkreibackxxxx",
) -> Dict[str, Any]:
    """Build a ``card_display_data``-shaped dict matching the structure the
    real ``EvmClient._build_card_display_data_payload`` would publish.
    Tests pass this through the patched ``build_recovery_payload_from_ipfs``
    so we don't depend on a live IPFS gateway."""
    return {
        "season_type": season_type,
        "season_number": 1,
        "season_size": season_size,
        "collection_mint_number": cmn,
        "archetype": archetype,
        "card_title": card_title,
        "primary_tag": primary_tag,
        "secondary_tag": secondary_tag,
        "front_image_url": f"https://gateway.pinata.cloud/ipfs/{front_cid}",
        "back_image_url": f"https://gateway.pinata.cloud/ipfs/{back_cid}",
        "qr_payload": f"https://polystars.app/cards/{slug}",
    }


class _RecordingNotifier:
    """Captures notify_claim_minted invocations so tests can assert that
    Telegram was (or wasn't) called and with which payload."""

    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)


# ---------------------------------------------------------------------------
# 1. Simple-flip recovery → all three hooks fire
# ---------------------------------------------------------------------------


class TestSimpleFlipPostHooks:
    """Branch 2 happy path: stuck PROCESSING with a recorded tx_hash, on-chain
    receipt returns ``success``. The flip lands cleanly (no cmn collision)
    and the new hook block must:

      * write token_id from the receipt,
      * call denormalize_card_onto_claim (card_* columns populated),
      * fire notify_claim_minted (TG announcement).
    """

    def test_writes_token_id_and_denormalizes_and_notifies(
        self, hooks_scheduler, hooks_season,
    ):
        cid = _insert_processing_with_metadata(
            season_id=hooks_season,
            wallet=_wallet(601),
            tx_hash="0xsimple_flip_winner",
            metadata_uri="ipfs://bafkreirecoverysimple",
            age_minutes=60,
            cmn=7,
        )
        hooks_scheduler._fake_verifier.mapping = {
            "0xsimple_flip_winner": ("success", 42, "0xCONTRACT/42"),
        }

        payload = _make_card_display_data(
            slug="simple-flip-slug", cmn=7, card_title="Simple Flip Star",
        )
        recorder = _RecordingNotifier()
        with mock.patch(
            "scripts.polystars_card_payload.build_recovery_payload_from_ipfs",
            return_value=payload,
        ), mock.patch(
            "scripts.telegram_notifier.notify_claim_minted",
            side_effect=recorder,
        ):
            result = hooks_scheduler._recover_stale_processing()

        assert result["auto_completed"] >= 1

        row = _read_claim_full(cid)
        # Core flip invariants — unchanged from the existing recovery tests.
        assert row["status"] == "COMPLETED"
        assert row["tx_hash"] == "0xsimple_flip_winner"
        assert row["asset_address"] == "0xCONTRACT/42"
        # New: token_id backfilled from the verified receipt.
        assert row["token_id"] == 42
        # New: card fields denormalized from the IPFS payload.
        assert row["card_slug"] == "simple-flip-slug"
        assert row["card_title"] == "Simple Flip Star"
        assert row["front_image_url"].endswith("/bafkreifrontxxx")
        assert row["back_image_url"].endswith("/bafkreibackxxxx")
        assert row["primary_tag"] == "Economy"
        assert row["secondary_tag"] == "Macro"
        # card_payload_json is JSONB → psycopg2 deserializes to a dict.
        assert isinstance(row["card_payload_json"], dict)
        assert row["card_payload_json"]["archetype"] == "SUBSTRATE"

        # New: Telegram notify fired exactly once with the expected payload.
        assert len(recorder.calls) == 1
        tg = recorder.calls[0]
        assert tg["season_type"] == "standard"
        assert tg["collection_mint_number"] == 7
        assert tg["season_capacity"] == 256
        assert tg["archetype"] == "SUBSTRATE"
        assert tg["card_url"] == "https://polystars.app/cards/simple-flip-slug"
        assert "bafkreifrontxxx" in tg["front_image_url"]


# ---------------------------------------------------------------------------
# 2. Historical-winner recovery → hooks fire with the PROMOTED tx_hash
# ---------------------------------------------------------------------------


class TestHistoricalWinnerPostHooks:
    """RBF race: latest tx_hash returns ``not_found`` but a historical
    attempt mined. Recovery promotes the older hash and falls through to
    the same flip + hooks. Token_id must come from the *historical* receipt,
    not the not_found latest."""

    def test_promote_then_write_hooks_with_historical_token_id(
        self, hooks_scheduler, hooks_season,
    ):
        # Two attempts on file: the original mined privately, the
        # replacement lost the nonce race and returns not_found.
        attempts = [
            {
                "hash": "0xoriginal_mined_rbf",
                "kind": "initial",
                "nonce": 16,
                "max_fee_wei": 1_000_000_000,
                "max_priority_wei": 1_000_000_000,
                "submitted_at": "2026-05-15T13:00:00+00:00",
                "recipient": _wallet(701),
                "metadata_uri": "ipfs://bafkreirbfrace",
            },
            {
                "hash": "0xreplacement_orphan",
                "kind": "replacement",
                "nonce": 16,
                "max_fee_wei": 3_000_000_000,
                "max_priority_wei": 2_000_000_000,
                "submitted_at": "2026-05-15T13:25:00+00:00",
                "recipient": _wallet(701),
                "metadata_uri": "ipfs://bafkreirbfrace",
            },
        ]
        cid = _insert_processing_with_metadata(
            season_id=hooks_season,
            wallet=_wallet(701),
            tx_hash="0xreplacement_orphan",
            metadata_uri="ipfs://bafkreirbfrace",
            age_minutes=120,
            cmn=11,
            tx_attempts=attempts,
        )
        hooks_scheduler._fake_verifier.mapping = {
            "0xreplacement_orphan": ("not_found", None, None),
            "0xoriginal_mined_rbf": ("success", 99, "0xCONTRACT/99"),
        }

        payload = _make_card_display_data(
            slug="rbf-race-slug", cmn=11, card_title="RBF Race Star",
            archetype="PASSENGER",
        )
        recorder = _RecordingNotifier()
        with mock.patch(
            "scripts.polystars_card_payload.build_recovery_payload_from_ipfs",
            return_value=payload,
        ), mock.patch(
            "scripts.telegram_notifier.notify_claim_minted",
            side_effect=recorder,
        ):
            result = hooks_scheduler._recover_stale_processing()

        assert result["auto_completed"] >= 1

        row = _read_claim_full(cid)
        # tx_hash promoted to the actually-mined hash so explorer links work.
        assert row["status"] == "COMPLETED"
        assert row["tx_hash"] == "0xoriginal_mined_rbf"
        assert row["asset_address"] == "0xCONTRACT/99"
        # token_id MUST come from the historical receipt (99), not from the
        # latest not_found one (None). This is the fix that the previous
        # ``_, _verified_token_id, _`` discard hid.
        assert row["token_id"] == 99
        assert row["card_slug"] == "rbf-race-slug"
        assert row["card_title"] == "RBF Race Star"

        assert len(recorder.calls) == 1
        assert recorder.calls[0]["archetype"] == "PASSENGER"


# ---------------------------------------------------------------------------
# 3. Renumber-path recovery → hooks fire after cmn reallocation
# ---------------------------------------------------------------------------


class TestRenumberPathPostHooks:
    """Branch 3: simple flip violates ``ux_claims_season_collection_mint``
    because a sibling already owns the cmn. Recovery rolls back the
    savepoint, picks ``MAX(cmn)+1``, and finalizes with the new number.
    Hooks must still fire after this renumber-path flip."""

    def test_renumber_then_hooks_fire(self, hooks_scheduler, hooks_season):
        # Sibling COMPLETED row that already owns cmn=4.
        _insert_claim(
            season_id=hooks_season,
            wallet=_wallet(801),
            status="COMPLETED",
            tx_hash="0xrenumber_sibling",
            cmn=4,
        )
        # Stuck row whose cmn=4 will collide on the simple flip.
        stuck = _insert_processing_with_metadata(
            season_id=hooks_season,
            wallet=_wallet(810),
            tx_hash="0xrenumber_stuck",
            metadata_uri="ipfs://bafkreirenumber",
            age_minutes=120,
            cmn=4,
        )
        hooks_scheduler._fake_verifier.mapping = {
            "0xrenumber_stuck": ("success", 77, "0xCONTRACT/77"),
        }

        payload = _make_card_display_data(
            slug="renumber-slug", cmn=4, card_title="Renumber Star",
        )
        recorder = _RecordingNotifier()
        with mock.patch(
            "scripts.polystars_card_payload.build_recovery_payload_from_ipfs",
            return_value=payload,
        ), mock.patch(
            "scripts.telegram_notifier.notify_claim_minted",
            side_effect=recorder,
        ):
            result = hooks_scheduler._recover_stale_processing()

        assert result["auto_completed"] >= 1
        assert result["renumbered"] >= 1

        row = _read_claim_full(stuck)
        assert row["status"] == "COMPLETED"
        # cmn re-allocated to next free (sibling held 4 → new gets 5).
        assert row["collection_mint_number"] == 5
        assert row["error_message"] is not None
        assert "auto-renumbered" in row["error_message"]
        # Hooks ran post-renumber.
        assert row["token_id"] == 77
        assert row["card_slug"] == "renumber-slug"
        assert row["card_title"] == "Renumber Star"
        assert len(recorder.calls) == 1


# ---------------------------------------------------------------------------
# 4. Graceful skip — legacy metadata without card_display_data
# ---------------------------------------------------------------------------


class TestPostHooksGracefulSkip:
    """A claim minted before the ``card_display_data`` block was added to
    metadata will have a valid ``metadata_uri`` but no payload to
    reconstruct from. ``build_recovery_payload_from_ipfs`` returns None;
    the hook block must still write ``token_id`` (it doesn't depend on the
    payload) and leave the card_* columns NULL — the recovery flip itself
    must NOT be undone."""

    def test_legacy_metadata_writes_token_id_only(
        self, hooks_scheduler, hooks_season,
    ):
        cid = _insert_processing_with_metadata(
            season_id=hooks_season,
            wallet=_wallet(901),
            tx_hash="0xlegacy_mint",
            metadata_uri="ipfs://bafkreirecoverylegacy",
            age_minutes=60,
            cmn=21,
        )
        hooks_scheduler._fake_verifier.mapping = {
            "0xlegacy_mint": ("success", 123, "0xCONTRACT/123"),
        }

        recorder = _RecordingNotifier()
        with mock.patch(
            # Returns None → legacy mint, no card_display_data block.
            "scripts.polystars_card_payload.build_recovery_payload_from_ipfs",
            return_value=None,
        ), mock.patch(
            "scripts.telegram_notifier.notify_claim_minted",
            side_effect=recorder,
        ):
            result = hooks_scheduler._recover_stale_processing()

        assert result["auto_completed"] >= 1

        row = _read_claim_full(cid)
        # Flip itself succeeded.
        assert row["status"] == "COMPLETED"
        assert row["asset_address"] == "0xCONTRACT/123"
        # token_id still written — that step runs BEFORE the payload-derived
        # branches, so a None payload doesn't block it.
        assert row["token_id"] == 123
        # Card fields stay NULL — no payload to denormalize.
        assert row["card_slug"] is None
        assert row["card_title"] is None
        assert row["front_image_url"] is None
        # Telegram NOT fired — we never invent a payload from scratch.
        assert len(recorder.calls) == 0


# ---------------------------------------------------------------------------
# 5. Hook failures MUST NOT undo the COMPLETED flip
# ---------------------------------------------------------------------------


class TestPostHooksFailureIsolation:
    """The COMPLETED flip is the durable artifact — once the on-chain mint
    succeeded and the DB row reflects it, downstream side-effect failures
    are operator-visible (logs / late-replay) but never destructive. A
    crashing denormalize or TG dispatcher must leave the row COMPLETED."""

    def test_denormalize_failure_does_not_revert_status(
        self, hooks_scheduler, hooks_season,
    ):
        cid = _insert_processing_with_metadata(
            season_id=hooks_season,
            wallet=_wallet(1001),
            tx_hash="0xhooks_denorm_boom",
            metadata_uri="ipfs://bafkreirecoveryboom",
            age_minutes=60,
            cmn=33,
        )
        hooks_scheduler._fake_verifier.mapping = {
            "0xhooks_denorm_boom": ("success", 333, "0xCONTRACT/333"),
        }

        payload = _make_card_display_data(slug="boom-slug", cmn=33)
        with mock.patch(
            "scripts.polystars_card_payload.build_recovery_payload_from_ipfs",
            return_value=payload,
        ), mock.patch(
            "scripts.polystars_card_payload.denormalize_card_onto_claim",
            side_effect=RuntimeError("denormalize blew up"),
        ), mock.patch(
            "scripts.telegram_notifier.notify_claim_minted",
        ):
            # Must not raise — recovery swallows hook errors.
            result = hooks_scheduler._recover_stale_processing()

        assert result["auto_completed"] >= 1
        row = _read_claim_full(cid)
        # COMPLETED stuck (durable artifact). Card columns may be NULL
        # because denormalize failed; that's the catch-up payload an
        # operator can replay via scripts/backfill_recovery_card_fields.py.
        assert row["status"] == "COMPLETED"
        assert row["asset_address"] == "0xCONTRACT/333"
        # token_id still backfilled — that step runs before denormalize.
        assert row["token_id"] == 333

    def test_telegram_failure_does_not_revert_status_or_skip_denormalize(
        self, hooks_scheduler, hooks_season,
    ):
        cid = _insert_processing_with_metadata(
            season_id=hooks_season,
            wallet=_wallet(1002),
            tx_hash="0xhooks_tg_boom",
            metadata_uri="ipfs://bafkreirecoverytgboom",
            age_minutes=60,
            cmn=34,
        )
        hooks_scheduler._fake_verifier.mapping = {
            "0xhooks_tg_boom": ("success", 334, "0xCONTRACT/334"),
        }

        payload = _make_card_display_data(slug="tg-boom-slug", cmn=34)
        with mock.patch(
            "scripts.polystars_card_payload.build_recovery_payload_from_ipfs",
            return_value=payload,
        ), mock.patch(
            "scripts.telegram_notifier.notify_claim_minted",
            side_effect=RuntimeError("telegram down"),
        ):
            result = hooks_scheduler._recover_stale_processing()

        assert result["auto_completed"] >= 1
        row = _read_claim_full(cid)
        # COMPLETED + denormalize succeeded (it runs before TG). TG failure
        # is logged, not propagated.
        assert row["status"] == "COMPLETED"
        assert row["token_id"] == 334
        assert row["card_slug"] == "tg-boom-slug"


# ---------------------------------------------------------------------------
# 6. Empty metadata_uri → no IPFS attempt, no TG, token_id still written
# ---------------------------------------------------------------------------


class TestPostHooksMissingMetadataUri:
    """Pre-RBF rows could land in PROCESSING with ``tx_hash`` set but
    ``metadata_uri = NULL`` (the pre-broadcast hook persists both, but
    legacy backfilled attempts may be missing the URI). The hook block
    must short-circuit cleanly — no gateway fetch, no TG dispatch, only
    the token_id backfill runs."""

    def test_null_metadata_uri_skips_payload_branches(
        self, hooks_scheduler, hooks_season,
    ):
        # Insert directly (no metadata_uri argument → stays NULL).
        cid = _insert_claim(
            season_id=hooks_season,
            wallet=_wallet(1101),
            status="PROCESSING",
            tx_hash="0xnometa",
            cmn=55,
            age_minutes=60,
        )
        hooks_scheduler._fake_verifier.mapping = {
            "0xnometa": ("success", 555, "0xCONTRACT/555"),
        }

        ipfs_mock = mock.MagicMock()
        recorder = _RecordingNotifier()
        with mock.patch(
            "scripts.polystars_card_payload.build_recovery_payload_from_ipfs",
            side_effect=ipfs_mock,
        ), mock.patch(
            "scripts.telegram_notifier.notify_claim_minted",
            side_effect=recorder,
        ):
            result = hooks_scheduler._recover_stale_processing()

        assert result["auto_completed"] >= 1
        row = _read_claim_full(cid)
        assert row["status"] == "COMPLETED"
        assert row["token_id"] == 555
        # IPFS fetch never attempted — saves a gateway call per stale row
        # when the URI is missing.
        ipfs_mock.assert_not_called()
        # No card fields, no TG.
        assert row["card_slug"] is None
        assert len(recorder.calls) == 0
