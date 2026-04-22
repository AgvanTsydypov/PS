"""
Unit tests for pure helper functions in scripts/simulate_user_generated_cards_batch.py.

Covers: _anomaly_balance_tier, _normalize_proxy_wallet_for_compare,
_resolve_card_claim_type, _showcase_pick_from_db_row,
_select_diverse_winner_row_plan, _build_card_payload_from_source_row.
"""

import pytest

from scripts.simulate_user_generated_cards_batch import (
    _anomaly_balance_tier,
    _normalize_proxy_wallet_for_compare,
    _resolve_card_claim_type,
    _showcase_pick_from_db_row,
    _select_diverse_winner_row_plan,
    _build_card_payload_from_source_row,
    _ShowcasePick,
    _SHOWCASE_CANDIDATE_BODY,
)

_VALID_ADDR = "0x" + "a" * 40
_OTHER_ADDR = "0x" + "b" * 40


# ---------------------------------------------------------------------------
# _anomaly_balance_tier
# ---------------------------------------------------------------------------

class TestAnomalyBalanceTier:
    def test_p99_returns_p99(self):
        assert _anomaly_balance_tier("ANOMALY", "P99", "P99", "P99") == "P99"

    def test_p90_returns_p90(self):
        assert _anomaly_balance_tier("ANOMALY", "P90", "P90", "P90") == "P90"

    def test_p70_returns_p70(self):
        assert _anomaly_balance_tier("ANOMALY", "P70", "P70", "P70") == "P70"

    def test_p50_returns_p50(self):
        assert _anomaly_balance_tier("ANOMALY", "P50", "P50", "P50") == "P50"

    def test_non_anomaly_archetype_returns_none(self):
        assert _anomaly_balance_tier("SIGNAL", "P99", "P99", "P99") is None

    def test_base_tier_returns_none(self):
        # BASE is not in _ANOMALY_SUBTIER_OPTIONS
        assert _anomaly_balance_tier("ANOMALY", "BASE", "BASE", "BASE") is None

    def test_mixed_tiers_returns_none(self):
        assert _anomaly_balance_tier("ANOMALY", "P99", "P90", "P99") is None

    def test_edge_matches_yield_not_gravity_returns_none(self):
        assert _anomaly_balance_tier("ANOMALY", "P99", "P99", "P70") is None

    def test_all_same_but_two_tiers_differ_returns_none(self):
        assert _anomaly_balance_tier("ANOMALY", "P70", "P70", "P50") is None

    def test_empty_archetype_returns_none(self):
        assert _anomaly_balance_tier("", "P99", "P99", "P99") is None


# ---------------------------------------------------------------------------
# _normalize_proxy_wallet_for_compare
# ---------------------------------------------------------------------------

class TestNormalizeProxyWallet:
    def test_valid_lowercase_returned_unchanged(self):
        assert _normalize_proxy_wallet_for_compare(_VALID_ADDR) == _VALID_ADDR

    def test_valid_uppercase_returned_lowercase(self):
        upper = "0x" + "A" * 40
        lower = "0x" + "a" * 40
        assert _normalize_proxy_wallet_for_compare(upper) == lower

    def test_mixed_case_returned_lowercase(self):
        addr = "0xAbCdEfAbCdEfAbCdEfAbCdEfAbCdEfAbCdEfAbCd"
        assert _normalize_proxy_wallet_for_compare(addr) == addr.lower()

    def test_none_returns_none(self):
        assert _normalize_proxy_wallet_for_compare(None) is None

    def test_empty_string_returns_none(self):
        assert _normalize_proxy_wallet_for_compare("") is None

    def test_no_0x_prefix_returns_none(self):
        assert _normalize_proxy_wallet_for_compare("a" * 42) is None

    def test_too_short_returns_none(self):
        # 41 chars total instead of 42
        assert _normalize_proxy_wallet_for_compare("0x" + "a" * 39) is None

    def test_too_long_returns_none(self):
        # 43 chars total instead of 42
        assert _normalize_proxy_wallet_for_compare("0x" + "a" * 41) is None

    def test_non_hex_body_returns_none(self):
        addr = "0x" + "g" * 40  # 'g' is not a hex character
        assert _normalize_proxy_wallet_for_compare(addr) is None

    def test_whitespace_stripped_before_validation(self):
        addr = "  " + _VALID_ADDR + "  "
        assert _normalize_proxy_wallet_for_compare(addr) == _VALID_ADDR

    def test_exact_42_chars_with_all_hex_digits_valid(self):
        addr = "0x" + "0123456789abcdef" * 2 + "01234567"
        assert len(addr) == 42
        assert _normalize_proxy_wallet_for_compare(addr) == addr.lower()


