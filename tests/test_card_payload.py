"""
Unit tests for pure helper functions in scripts/polystars_card_payload.py.

Covers: _fmt_date_field, _to_float, _normalize_choice, _normalize_archetype,
_normalize_entry_bracket, _build_card_payload_from_source_row,
_remote_image_to_data_uri, _slug_from_qr_payload.

No DB, no network, no Playwright required.
"""

import base64
import io
from datetime import date, datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import pytest

from scripts.polystars_card_payload import (
    CARD_ARCHETYPE_OPTIONS,
    CARD_ENTRY_BRACKET_OPTIONS,
    CARD_TIER_OPTIONS,
    _build_card_payload_from_source_row,
    _fmt_date_field,
    _normalize_archetype,
    _normalize_choice,
    _normalize_entry_bracket,
    _remote_image_to_data_uri,
    _slug_from_qr_payload,
    _to_float,
)


# ---------------------------------------------------------------------------
# _fmt_date_field
# ---------------------------------------------------------------------------

class TestFmtDateField:
    def test_none_returns_none(self):
        assert _fmt_date_field(None) is None

    def test_date_object_returns_iso(self):
        assert _fmt_date_field(date(2024, 7, 6)) == "2024-07-06"

    def test_datetime_with_utc_returns_date_iso(self):
        dt = datetime(2024, 7, 6, 15, 30, 0, tzinfo=timezone.utc)
        assert _fmt_date_field(dt) == "2024-07-06"

    def test_datetime_with_offset_converts_to_utc(self):
        # +02:00 → UTC same date
        offset = timezone(timedelta(hours=2))
        dt = datetime(2024, 7, 6, 1, 0, 0, tzinfo=offset)
        result = _fmt_date_field(dt)
        # 2024-07-06 01:00 +02 = 2024-07-05 23:00 UTC
        assert result == "2024-07-05"

    def test_naive_datetime_returns_date_iso(self):
        dt = datetime(2024, 7, 6, 12, 0, 0)
        assert _fmt_date_field(dt) == "2024-07-06"

    def test_string_longer_than_10_truncated(self):
        assert _fmt_date_field("2024-07-06T12:00:00Z") == "2024-07-06"

    def test_string_exactly_10_returned(self):
        assert _fmt_date_field("2024-07-06") == "2024-07-06"

    def test_short_string_returned_as_is(self):
        result = _fmt_date_field("2024")
        assert result == "2024"

    def test_empty_string_returns_none(self):
        assert _fmt_date_field("") is None

    def test_whitespace_only_string_returns_none(self):
        assert _fmt_date_field("   ") is None


# ---------------------------------------------------------------------------
# _to_float
# ---------------------------------------------------------------------------

class TestToFloat:
    def test_int_converted(self):
        assert _to_float(5) == 5.0

    def test_float_returned(self):
        assert _to_float(3.14) == pytest.approx(3.14)

    def test_string_float_converted(self):
        assert _to_float("1.23") == pytest.approx(1.23)

    def test_none_returns_none(self):
        assert _to_float(None) is None

    def test_empty_string_returns_none(self):
        assert _to_float("") is None

    def test_whitespace_string_returns_none(self):
        assert _to_float("   ") is None

    def test_non_numeric_string_returns_none(self):
        assert _to_float("abc") is None

    def test_zero_returns_zero(self):
        assert _to_float(0) == 0.0

    def test_negative_float(self):
        assert _to_float(-42.5) == pytest.approx(-42.5)


# ---------------------------------------------------------------------------
# _normalize_choice
# ---------------------------------------------------------------------------

class TestNormalizeChoice:
    def test_exact_match_returned(self):
        assert _normalize_choice("P99", CARD_TIER_OPTIONS, "BASE") == "P99"

    def test_lowercase_match_returned(self):
        assert _normalize_choice("p90", CARD_TIER_OPTIONS, "BASE") == "P90"

    def test_no_match_returns_fallback(self):
        assert _normalize_choice("MYTHIC", CARD_TIER_OPTIONS, "BASE") == "BASE"

    def test_none_returns_fallback(self):
        assert _normalize_choice(None, CARD_TIER_OPTIONS, "BASE") == "BASE"

    def test_empty_string_returns_fallback(self):
        assert _normalize_choice("", CARD_TIER_OPTIONS, "BASE") == "BASE"

    def test_bracket_match_case_insensitive(self):
        result = _normalize_choice("[0.00 - 0.20]", CARD_ENTRY_BRACKET_OPTIONS, "[0.80 - 0.97]")
        assert result == "[0.00 - 0.20]"


