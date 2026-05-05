"""
Integration tests for the admin showcase simulator + slug-continuity glue.

Covers what unit tests can't:

  1. Eligibility query (``_count_eligible_per_archetype``, ``_fetch_candidates``)
     against real ``participants`` + ``event_cards`` + ``preview_cards`` joins,
     including the ``NOT EXISTS`` dedup predicate.
  2. End-to-end ``run_admin_simulated_card_generations`` writes correctly
     shaped rows into ``preview_cards``, the ``BEFORE INSERT`` trigger
     populates ``collection_mint_number``, and a second run skips slots
     already populated.
  3. ``ux_preview_cards_logical_slot`` UNIQUE constraint blocks duplicate
     slot inserts at the DB layer (defense-in-depth against the
     ``NOT EXISTS`` race window).
  4. Slug continuity: ``_lookup_preview_slug_for_slot`` in
     ``polystars_card_payload`` finds the matching preview by
     ``(season_id, event_slug, LOWER(proxy_wallet))`` and that slug flows
     into ``_build_card_payload_from_source_row(preview_slug=…)`` so a real
     mint inherits the public link.

Render/upload are stubbed (``render_card_pngs`` and
``upload_card_assets_to_r2`` swapped for fakes) — Playwright and S3
exercise their own coverage and would add ~30 s + cloud creds per run.
"""

from __future__ import annotations

import unittest.mock
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Tuple

import psycopg2.extras
import pytest

from tests.integration.conftest import (
    _DirectDBManager,
    _patch_card_payload_psycopg2,
    make_real_connection,
)


# ── Sentinel ids: chosen far from any other integration test's namespace ────
_SEASON_NUMBER = 99201
_EVENT_PREFIX = "evt-sim-99201"
_SLUG_PREFIX = "polymarket-sim-99201"

# Keep the participant pool small but archetype-diverse so the diversity
# allocator has something to redistribute. 5 archetypes × 4 rows = 20 rows.
_ARCHETYPES_TO_SEED = ["ICARUS", "BURNER", "BOT", "EXTRACTOR", "PASSENGER"]
_ROWS_PER_ARCHETYPE = 4


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _hex_wallet(seed: int) -> str:
    """40-hex EVM address derived from ``seed``. Stable across runs so we
    can match preview rows back to their participant by wallet."""
    return "0x" + format(seed, "x").rjust(40, "0")


# ── Fixture ────────────────────────────────────────────────────────────────

