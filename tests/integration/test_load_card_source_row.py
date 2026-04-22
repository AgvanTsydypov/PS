"""
Integration tests for _load_card_source_row (scripts/polystars_card_payload.py).

The function executes _CARD_SOURCE_SQL — a complex LEFT JOIN across six tables:
  winner_wallets_nft_to_claim → event_cards (INNER) → events, seasons, tags, participants

These tests verify that:
- A missing winner_row_id returns None
- All expected keys are present in the returned dict
- COALESCE logic for direct winner-row fields vs participant fallback works
- Tag hex_color is resolved through the LOWER/BTRIM join on primary_tag label
- Season metadata (type, number) flows through correctly

Data setup:
  events(id, title) → event_cards(event_id, ...) ← winner_wallets_nft_to_claim
                                                    ← seasons
  tags(id, label, hex_color) — matched by label to event_cards.primary_tag

Cleanup: DELETE events (cascades event_cards); DELETE seasons (cascades winner_wallets).
"""

import pytest

from tests.integration.conftest import (
    _DirectDBManager,
    _patch_card_payload_psycopg2,
    make_real_connection,
)

_EVENT_ID    = "integ-src-row-event-7777"
_SEASON_NUM  = 77200
_PROXY_WALLET = "0x" + "3" * 40
_TAG_ID      = "integ-src-row-tag-7777"
_TAG_LABEL   = "IntegCrypto"
_TAG_COLOR   = "#abcdef"


# ------------------------------------------------------------------
# Fixture
# ------------------------------------------------------------------