# ---------------------------------------------------------------------------
# _normalize_archetype
# ---------------------------------------------------------------------------

class TestNormalizeArchetype:
    def test_valid_archetype_returned(self):
        assert _normalize_archetype("SIGNAL", "OPERATOR") == "SIGNAL"

    def test_lowercase_archetype_normalized(self):
        assert _normalize_archetype("signal", "OPERATOR") == "SIGNAL"

    def test_the_prefix_stripped(self):
        assert _normalize_archetype("THE SIGNAL", "OPERATOR") == "SIGNAL"

    def test_the_prefix_case_insensitive(self):
        assert _normalize_archetype("the anomaly", "OPERATOR") == "ANOMALY"

    def test_legacy_harvester_maps_to_extractor(self):
        assert _normalize_archetype("HARVESTER", "OPERATOR") == "EXTRACTOR"

    def test_legacy_martyr_maps_to_icarus(self):
        assert _normalize_archetype("MARTYR", "OPERATOR") == "ICARUS"

    def test_legacy_lowercase_harvester(self):
        assert _normalize_archetype("harvester", "OPERATOR") == "EXTRACTOR"

    def test_unknown_raw_returns_inferred(self):
        assert _normalize_archetype("NONEXISTENT", "EQUILIBRIUM") == "EQUILIBRIUM"

    def test_none_raw_returns_inferred(self):
        assert _normalize_archetype(None, "SIGNAL") == "SIGNAL"

    def test_empty_raw_returns_inferred(self):
        assert _normalize_archetype("", "VECTOR") == "VECTOR"

    def test_all_valid_archetypes_pass_through(self):
        for archetype in CARD_ARCHETYPE_OPTIONS:
            assert _normalize_archetype(archetype, "OPERATOR") == archetype


# ---------------------------------------------------------------------------
# _normalize_entry_bracket
# ---------------------------------------------------------------------------

class TestNormalizeEntryBracket:
    def test_valid_bracket_returned(self):
        assert _normalize_entry_bracket("[0.00 - 0.20]") == "[0.00 - 0.20]"

    def test_all_valid_brackets_pass_through(self):
        for bracket in CARD_ENTRY_BRACKET_OPTIONS:
            assert _normalize_entry_bracket(bracket) == bracket

    def test_none_returns_default(self):
        assert _normalize_entry_bracket(None) == "[0.80 - 0.97]"

    def test_empty_returns_default(self):
        assert _normalize_entry_bracket("") == "[0.80 - 0.97]"

    def test_unknown_returns_default(self):
        assert _normalize_entry_bracket("UNKNOWN") == "[0.80 - 0.97]"

    def test_legacy_anomaly_maps_to_bracket(self):
        assert _normalize_entry_bracket("ANOMALY") == "[0.00 - 0.20]"

    def test_legacy_oracle_maps_to_bracket(self):
        assert _normalize_entry_bracket("ORACLE") == "[0.20 - 0.40]"

    def test_legacy_outlier_maps_to_bracket(self):
        assert _normalize_entry_bracket("OUTLIER") == "[0.40 - 0.60]"

    def test_legacy_vector_maps_to_bracket(self):
        assert _normalize_entry_bracket("VECTOR") == "[0.60 - 0.80]"

    def test_legacy_harvester_maps_to_bracket(self):
        assert _normalize_entry_bracket("HARVESTER") == "[0.80 - 0.97]"

    def test_legacy_extractor_maps_to_bracket(self):
        assert _normalize_entry_bracket("EXTRACTOR") == "[0.97 - 1.00]"

    def test_legacy_passenger_maps_to_bracket(self):
        assert _normalize_entry_bracket("PASSENGER") == "[0.97 - 1.00]"

    def test_legacy_lowercase_maps_correctly(self):
        assert _normalize_entry_bracket("anomaly") == "[0.00 - 0.20]"