# ---------------------------------------------------------------------------
# _resolve_card_claim_type
# ---------------------------------------------------------------------------

class TestResolveCardClaimType:
    def test_same_address_returns_origin(self):
        assert _resolve_card_claim_type(_VALID_ADDR, _VALID_ADDR) == "origin"

    def test_case_insensitive_match_returns_origin(self):
        # Keep "0x" prefix lowercase (required by the validator); vary body case
        upper_body = "0x" + "A" * 40
        lower_body = "0x" + "a" * 40
        assert _resolve_card_claim_type(upper_body, lower_body) == "origin"

    def test_different_addresses_returns_looter(self):
        assert _resolve_card_claim_type(_VALID_ADDR, _OTHER_ADDR) == "looter"

    def test_both_none_returns_looter(self):
        assert _resolve_card_claim_type(None, None) == "looter"

    def test_winner_none_returns_looter(self):
        assert _resolve_card_claim_type(None, _VALID_ADDR) == "looter"

    def test_session_none_returns_looter(self):
        assert _resolve_card_claim_type(_VALID_ADDR, None) == "looter"

    def test_invalid_winner_format_returns_looter(self):
        assert _resolve_card_claim_type("not_an_address", _VALID_ADDR) == "looter"

    def test_invalid_session_format_returns_looter(self):
        assert _resolve_card_claim_type(_VALID_ADDR, "bad") == "looter"

    def test_both_invalid_returns_looter(self):
        assert _resolve_card_claim_type("foo", "bar") == "looter"


# ---------------------------------------------------------------------------
# _showcase_pick_from_db_row
# ---------------------------------------------------------------------------

def _candidate_row(**overrides):
    row = {
        "id": 42,
        "manual_image_url": "https://example.com/card.png",
        "entry_bracket": "[0.00 - 0.20]",
        "edge": "P99",
        "yield": "P99",
        "gravity": "P99",
        "archetype_coalesced": "ANOMALY",
        "entry_cwap": 0.1,
        "total_volume": 1000.0,
        "total_pnl": 500.0,
    }
    row.update(overrides)
    return row


