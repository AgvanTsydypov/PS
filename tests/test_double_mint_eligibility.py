"""
Unit tests for SeasonManager.check_user_eligibility (Double Mint logic).

This tests the top-level aggregation that combines genesis and standard stream
eligibility into a single response including the double_mint section.
All DB calls are mocked.
"""

from unittest.mock import patch, MagicMock
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _stream_elig(
    season_id=1,
    season_type="standard",
    phase="breach",
    eligible_now=True,
    already_claimed=False,
    ineligible_reason=None,
    requires_origin=False,
    is_claim_open=True,
    is_origin_wallet=False,
    origin_snapshot_is_minted=False,
    origin_snapshot_minted_to_wallet=None,
):
    return {
        "season_id": season_id,
        "season_type": season_type,
        "phase": phase,
        "phase_reason": f"{phase} active",
        "already_claimed": already_claimed,
        "eligible_now": eligible_now,
        "ineligible_reason": ineligible_reason,
        "requires_origin": requires_origin,
        "is_claim_open": is_claim_open,
        "is_origin_wallet": is_origin_wallet,
        "origin_snapshot_is_minted": origin_snapshot_is_minted,
        "origin_snapshot_minted_to_wallet": origin_snapshot_minted_to_wallet,
    }


def _run(
    wallet="0xAbC",
    is_origin=False,
    genesis_season=None,
    standard_season=None,
    genesis_elig=None,
    standard_elig=None,
):
    if genesis_season is None:
        genesis_season = {"id": 1}
    if standard_season is None:
        standard_season = {"id": 2}
    if genesis_elig is None:
        genesis_elig = _stream_elig(season_id=1, season_type="genesis")
    if standard_elig is None:
        standard_elig = _stream_elig(season_id=2, season_type="standard")

    from scripts.season_manager import SeasonManager
    sm = SeasonManager.__new__(SeasonManager)

    def get_season(season_type):
        return genesis_season if season_type == "genesis" else standard_season

    def get_eligibility(wallet_address, season_id):
        gen_id = (genesis_season or {}).get("id") if genesis_season else None
        if season_id == gen_id:
            return genesis_elig
        return standard_elig

    with (
        patch.object(sm, "is_origin_wallet", return_value=is_origin),
        patch.object(sm, "_get_current_season_by_type", side_effect=get_season),
        patch.object(sm, "check_user_eligibility_for_season", side_effect=get_eligibility),
    ):
        return sm.check_user_eligibility(wallet)


# ---------------------------------------------------------------------------
# Response structure
# ---------------------------------------------------------------------------

class TestResponseStructure:
    def test_all_top_level_keys_present(self):
        result = _run()
        assert set(result.keys()) == {
            "wallet_address", "is_origin_wallet", "genesis", "standard", "double_mint"
        }

    def test_double_mint_keys_present(self):
        dm = _run()["double_mint"]
        assert set(dm.keys()) == {
            "can_claim_genesis", "can_claim_standard", "can_claim_both_now"
        }

    def test_wallet_normalized_to_lowercase(self):
        result = _run(wallet="0xABCDEF")
        assert result["wallet_address"] == "0xabcdef"

    def test_is_origin_wallet_reflected(self):
        result = _run(is_origin=True)
        assert result["is_origin_wallet"] is True

    def test_genesis_stream_included(self):
        result = _run()
        assert result["genesis"]["season_type"] == "genesis"

    def test_standard_stream_included(self):
        result = _run()
        assert result["standard"]["season_type"] == "standard"


# ---------------------------------------------------------------------------
# Both streams eligible
# ---------------------------------------------------------------------------

class TestBothEligible:
    def test_can_claim_both_when_both_eligible(self):
        result = _run(
            genesis_elig=_stream_elig(season_id=1, season_type="genesis", eligible_now=True),
            standard_elig=_stream_elig(season_id=2, season_type="standard", eligible_now=True),
        )
        dm = result["double_mint"]
        assert dm["can_claim_genesis"] is True
        assert dm["can_claim_standard"] is True
        assert dm["can_claim_both_now"] is True


# ---------------------------------------------------------------------------
# Only one stream eligible
# ---------------------------------------------------------------------------

class TestSingleStreamEligible:
    def test_only_genesis_eligible(self):
        result = _run(
            genesis_elig=_stream_elig(season_id=1, eligible_now=True),
            standard_elig=_stream_elig(season_id=2, eligible_now=False, ineligible_reason="closed"),
        )
        dm = result["double_mint"]
        assert dm["can_claim_genesis"] is True
        assert dm["can_claim_standard"] is False
        assert dm["can_claim_both_now"] is False

    def test_only_standard_eligible(self):
        result = _run(
            genesis_elig=_stream_elig(season_id=1, eligible_now=False, ineligible_reason="claimed"),
            standard_elig=_stream_elig(season_id=2, eligible_now=True),
        )
        dm = result["double_mint"]
        assert dm["can_claim_genesis"] is False
        assert dm["can_claim_standard"] is True
        assert dm["can_claim_both_now"] is False


