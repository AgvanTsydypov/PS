"""
Integration test: preview card → minted claim transition keeps the slug.

The contract under test:

  1. Admin showcase simulator INSERTs a row into ``preview_cards`` with
     slug X for slot ``(season_id, event_slug, proxy_wallet)``.
  2. A real user later mints onto that slot (origin path: their proxy
     matches a participant). ``run_queue_mint_request`` writes a QUEUED
     ``claims`` row with the snapshot.
  3. The cron worker calls ``build_polystars_card_from_claim``, which
     now (post-migration) looks up ``preview_cards`` by the same slot
     identity and reuses the preview's slug as ``preview_slug=`` for the
     payload builder. The card payload's ``qr_payload`` therefore points
     at ``/cards/X`` — same URL as the showcase ticker had been
     advertising.
  4. After the on-chain mint succeeds, ``denormalize_card_onto_claim``
     does an atomic ``UPDATE claims SET card_slug=X, …`` plus
     ``DELETE FROM preview_cards WHERE slug=X`` in a single transaction.

Together this keeps ``/cards/X`` resolving permanently, but the
underlying table flips from preview to minted (``is_preview`` flag
flips, minted-only chips appear). Without this, the QR code burned onto
the physical card and the public link the showcase first surfaced would
diverge after mint.

Render+upload (``_attach_generated_card_images`` → Playwright + Pinata)
is stubbed: this file exercises slug plumbing and DB transitions, not
image generation. Image rasterization has its own coverage.
"""

from __future__ import annotations

import unittest.mock
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

import psycopg2.extras
import pytest

from tests.integration.conftest import (
    _patch_card_payload_psycopg2,
    make_real_connection,
)


# Sentinels far enough from other integration test namespaces
_SEASON_NUMBER = 99401
_EVENT_ID = "evt-trans-99401"
_EVENT_SLUG = "polymarket-trans-99401"
_PROXY_WALLET = "0x" + "a4" * 20            # participant's proxy + minter's wallet (origin path)
_RECIPIENT_ADDRESS = "0x" + "b4" * 20        # where the NFT is minted to
_PREVIEW_SLUG = "preview-trans-99401-fixed"  # deterministic slug we pre-INSERT

_SNAPSHOT = {
    "archetype":     "ICARUS",
    "entry_bracket": "[0.60 - 0.80]",
    "edge":          "P99",
    "yield":         "P50",
    "gravity":       "BASE",
}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


# ── Fixture: full slot setup (event_cards + season + participants + preview) ─

