"""
Integration tests for promote_preview_to_claim.

Each test inserts a minimal chain of real rows:

  seasons → winner_wallets_nft_to_claim → claims + preview_cards

…calls promote_preview_to_claim against the live PostgreSQL instance,
then asserts the actual DB state.  The fixture tears everything down
regardless of test outcome.

FK notes (from schema):
  preview_cards.winner_row_id  ON DELETE CASCADE   → auto-deleted with winner row
  claims.winner_row_id         ON DELETE SET NULL  → nulled when winner row is deleted

Cleanup order: DELETE claims → DELETE winner_row (cascades preview_cards) → DELETE season.
"""

import json
import pytest

from tests.integration.conftest import _DirectDBManager, make_real_connection

# ------------------------------------------------------------------
# Sentinel values — valid wallet format, unique enough not to exist
# in real production data.
# ------------------------------------------------------------------
_OWNER_WALLET = "0x" + "1" * 40   # 0x1111...1111  (EOA for claims)
_PROXY_WALLET = "0x" + "2" * 40   # 0x2222...2222  (Polymarket proxy, winner identity)
_TEST_SLUG    = "polystar-integration-test-slug-9999"
_FRONT_URL    = "https://r2.example.com/integration-front.png"
_BACK_URL     = "https://r2.example.com/integration-back.png"


# ------------------------------------------------------------------
# Fixture
# ------------------------------------------------------------------

@pytest.fixture()
def db_rows():
    """
    Inserts a minimal set of test rows and yields their IDs.
    Always cleans up — even when the test raises or the promote call
    deletes some rows itself.
    """
    conn = make_real_connection()
    season_id = winner_id = claim_id = None

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO seasons
                    (type, season_number, start_date, end_date,
                     total_supply, remaining_supply, is_active)
                VALUES ('standard', 99999,
                        '2099-01-01 00:00:00+00',
                        '2099-12-31 00:00:00+00',
                        100, 100, false)
                RETURNING id
                """,
            )
            season_id = cur.fetchone()[0]

            cur.execute(
                """
                INSERT INTO winner_wallets_nft_to_claim
                    (season_id, proxy_wallet,
                     window_start, window_end)
                VALUES (%s, %s,
                        '2099-01-01 00:00:00+00',
                        '2099-12-31 00:00:00+00')
                RETURNING id
                """,
                (season_id, _PROXY_WALLET),
            )
            winner_id = cur.fetchone()[0]

            cur.execute(
                """
                INSERT INTO claims
                    (user_wallet, season_id, phase_type, status)
                VALUES (%s, %s, 'breach', 'PENDING')
                RETURNING id
                """,
                (_OWNER_WALLET, season_id),
            )
            claim_id = cur.fetchone()[0]

            cur.execute(
                """
                INSERT INTO preview_cards
                    (slug, owner_wallet, winner_row_id, season_id,
                     front_image_path, back_image_path, card_payload_json)
                VALUES (%s, %s, %s, %s, %s, %s, '{}'::jsonb)
                """,
                (_TEST_SLUG, _OWNER_WALLET, winner_id, season_id,
                 _FRONT_URL, _BACK_URL),
            )

        conn.commit()
        yield {
            "season_id": season_id,
            "winner_id": winner_id,
            "claim_id":  claim_id,
            "slug":      _TEST_SLUG,
        }

    except Exception:
        conn.rollback()
        raise

    finally:
        try:
            with conn.cursor() as cur:
                if claim_id:
                    cur.execute("DELETE FROM claims WHERE id = %s", (claim_id,))
                if winner_id:
                    # CASCADE deletes preview_cards; SET NULL on claims.winner_row_id
                    cur.execute(
                        "DELETE FROM winner_wallets_nft_to_claim WHERE id = %s",
                        (winner_id,),
                    )
                if season_id:
                    cur.execute("DELETE FROM seasons WHERE id = %s", (season_id,))
            conn.commit()
        except Exception:
            conn.rollback()
        finally:
            conn.close()


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _make_card(slug: str = _TEST_SLUG) -> dict:
    return {
        "card_title":       "Integration STAR",
        "primary_tag":      "Whale",
        "secondary_tag":    "Legend",
        "pattern":          "mosaic",
        "front_image_url":  _FRONT_URL,
        "back_image_url":   _BACK_URL,
        "qr_payload":       f"https://polystars.app/cards/{slug}",
    }


def _fetch_claim(claim_id: int) -> dict | None:
    conn = make_real_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT card_slug, card_title, front_image_url, back_image_url,
                       primary_tag, secondary_tag, pattern,
                       winner_row_id, card_payload_json
                FROM claims WHERE id = %s
                """,
                (claim_id,),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    keys = ("card_slug", "card_title", "front_image_url", "back_image_url",
            "primary_tag", "secondary_tag", "pattern",
            "winner_row_id", "card_payload_json")
    return dict(zip(keys, row))