# ---------------------------------------------------------------------------
# _build_card_payload_from_source_row
# ---------------------------------------------------------------------------

def _base_row(**overrides):
    row = {
        "entry_bracket": "[0.80 - 0.97]",
        "edge": "P90",
        "yield": "P90",
        "gravity": "P70",
        "entry_cwap": 0.85,
        "total_volume": 1000.0,
        "total_pnl": 500.0,
        "archetype": "EQUILIBRIUM",
        "archetype_description": "high edge and yield",
        "archetype_math": "edge * yield",
        "rarity_bracket": "P90",
        "proxy_wallet": "0xabc123",
        "reccurence": None,
        "manual_image_url": "https://example.com/img.png",
        "season_type": "standard",
        "season_number": 1,
        "card_title": "Test Card",
        "event_title": "Test Event",
        "card_lore": "Some lore text",
        "primary_tag": "CRYPTO",
        "primary_tag_hex_color": "#FF0000",
        "secondary_tag": "FINANCE",
        "rank": 3,
        "season_start_date": date(2024, 7, 6),
        "season_end_date": date(2024, 7, 16),
        "season_size": 500,
    }
    row.update(overrides)
    return row


class TestBuildCardPayload:
    def test_happy_path_returns_dict(self):
        payload = _build_card_payload_from_source_row(
            _base_row(), claim_id=7, claim_type="looter"
        )
        assert isinstance(payload, dict)

    def test_required_keys_present(self):
        payload = _build_card_payload_from_source_row(
            _base_row(), claim_id=7, claim_type="looter"
        )
        expected = {
            "season_type", "season_number", "recurrence", "claim_type",
            "image_url", "card_title", "card_lore", "primary_tag",
            "primary_tag_color", "secondary_tag", "entry_bracket", "archetype",
            "archetype_description", "archetype_math", "rarity_bracket",
            "proxy_wallet", "edge", "yield", "gravity", "border_color",
            "leaderboard_rank", "season_start_date", "season_end_date",
            "season_size", "collection_mint_number", "qr_payload",
        }
        assert expected.issubset(set(payload.keys()))

    def test_missing_image_url_raises(self):
        with pytest.raises(ValueError, match="manual_image_url"):
            _build_card_payload_from_source_row(
                _base_row(manual_image_url=""), claim_id=1, claim_type="looter"
            )

    def test_none_image_url_raises(self):
        with pytest.raises(ValueError):
            _build_card_payload_from_source_row(
                _base_row(manual_image_url=None), claim_id=1, claim_type="looter"
            )

    def test_claim_type_normalized_lowercase(self):
        payload = _build_card_payload_from_source_row(
            _base_row(), claim_id=1, claim_type="ORIGIN"
        )
        assert payload["claim_type"] == "origin"

    def test_empty_claim_type_defaults_to_looter(self):
        payload = _build_card_payload_from_source_row(
            _base_row(), claim_id=1, claim_type=""
        )
        assert payload["claim_type"] == "looter"

    def test_collection_mint_number_used_when_provided(self):
        payload = _build_card_payload_from_source_row(
            _base_row(), claim_id=5, claim_type="looter", collection_mint_number=42
        )
        assert payload["collection_mint_number"] == 42

    def test_collection_mint_number_falls_back_to_claim_id(self):
        payload = _build_card_payload_from_source_row(
            _base_row(), claim_id=99, claim_type="looter"
        )
        assert payload["collection_mint_number"] == 99

    def test_card_title_from_card_title_field(self):
        payload = _build_card_payload_from_source_row(
            _base_row(card_title="My Title", event_title="Event Title"),
            claim_id=1, claim_type="looter"
        )
        assert payload["card_title"] == "My Title"

    def test_card_title_falls_back_to_event_title(self):
        payload = _build_card_payload_from_source_row(
            _base_row(card_title="", event_title="Fallback Title"),
            claim_id=1, claim_type="looter"
        )
        assert payload["card_title"] == "Fallback Title"

    def test_season_number_defaults_to_1(self):
        payload = _build_card_payload_from_source_row(
            _base_row(season_number=None), claim_id=1, claim_type="looter"
        )
        assert payload["season_number"] == 1

    def test_season_start_date_formatted(self):
        payload = _build_card_payload_from_source_row(
            _base_row(season_start_date=date(2024, 7, 6)), claim_id=1, claim_type="looter"
        )
        assert payload["season_start_date"] == "2024-07-06"

    def test_standard_season_uses_db_dates(self):
        payload = _build_card_payload_from_source_row(
            _base_row(season_type="standard", season_start_date=date(2024, 7, 6)),
            claim_id=1, claim_type="looter"
        )
        assert payload["season_start_date"] == "2024-07-06"

    def test_genesis_season_uses_env_dates_when_set(self):
        with (
            patch("scripts.polystars_card_payload.GENESIS_START_DATE", "2024-07-06"),
            patch("scripts.polystars_card_payload.GENESIS_END_DATE", "2026-01-05"),
        ):
            payload = _build_card_payload_from_source_row(
                _base_row(season_type="genesis", season_start_date=date(2020, 1, 1)),
                claim_id=1, claim_type="looter"
            )
        assert payload["season_start_date"] == "2024-07-06"
        assert payload["season_end_date"] == "2026-01-05"

    def test_genesis_season_uses_db_dates_when_env_not_set(self):
        with (
            patch("scripts.polystars_card_payload.GENESIS_START_DATE", None),
            patch("scripts.polystars_card_payload.GENESIS_END_DATE", None),
        ):
            payload = _build_card_payload_from_source_row(
                _base_row(season_type="genesis", season_start_date=date(2024, 7, 6)),
                claim_id=1, claim_type="looter"
            )
        assert payload["season_start_date"] == "2024-07-06"

    def test_recurrence_none_stays_none(self):
        payload = _build_card_payload_from_source_row(
            _base_row(reccurence=None), claim_id=1, claim_type="looter"
        )
        assert payload["recurrence"] is None

    def test_recurrence_value_preserved(self):
        payload = _build_card_payload_from_source_row(
            _base_row(reccurence="weekly"), claim_id=1, claim_type="looter"
        )
        assert payload["recurrence"] == "weekly"

    def test_recurrence_empty_string_becomes_none(self):
        payload = _build_card_payload_from_source_row(
            _base_row(reccurence=""), claim_id=1, claim_type="looter"
        )
        assert payload["recurrence"] is None

    def test_legacy_archetype_harvester_remapped(self):
        payload = _build_card_payload_from_source_row(
            _base_row(archetype="HARVESTER"), claim_id=1, claim_type="looter"
        )
        assert payload["archetype"] == "EXTRACTOR"

    def test_legacy_archetype_martyr_remapped(self):
        payload = _build_card_payload_from_source_row(
            _base_row(archetype="MARTYR"), claim_id=1, claim_type="looter"
        )
        assert payload["archetype"] == "ICARUS"

    def test_legacy_entry_bracket_remapped(self):
        payload = _build_card_payload_from_source_row(
            _base_row(entry_bracket="HARVESTER"), claim_id=1, claim_type="looter"
        )
        assert payload["entry_bracket"] == "[0.80 - 0.97]"

    def test_qr_payload_starts_with_base_url(self):
        payload = _build_card_payload_from_source_row(
            _base_row(), claim_id=1, claim_type="looter"
        )
        import scripts.polystars_card_payload as m
        assert payload["qr_payload"].startswith(m.CARD_BASE_URL + "/cards/")

    def test_primary_tag_defaults_to_unknown(self):
        payload = _build_card_payload_from_source_row(
            _base_row(primary_tag=None), claim_id=1, claim_type="looter"
        )
        assert payload["primary_tag"] == "UNKNOWN"

    def test_secondary_tag_defaults_to_none_label(self):
        payload = _build_card_payload_from_source_row(
            _base_row(secondary_tag=None), claim_id=1, claim_type="looter"
        )
        assert payload["secondary_tag"] == "NONE"

    def test_leaderboard_rank_from_row(self):
        payload = _build_card_payload_from_source_row(
            _base_row(rank=15), claim_id=1, claim_type="looter"
        )
        assert payload["leaderboard_rank"] == 15

    def test_leaderboard_rank_none_defaults_zero(self):
        payload = _build_card_payload_from_source_row(
            _base_row(rank=None), claim_id=1, claim_type="looter"
        )
        assert payload["leaderboard_rank"] == 0