class TestShowcasePickFromDbRow:
    def test_valid_row_returns_showcase_pick(self):
        pick = _showcase_pick_from_db_row(_candidate_row())
        assert isinstance(pick, _ShowcasePick)

    def test_winner_row_id_set_correctly(self):
        pick = _showcase_pick_from_db_row(_candidate_row(id=99))
        assert pick.winner_row_id == 99

    def test_image_key_is_lowercased_url(self):
        row = _candidate_row(manual_image_url="https://EXAMPLE.com/IMG.PNG")
        pick = _showcase_pick_from_db_row(row)
        assert pick.image_key == "https://example.com/img.png"

    def test_missing_id_key_returns_none(self):
        row = _candidate_row()
        del row["id"]
        assert _showcase_pick_from_db_row(row) is None

    def test_non_integer_id_returns_none(self):
        assert _showcase_pick_from_db_row(_candidate_row(id="not_an_int")) is None

    def test_empty_image_url_returns_none(self):
        assert _showcase_pick_from_db_row(_candidate_row(manual_image_url="")) is None

    def test_none_image_url_returns_none(self):
        assert _showcase_pick_from_db_row(_candidate_row(manual_image_url=None)) is None

    def test_anomaly_with_matching_p99_tiers_sets_anomaly_tier(self):
        pick = _showcase_pick_from_db_row(_candidate_row())
        assert pick.anomaly_tier == "P99"

    def test_non_anomaly_archetype_has_no_anomaly_tier(self):
        # SIGNAL: entry [0.00-0.20], edge P99, yield P90 — not all equal so not ANOMALY
        row = {
            "id": 1,
            "manual_image_url": "https://example.com/1.png",
            "entry_bracket": "[0.00 - 0.20]",
            "edge": "P99",
            "yield": "P90",
            "gravity": "P50",
            "archetype_coalesced": "SIGNAL",
            "entry_cwap": 0.1,
            "total_volume": 1000.0,
            "total_pnl": 500.0,
        }
        pick = _showcase_pick_from_db_row(row)
        assert pick is not None
        assert pick.anomaly_tier is None

    def test_metrics_quad_is_4_element_tuple(self):
        pick = _showcase_pick_from_db_row(_candidate_row())
        assert len(pick.metrics_quad) == 4

    def test_archetype_resolved_from_metrics_when_coalesced_is_none(self):
        # entry [0.97-1.00], total_volume >= 5000 → EXTRACTOR
        row = {
            "id": 5,
            "manual_image_url": "https://example.com/5.png",
            "entry_bracket": "[0.97 - 1.00]",
            "edge": "BASE",
            "yield": "BASE",
            "gravity": "BASE",
            "archetype_coalesced": None,
            "entry_cwap": 0.99,
            "total_volume": 5000.0,
            "total_pnl": 100.0,
        }
        pick = _showcase_pick_from_db_row(row)
        assert pick is not None
        assert pick.archetype == "EXTRACTOR"


# ---------------------------------------------------------------------------
# _select_diverse_winner_row_plan
# ---------------------------------------------------------------------------

def _make_candidate(wid, image_url, archetype="OPERATOR", entry_bracket="[0.80 - 0.97]"):
    return {
        "id": wid,
        "manual_image_url": image_url,
        "entry_bracket": entry_bracket,
        "edge": "P50",
        "yield": "P50",
        "gravity": "P50",
        "archetype_coalesced": archetype,
        "entry_cwap": None,
        "total_volume": None,
        "total_pnl": None,
    }


