"""
Integration tests for the Polymarket profile enrichment of ``claims``
(x_username / profile_name) against real PostgreSQL.

Two code paths, two tests:

  1. Insert path — ``run_queue_mint_request`` threads the best-effort
     ``fetch_proxy_profile_identity`` result through the 27-placeholder INSERT
     in ``_insert_queued_claim`` and lands it in the right columns. Off-by-one
     positional binding (the same bug class the signature test guards) would
     surface only against real psycopg2. The network call is monkeypatched so
     the test is deterministic and offline.

  2. Backfill path — ``scripts.backfill_claims_profile`` finds rows whose pair
     is still NULL via ``_fetch_unenriched_wallets`` and fills every matching
     row with ``_update_wallet``. We assert: multi-row fan-out per wallet, the
     "only-still-NULL" guard never clobbers an already-stamped value, and the
     wallet drops out of the un-enriched set afterwards (idempotency).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import psycopg2.extras
import pytest

import scripts.backfill_claims_profile as backfill
from tests.integration.conftest import make_real_connection

_SEASON_NUMBER     = 99002
_USER_WALLET       = "0x" + "a1" * 20  # == proxy so the Origin path picks it
_RECIPIENT_ADDRESS = "0x" + "a3" * 20
_EVENT_ID          = "evt-profile-99002"
_EVENT_SLUG        = "polymarket-profile-test-99002"

# Wallet used by the backfill test (no participant row needed; we INSERT the
# claims directly with a NULL identity, simulating pre-feature rows).
_BACKFILL_PROXY    = "0x" + "b2" * 20


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


@pytest.fixture()
def profile_test_setup():
    """Provision events / event_cards / a Breach-phase season / participants
    partition + one Origin row whose proxy_wallet == _USER_WALLET."""
    conn = make_real_connection()
    season_id = None
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO events (id, slug, title) VALUES (%s, %s, %s) "
                "ON CONFLICT (id) DO NOTHING",
                (_EVENT_ID, _EVENT_SLUG, "Profile Enrichment Sentinel Event"),
            )
            cur.execute(
                """
                INSERT INTO event_cards
                    (event_id, reccurence, primary_tag, manual_image_url, card_title)
                VALUES (%s, 'daily', 'TEST-TAG', 'https://example.invalid/img.png', 'Profile Card')
                ON CONFLICT (event_id) DO UPDATE
                    SET primary_tag = EXCLUDED.primary_tag
                """,
                (_EVENT_ID,),
            )
            start = _now_utc() - timedelta(days=1)
            end = start + timedelta(days=30)
            cur.execute(
                """
                INSERT INTO seasons
                    (type, season_number, start_date, end_date,
                     total_supply, remaining_supply, is_active)
                VALUES ('genesis', %s, %s, %s, 10, 10, TRUE)
                RETURNING id
                """,
                (_SEASON_NUMBER, start, end),
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
                VALUES (%s, %s, %s, %s, '[0.60 - 0.80]', 'P99', 'P50', 'BASE',
                        'ICARUS', 'desc', 'math', 'BEHAVIORAL FREQUENCY: ~ 2.0%%',
                        100.0, 1000.0, 50.0, 5.0, 1)
                """,
                (season_id, _USER_WALLET, _EVENT_ID, _EVENT_SLUG),
            )
        conn.commit()
        yield {"season_id": season_id}
    finally:
        with conn.cursor() as cur:
            if season_id is not None:
                cur.execute("SELECT participants_drop_partition(%s)", (season_id,))
                cur.execute("DELETE FROM claims WHERE season_id = %s", (season_id,))
                cur.execute("DELETE FROM seasons WHERE id = %s", (season_id,))
            cur.execute("DELETE FROM event_cards WHERE event_id = %s", (_EVENT_ID,))
            cur.execute("DELETE FROM events WHERE id = %s", (_EVENT_ID,))
        conn.commit()
        conn.close()


def _read_claim(claim_id: int) -> dict:
    conn = make_real_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id, status, proxy_wallet, x_username, profile_name "
                "FROM claims WHERE id = %s",
                (claim_id,),
            )
            row = cur.fetchone()
            return dict(row) if row else {}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Path 1 — insert-time stamping
# ---------------------------------------------------------------------------