@pytest.fixture()
def simulator_setup():
    """Provision: 1 season + N events (one per archetype) + N×4 participants.

    Layout:
      - One Genesis season with start_date 1 day ago (active for phase logic
        even though the simulator doesn't gate on that).
      - One ``events`` + ``event_cards`` row per archetype, with
        ``manual_image_url`` populated so the row is eligible.
      - Four participants per archetype, each with a distinct proxy_wallet.

    Yields a dict with ``season_id``, ``event_ids`` (list), ``proxy_wallets``
    (list of all 20 wallets), and ``proxy_by_archetype``.
    """
    conn = make_real_connection()
    season_id: int | None = None
    event_ids: List[str] = []
    proxy_wallets: List[str] = []
    proxy_by_archetype: Dict[str, List[str]] = {}
    try:
        with conn.cursor() as cur:
            # ── Events + event_cards (one per archetype) ─────────────────
            for arch_idx, archetype in enumerate(_ARCHETYPES_TO_SEED):
                event_id = f"{_EVENT_PREFIX}-{archetype.lower()}"
                event_slug = f"{_SLUG_PREFIX}-{archetype.lower()}"
                event_ids.append(event_id)
                cur.execute(
                    """
                    INSERT INTO events (id, slug, title)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (id) DO NOTHING
                    """,
                    (event_id, event_slug, f"Sim Test Event {archetype}"),
                )
                cur.execute(
                    """
                    INSERT INTO event_cards
                        (event_id, primary_tag, secondary_tag,
                         manual_image_url, card_title, card_lore, reccurence)
                    VALUES (%s, 'SIM-TAG', 'SIM-SECONDARY',
                            'https://example.invalid/img.png',
                            %s, 'Lore', 'one-shot')
                    ON CONFLICT (event_id) DO UPDATE
                        SET manual_image_url = EXCLUDED.manual_image_url,
                            card_title       = EXCLUDED.card_title,
                            primary_tag      = EXCLUDED.primary_tag
                    """,
                    (event_id, f"Sim Card {archetype}"),
                )

            # ── Season (Genesis, active) ─────────────────────────────────
            start = _now_utc() - timedelta(days=1)
            end = start + timedelta(days=30)
            cur.execute(
                """
                INSERT INTO seasons
                    (type, season_number, start_date, end_date,
                     total_supply, remaining_supply, is_active)
                VALUES ('genesis', %s, %s, %s, 100, 100, TRUE)
                RETURNING id
                """,
                (_SEASON_NUMBER, start, end),
            )
            season_id = cur.fetchone()[0]

            # ── Participants partition + rows ────────────────────────────
            cur.execute("SELECT participants_ensure_partition(%s)", (season_id,))
            seed_base = 100000 * _SEASON_NUMBER  # collision-proof against other tests
            for arch_idx, archetype in enumerate(_ARCHETYPES_TO_SEED):
                event_id = event_ids[arch_idx]
                event_slug = f"{_SLUG_PREFIX}-{archetype.lower()}"
                wallets_for_arch: List[str] = []
                for row_idx in range(_ROWS_PER_ARCHETYPE):
                    wallet = _hex_wallet(seed_base + arch_idx * 100 + row_idx)
                    wallets_for_arch.append(wallet)
                    proxy_wallets.append(wallet)
                    cur.execute(
                        """
                        INSERT INTO participants
                            (season_id, proxy_wallet, event_id, event_slug,
                             entry_bracket, edge, yield, gravity, archetype,
                             archetype_description, archetype_math, rarity_bracket,
                             entry_cwap, total_volume, total_pnl, roi_percentage, rank)
                        VALUES (%s, %s, %s, %s,
                                '[0.60 - 0.80]', 'P99', 'P50', 'BASE', %s,
                                'desc', 'math', 'BEHAVIORAL FREQUENCY: ~ 2.0%%',
                                100.0, 1000.0, 50.0, 5.0, %s)
                        """,
                        (season_id, wallet, event_id, event_slug, archetype, row_idx + 1),
                    )
                proxy_by_archetype[archetype] = wallets_for_arch
        conn.commit()
        yield {
            "season_id": season_id,
            "event_ids": event_ids,
            "proxy_wallets": proxy_wallets,
            "proxy_by_archetype": proxy_by_archetype,
        }
    finally:
        with conn.cursor() as cur:
            if season_id is not None:
                # preview_cards has FK ON DELETE CASCADE on season_id, so
                # dropping the season also clears any rows we created.
                cur.execute("SELECT participants_drop_partition(%s)", (season_id,))
                cur.execute("DELETE FROM claims WHERE season_id = %s", (season_id,))
                cur.execute("DELETE FROM seasons WHERE id = %s", (season_id,))
            for ev_id in event_ids:
                cur.execute("DELETE FROM event_cards WHERE event_id = %s", (ev_id,))
                cur.execute("DELETE FROM events       WHERE id       = %s", (ev_id,))
        conn.commit()
        conn.close()


# ── Stubbed render/upload pipeline ─────────────────────────────────────────