# ---------------------------------------------------------------------------
# Preview slug re-use through the preview → mint transition
#
# Pins the contract that a slug, once minted from a preview row, lives through
# the preview → mint transition: the on-chain QR code baked into the NFT
# (``polystars_card.qr_payload``) must use the existing preview slug instead
# of a freshly-generated random string. Losing this would reintroduce the
# bug where ``/preview/{preview_slug}`` and ``/cards/{mint_slug}`` pointed at
# two unrelated random strings for the same physical card.
# ---------------------------------------------------------------------------

class TestBuildCardPayloadPreviewSlugReuse:
    def test_preview_slug_is_used_verbatim_in_qr_payload(self):
        preview_slug = "card-standard-s1-from-preview-42"
        payload = _build_card_payload_from_source_row(
            _base_row(),
            claim_id=7,
            claim_type="looter",
            preview_slug=preview_slug,
        )
        assert payload["qr_payload"].endswith(f"/cards/{preview_slug}"), (
            "qr_payload must bake the preview slug so the on-chain QR code "
            "points at the same ``/cards/{slug}`` the preview already occupies"
        )

    def test_missing_preview_slug_falls_back_to_generation(self):
        payload = _build_card_payload_from_source_row(
            _base_row(season_type="genesis", season_number=4),
            claim_id=1,
            claim_type="looter",
            preview_slug=None,
        )
        # Without a preview to reuse we must still produce a slug (admin mints
        # without a preview round-trip must not crash). Generated slugs carry
        # the season type/number prefix.
        assert "/cards/card-genesis-s4-" in payload["qr_payload"]

    def test_empty_preview_slug_falls_back_to_generation(self):
        payload = _build_card_payload_from_source_row(
            _base_row(),
            claim_id=1,
            claim_type="looter",
            preview_slug="   ",
        )
        slug_tail = payload["qr_payload"].rstrip("/").rsplit("/", 1)[-1]
        assert slug_tail.startswith("card-standard-s1-"), (
            "A blank preview_slug is equivalent to None — the builder must "
            "fall back to slug generation rather than emit an empty path"
        )


