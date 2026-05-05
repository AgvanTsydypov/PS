"""
Integration test: home-page ticker (``/api/cards/ticker`` SQL) surfaces
both minted ``claims`` and unminted ``preview_cards``.

The post-migration regression that motivated this test: after
``winner_wallet_nft_to_claim → claims`` migration v3, the ticker SQL was
changed to read **only** from claims. That left the admin showcase
simulator with nothing to feed the home page — preview rows existed in
the DB but never surfaced on the user web. The fix is a UNION ALL across
both sources, which this test executes against a real Postgres to prove
the SQL planner-validates and the rows actually come back.

We bypass the FastAPI route layer (no caching, no rate limit) and
execute the SQL string directly so the assertion targets exactly the
contract the route relies on.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import psycopg2.extras
import pytest

from tests.integration.conftest import make_real_connection

_SEASON_NUMBER = 99301
_EVENT_ID = "evt-ticker-99301"
_EVENT_SLUG = "polymarket-ticker-99301"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _hex_wallet(seed: int) -> str:
    return "0x" + format(seed, "x").rjust(40, "0")


@pytest.fixture()
def ticker_setup():
    """Seed: 1 season + 1 event/event_card + 2 claims (one COMPLETED, one
    QUEUED) + 2 preview rows (one rendered, one with empty image paths
    representing an in-flight simulator INSERT).

    The COMPLETED claim and the rendered preview must show up in the
    ticker; the QUEUED claim and the in-flight preview must not.
    """
    conn = make_real_connection()
    season_id: int | None = None
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO events (id, slug, title) VALUES (%s, %s, %s) "
                "ON CONFLICT (id) DO NOTHING",
                (_EVENT_ID, _EVENT_SLUG, "Ticker Test Event"),
            )
            cur.execute(
                """
                INSERT INTO event_cards
                    (event_id, primary_tag, secondary_tag, manual_image_url,
                     card_title, card_lore, reccurence)
                VALUES (%s, 'TICKER-TAG', 'NONE',
                        'https://example.invalid/img.png',
                        'Ticker Card', 'Lore', 'one-shot')
                ON CONFLICT (event_id) DO UPDATE
                    SET manual_image_url = EXCLUDED.manual_image_url
                """,
                (_EVENT_ID,),
            )
            start = _now_utc() - timedelta(days=1)
            cur.execute(
                """
                INSERT INTO seasons
                    (type, season_number, start_date, end_date,
                     total_supply, remaining_supply, is_active)
                VALUES ('genesis', %s, %s, %s, 100, 100, TRUE)
                RETURNING id
                """,
                (_SEASON_NUMBER, start, start + timedelta(days=30)),
            )
            season_id = cur.fetchone()[0]
            cur.execute("SELECT participants_ensure_partition(%s)", (season_id,))

            # Two claims: COMPLETED with images (eligible) + QUEUED (not eligible).
            cur.execute(
                """
                INSERT INTO claims
                    (user_wallet, recipient_address, season_id, phase_type,
                     status, proxy_wallet, event_id, event_slug, claim_type,
                     card_slug, card_title, front_image_url, back_image_url)
                VALUES (%s, %s, %s, 'breach', 'COMPLETED',
                        %s, %s, %s, 'looter',
                        'ticker-completed-1', 'Completed Card',
                        'https://stub/c1/front.png', 'https://stub/c1/back.png')
                """,
                (_hex_wallet(900001), _hex_wallet(900002), season_id,
                 _hex_wallet(900003), _EVENT_ID, _EVENT_SLUG),
            )
            cur.execute(
                """
                INSERT INTO claims
                    (user_wallet, recipient_address, season_id, phase_type,
                     status, proxy_wallet, event_id, event_slug, claim_type,
                     card_slug, card_title, front_image_url, back_image_url)
                VALUES (%s, %s, %s, 'breach', 'QUEUED',
                        %s, %s, %s, 'looter',
                        NULL, 'Queued Card',
                        NULL, NULL)
                """,
                (_hex_wallet(900011), _hex_wallet(900012), season_id,
                 _hex_wallet(900013), _EVENT_ID, _EVENT_SLUG),
            )

            # Two previews: one rendered (eligible) + one in-flight (not).
            cur.execute(
                """
                INSERT INTO preview_cards
                    (slug, owner_wallet, owner_proxy_wallet, season_id,
                     event_id, event_slug, card_title, primary_tag, secondary_tag,
                     front_image_path, back_image_path, card_payload_json)
                VALUES ('ticker-preview-rendered', %s, %s, %s,
                        %s, %s, 'Rendered Preview', 'TICKER-TAG', 'NONE',
                        'https://stub/p1/front.png', 'https://stub/p1/back.png',
                        '{}'::jsonb)
                """,
                (_hex_wallet(900021), _hex_wallet(900022), season_id,
                 _EVENT_ID, _EVENT_SLUG),
            )
            cur.execute(
                """
                INSERT INTO preview_cards
                    (slug, owner_wallet, owner_proxy_wallet, season_id,
                     event_id, event_slug, card_title, primary_tag, secondary_tag,
                     front_image_path, back_image_path, card_payload_json)
                VALUES ('ticker-preview-inflight', %s, %s, %s,
                        %s, %s, 'In-flight Preview', 'TICKER-TAG', 'NONE',
                        '', '',
                        '{}'::jsonb)
                """,
                (_hex_wallet(900031), _hex_wallet(900032), season_id,
                 _EVENT_ID, _EVENT_SLUG),
            )
        conn.commit()
        yield {"season_id": season_id}
    finally:
        with conn.cursor() as cur:
            if season_id is not None:
                # preview_cards has FK ON DELETE CASCADE on season_id, so
                # the DELETE seasons below cleans both. Drop participants
                # partition explicitly though.
                cur.execute("SELECT participants_drop_partition(%s)", (season_id,))
                cur.execute("DELETE FROM claims WHERE season_id = %s", (season_id,))
                cur.execute("DELETE FROM seasons WHERE id = %s", (season_id,))
            cur.execute("DELETE FROM event_cards WHERE event_id = %s", (_EVENT_ID,))
            cur.execute("DELETE FROM events       WHERE id       = %s", (_EVENT_ID,))
        conn.commit()
        conn.close()


def _run_ticker_sql(limit: int) -> List[Dict[str, Any]]:
    from user_web_backend.main import _CARDS_TICKER_SAMPLE_SQL

    conn = make_real_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(_CARDS_TICKER_SAMPLE_SQL, (limit,))
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


class TestTickerUnion:
    def test_includes_completed_claim(self, ticker_setup):
        rows = _run_ticker_sql(50)
        slugs = [r["slug"] for r in rows]
        assert "ticker-completed-1" in slugs, (
            "COMPLETED claim missing from ticker — minted branch is broken"
        )

    def test_includes_rendered_preview(self, ticker_setup):
        rows = _run_ticker_sql(50)
        slugs = [r["slug"] for r in rows]
        assert "ticker-preview-rendered" in slugs, (
            "Rendered preview row missing from ticker — preview UNION branch "
            "is gone, so admin showcase simulator output never reaches the "
            "user web home page"
        )

    def test_excludes_queued_claim_without_images(self, ticker_setup):
        rows = _run_ticker_sql(50)
        slugs = [r["slug"] for r in rows]
        # QUEUED claim has card_slug=NULL, so by ``card_slug IS NOT NULL``
        # it cannot land in the result. Defense check.
        assert all(s is not None for s in slugs)
        # No COMPLETED row carries the queued-card title.
        assert all((r.get("card_title") or "") != "Queued Card" for r in rows)

    def test_excludes_inflight_preview_with_empty_images(self, ticker_setup):
        rows = _run_ticker_sql(50)
        slugs = [r["slug"] for r in rows]
        assert "ticker-preview-inflight" not in slugs, (
            "In-flight preview (empty image paths) leaked into the ticker "
            "— front-end would render a broken <img> for it"
        )

    def test_ticker_row_shape_matches_frontend_contract(self, ticker_setup):
        rows = _run_ticker_sql(50)
        # Pick our seeded rendered preview to assert column shape end-to-end.
        preview_row = next(
            (r for r in rows if r["slug"] == "ticker-preview-rendered"), None
        )
        assert preview_row is not None
        # Frontend reads exactly these aliases.
        assert preview_row["card_title"] == "Rendered Preview"
        assert preview_row["front_image_path"] == "https://stub/p1/front.png"
        assert preview_row["back_image_path"] == "https://stub/p1/back.png"
        assert preview_row["created_at"] is not None