class _StubAssets:
    """Stand-ins for ``render_card_pngs`` and ``upload_card_assets_to_r2``
    that don't touch Chromium or S3 but preserve the contract: the upload
    fn returns ``(front_url, back_url, front_key, back_key)``.

    Tracking lists let tests assert how many times the pipeline ran.
    """

    def __init__(self) -> None:
        self.render_calls: List[Dict[str, Any]] = []
        self.upload_calls: List[Tuple[str, bytes, bytes]] = []

    def render(self, render_payload: Dict[str, Any]) -> Tuple[bytes, bytes]:
        self.render_calls.append(render_payload)
        return b"front-png-stub", b"back-png-stub"

    def upload(
        self, slug: str, front_png: bytes, back_png: bytes
    ) -> Tuple[str, str, str, str]:
        self.upload_calls.append((slug, front_png, back_png))
        return (
            f"https://stub/{slug}/front.png",
            f"https://stub/{slug}/back.png",
            f"cards-images/{slug}/front.png",
            f"cards-images/{slug}/back.png",
        )


@pytest.fixture()
def stub_pipeline():
    """Patch the simulator's render+upload functions in-place.

    We patch the *names imported into* the simulator module (not the
    originals in ``scripts.cardgen.assets``) because Python binds names at
    import time. Patching the source module would have no effect on the
    simulator's already-resolved references.

    Also patches ``_remote_image_to_data_uri`` in the payload-builder module
    so ``_build_render_payload`` doesn't try to fetch the placeholder
    ``manual_image_url`` from the public internet during card-payload assembly.
    """
    import scripts.simulate_user_generated_cards_batch as sim_mod
    import scripts.polystars_card_payload as cp_mod

    stubs = _StubAssets()
    with unittest.mock.patch.object(sim_mod, "render_card_pngs", stubs.render):
        with unittest.mock.patch.object(sim_mod, "upload_card_assets_to_r2", stubs.upload):
            with unittest.mock.patch.object(
                cp_mod, "_remote_image_to_data_uri", lambda url, **kw: url
            ):
                yield stubs


# ── DB helpers ─────────────────────────────────────────────────────────────

def _count_previews(season_id: int) -> int:
    conn = make_real_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM preview_cards WHERE season_id = %s",
                (season_id,),
            )
            return int(cur.fetchone()[0])
    finally:
        conn.close()


def _fetch_preview_archetypes(season_id: int) -> Dict[str, int]:
    """Read archetype distribution back from the persisted ``card_payload_json``."""
    conn = make_real_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT card_payload_json -> 'archetype' AS arch
                FROM preview_cards
                WHERE season_id = %s
                """,
                (season_id,),
            )
            counts: Dict[str, int] = {}
            for row in cur.fetchall():
                arch = str(row["arch"] or "").strip('"')  # JSONB string still has quotes
                counts[arch] = counts.get(arch, 0) + 1
            return counts
    finally:
        conn.close()


def _insert_preview_directly(
    *,
    slug: str,
    season_id: int,
    event_id: str,
    event_slug: str,
    proxy_wallet: str,
    owner_wallet: str | None = None,
    archetype: str | None = None,
) -> int:
    """Direct-INSERT a preview row, bypassing the simulator. Used to set up
    'pre-existing' preview state for dedup/slug-continuity tests.

    ``archetype`` is stamped into ``card_payload_json`` so tests that count
    archetype distribution in the JSONB see the same shape the simulator
    would have produced.
    """
    import json as _json

    if owner_wallet is None:
        owner_wallet = "0x" + "ab" * 20
    payload_json = _json.dumps({"archetype": archetype} if archetype else {})
    conn = make_real_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO preview_cards
                    (slug, owner_wallet, owner_proxy_wallet, season_id,
                     event_id, event_slug, card_title, primary_tag, secondary_tag,
                     front_image_path, back_image_path, card_payload_json)
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'SIM-TAG', 'SIM-SECONDARY',
                        '', '', %s::jsonb)
                RETURNING id
                """,
                (slug, owner_wallet, proxy_wallet, season_id, event_id,
                 event_slug, f"Pre-existing preview {slug}", payload_json),
            )
            preview_id = int(cur.fetchone()[0])
        conn.commit()
        return preview_id
    finally:
        conn.close()


# ── Tests ──────────────────────────────────────────────────────────────────