@pytest.fixture()
def transition_setup():
    """Provision a real eligible slot AND a pre-existing preview for it.

    Yields ``{season_id, preview_id}``. Cleanup drops the partition,
    seasons (cascades preview_cards via FK), event_cards, events.
    """
    conn = make_real_connection()
    season_id: int | None = None
    preview_id: int | None = None
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO events (id, slug, title)
                VALUES (%s, %s, %s)
                ON CONFLICT (id) DO NOTHING
                """,
                (_EVENT_ID, _EVENT_SLUG, "Transition Sentinel Event"),
            )
            cur.execute(
                """
                INSERT INTO event_cards
                    (event_id, primary_tag, secondary_tag, manual_image_url,
                     card_title, card_lore, reccurence)
                VALUES (%s, 'TRANS-TAG', 'NONE',
                        'https://example.invalid/img.png',
                        'Transition Card', 'Lore', 'one-shot')
                ON CONFLICT (event_id) DO UPDATE
                    SET manual_image_url = EXCLUDED.manual_image_url,
                        card_title       = EXCLUDED.card_title,
                        primary_tag      = EXCLUDED.primary_tag
                """,
                (_EVENT_ID,),
            )
            start = _now_utc() - timedelta(days=1)
            cur.execute(
                """
                INSERT INTO seasons
                    (type, season_number, start_date, end_date,
                     total_supply, remaining_supply, is_active)
                VALUES ('genesis', %s, %s, %s, 10, 10, TRUE)
                RETURNING id
                """,
                (_SEASON_NUMBER, start, start + timedelta(days=30)),
            )
            season_id = cur.fetchone()[0]
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

            # Pre-existing preview row — what the simulator would have
            # written. Empty image paths intentionally: the simulator
            # INSERTs placeholder paths first, but the slug is what
            # build_polystars_card_from_claim reads — image paths don't
            # matter for the slot-identity lookup.
            cur.execute(
                """
                INSERT INTO preview_cards
                    (slug, owner_wallet, owner_proxy_wallet, season_id,
                     event_id, event_slug, card_title, primary_tag, secondary_tag,
                     front_image_path, back_image_path, card_payload_json)
                VALUES (%s, %s, %s, %s, %s, %s, 'Preview-Title', 'TRANS-TAG', 'NONE',
                        'https://r2.stub/preview/front.png',
                        'https://r2.stub/preview/back.png',
                        '{}'::jsonb)
                RETURNING id
                """,
                (_PREVIEW_SLUG, "0x" + "c4" * 20, _PROXY_WALLET, season_id,
                 _EVENT_ID, _EVENT_SLUG),
            )
            preview_id = int(cur.fetchone()[0])
        conn.commit()
        yield {"season_id": season_id, "preview_id": preview_id}
    finally:
        with conn.cursor() as cur:
            if season_id is not None:
                cur.execute("SELECT participants_drop_partition(%s)", (season_id,))
                cur.execute("DELETE FROM claims WHERE season_id = %s", (season_id,))
                cur.execute("DELETE FROM seasons WHERE id = %s", (season_id,))
            cur.execute("DELETE FROM event_cards WHERE event_id = %s", (_EVENT_ID,))
            cur.execute("DELETE FROM events       WHERE id       = %s", (_EVENT_ID,))
        conn.commit()
        conn.close()


# ── Helpers ────────────────────────────────────────────────────────────────

def _read_claim(claim_id: int) -> Dict[str, Any]:
    conn = make_real_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, status, card_slug, card_title, front_image_url,
                       back_image_url, card_payload_json, primary_tag,
                       secondary_tag, pattern, season_id, event_slug, proxy_wallet
                FROM claims WHERE id = %s
                """,
                (claim_id,),
            )
            row = cur.fetchone()
            return dict(row) if row else {}
    finally:
        conn.close()


def _preview_exists(slug: str) -> bool:
    conn = make_real_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM preview_cards WHERE slug = %s", (slug,))
            return cur.fetchone() is not None
    finally:
        conn.close()


def _slug_from_qr(qr_payload: str) -> str:
    """Mirror polystars_card_payload._slug_from_qr_payload's tail-extract."""
    return str(qr_payload or "").rstrip("/").rsplit("/", 1)[-1].strip()


# ── Stub for the heavy render+upload step ──────────────────────────────────