class TestSelectDiverseWinnerRowPlan:
    def test_empty_candidates_returns_empty(self):
        assert _select_diverse_winner_row_plan([], k=5) == []

    def test_k_zero_returns_empty(self):
        candidates = [_make_candidate(1, "https://a.com/1.png")]
        assert _select_diverse_winner_row_plan(candidates, k=0) == []

    def test_single_candidate_k1_returns_that_id(self):
        plan = _select_diverse_winner_row_plan(
            [_make_candidate(7, "https://a.com/7.png")], k=1
        )
        assert plan == [7]

    def test_k_greater_than_pool_returns_all_unique(self):
        candidates = [_make_candidate(i, f"https://a.com/{i}.png") for i in range(3)]
        plan = _select_diverse_winner_row_plan(candidates, k=10)
        assert len(plan) == 3

    def test_plan_length_respects_k(self):
        candidates = [_make_candidate(i, f"https://a.com/{i}.png") for i in range(10)]
        plan = _select_diverse_winner_row_plan(candidates, k=4)
        assert len(plan) == 4

    def test_result_is_list_of_ints(self):
        candidates = [_make_candidate(i, f"https://a.com/{i}.png") for i in range(3)]
        plan = _select_diverse_winner_row_plan(candidates, k=3)
        assert all(isinstance(x, int) for x in plan)

    def test_duplicate_candidate_ids_deduplicated(self):
        dup = _make_candidate(5, "https://a.com/5.png")
        plan = _select_diverse_winner_row_plan([dup, dup], k=2)
        assert len(plan) == 1
        assert plan[0] == 5

    def test_unique_image_always_preferred_after_duplicate_selected(self):
        # Candidates 1 & 2 share an image; candidate 3 has a unique image.
        # In any 2-pick plan from these 3, candidate 3 must appear:
        # - If 1 or 2 is picked first → second pick must be 3 (unique image wins)
        # - If 3 is picked first → second pick is 1 or 2 (both still nu=1 at that point)
        # Either way, 3 is always in the final plan.
        same_img = "https://a.com/shared.png"
        candidates = [
            _make_candidate(1, same_img, archetype="OPERATOR"),
            _make_candidate(2, same_img, archetype="SIGNAL"),
            _make_candidate(3, "https://a.com/unique.png", archetype="AMASSER"),
        ]
        for _ in range(10):
            plan = _select_diverse_winner_row_plan(candidates, k=2)
            assert 3 in plan, "Candidate with unique image must appear in every plan"

    def test_no_duplicate_ids_in_output(self):
        candidates = [_make_candidate(i, f"https://a.com/{i}.png") for i in range(5)]
        plan = _select_diverse_winner_row_plan(candidates, k=5)
        assert len(plan) == len(set(plan))

    def test_all_returned_ids_were_in_input(self):
        candidates = [_make_candidate(i, f"https://a.com/{i}.png") for i in range(5)]
        valid_ids = {c["id"] for c in candidates}
        plan = _select_diverse_winner_row_plan(candidates, k=5)
        assert all(pid in valid_ids for pid in plan)


# ---------------------------------------------------------------------------
# _build_card_payload_from_source_row
# ---------------------------------------------------------------------------

_PROXY = "0x" + "1" * 40
_OTHER_PROXY = "0x" + "2" * 40

_EXPECTED_KEYS = {
    "season_type", "season_number", "recurrence", "claim_type",
    "image_url", "card_title", "card_lore", "primary_tag",
    "primary_tag_color", "secondary_tag", "entry_bracket", "archetype",
    "archetype_description", "archetype_math", "rarity_bracket",
    "proxy_wallet", "edge", "yield", "gravity", "border_color",
    "leaderboard_rank", "season_start_date", "season_end_date", "season_size",
}


def _source_row(**overrides):
    row = {
        "season_type": "standard",
        "season_number": 1,
        "proxy_wallet": _PROXY,
        "entry_bracket": "[0.80 - 0.97]",
        "archetype": "OPERATOR",
        "archetype_description": "Methodical participant",
        "archetype_math": "edge * gravity",
        "rarity_bracket": "R1",
        "edge": "P50",
        "yield": "P70",
        "gravity": "P50",
        "rank": 42,
        "reccurence": None,
        "manual_image_url": "https://cdn.example.com/card.png",
        "card_title": "Test Card",
        "card_lore": "Some lore text",
        "primary_tag": "CRYPTO",
        "secondary_tag": "DEFI",
        "primary_tag_hex_color": "#FF0000",
        "season_start_date": "2025-01-01",
        "season_end_date": "2025-03-31",
        "season_size": 500,
        "entry_cwap": 0.85,
        "total_volume": 10000.0,
        "total_pnl": 500.0,
    }
    row.update(overrides)
    return row