# ---------------------------------------------------------------------------
# promote_preview_to_claim — canonical mint-time writer
#
# The admin mint flow must call ``promote_preview_to_claim`` to denormalize
# card-detail fields onto the claim row. The helper must be importable from
# the module's public surface and must still be callable after the rename
# from ``persist_user_generated_card_for_mint``.
# ---------------------------------------------------------------------------

class TestPromotePreviewToClaimSurface:
    def test_promote_helper_is_importable(self):
        from scripts.polystars_card_payload import promote_preview_to_claim

        assert callable(promote_preview_to_claim)

    def test_legacy_persist_helper_is_removed(self):
        import scripts.polystars_card_payload as mod

        assert not hasattr(mod, "persist_user_generated_card_for_mint"), (
            "The legacy persist_user_generated_card_for_mint helper has been "
            "replaced by promote_preview_to_claim and must not linger as an "
            "alias — keeping it would invite silent regressions back to the "
            "preview-only-table-as-canonical-store shape we removed"
        )


# ---------------------------------------------------------------------------
# promote_preview_to_claim — DB interaction shape
#
# Stage 2 behaviour: the helper must UPDATE the matching ``claims`` row with
# the denormalized card fields AND DELETE the corresponding preview row from
# ``preview_cards``. The old dual-write UPSERT is gone — if it ever comes
# back, the preview ticker will start surfacing minted STARs again.
# ---------------------------------------------------------------------------