class _StubAttachAssets:
    """Replacement for ``_attach_generated_card_images``. Bypasses
    Playwright+Pinata; just stamps placeholder URLs on the payload so
    downstream ``denormalize_card_onto_claim`` has something to write
    into ``claims.front_image_url``/``back_image_url``.
    """

    def __init__(self) -> None:
        self.calls: list[Dict[str, Any]] = []

    def __call__(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        self.calls.append(payload)
        out = dict(payload)
        slug = _slug_from_qr(out.get("qr_payload", ""))
        out["front_image_url"] = f"https://pinata.stub/{slug}/front.png"
        out["back_image_url"] = f"https://pinata.stub/{slug}/back.png"
        out["front_image_mime"] = "image/png"
        out["back_image_mime"] = "image/png"
        return out


@pytest.fixture()
def stub_attach():
    """Patch ``_attach_generated_card_images`` so build_polystars_card_from_claim
    skips Playwright+Pinata. Tests target slug plumbing + DB transitions, not
    image rasterization."""
    import scripts.polystars_card_payload as cp_mod

    stub = _StubAttachAssets()
    with unittest.mock.patch.object(cp_mod, "_attach_generated_card_images", stub):
        yield stub


# ── Tests ──────────────────────────────────────────────────────────────────

class TestSlugContinuityAtBuild:
    """Step 3 of the pipeline: build_polystars_card_from_claim must reuse
    the preview's slug as the new claim's slug. Without this, the QR code
    on the minted card and the showcase URL would diverge."""

    def test_build_polystars_card_uses_preview_slug(
        self, workbench, transition_setup, stub_attach
    ):
        from admin_backend.claims_mint import MintClaimRequest
        from scripts.polystars_card_payload import build_polystars_card_from_claim

        season_id = transition_setup["season_id"]

        # Origin path — minter's wallet equals participant's proxy_wallet,
        # so _allocate_for_origin returns this row instead of falling
        # through to the looter pool.
        result = workbench.run_queue_mint_request(MintClaimRequest(
            wallet            = _PROXY_WALLET,
            recipient_address = _RECIPIENT_ADDRESS,
            season_id         = season_id,
            phase             = "breach",
            auto_phase        = False,
        ))
        claim_id = int(result["claim_id"])

        with _patch_card_payload_psycopg2():
            payload = build_polystars_card_from_claim(workbench.manager, claim_id=claim_id)

        # Slug from qr_payload must equal the preview's slug we pre-inserted
        # — that's the entire promise of slug continuity.
        assert _slug_from_qr(payload.get("qr_payload", "")) == _PREVIEW_SLUG, (
            f"build_polystars_card_from_claim ignored the matching preview "
            f"row; payload qr_payload={payload.get('qr_payload')!r}"
        )

        # Stub attach saw exactly one call — and the front/back URLs it
        # stamped reflect the inherited slug, not a fresh one.
        assert len(stub_attach.calls) == 1
        assert payload.get("front_image_url") == f"https://pinata.stub/{_PREVIEW_SLUG}/front.png"
        assert payload.get("back_image_url")  == f"https://pinata.stub/{_PREVIEW_SLUG}/back.png"


class TestDenormalizeAtomicTransition:
    """Step 4: denormalize_card_onto_claim writes the slug onto claims AND
    deletes the matching preview row, atomically. After this step:
      - claims has card_slug, card_title, front_image_url, back_image_url
      - preview_cards no longer has the row
      - /cards/{slug} resolves to claims (is_preview=false), not preview_cards
    """

    def test_preview_row_deleted_and_claim_denormalized_in_single_commit(
        self, workbench, transition_setup, stub_attach
    ):
        from admin_backend.claims_mint import MintClaimRequest
        from scripts.polystars_card_payload import (
            build_polystars_card_from_claim,
            denormalize_card_onto_claim,
        )

        season_id = transition_setup["season_id"]

        # Sanity: preview exists before the transition.
        assert _preview_exists(_PREVIEW_SLUG), "fixture failed to seed preview"

        result = workbench.run_queue_mint_request(MintClaimRequest(
            wallet            = _PROXY_WALLET,
            recipient_address = _RECIPIENT_ADDRESS,
            season_id         = season_id,
            phase             = "breach",
            auto_phase        = False,
        ))
        claim_id = int(result["claim_id"])

        with _patch_card_payload_psycopg2():
            payload = build_polystars_card_from_claim(workbench.manager, claim_id=claim_id)
            denormalize_card_onto_claim(
                workbench.manager, claim_id=claim_id, polystars_card=payload
            )

        # ── Claims side ─────────────────────────────────────────────────
        claim_row = _read_claim(claim_id)
        assert claim_row, "claim row vanished"
        assert claim_row["card_slug"] == _PREVIEW_SLUG, (
            "claim did not inherit the preview slug — slug continuity broken"
        )
        assert claim_row["card_title"] == "Transition Card"
        assert claim_row["front_image_url"] == f"https://pinata.stub/{_PREVIEW_SLUG}/front.png"
        assert claim_row["back_image_url"]  == f"https://pinata.stub/{_PREVIEW_SLUG}/back.png"
        # primary_tag/secondary_tag denormalized from the payload
        assert claim_row["primary_tag"] == "TRANS-TAG"
        # card_payload_json is JSONB; psycopg2 returns dict.
        cpj = claim_row["card_payload_json"]
        assert isinstance(cpj, dict)
        assert cpj.get("archetype") in ("ICARUS",)  # round-tripped from snapshot
        assert _slug_from_qr(cpj.get("qr_payload", "")) == _PREVIEW_SLUG

        # ── Preview side ────────────────────────────────────────────────
        assert not _preview_exists(_PREVIEW_SLUG), (
            "preview_cards row survived denormalize_card_onto_claim — the "
            "DELETE side of the same-transaction UPDATE+DELETE didn't fire"
        )

    def test_no_preview_branch_still_writes_claim(
        self, workbench, transition_setup, stub_attach
    ):
        """Looter case: claim's slot has no matching preview. Build assigns
        a fresh slug; denormalize updates claims and the DELETE is a no-op
        (no row matches the fresh slug). Preserves robustness when the
        showcase simulator hasn't pre-warmed every slot.
        """
        from admin_backend.claims_mint import MintClaimRequest
        from scripts.polystars_card_payload import (
            build_polystars_card_from_claim,
            denormalize_card_onto_claim,
        )

        season_id = transition_setup["season_id"]

        # First, drop the seeded preview so the lookup misses.
        conn = make_real_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM preview_cards WHERE slug = %s", (_PREVIEW_SLUG,))
            conn.commit()
        finally:
            conn.close()
        assert not _preview_exists(_PREVIEW_SLUG)

        result = workbench.run_queue_mint_request(MintClaimRequest(
            wallet            = _PROXY_WALLET,
            recipient_address = _RECIPIENT_ADDRESS,
            season_id         = season_id,
            phase             = "breach",
            auto_phase        = False,
        ))
        claim_id = int(result["claim_id"])

        with _patch_card_payload_psycopg2():
            payload = build_polystars_card_from_claim(workbench.manager, claim_id=claim_id)
            denormalize_card_onto_claim(
                workbench.manager, claim_id=claim_id, polystars_card=payload
            )

        new_slug = _slug_from_qr(payload.get("qr_payload", ""))
        # Generated fresh, not equal to the (now-gone) preview's slug.
        assert new_slug != _PREVIEW_SLUG
        assert new_slug, "qr_payload had no slug"

        claim_row = _read_claim(claim_id)
        assert claim_row["card_slug"] == new_slug
        assert claim_row["front_image_url"] == f"https://pinata.stub/{new_slug}/front.png"

        # No preview row should exist for this claim's slug; we never
        # created one and the lookup correctly missed.
        assert not _preview_exists(new_slug)

    def test_denormalize_only_deletes_matching_slug_not_other_previews(
        self, workbench, transition_setup, stub_attach
    ):
        """Defense check: denormalize must not nuke unrelated preview rows.
        Seed a second preview for a different slot in the same season; after
        a transition for slot A, only slot A's preview is deleted.
        """
        from admin_backend.claims_mint import MintClaimRequest
        from scripts.polystars_card_payload import (
            build_polystars_card_from_claim,
            denormalize_card_onto_claim,
        )

        season_id = transition_setup["season_id"]
        unrelated_slug = "unrelated-preview-slot"
        unrelated_proxy = "0x" + "ee" * 20

        conn = make_real_connection()
        try:
            with conn.cursor() as cur:
                # Different proxy_wallet, different slug. We use a fake
                # event_slug here — preview_cards has no FK on event_slug,
                # so this is fine; we just need the row to exist as a
                # control sample.
                cur.execute(
                    """
                    INSERT INTO preview_cards
                        (slug, owner_wallet, owner_proxy_wallet, season_id,
                         event_id, event_slug, card_title, primary_tag,
                         secondary_tag, front_image_path, back_image_path,
                         card_payload_json)
                    VALUES (%s, %s, %s, %s, %s, 'unrelated-event-slug',
                            'Other', 'OTHER-TAG', 'NONE', '', '', '{}'::jsonb)
                    """,
                    (unrelated_slug, "0x" + "ff" * 20, unrelated_proxy,
                     season_id, _EVENT_ID),
                )
            conn.commit()
        finally:
            conn.close()

        result = workbench.run_queue_mint_request(MintClaimRequest(
            wallet            = _PROXY_WALLET,
            recipient_address = _RECIPIENT_ADDRESS,
            season_id         = season_id,
            phase             = "breach",
            auto_phase        = False,
        ))
        claim_id = int(result["claim_id"])

        with _patch_card_payload_psycopg2():
            payload = build_polystars_card_from_claim(workbench.manager, claim_id=claim_id)
            denormalize_card_onto_claim(
                workbench.manager, claim_id=claim_id, polystars_card=payload
            )

        # Slot-A's preview gone, unrelated preview still around.
        assert not _preview_exists(_PREVIEW_SLUG)
        assert _preview_exists(unrelated_slug)


class TestTickerVisibilityFlipsOnTransition:
    """Functional check from the user's POV: before transition, the
    showcase ticker SQL surfaces the preview branch with the slug; after
    transition, the same slug is surfaced via the claims branch (status
    ``COMPLETED``). The public link ``/cards/{slug}`` keeps working
    seamlessly while the underlying source flips.
    """

    def test_slug_appears_in_ticker_before_and_after_transition(
        self, workbench, transition_setup, stub_attach
    ):
        from admin_backend.claims_mint import MintClaimRequest
        from scripts.polystars_card_payload import (
            build_polystars_card_from_claim,
            denormalize_card_onto_claim,
        )
        from user_web_backend.main import _CARDS_TICKER_SAMPLE_SQL

        def ticker_slugs() -> set:
            conn = make_real_connection()
            try:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(_CARDS_TICKER_SAMPLE_SQL, (200,))
                    return {row["slug"] for row in cur.fetchall()}
            finally:
                conn.close()

        # ── Phase 1: preview exists, claims has nothing ─────────────────
        # The seeded preview already has non-empty image paths
        # (https://r2.stub/preview/...), so it qualifies for the ticker.
        before = ticker_slugs()
        assert _PREVIEW_SLUG in before, (
            "ticker UNION's preview branch did not surface the simulator-"
            "style preview row — home page wouldn't show it"
        )

        # ── Phase 2: queue the claim, build, denormalize ────────────────
        season_id = transition_setup["season_id"]
        result = workbench.run_queue_mint_request(MintClaimRequest(
            wallet            = _PROXY_WALLET,
            recipient_address = _RECIPIENT_ADDRESS,
            season_id         = season_id,
            phase             = "breach",
            auto_phase        = False,
        ))
        claim_id = int(result["claim_id"])

        # Bring the claim to COMPLETED. denormalize_card_onto_claim does
        # not flip status (the cron worker does that on a separate commit
        # in production), so we do it here to enable the claims branch
        # of the ticker UNION.
        conn = make_real_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE claims SET status = 'COMPLETED' WHERE id = %s",
                    (claim_id,),
                )
            conn.commit()
        finally:
            conn.close()

        with _patch_card_payload_psycopg2():
            payload = build_polystars_card_from_claim(workbench.manager, claim_id=claim_id)
            denormalize_card_onto_claim(
                workbench.manager, claim_id=claim_id, polystars_card=payload
            )

        # ── Phase 3: same slug, different source ────────────────────────
        after = ticker_slugs()
        assert _PREVIEW_SLUG in after, (
            "slug disappeared from the ticker after transition — link "
            "continuity broken: /cards/X used to render preview, now 404"
        )
        # Sanity: preview source is gone, so the slug now comes from the
        # claims branch only. We can verify by counting rows directly.
        conn = make_real_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM preview_cards WHERE slug = %s",
                    (_PREVIEW_SLUG,),
                )
                assert int(cur.fetchone()[0]) == 0
                cur.execute(
                    "SELECT COUNT(*) FROM claims WHERE card_slug = %s "
                    "AND status = 'COMPLETED'",
                    (_PREVIEW_SLUG,),
                )
                assert int(cur.fetchone()[0]) == 1
        finally:
            conn.close()