class TestInsertPathStampsProfile:
    def test_queue_insert_persists_x_username_and_profile_name(
        self, workbench, profile_test_setup, monkeypatch
    ):
        import admin_backend.claims_mint as claims_mint
        from admin_backend.claims_mint import MintClaimRequest

        # Deterministic, offline identity for the allocated proxy_wallet.
        monkeypatch.setattr(
            claims_mint, "fetch_proxy_profile_identity",
            lambda proxy_wallet: ("neo_anderson", "Thomas Anderson"),
        )

        result = workbench.run_queue_mint_request(MintClaimRequest(
            wallet            = _USER_WALLET,
            recipient_address = _RECIPIENT_ADDRESS,
            season_id         = profile_test_setup["season_id"],
            phase             = "breach",
            auto_phase        = False,
        ))
        row = _read_claim(int(result["claim_id"]))
        assert row, "queued claim row not found"
        assert row["status"] == "QUEUED"
        assert row["x_username"] == "neo_anderson"
        assert row["profile_name"] == "Thomas Anderson"

    def test_failed_lookup_leaves_columns_null_without_blocking_mint(
        self, workbench, profile_test_setup, monkeypatch
    ):
        import admin_backend.claims_mint as claims_mint
        from admin_backend.claims_mint import MintClaimRequest

        # Simulate a profile miss / network failure: helper returns (None, None).
        monkeypatch.setattr(
            claims_mint, "fetch_proxy_profile_identity",
            lambda proxy_wallet: (None, None),
        )

        result = workbench.run_queue_mint_request(MintClaimRequest(
            wallet            = _USER_WALLET,
            recipient_address = _RECIPIENT_ADDRESS,
            season_id         = profile_test_setup["season_id"],
            phase             = "breach",
            auto_phase        = False,
        ))
        row = _read_claim(int(result["claim_id"]))
        assert row["status"] == "QUEUED", "mint must still succeed on a profile miss"
        assert row["x_username"] is None
        assert row["profile_name"] is None


# ---------------------------------------------------------------------------
# Path 2 — backfill of pre-existing rows
# ---------------------------------------------------------------------------

