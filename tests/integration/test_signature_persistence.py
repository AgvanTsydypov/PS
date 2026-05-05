"""
Integration test: structural signature round-trips end-to-end.

This guards the contract between ``compute_structural_signature`` and the
SQL plumbing around ``claims.signature``. Unit tests already cover every
encoding branch of the function itself; this single integration test
exercises what unit tests cannot:

  1. The 25-placeholder INSERT in ``_insert_queued_claim`` binds the
     signature parameter at the correct position. Off-by-one in the
     positional binding would only surface against real psycopg2.
  2. ``_resolve_event_card_meta`` and ``_resolve_season_meta`` actually
     return shapes that ``compute_structural_signature`` accepts when fed
     by the live cursor (RealDictCursor row → dict).
  3. The new ``c.signature`` column in ``_CARD_SOURCE_FROM_CLAIM_SQL``
     joins back through to the payload builder so the cron worker's
     render step sees the same string the queue path persisted.

We deliberately do NOT call ``_attach_generated_card_images`` (which
uploads to Pinata and rasterizes via Playwright) — image rendering has
its own isolated unit coverage and would just add 30s of network cost.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import psycopg2.extras
import pytest

from scripts.cardgen.generate_card import compute_structural_signature
from tests.integration.conftest import (
    _patch_card_payload_psycopg2,
    make_real_connection,
)

# Sentinel values chosen so the resulting signature exercises every segment
# with a non-default value — if any encoding step short-circuits on a
# trivial/default input, the assertion will catch it.
_SEASON_NUMBER     = 99001          # unique-enough sentinel
_PROXY_WALLET      = "0x" + "f1" * 20
_USER_WALLET       = "0x" + "f2" * 20
_RECIPIENT_ADDRESS = "0x" + "f3" * 20
_EVENT_ID          = "evt-sig-99001"
_EVENT_SLUG        = "polymarket-sig-test-99001"

# Snapshot field values: chosen so each signature segment has a distinct
# non-default character. ARCH=ICA, P(E)=6, EYG=X5B, INST=D, CLAIM=Ø, S0,
# POL{event_id}.
_SNAPSHOT = {
    "archetype":     "ICARUS",
    "entry_bracket": "[0.60 - 0.80]",
    "edge":          "P99",
    "yield":         "P50",
    "gravity":       "BASE",
}
_RECURRENCE  = "daily"          # → INST = D (not the U default)
_SEASON_TYPE = "genesis"        # → S0 (permanent reservation)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Fixture: full live setup needed for run_queue_mint_request to succeed
# ---------------------------------------------------------------------------

@pytest.fixture()
def signature_test_setup():
    """Provision events / event_cards / seasons / participants partition + row.

    Tear down in reverse: drop partition (clears participants), then DELETE
    the rows we created. Keep cleanup tolerant of mid-test failures.
    """
    conn = make_real_connection()
    season_id: int | None = None
    try:
        with conn.cursor() as cur:
            # 1. Event row — required FK for event_cards.event_id.
            cur.execute(
                """
                INSERT INTO events (id, slug, title)
                VALUES (%s, %s, %s)
                ON CONFLICT (id) DO NOTHING
                """,
                (_EVENT_ID, _EVENT_SLUG, "Signature Round-Trip Sentinel Event"),
            )
            # 2. event_cards row — supplies recurrence ('daily') and the
            #    primary_tag the cap trigger expects, plus manual_image_url
            #    so the payload builder doesn't reject the row.
            cur.execute(
                """
                INSERT INTO event_cards
                    (event_id, reccurence, primary_tag, manual_image_url, card_title)
                VALUES (%s, %s, 'TEST-TAG', 'https://example.invalid/img.png', 'Sig Card')
                ON CONFLICT (event_id) DO UPDATE
                    SET reccurence       = EXCLUDED.reccurence,
                        primary_tag      = EXCLUDED.primary_tag,
                        manual_image_url = EXCLUDED.manual_image_url
                """,
                (_EVENT_ID, _RECURRENCE),
            )
            # 3. Genesis season starting 1 day ago (active in Breach phase).
            start = _now_utc() - timedelta(days=1)
            end   = start + timedelta(days=30)
            cur.execute(
                """
                INSERT INTO seasons
                    (type, season_number, start_date, end_date,
                     total_supply, remaining_supply, is_active)
                VALUES (%s, %s, %s, %s, 10, 10, TRUE)
                RETURNING id
                """,
                (_SEASON_TYPE, _SEASON_NUMBER, start, end),
            )
            season_id = cur.fetchone()[0]
            # 4. Partition + participant row carrying the full snapshot.
            #    The Origin path keys on (season_id, LOWER(proxy_wallet)),
            #    so the user_wallet of the mint request must match
            #    proxy_wallet to land on _allocate_for_origin.
            cur.execute("SELECT participants_ensure_partition(%s)", (season_id,))
            cur.execute(
                """
                INSERT INTO participants
                    (season_id, proxy_wallet, event_id, event_slug,
                     entry_bracket, edge, yield, gravity, archetype,
                     archetype_description, archetype_math, rarity_bracket,
                     entry_cwap, total_volume, total_pnl, roi_percentage, rank)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s,
                        'desc', 'math', 'BEHAVIORAL FREQUENCY: ~ 2.0%%',
                        100.0, 1000.0, 50.0, 5.0, 1)
                """,
                (
                    season_id, _PROXY_WALLET, _EVENT_ID, _EVENT_SLUG,
                    _SNAPSHOT["entry_bracket"], _SNAPSHOT["edge"],
                    _SNAPSHOT["yield"], _SNAPSHOT["gravity"], _SNAPSHOT["archetype"],
                ),
            )
        conn.commit()
        yield {"season_id": season_id}
    finally:
        with conn.cursor() as cur:
            if season_id is not None:
                # Partition drop also clears the partition's participants rows.
                cur.execute("SELECT participants_drop_partition(%s)", (season_id,))
                cur.execute("DELETE FROM claims WHERE season_id = %s", (season_id,))
                cur.execute("DELETE FROM seasons WHERE id = %s", (season_id,))
            cur.execute("DELETE FROM event_cards WHERE event_id = %s", (_EVENT_ID,))
            cur.execute("DELETE FROM events       WHERE id       = %s", (_EVENT_ID,))
        conn.commit()
        conn.close()


# ---------------------------------------------------------------------------
# Helpers: read claims back; expected signature derived from the same input
# the snapshot was inserted with
# ---------------------------------------------------------------------------

def _read_claim(claim_id: int) -> dict:
    conn = make_real_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id, status, signature, claim_type, event_id "
                "FROM claims WHERE id = %s",
                (claim_id,),
            )
            row = cur.fetchone()
            return dict(row) if row else {}
    finally:
        conn.close()


def _expected_signature(*, claim_type: str) -> str:
    """The signature the persistence path *should* produce for our setup."""
    return compute_structural_signature({
        **_SNAPSHOT,
        "claim_type":    claim_type,
        "event_id":      _EVENT_ID,
        "recurrence":    _RECURRENCE,
        "season_type":   _SEASON_TYPE,
        "season_number": _SEASON_NUMBER,
    })


# ---------------------------------------------------------------------------
# The test
# ---------------------------------------------------------------------------

class TestSignaturePersistence:
    def test_origin_queue_insert_persists_signature_consistent_with_payload(
        self, workbench, signature_test_setup
    ):
        from admin_backend.claims_mint import MintClaimRequest
        from scripts.polystars_card_payload import (
            _build_card_payload_from_source_row,
            _load_card_source_row_from_claim,
        )

        season_id = signature_test_setup["season_id"]

        # ── Phase 1: queue insert via the production code path ──────────────
        # auto_phase=False bypasses SeasonManager (which we did not wire into
        # this fixture). The user_wallet matches the participant's proxy_wallet
        # so _allocate_for_origin returns the row instead of falling through
        # to the looter pool.
        result = workbench.run_queue_mint_request(MintClaimRequest(
            wallet            = _PROXY_WALLET,
            recipient_address = _RECIPIENT_ADDRESS,
            season_id         = season_id,
            phase             = "breach",
            auto_phase        = False,
        ))
        claim_id = int(result["claim_id"])

        expected = _expected_signature(claim_type="origin")
        # Sanity: the expected string actually exercises every segment.
        assert expected == "ICA-6-X5B-D-Ø-S0-POLevt-sig-99001"

        # ── Phase 2: signature actually landed in the row ───────────────────
        row = _read_claim(claim_id)
        assert row, "queued claim row not found"
        assert row["status"] == "QUEUED"
        assert row["claim_type"] == "origin"
        assert row["event_id"] == _EVENT_ID
        # The actual contract: persisted == compute(same input).
        # If positional binding in _insert_queued_claim drifts, this fails
        # because some other column's value lands in claims.signature.
        assert row["signature"] == expected, (
            f"persisted signature {row['signature']!r} "
            f"does not match expected {expected!r}"
        )

        # ── Phase 3: payload-builder JOIN reads c.signature back through ────
        # This catches the second bug class: the new column added to
        # _CARD_SOURCE_FROM_CLAIM_SQL must actually surface in the dict that
        # the cron worker's renderer consumes. We stop short of rasterizing
        # (no Pinata, no Playwright) — _build_card_payload_from_source_row is
        # the last pure step before image generation.
        with _patch_card_payload_psycopg2():
            source_row = _load_card_source_row_from_claim(workbench.manager, claim_id)
        assert source_row is not None
        assert source_row.get("signature") == expected, (
            "c.signature did not round-trip through _CARD_SOURCE_FROM_CLAIM_SQL"
        )

        payload = _build_card_payload_from_source_row(
            source_row,
            claim_id=claim_id,
            claim_type="origin",
            collection_mint_number=None,
            preview_slug=None,
        )
        assert payload["signature"] == expected, (
            "_build_card_payload_from_source_row dropped the signature; "
            "the back-of-card renderer would re-compute and risk drift."
        )