class TestBuildCardPayloadFromSourceRow:
    def test_has_all_expected_keys(self):
        payload = _build_card_payload_from_source_row(_source_row())
        assert _EXPECTED_KEYS.issubset(payload.keys())

    def test_origin_claim_when_wallets_match(self):
        payload = _build_card_payload_from_source_row(
            _source_row(), session_signin_proxy_wallet=_PROXY
        )
        assert payload["claim_type"] == "origin"

    def test_looter_claim_when_wallets_differ(self):
        payload = _build_card_payload_from_source_row(
            _source_row(), session_signin_proxy_wallet=_OTHER_PROXY
        )
        assert payload["claim_type"] == "looter"

    def test_looter_claim_when_no_session_wallet(self):
        payload = _build_card_payload_from_source_row(_source_row())
        assert payload["claim_type"] == "looter"

    def test_harvester_normalized_to_extractor(self):
        row = _source_row(
            archetype="HARVESTER",
            entry_bracket="[0.97 - 1.00]",
            total_volume=10000.0,
            total_pnl=100.0,
        )
        payload = _build_card_payload_from_source_row(row)
        assert payload["archetype"] == "EXTRACTOR"

    def test_martyr_normalized_to_icarus(self):
        row = _source_row(
            archetype="MARTYR",
            total_pnl=-100.0,
            entry_cwap=0.3,
        )
        payload = _build_card_payload_from_source_row(row)
        assert payload["archetype"] == "ICARUS"

    def test_recurrence_none_stays_none(self):
        payload = _build_card_payload_from_source_row(_source_row(reccurence=None))
        assert payload["recurrence"] is None

    def test_recurrence_empty_string_becomes_none(self):
        payload = _build_card_payload_from_source_row(_source_row(reccurence=""))
        assert payload["recurrence"] is None

    def test_recurrence_value_preserved(self):
        payload = _build_card_payload_from_source_row(_source_row(reccurence="7"))
        assert payload["recurrence"] == "7"

    def test_season_type_lowercased_in_output(self):
        payload = _build_card_payload_from_source_row(_source_row(season_type="STANDARD"))
        assert payload["season_type"] == "standard"

    def test_yield_p99_gives_gold_border_color(self):
        row = _source_row()
        row["yield"] = "P99"
        payload = _build_card_payload_from_source_row(row)
        assert payload["border_color"] == "#FFD700"

    def test_yield_base_gives_grey_border_color(self):
        row = _source_row()
        row["yield"] = "BASE"
        payload = _build_card_payload_from_source_row(row)
        assert payload["border_color"] == "#B6BBC8"

    def test_leaderboard_rank_from_row(self):
        payload = _build_card_payload_from_source_row(_source_row(rank=17))
        assert payload["leaderboard_rank"] == 17

    def test_season_start_end_dates_from_row(self):
        payload = _build_card_payload_from_source_row(
            _source_row(season_start_date="2025-01-01", season_end_date="2025-03-31")
        )
        assert payload["season_start_date"] == "2025-01-01"
        assert payload["season_end_date"] == "2025-03-31"

    def test_image_url_from_manual_image_url(self):
        payload = _build_card_payload_from_source_row(
            _source_row(manual_image_url="https://cdn.example.com/test.png")
        )
        assert payload["image_url"] == "https://cdn.example.com/test.png"


# ---------------------------------------------------------------------------
# _SHOWCASE_CANDIDATE_BODY SQL guard
#
# The admin mint flow deletes the ``preview_cards`` preview row on
# successful mint. Without an ``is_minted = FALSE`` filter on the candidate
# query, a minted winner row would become "eligible" again the moment its
# preview is removed and could be re-picked for a fresh showcase card — which
# would silently reintroduce the very overlap between showcase and minted
# Stars we are trying to eliminate. These tests pin the SQL constant so that
# a future refactor can't regress the invariant.
# ---------------------------------------------------------------------------

class TestShowcaseCandidateBodyExcludesMinted:
    def test_filters_out_minted_winner_rows(self):
        normalized = " ".join(_SHOWCASE_CANDIDATE_BODY.split()).lower()
        assert "coalesce(w.is_minted, false) = false" in normalized

    def test_still_filters_out_rows_with_existing_preview(self):
        normalized = " ".join(_SHOWCASE_CANDIDATE_BODY.split()).lower()
        assert "gc.id is null" in normalized

    def test_still_requires_manual_image(self):
        normalized = " ".join(_SHOWCASE_CANDIDATE_BODY.split()).lower()
        assert "ec.manual_image_url is not null" in normalized
        assert "btrim(ec.manual_image_url) <> ''" in normalized