class TestBackfillPath:
    def _insert_unenriched_claim(self, cur, season_id, user_wallet):
        """A pre-feature claim row: proxy set, identity columns NULL.

        status='FAILED' is deliberate: the active-set uniqueness index
        ux_claims_active_proxy_wallet excludes FAILED, which lets two rows
        share one proxy_wallet so we can prove the per-wallet fan-out of the
        UPDATE. event_id is NULL so the per-event cap never trips either.
        FAILED rows are still valid backfill targets — the selector keys on the
        NULL identity columns, not on status.
        """
        cur.execute(
            """
            INSERT INTO claims (user_wallet, season_id, phase_type, status,
                                proxy_wallet, mint_chain)
            VALUES (%s, %s, 'breach', 'FAILED', %s, 'ethereum')
            RETURNING id
            """,
            (user_wallet, season_id, _BACKFILL_PROXY),
        )
        return int(cur.fetchone()[0])

    def test_backfill_fills_all_rows_then_is_idempotent(self, profile_test_setup):
        season_id = profile_test_setup["season_id"]
        conn = make_real_connection()
        try:
            # Two claims, same proxy_wallet, distinct user_wallet → fan-out.
            with conn.cursor() as cur:
                id_a = self._insert_unenriched_claim(cur, season_id, "0x" + "c1" * 20)
                id_b = self._insert_unenriched_claim(cur, season_id, "0x" + "c2" * 20)
            conn.commit()

            # The wallet shows up in the un-enriched set before backfill.
            wallets_before = backfill._fetch_unenriched_wallets(conn, limit=None)
            assert _BACKFILL_PROXY in wallets_before

            # Real UPDATE against PostgreSQL: both rows get the identity.
            n = backfill._update_wallet(conn, _BACKFILL_PROXY, "trinity", "Trinity")
            conn.commit()
            assert n == 2

            for cid in (id_a, id_b):
                row = _read_claim(cid)
                assert row["x_username"] == "trinity"
                assert row["profile_name"] == "Trinity"

            # It dropped out of the un-enriched set (idempotent next run).
            wallets_after = backfill._fetch_unenriched_wallets(conn, limit=None)
            assert _BACKFILL_PROXY not in wallets_after

            # Re-running updates nothing and never clobbers existing values.
            n2 = backfill._update_wallet(conn, _BACKFILL_PROXY, "MORPHEUS", "Morpheus")
            conn.commit()
            assert n2 == 0
            row = _read_claim(id_a)
            assert row["x_username"] == "trinity"
            assert row["profile_name"] == "Trinity"
        finally:
            conn.close()

    def test_partial_existing_value_is_not_treated_as_unenriched(self, profile_test_setup):
        """A row with only one of the two columns filled is NOT in the
        un-enriched set (the guard requires BOTH NULL), so backfill leaves it
        untouched rather than overwriting a partially-known identity."""
        season_id = profile_test_setup["season_id"]
        conn = make_real_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO claims (user_wallet, season_id, phase_type, status,
                                        proxy_wallet, mint_chain, x_username)
                    VALUES (%s, %s, 'breach', 'QUEUED', %s, 'ethereum', 'preset_handle')
                    RETURNING id
                    """,
                    ("0x" + "d1" * 20, season_id, "0x" + "d9" * 20),
                )
                cid = int(cur.fetchone()[0])
            conn.commit()

            wallets = backfill._fetch_unenriched_wallets(conn, limit=None)
            assert ("0x" + "d9" * 20) not in wallets

            n = backfill._update_wallet(conn, "0x" + "d9" * 20, "should_not", "Apply")
            conn.commit()
            assert n == 0
            row = _read_claim(cid)
            assert row["x_username"] == "preset_handle"
            assert row["profile_name"] is None
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Card detail SQL — the two columns reach the /api/cards/{slug} response
# ---------------------------------------------------------------------------

class TestCardDetailSqlSurfacesProfile:
    """The card detail page reads x_username / profile_name straight off the
    SQL row (``_format_generated_card_row`` does ``dict(row)``), so executing
    the SQL strings directly proves the columns reach the frontend contract.
    """

    def test_minted_sql_returns_identity_columns(self, profile_test_setup):
        from user_web_backend.main import _MINTED_CARD_DETAIL_SQL

        season_id = profile_test_setup["season_id"]
        conn = make_real_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO claims
                        (user_wallet, season_id, phase_type, status, proxy_wallet,
                         event_id, event_slug, claim_type, card_slug, card_title,
                         front_image_url, back_image_url,
                         x_username, profile_name, mint_chain)
                    VALUES (%s, %s, 'breach', 'COMPLETED', %s, %s, %s, 'looter',
                            'profile-detail-slug', 'Detail Card',
                            'https://stub/f.png', 'https://stub/b.png',
                            'neo_x', 'Neo', 'ethereum')
                    """,
                    ("0x" + "e1" * 20, season_id, "0x" + "e9" * 20, _EVENT_ID, _EVENT_SLUG),
                )
            conn.commit()
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(_MINTED_CARD_DETAIL_SQL, ("profile-detail-slug",))
                row = dict(cur.fetchone())
            assert row["x_username"] == "neo_x"
            assert row["profile_name"] == "Neo"
        finally:
            conn.close()

    def test_preview_sql_returns_null_identity(self, profile_test_setup):
        from user_web_backend.main import _PREVIEW_CARD_DETAIL_SQL

        season_id = profile_test_setup["season_id"]
        conn = make_real_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO preview_cards
                        (slug, owner_wallet, owner_proxy_wallet, season_id,
                         event_id, event_slug, card_title, primary_tag, secondary_tag,
                         front_image_path, back_image_path, card_payload_json)
                    VALUES ('profile-preview-slug', %s, %s, %s, %s, %s,
                            'Preview Card', 'TEST-TAG', 'NONE',
                            'https://stub/pf.png', 'https://stub/pb.png', '{}'::jsonb)
                    """,
                    ("0x" + "f1" * 20, "0x" + "f9" * 20, season_id, _EVENT_ID, _EVENT_SLUG),
                )
            conn.commit()
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(_PREVIEW_CARD_DETAIL_SQL, ("profile-preview-slug",))
                row = dict(cur.fetchone())
            # Columns exist in the row shape but are NULL (no claim behind it).
            assert row["x_username"] is None
            assert row["profile_name"] is None
        finally:
            conn.close()