class TestPromotePreviewToClaimDbWrites:
    def _build_polystars_card(self, *, slug: str = "polystar-s1-0001") -> dict:
        return {
            "card_title": "Test STAR",
            "primary_tag": "Whale",
            "secondary_tag": "Legend",
            "pattern": "mosaic",
            "front_image_url": "https://ipfs.pinata.cloud/ipfs/front.png",
            "back_image_url": "https://ipfs.pinata.cloud/ipfs/back.png",
            "qr_payload": f"https://polystars.app/cards/{slug}",
        }

    def _run_promote(self, polystars_card: dict):
        from scripts.polystars_card_payload import promote_preview_to_claim

        cursor = MagicMock()
        cursor.__enter__ = lambda self: self
        cursor.__exit__ = MagicMock(return_value=False)

        conn = MagicMock()
        conn.cursor.return_value = cursor

        manager = MagicMock()
        manager.get_connection.return_value = conn

        promote_preview_to_claim(
            manager,
            claim_id=42,
            winner_row_id=7,
            owner_wallet="0xOWNER",
            polystars_card=polystars_card,
        )
        return cursor, conn

    def _statements(self, cursor: MagicMock) -> list[str]:
        return [
            " ".join(str(call.args[0]).split()).lower()
            for call in cursor.execute.call_args_list
        ]

    def test_updates_claims_with_card_denormalized_fields(self):
        card = self._build_polystars_card()
        cursor, _conn = self._run_promote(card)
        stmts = self._statements(cursor)
        assert any("update claims" in s for s in stmts), (
            "promote_preview_to_claim must UPDATE claims with card_slug, "
            "card_title, front_image_url, back_image_url, etc."
        )

    def test_deletes_preview_row_for_winner(self):
        card = self._build_polystars_card()
        cursor, _conn = self._run_promote(card)
        stmts = self._statements(cursor)
        assert any("delete from preview_cards" in s for s in stmts), (
            "Stage 2: the preview row must be DELETED on mint — leaving it "
            "behind would let it keep showing up in the home ticker"
        )

    def test_does_not_upsert_preview_row_anymore(self):
        """The Stage 1 dual-write UPSERT into preview_cards is gone."""
        card = self._build_polystars_card()
        cursor, _conn = self._run_promote(card)
        stmts = self._statements(cursor)
        assert not any(
            "insert into preview_cards" in s for s in stmts
        ), "Stage 2: the Stage 1 UPSERT into preview_cards must be gone"

    def test_commits_on_success(self):
        card = self._build_polystars_card()
        _cursor, conn = self._run_promote(card)
        assert conn.commit.called
        assert not conn.rollback.called

    def test_noop_when_slug_cannot_be_extracted(self):
        card = self._build_polystars_card()
        card["qr_payload"] = ""
        cursor, conn = self._run_promote(card)
        assert not cursor.execute.called
        assert not conn.commit.called


# ---------------------------------------------------------------------------
# promote_preview_to_claim — contract details
#
# Early-return guards, rollback semantics, and parameter-binding checks that
# go beyond the SQL-shape tests above.  The shape tests only verify WHICH
# statements are issued; these tests verify the PARAMETERS bound to them and
# the error path.
# ---------------------------------------------------------------------------