class TestSimulatorSelection:
    """Exercise the SQL-side query layer against real DB tables."""

    def test_count_eligible_per_archetype_returns_seeded_archetypes(
        self, simulator_setup
    ):
        """Every seeded archetype shows up with its full row count, and no
        unrelated archetype leaks in."""
        from scripts.simulate_user_generated_cards_batch import (
            _all_season_ids,
            _count_eligible_per_archetype,
        )

        conn = make_real_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                season_ids = _all_season_ids(cur)
                total, counts = _count_eligible_per_archetype(
                    cur, [simulator_setup["season_id"]]
                )
        finally:
            conn.close()

        for arch in _ARCHETYPES_TO_SEED:
            assert counts.get(arch) == _ROWS_PER_ARCHETYPE, (
                f"archetype {arch}: expected {_ROWS_PER_ARCHETYPE} eligible "
                f"rows, got {counts.get(arch)}"
            )
        # Total = 5 archetypes × 4 rows = 20 (assuming nothing else seeded)
        seeded_total = sum(counts.get(a, 0) for a in _ARCHETYPES_TO_SEED)
        assert seeded_total == len(_ARCHETYPES_TO_SEED) * _ROWS_PER_ARCHETYPE
        assert total >= seeded_total
        assert simulator_setup["season_id"] in season_ids

    def test_count_excludes_participants_with_existing_preview(
        self, simulator_setup
    ):
        """``NOT EXISTS preview_cards`` must drop already-previewed slots
        from the eligibility count, otherwise re-runs would double up."""
        from scripts.simulate_user_generated_cards_batch import (
            _count_eligible_per_archetype,
        )

        season_id = simulator_setup["season_id"]
        # Pre-fill one slot with an ICARUS preview so the eligible count drops
        # by exactly one for that archetype.
        icarus_wallet = simulator_setup["proxy_by_archetype"]["ICARUS"][0]
        icarus_event = simulator_setup["event_ids"][0]  # ICARUS is index 0
        icarus_slug = f"{_SLUG_PREFIX}-icarus"
        _insert_preview_directly(
            slug="pre-existing-icarus-1",
            season_id=season_id,
            event_id=icarus_event,
            event_slug=icarus_slug,
            proxy_wallet=icarus_wallet,
        )

        conn = make_real_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                _, counts = _count_eligible_per_archetype(cur, [season_id])
        finally:
            conn.close()

        assert counts["ICARUS"] == _ROWS_PER_ARCHETYPE - 1
        # Other archetypes untouched.
        for arch in ("BURNER", "BOT", "EXTRACTOR", "PASSENGER"):
            assert counts[arch] == _ROWS_PER_ARCHETYPE