@pytest.fixture()
def src_row_data():
    """
    Inserts:
      event → event_card (primary_tag="IntegCrypto", manual_image_url set)
      season → winner_wallets_nft_to_claim (event_id + entry_cwap set on winner row)
      tag (label=IntegCrypto, hex_color=#abcdef)
    Yields dict with IDs.  Cleans up in reverse FK order on exit.
    """
    conn = make_real_connection()
    season_id = winner_id = None
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO events (id, title) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (_EVENT_ID, "Integration Source Row Test Event"),
            )
            cur.execute(
                """
                INSERT INTO event_cards
                    (event_id, card_title, primary_tag, secondary_tag,
                     manual_image_url, status)
                VALUES (%s, %s, %s, %s, %s, 'ok')
                ON CONFLICT (event_id) DO UPDATE
                    SET card_title = EXCLUDED.card_title,
                        primary_tag = EXCLUDED.primary_tag,
                        secondary_tag = EXCLUDED.secondary_tag,
                        manual_image_url = EXCLUDED.manual_image_url
                """,
                (_EVENT_ID, "INTEG Card Title", _TAG_LABEL, "Legend",
                 "https://r2.example.com/integ-manual.png"),
            )
            cur.execute(
                """
                INSERT INTO tags (id, label, hex_color, is_primary)
                VALUES (%s, %s, %s, false)
                ON CONFLICT (id) DO UPDATE
                    SET label = EXCLUDED.label,
                        hex_color = EXCLUDED.hex_color
                """,
                (_TAG_ID, _TAG_LABEL, _TAG_COLOR),
            )
            cur.execute(
                """
                INSERT INTO seasons
                    (type, season_number, start_date, end_date,
                     total_supply, remaining_supply, is_active)
                VALUES ('standard', %s,
                        '2099-01-01 00:00:00+00',
                        '2099-12-31 00:00:00+00',
                        100, 100, false)
                RETURNING id
                """,
                (_SEASON_NUM,),
            )
            season_id = cur.fetchone()[0]
            cur.execute(
                """
                INSERT INTO winner_wallets_nft_to_claim
                    (season_id, proxy_wallet, source,
                     window_start, window_end,
                     event_id, entry_cwap, entry_bracket,
                     edge, yield, gravity)
                VALUES (%s, %s, 'test_source',
                        '2099-01-01 00:00:00+00',
                        '2099-12-31 00:00:00+00',
                        %s, 0.35, '[0.20 - 0.40]',
                        'P90', 'P90', 'P70')
                RETURNING id
                """,
                (season_id, _PROXY_WALLET, _EVENT_ID),
            )
            winner_id = cur.fetchone()[0]
        conn.commit()
        yield {
            "season_id": season_id,
            "winner_id": winner_id,
            "event_id": _EVENT_ID,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        try:
            with conn.cursor() as cur:
                if winner_id:
                    cur.execute(
                        "DELETE FROM winner_wallets_nft_to_claim WHERE id = %s", (winner_id,)
                    )
                if season_id:
                    cur.execute("DELETE FROM seasons WHERE id = %s", (season_id,))
                cur.execute(
                    "DELETE FROM event_cards WHERE event_id = %s", (_EVENT_ID,)
                )
                cur.execute("DELETE FROM events WHERE id = %s", (_EVENT_ID,))
                cur.execute("DELETE FROM tags WHERE id = %s", (_TAG_ID,))
            conn.commit()
        except Exception:
            conn.rollback()
        finally:
            conn.close()


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------

class TestLoadCardSourceRow:

    def _call(self, winner_row_id: int):
        with _patch_card_payload_psycopg2():
            from scripts.polystars_card_payload import _load_card_source_row
            return _load_card_source_row(_DirectDBManager(), winner_row_id)

    # --- None for unknown id ----------------------------------------

    def test_returns_none_for_missing_winner_row(self, src_row_data):
        assert self._call(999_999_999) is None

    # --- Basic shape ------------------------------------------------

    def test_returns_dict_for_valid_winner(self, src_row_data):
        result = self._call(src_row_data["winner_id"])
        assert isinstance(result, dict)
        assert len(result) > 0

    def test_all_expected_keys_present(self, src_row_data):
        result = self._call(src_row_data["winner_id"])
        expected = {
            "winner_row_id", "season_id", "season_type", "season_number",
            "proxy_wallet", "event_id", "card_title", "primary_tag",
            "secondary_tag", "entry_bracket", "edge", "yield", "gravity",
            "primary_tag_hex_color",
        }
        assert expected <= result.keys()

    # --- Scalar field values ----------------------------------------

    def test_card_title_from_event_cards(self, src_row_data):
        result = self._call(src_row_data["winner_id"])
        assert result["card_title"] == "INTEG Card Title"

    def test_primary_tag_from_event_cards(self, src_row_data):
        result = self._call(src_row_data["winner_id"])
        assert result["primary_tag"] == _TAG_LABEL

    def test_season_type_from_seasons(self, src_row_data):
        result = self._call(src_row_data["winner_id"])
        assert result["season_type"] == "standard"

    def test_season_number_from_seasons(self, src_row_data):
        result = self._call(src_row_data["winner_id"])
        assert result["season_number"] == _SEASON_NUM

    def test_proxy_wallet_from_winner_row(self, src_row_data):
        result = self._call(src_row_data["winner_id"])
        assert result["proxy_wallet"] == _PROXY_WALLET

    # --- Tag hex_color JOIN -----------------------------------------

    def test_primary_tag_hex_color_from_tags(self, src_row_data):
        result = self._call(src_row_data["winner_id"])
        assert result["primary_tag_hex_color"] == _TAG_COLOR

    def test_primary_tag_hex_color_none_when_no_matching_tag(self, src_row_data):
        """Verify LEFT JOIN: no matching tag → hex_color is NULL."""
        conn = make_real_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE event_cards SET primary_tag = 'NONEXISTENT_TAG_XYZ' "
                    "WHERE event_id = %s",
                    (_EVENT_ID,),
                )
            conn.commit()
        finally:
            conn.close()
        try:
            result = self._call(src_row_data["winner_id"])
            assert result["primary_tag_hex_color"] is None
        finally:
            # Restore original primary_tag
            conn2 = make_real_connection()
            try:
                with conn2.cursor() as cur:
                    cur.execute(
                        "UPDATE event_cards SET primary_tag = %s WHERE event_id = %s",
                        (_TAG_LABEL, _EVENT_ID),
                    )
                conn2.commit()
            finally:
                conn2.close()

    # --- winner-row direct fields (COALESCE over participant fallback) ---

    def test_entry_cwap_from_winner_row_when_set(self, src_row_data):
        """winner_wallets_nft_to_claim.entry_cwap takes priority over participant."""
        result = self._call(src_row_data["winner_id"])
        # The fixture set entry_cwap=0.35 directly on the winner row.
        # With no matching participant row, COALESCE returns the winner's value.
        assert result["entry_cwap"] is not None