class TestPromotePreviewToClaimContractDetails:
    def _build_card(
        self,
        *,
        slug: str = "polystar-s1-0001",
        front_url: str | None = None,
        back_url: str | None = None,
    ) -> dict:
        return {
            "card_title": "Contract STAR",
            "primary_tag": "Oracle",
            "secondary_tag": "Degen",
            "pattern": "grid",
            "front_image_url": front_url if front_url is not None else f"https://r2.example.com/{slug}/front.png",
            "back_image_url": back_url if back_url is not None else f"https://r2.example.com/{slug}/back.png",
            "qr_payload": f"https://polystars.app/cards/{slug}",
        }

    def _run(
        self,
        card: dict,
        *,
        claim_id: int = 42,
        winner_row_id: int = 7,
        execute_side_effect=None,
    ):
        from scripts.polystars_card_payload import promote_preview_to_claim

        cursor = MagicMock()
        cursor.__enter__ = lambda self: self
        cursor.__exit__ = MagicMock(return_value=False)
        if execute_side_effect is not None:
            cursor.execute.side_effect = execute_side_effect

        conn = MagicMock()
        conn.cursor.return_value = cursor

        manager = MagicMock()
        manager.get_connection.return_value = conn

        promote_preview_to_claim(
            manager,
            claim_id=claim_id,
            winner_row_id=winner_row_id,
            owner_wallet="0xOWNER",
            polystars_card=card,
        )
        return cursor, conn

    # --- Early-return guards ------------------------------------------------

    def test_noop_when_front_image_url_is_empty(self):
        """Missing front image must abort before touching the DB."""
        card = self._build_card(front_url="")
        cursor, conn = self._run(card)
        assert not cursor.execute.called
        assert not conn.commit.called

    def test_noop_when_back_image_url_is_empty(self):
        """Missing back image must abort before touching the DB."""
        card = self._build_card(back_url="")
        cursor, conn = self._run(card)
        assert not cursor.execute.called
        assert not conn.commit.called

    # --- Rollback semantics -------------------------------------------------

    def test_rollback_on_db_exception_and_exception_reraised(self):
        """If cursor.execute raises, conn.rollback must be called and the
        exception must propagate so the caller can surface the failure."""
        from scripts.polystars_card_payload import promote_preview_to_claim

        cursor = MagicMock()
        cursor.__enter__ = lambda self: self
        cursor.__exit__ = MagicMock(return_value=False)
        cursor.execute.side_effect = RuntimeError("simulated DB failure")

        conn = MagicMock()
        conn.cursor.return_value = cursor
        manager = MagicMock()
        manager.get_connection.return_value = conn

        with pytest.raises(RuntimeError, match="simulated DB failure"):
            promote_preview_to_claim(
                manager,
                claim_id=1,
                winner_row_id=2,
                owner_wallet="0xOWNER",
                polystars_card=self._build_card(),
            )

        conn.rollback.assert_called_once()
        assert not conn.commit.called

    # --- UPDATE column coverage ---------------------------------------------

    def test_update_sets_all_required_columns(self):
        """Every denormalized card field must appear in the UPDATE statement."""
        card = self._build_card()
        cursor, _ = self._run(card)
        update_stmt = next(
            " ".join(str(c.args[0]).split()).lower()
            for c in cursor.execute.call_args_list
            if "update claims" in " ".join(str(c.args[0]).split()).lower()
        )
        for col in (
            "card_slug",
            "card_title",
            "front_image_url",
            "back_image_url",
            "primary_tag",
            "secondary_tag",
            "pattern",
            "winner_row_id",
            "card_payload_json",
        ):
            assert col in update_stmt, (
                f"UPDATE claims is missing column {col!r} — the /api/cards/{{slug}} "
                "endpoint reads directly from claims, so every field must be written"
            )

    # --- Parameter binding --------------------------------------------------

    def test_update_where_param_is_claim_id(self):
        """claim_id must be the final (WHERE) parameter of the UPDATE so the
        right claim row is overwritten."""
        card = self._build_card()
        cursor, _ = self._run(card, claim_id=99)
        update_call = next(
            c for c in cursor.execute.call_args_list
            if "update claims" in " ".join(str(c.args[0]).split()).lower()
        )
        params = update_call.args[1]
        assert params[-1] == 99, (
            "claim_id must be the last UPDATE parameter (bound to the WHERE id = %%s clause)"
        )

    def test_update_params_include_winner_row_id(self):
        """winner_row_id must be among the UPDATE SET params so the claims row
        is linked back to the winner that triggered the mint."""
        card = self._build_card()
        cursor, _ = self._run(card, winner_row_id=77)
        update_call = next(
            c for c in cursor.execute.call_args_list
            if "update claims" in " ".join(str(c.args[0]).split()).lower()
        )
        params = update_call.args[1]
        assert 77 in params, "winner_row_id must appear in UPDATE params"

    def test_delete_parameter_is_winner_row_id(self):
        """The preview row must be deleted by winner_row_id — using any other
        column would leave the wrong rows in the ticker buffer."""
        card = self._build_card()
        cursor, _ = self._run(card, winner_row_id=55)
        delete_call = next(
            c for c in cursor.execute.call_args_list
            if "delete from preview_cards" in " ".join(str(c.args[0]).split()).lower()
        )
        params = delete_call.args[1]
        assert params[0] == 55, (
            "winner_row_id must be the DELETE filter parameter — using claim_id "
            "instead would leave orphaned preview rows in the home ticker"
        )

    def test_card_payload_json_serialized_as_json_string(self):
        """polystars_card must be serialized to a JSON string before binding;
        passing the raw dict would cause a psycopg2 type error at runtime."""
        import json

        card = self._build_card()
        cursor, _ = self._run(card)
        update_call = next(
            c for c in cursor.execute.call_args_list
            if "update claims" in " ".join(str(c.args[0]).split()).lower()
        )
        params = update_call.args[1]
        json_params = [p for p in params if isinstance(p, str) and p.startswith("{")]
        assert json_params, "card_payload_json must be a JSON-serialized string in params"
        parsed = json.loads(json_params[0])
        assert parsed["card_title"] == card["card_title"]
        assert parsed["primary_tag"] == card["primary_tag"]