class TestSimulatorEndToEnd:
    """``run_admin_simulated_card_generations`` against real DB +
    stubbed render/upload."""

    def test_creates_preview_rows_and_assigns_collection_mint_numbers(
        self, simulator_setup, stub_pipeline
    ):
        from scripts.simulate_user_generated_cards_batch import (
            run_admin_simulated_card_generations,
        )

        season_id = simulator_setup["season_id"]
        n_target = 10
        # Confine the DB lookup to our seeded season — _all_season_ids would
        # otherwise sweep up Genesis/Standard rows other tests left behind.
        manager = _DirectDBManager()

        with unittest.mock.patch(
            "scripts.simulate_user_generated_cards_batch._all_season_ids",
            lambda cur: [season_id],
        ):
            result = run_admin_simulated_card_generations(
                manager,
                request_id="test-req-1",
                max_count=n_target,
                origin_match_fraction=0.5,
                maximum_diversity=True,
            )

        assert result["request_id"] == "test-req-1"
        assert result["requested"] == n_target
        assert result["planned"] == n_target
        assert result["generated"] == n_target
        assert result["errors"] == []
        assert result["showcase_eligible_total"] == (
            len(_ARCHETYPES_TO_SEED) * _ROWS_PER_ARCHETYPE
        )

        # Render+upload pipeline ran exactly ``generated`` times.
        assert len(stub_pipeline.render_calls) == n_target
        assert len(stub_pipeline.upload_calls) == n_target

        # DB now has ``n_target`` previews, all with non-null cmn (assigned
        # by the BEFORE INSERT trigger) and pointing at our stub URLs.
        conn = make_real_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT collection_mint_number, front_image_path, back_image_path,
                           card_payload_json -> 'archetype' AS arch
                    FROM preview_cards
                    WHERE season_id = %s
                    ORDER BY collection_mint_number
                    """,
                    (season_id,),
                )
                rows = [dict(r) for r in cur.fetchall()]
        finally:
            conn.close()
        assert len(rows) == n_target
        # cmn is per-season serial starting at 1.
        assert [r["collection_mint_number"] for r in rows] == list(range(1, n_target + 1))
        for row in rows:
            assert row["front_image_path"].startswith("https://stub/"), row
            assert row["back_image_path"].startswith("https://stub/"), row
            assert row["arch"] is not None

    def test_diversity_balances_archetypes_with_short_pool_redistribution(
        self, simulator_setup, stub_pipeline
    ):
        """Ask for more than fits if shared evenly: short archetype caps,
        leftover slots redistribute. Total should match what is available."""
        from scripts.simulate_user_generated_cards_batch import (
            run_admin_simulated_card_generations,
        )

        season_id = simulator_setup["season_id"]
        # Pre-fill 3 of 4 ICARUS slots so only 1 ICARUS row is eligible.
        # Diversity wants 4 per archetype (n=20 → 4 each); ICARUS gives 1,
        # leftover 3 spills round-robin onto archetypes with surplus.
        icarus_wallets = simulator_setup["proxy_by_archetype"]["ICARUS"]
        icarus_event = simulator_setup["event_ids"][0]
        icarus_slug = f"{_SLUG_PREFIX}-icarus"
        for i, wallet in enumerate(icarus_wallets[:3]):
            _insert_preview_directly(
                slug=f"pre-icarus-{i}",
                season_id=season_id,
                event_id=icarus_event,
                event_slug=icarus_slug,
                proxy_wallet=wallet,
                archetype="ICARUS",
            )
        # Eligible pool now: ICARUS=1, others=4. n_target=20 → can only get
        # 1+4+4+4+4 = 17. Simulator caps at 17, marks completed_short.
        manager = _DirectDBManager()

        with unittest.mock.patch(
            "scripts.simulate_user_generated_cards_batch._all_season_ids",
            lambda cur: [season_id],
        ):
            result = run_admin_simulated_card_generations(
                manager,
                request_id="test-req-redist",
                max_count=20,
                origin_match_fraction=0.0,
                maximum_diversity=True,
            )

        # 3 pre-existing + 17 newly generated = 20 total preview rows.
        assert _count_previews(season_id) == 20, _count_previews(season_id)
        assert result["generated"] == 17
        assert result["showcase_eligible_total"] == 17

        # All 4 BURNER/BOT/EXTRACTOR/PASSENGER drained; ICARUS got its 1.
        arch_counts = _fetch_preview_archetypes(season_id)
        assert arch_counts.get("ICARUS") == 4   # 3 pre + 1 new
        assert arch_counts.get("BURNER") == 4
        assert arch_counts.get("BOT") == 4
        assert arch_counts.get("EXTRACTOR") == 4
        assert arch_counts.get("PASSENGER") == 4

    def test_second_run_skips_slots_already_covered(
        self, simulator_setup, stub_pipeline
    ):
        """Idempotency: a back-to-back invocation only touches new slots."""
        from scripts.simulate_user_generated_cards_batch import (
            run_admin_simulated_card_generations,
        )

        season_id = simulator_setup["season_id"]
        manager = _DirectDBManager()

        with unittest.mock.patch(
            "scripts.simulate_user_generated_cards_batch._all_season_ids",
            lambda cur: [season_id],
        ):
            first = run_admin_simulated_card_generations(
                manager,
                request_id="run-1",
                max_count=5,
                origin_match_fraction=0.0,
                maximum_diversity=False,
            )
            second = run_admin_simulated_card_generations(
                manager,
                request_id="run-2",
                max_count=5,
                origin_match_fraction=0.0,
                maximum_diversity=False,
            )

        assert first["generated"] == 5
        assert second["generated"] == 5
        # 5 + 5 = 10 distinct slots; if dedup leaked we'd have collision
        # errors and ``generated`` would be lower than 5 on the second run.
        assert _count_previews(season_id) == 10
        assert second["errors"] == []


class TestUniqueSlotIndex:
    """``ux_preview_cards_logical_slot`` is the safety net under the
    simulator's NOT EXISTS predicate. Direct INSERTs that bypass the
    predicate must still be rejected."""

    def test_duplicate_logical_slot_rejected(self, simulator_setup):
        season_id = simulator_setup["season_id"]
        wallet = simulator_setup["proxy_by_archetype"]["ICARUS"][0]
        event_id = simulator_setup["event_ids"][0]
        event_slug = f"{_SLUG_PREFIX}-icarus"

        _insert_preview_directly(
            slug="dupe-slot-1",
            season_id=season_id,
            event_id=event_id,
            event_slug=event_slug,
            proxy_wallet=wallet,
        )
        # Same (season_id, event_slug, LOWER(proxy)) — second INSERT must
        # raise UniqueViolation, even with a different ``slug``.
        import psycopg2.errors

        with pytest.raises(psycopg2.errors.UniqueViolation):
            _insert_preview_directly(
                slug="dupe-slot-2",
                season_id=season_id,
                event_id=event_id,
                event_slug=event_slug,
                proxy_wallet=wallet.upper(),  # case-flip to confirm LOWER() index
            )


class TestSlugContinuity:
    """``build_polystars_card_from_claim`` must reuse a matching preview's
    slug so ``/cards/{slug}`` survives the preview→minted transition."""

    def test_lookup_returns_preview_slug_for_matching_slot(
        self, simulator_setup
    ):
        from scripts.polystars_card_payload import _lookup_preview_slug_for_slot

        season_id = simulator_setup["season_id"]
        wallet = simulator_setup["proxy_by_archetype"]["BOT"][0]
        event_id = simulator_setup["event_ids"][2]  # BOT is index 2
        event_slug = f"{_SLUG_PREFIX}-bot"

        _insert_preview_directly(
            slug="bot-preview-slug-xyz",
            season_id=season_id,
            event_id=event_id,
            event_slug=event_slug,
            proxy_wallet=wallet,
        )

        manager = _DirectDBManager()
        with _patch_card_payload_psycopg2():
            found = _lookup_preview_slug_for_slot(
                manager,
                season_id=season_id,
                event_slug=event_slug,
                # Case-flip to confirm ``LOWER()`` matching on both sides.
                proxy_wallet=wallet.upper(),
            )
        assert found == "bot-preview-slug-xyz"

    def test_lookup_returns_none_for_unmatched_slot(self, simulator_setup):
        from scripts.polystars_card_payload import _lookup_preview_slug_for_slot

        manager = _DirectDBManager()
        with _patch_card_payload_psycopg2():
            assert (
                _lookup_preview_slug_for_slot(
                    manager,
                    season_id=simulator_setup["season_id"],
                    event_slug="never-existed",
                    proxy_wallet=_hex_wallet(99999),
                )
                is None
            )

    def test_lookup_safely_returns_none_on_missing_inputs(self):
        from scripts.polystars_card_payload import _lookup_preview_slug_for_slot

        manager = _DirectDBManager()
        # No DB roundtrip needed — short-circuits before opening a connection.
        assert (
            _lookup_preview_slug_for_slot(
                manager, season_id=None, event_slug="x", proxy_wallet="0x" + "1" * 40
            )
            is None
        )
        assert (
            _lookup_preview_slug_for_slot(
                manager, season_id=1, event_slug="", proxy_wallet="0x" + "1" * 40
            )
            is None
        )