# ---------------------------------------------------------------------------
# Neither eligible
# ---------------------------------------------------------------------------

class TestNeitherEligible:
    def test_neither_eligible_both_false(self):
        result = _run(
            genesis_elig=_stream_elig(season_id=1, eligible_now=False, ineligible_reason="closed"),
            standard_elig=_stream_elig(season_id=2, eligible_now=False, ineligible_reason="claimed"),
        )
        dm = result["double_mint"]
        assert dm["can_claim_genesis"] is False
        assert dm["can_claim_standard"] is False
        assert dm["can_claim_both_now"] is False


# ---------------------------------------------------------------------------
# Missing seasons
# ---------------------------------------------------------------------------

class TestMissingSeasons:
    def test_no_genesis_season_shows_no_active(self):
        from scripts.season_manager import SeasonManager
        sm = SeasonManager.__new__(SeasonManager)

        def get_season(season_type):
            return None if season_type == "genesis" else {"id": 2}

        std_elig = _stream_elig(season_id=2, season_type="standard", eligible_now=True)

        with (
            patch.object(sm, "is_origin_wallet", return_value=False),
            patch.object(sm, "_get_current_season_by_type", side_effect=get_season),
            patch.object(sm, "check_user_eligibility_for_season", return_value=std_elig),
        ):
            result = sm.check_user_eligibility("0xabc")

        assert result["genesis"]["eligible_now"] is False
        assert result["genesis"]["ineligible_reason"] == "No active season"
        assert result["double_mint"]["can_claim_genesis"] is False
        assert result["double_mint"]["can_claim_standard"] is True

    def test_no_standard_season_shows_no_active(self):
        from scripts.season_manager import SeasonManager
        sm = SeasonManager.__new__(SeasonManager)

        def get_season(season_type):
            return {"id": 1} if season_type == "genesis" else None

        gen_elig = _stream_elig(season_id=1, season_type="genesis", eligible_now=True)

        with (
            patch.object(sm, "is_origin_wallet", return_value=False),
            patch.object(sm, "_get_current_season_by_type", side_effect=get_season),
            patch.object(sm, "check_user_eligibility_for_season", return_value=gen_elig),
        ):
            result = sm.check_user_eligibility("0xabc")

        assert result["standard"]["eligible_now"] is False
        assert result["standard"]["ineligible_reason"] == "No active season"
        assert result["double_mint"]["can_claim_both_now"] is False

    def test_both_seasons_missing_nothing_claimable(self):
        from scripts.season_manager import SeasonManager
        sm = SeasonManager.__new__(SeasonManager)

        with (
            patch.object(sm, "is_origin_wallet", return_value=False),
            patch.object(sm, "_get_current_season_by_type", return_value=None),
            patch.object(sm, "check_user_eligibility_for_season") as mock_elig,
        ):
            result = sm.check_user_eligibility("0xabc")

        mock_elig.assert_not_called()
        dm = result["double_mint"]
        assert dm["can_claim_genesis"] is False
        assert dm["can_claim_standard"] is False
        assert dm["can_claim_both_now"] is False

    def test_missing_season_stream_has_none_season_id(self):
        from scripts.season_manager import SeasonManager
        sm = SeasonManager.__new__(SeasonManager)

        with (
            patch.object(sm, "is_origin_wallet", return_value=False),
            patch.object(sm, "_get_current_season_by_type", return_value=None),
        ):
            result = sm.check_user_eligibility("0xwallet")

        assert result["genesis"]["season_id"] is None
        assert result["standard"]["season_id"] is None


# ---------------------------------------------------------------------------
# Already claimed in one stream
# ---------------------------------------------------------------------------

class TestAlreadyClaimed:
    def test_genesis_already_claimed_standard_open(self):
        result = _run(
            genesis_elig=_stream_elig(
                season_id=1, eligible_now=False, already_claimed=True,
                ineligible_reason="User already claimed"
            ),
            standard_elig=_stream_elig(season_id=2, eligible_now=True),
        )
        assert result["double_mint"]["can_claim_genesis"] is False
        assert result["double_mint"]["can_claim_standard"] is True
        assert result["genesis"]["already_claimed"] is True

    def test_both_already_claimed_neither_eligible(self):
        result = _run(
            genesis_elig=_stream_elig(season_id=1, eligible_now=False, already_claimed=True),
            standard_elig=_stream_elig(season_id=2, eligible_now=False, already_claimed=True),
        )
        assert result["double_mint"]["can_claim_both_now"] is False


# ---------------------------------------------------------------------------
# Origin wallet checks
# ---------------------------------------------------------------------------

class TestOriginWallet:
    def test_origin_flag_true_reflected(self):
        result = _run(is_origin=True)
        assert result["is_origin_wallet"] is True

    def test_non_origin_flag_false(self):
        result = _run(is_origin=False)
        assert result["is_origin_wallet"] is False