def _preview_exists(winner_row_id: int) -> bool:
    conn = make_real_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM preview_cards WHERE winner_row_id = %s",
                (winner_row_id,),
            )
            return cur.fetchone() is not None
    finally:
        conn.close()


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------

class TestPromotePreviewToClaimIntegration:

    # --- Precondition sanity ----------------------------------------

    def test_fixture_creates_preview_row(self, db_rows):
        """Confirm the fixture actually inserted the preview row."""
        assert _preview_exists(db_rows["winner_id"])

    def test_fixture_creates_pending_claim(self, db_rows):
        conn = make_real_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT status, card_slug FROM claims WHERE id = %s",
                    (db_rows["claim_id"],),
                )
                row = cur.fetchone()
        finally:
            conn.close()
        assert row is not None
        assert row[0] == "PENDING"
        assert row[1] is None  # no card data yet

    # --- Core: preview row removed ----------------------------------

    def test_preview_row_deleted_from_db(self, db_rows):
        from scripts.polystars_card_payload import promote_preview_to_claim

        promote_preview_to_claim(
            _DirectDBManager(),
            claim_id=db_rows["claim_id"],
            winner_row_id=db_rows["winner_id"],
            owner_wallet=_OWNER_WALLET,
            polystars_card=_make_card(db_rows["slug"]),
        )

        assert not _preview_exists(db_rows["winner_id"]), (
            "promote_preview_to_claim must DELETE the preview_cards row so it "
            "no longer appears in the home ticker"
        )

    # --- Core: claims row populated ---------------------------------

    def test_claims_row_card_slug_written(self, db_rows):
        from scripts.polystars_card_payload import promote_preview_to_claim

        promote_preview_to_claim(
            _DirectDBManager(),
            claim_id=db_rows["claim_id"],
            winner_row_id=db_rows["winner_id"],
            owner_wallet=_OWNER_WALLET,
            polystars_card=_make_card(db_rows["slug"]),
        )

        claim = _fetch_claim(db_rows["claim_id"])
        assert claim["card_slug"] == db_rows["slug"]

    def test_claims_row_all_card_fields_written(self, db_rows):
        from scripts.polystars_card_payload import promote_preview_to_claim

        card = _make_card(db_rows["slug"])
        promote_preview_to_claim(
            _DirectDBManager(),
            claim_id=db_rows["claim_id"],
            winner_row_id=db_rows["winner_id"],
            owner_wallet=_OWNER_WALLET,
            polystars_card=card,
        )

        claim = _fetch_claim(db_rows["claim_id"])
        assert claim["card_title"]      == card["card_title"]
        assert claim["front_image_url"] == card["front_image_url"]
        assert claim["back_image_url"]  == card["back_image_url"]
        assert claim["primary_tag"]     == card["primary_tag"]
        assert claim["secondary_tag"]   == card["secondary_tag"]
        assert claim["pattern"]         == card["pattern"]
        assert claim["winner_row_id"]   == db_rows["winner_id"]

    def test_claims_card_payload_json_contains_card_data(self, db_rows):
        """JSONB column must store the full polystars_card dict."""
        from scripts.polystars_card_payload import promote_preview_to_claim

        card = _make_card(db_rows["slug"])
        promote_preview_to_claim(
            _DirectDBManager(),
            claim_id=db_rows["claim_id"],
            winner_row_id=db_rows["winner_id"],
            owner_wallet=_OWNER_WALLET,
            polystars_card=card,
        )

        claim = _fetch_claim(db_rows["claim_id"])
        payload = claim["card_payload_json"]  # psycopg2 returns JSONB as dict
        assert payload["card_title"]   == card["card_title"]
        assert payload["primary_tag"]  == card["primary_tag"]
        assert payload["secondary_tag"] == card["secondary_tag"]

    # --- Atomicity --------------------------------------------------

    def test_both_writes_are_atomic(self, db_rows):
        """Verify that both the UPDATE and DELETE landed in the same commit —
        i.e. claims is populated AND preview is gone at the same time."""
        from scripts.polystars_card_payload import promote_preview_to_claim

        promote_preview_to_claim(
            _DirectDBManager(),
            claim_id=db_rows["claim_id"],
            winner_row_id=db_rows["winner_id"],
            owner_wallet=_OWNER_WALLET,
            polystars_card=_make_card(db_rows["slug"]),
        )

        claim = _fetch_claim(db_rows["claim_id"])
        assert claim["card_slug"] is not None, "claims must have card_slug after promote"
        assert not _preview_exists(db_rows["winner_id"]), "preview must be gone after promote"

    # --- Idempotency ------------------------------------------------

    def test_second_call_is_a_noop_not_an_error(self, db_rows):
        """Calling promote twice must not raise — the DELETE WHERE on a
        missing row is a valid SQL no-op and the UPDATE is idempotent."""
        from scripts.polystars_card_payload import promote_preview_to_claim

        manager = _DirectDBManager()
        card = _make_card(db_rows["slug"])
        kwargs = dict(
            claim_id=db_rows["claim_id"],
            winner_row_id=db_rows["winner_id"],
            owner_wallet=_OWNER_WALLET,
            polystars_card=card,
        )
        promote_preview_to_claim(manager, **kwargs)
        promote_preview_to_claim(manager, **kwargs)  # must not raise

        claim = _fetch_claim(db_rows["claim_id"])
        assert claim["card_slug"] == db_rows["slug"]

    # --- Early-return guards (no DB writes expected) ----------------

    def test_missing_front_url_leaves_db_unchanged(self, db_rows):
        """With no front_image_url, the function must return before touching
        the DB — preview row must still exist, claim must still be blank."""
        from scripts.polystars_card_payload import promote_preview_to_claim

        bad_card = _make_card(db_rows["slug"])
        bad_card["front_image_url"] = ""

        promote_preview_to_claim(
            _DirectDBManager(),
            claim_id=db_rows["claim_id"],
            winner_row_id=db_rows["winner_id"],
            owner_wallet=_OWNER_WALLET,
            polystars_card=bad_card,
        )

        assert _preview_exists(db_rows["winner_id"]), "preview row must survive no-op call"
        claim = _fetch_claim(db_rows["claim_id"])
        assert claim["card_slug"] is None, "claims must not be updated on no-op call"

    def test_missing_qr_payload_leaves_db_unchanged(self, db_rows):
        from scripts.polystars_card_payload import promote_preview_to_claim

        bad_card = _make_card(db_rows["slug"])
        bad_card["qr_payload"] = ""

        promote_preview_to_claim(
            _DirectDBManager(),
            claim_id=db_rows["claim_id"],
            winner_row_id=db_rows["winner_id"],
            owner_wallet=_OWNER_WALLET,
            polystars_card=bad_card,
        )

        assert _preview_exists(db_rows["winner_id"])
        claim = _fetch_claim(db_rows["claim_id"])
        assert claim["card_slug"] is None