# ---------------------------------------------------------------------------
# _remote_image_to_data_uri
# ---------------------------------------------------------------------------

def _mock_urlopen(content=b"fake_image_bytes", content_type="image/png"):
    mock_resp = MagicMock()
    mock_resp.read.return_value = content
    mock_resp.headers.get_content_type.return_value = content_type
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


class TestRemoteImageToDataUri:
    def test_empty_string_returned_as_is(self):
        result = _remote_image_to_data_uri("", timeout_seconds=5)
        assert result == ""

    def test_none_coerced_to_empty_returned_as_is(self):
        result = _remote_image_to_data_uri(None, timeout_seconds=5)
        assert result == ""

    def test_existing_data_uri_returned_as_is(self):
        data_uri = "data:image/png;base64,abc123"
        assert _remote_image_to_data_uri(data_uri, timeout_seconds=5) == data_uri

    def test_relative_url_returned_as_is(self):
        url = "/static/images/card.png"
        assert _remote_image_to_data_uri(url, timeout_seconds=5) == url

    def test_http_url_fetched_and_encoded(self):
        raw = b"imagebytes"
        expected_b64 = base64.b64encode(raw).decode("ascii")
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(raw, "image/jpeg")):
            result = _remote_image_to_data_uri("http://example.com/img.jpg", timeout_seconds=5)
        assert result == f"data:image/jpeg;base64,{expected_b64}"

    def test_https_url_fetched_and_encoded(self):
        raw = b"pngdata"
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(raw, "image/png")):
            result = _remote_image_to_data_uri("https://cdn.example.com/img.png", timeout_seconds=5)
        assert result.startswith("data:image/png;base64,")

    def test_empty_response_raises(self):
        with (
            patch("urllib.request.urlopen", return_value=_mock_urlopen(b"", "image/png")),
            pytest.raises(ValueError, match="empty"),
        ):
            _remote_image_to_data_uri("https://example.com/img.png", timeout_seconds=5)

    def test_content_type_included_in_data_uri(self):
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(b"gif", "image/gif")):
            result = _remote_image_to_data_uri("https://example.com/img.gif", timeout_seconds=5)
        assert "image/gif" in result


# ---------------------------------------------------------------------------
# _slug_from_qr_payload
# ---------------------------------------------------------------------------

class TestSlugFromQrPayload:
    def test_none_returns_none(self):
        assert _slug_from_qr_payload(None) is None

    def test_empty_string_returns_none(self):
        assert _slug_from_qr_payload("") is None

    def test_full_url_extracts_last_segment(self):
        slug = _slug_from_qr_payload("https://polystars.app/cards/my-card-slug-abc123")
        assert slug == "my-card-slug-abc123"

    def test_url_with_trailing_slash_still_extracts(self):
        slug = _slug_from_qr_payload("https://polystars.app/cards/my-slug/")
        assert slug == "my-slug"

    def test_bare_slug_returned(self):
        slug = _slug_from_qr_payload("just-a-slug")
        assert slug == "just-a-slug"

    def test_whitespace_string_returns_none(self):
        assert _slug_from_qr_payload("   ") is None
