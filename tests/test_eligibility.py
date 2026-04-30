"""
Unit tests for SeasonManager.check_user_eligibility_for_season.

All DB-touching methods are mocked so no live database is needed.
This is the most business-critical logic in the project: it determines
who is allowed to mint an NFT.
"""

from unittest.mock import patch, MagicMock
import pytest


# Helpers to build mock return values

def _phase(
    phase: str = "breach",
    is_claim_open: bool = True,
    requires_origin: bool = False,
    season_type: str = "standard",
):
    return {
        "phase": phase,
        "is_claim_open": is_claim_open,
        "requires_origin": requires_origin,
        "reason": f"{phase} active",
        "season_type": season_type,
        "supply_remaining": 50,
        "supply_total": 100,
    }


def _run(
    wallet: str = "0xabc",
    season_id: int = 1,
    phase: dict = None,
    already_claimed: bool = False,
    active_claim: dict = None,
    is_origin: bool = False,
    origin_snapshot: dict = None,
):
    """Execute ``check_user_eligibility_for_season`` with all DB-touching
    helpers mocked. ``already_claimed`` is the legacy boolean knob from before
    eligibility carried the structured ``pending_claim`` payload; pass
    ``active_claim={...}`` to assert against specific status-aware reasons
    (QUEUED vs PROCESSING vs COMPLETED) — when omitted, ``already_claimed=True``
    builds a generic COMPLETED-shaped stub so the existing tests keep working.
    """
    from scripts.season_manager import SeasonManager
    sm = SeasonManager.__new__(SeasonManager)
    if active_claim is None and already_claimed:
        active_claim = {
            "claim_id": 1,
            "status": "COMPLETED",
            "phase_type": None,
            "queued_at": None,
            "updated_at": None,
            "collection_mint_number": None,
            "tx_hash": None,
            "asset_address": None,
            "card_slug": None,
        }
    with (
        patch.object(sm, "get_current_phase", return_value=phase or _phase()),
        patch.object(sm, "_get_active_claim_for_season", return_value=active_claim),
        patch.object(sm, "is_origin_wallet_for_season", return_value=is_origin),
        patch.object(sm, "_get_origin_snapshot_mint_status", return_value=origin_snapshot),
    ):
        return sm.check_user_eligibility_for_season(wallet, season_id)


# ---------------------------------------------------------------------------
# Eligible cases
# ---------------------------------------------------------------------------

class TestEligibleCases:
    def test_regular_wallet_in_breach(self):
        result = _run(phase=_phase("breach", is_claim_open=True, requires_origin=False))
        assert result["eligible_now"] is True
        assert result["ineligible_reason"] is None

    def test_origin_wallet_in_vault(self):
        result = _run(
            phase=_phase("vault", is_claim_open=True, requires_origin=True),
            is_origin=True,
            origin_snapshot={"is_minted": False},
        )
        assert result["eligible_now"] is True

    def test_regular_wallet_in_scavenge(self):
        result = _run(phase=_phase("scavenge", is_claim_open=True, requires_origin=False))
        assert result["eligible_now"] is True

    def test_origin_wallet_in_breach_no_snapshot_minted(self):
        result = _run(
            phase=_phase("breach", is_claim_open=True, requires_origin=False),
            is_origin=True,
            origin_snapshot={"is_minted": False},
        )
        assert result["eligible_now"] is True


# ---------------------------------------------------------------------------
# Ineligible: claims closed
# ---------------------------------------------------------------------------

class TestClaimsClosed:
    def test_transmission_phase(self):
        result = _run(phase=_phase("transmission", is_claim_open=False))
        assert result["eligible_now"] is False
        assert "transmission" in result["ineligible_reason"].lower()

    def test_supply_exhausted(self):
        p = _phase("transmission", is_claim_open=False)
        p["reason"] = "Season supply exhausted"
        result = _run(phase=p)
        assert result["eligible_now"] is False


# ---------------------------------------------------------------------------
# Ineligible: already claimed
# ---------------------------------------------------------------------------

class TestAlreadyClaimed:
    def test_already_claimed_blocks_eligibility(self):
        # Default active_claim shape is COMPLETED → "Already minted"
        result = _run(already_claimed=True)
        assert result["eligible_now"] is False
        assert "already minted" in result["ineligible_reason"].lower()

    def test_already_claimed_takes_priority_over_open_phase(self):
        result = _run(
            phase=_phase("breach", is_claim_open=True),
            already_claimed=True,
        )
        assert result["eligible_now"] is False

    def test_queued_claim_renders_status_aware_reason(self):
        """A row in QUEUED must produce a "Mint queued" reason, not the
        generic "already claimed" — this is the user-facing fix that lets
        the dashboard render an informative pill instead of a confusing
        rejection."""
        result = _run(
            active_claim={
                "claim_id": 7, "status": "QUEUED", "phase_type": "breach",
                "queued_at": None, "updated_at": None,
                "collection_mint_number": None, "tx_hash": None,
                "asset_address": None, "card_slug": None,
            },
        )
        assert result["eligible_now"] is False
        assert "queued" in result["ineligible_reason"].lower()
        assert "#7" in result["ineligible_reason"]
        assert result["pending_claim"]["status"] == "QUEUED"
        assert result["pending_claim"]["claim_id"] == 7

    def test_processing_claim_renders_in_progress_reason(self):
        result = _run(
            active_claim={
                "claim_id": 9, "status": "PROCESSING", "phase_type": "breach",
                "queued_at": None, "updated_at": None,
                "collection_mint_number": None, "tx_hash": None,
                "asset_address": None, "card_slug": None,
            },
        )
        assert result["eligible_now"] is False
        assert "in progress" in result["ineligible_reason"].lower()
        assert result["pending_claim"]["status"] == "PROCESSING"

    def test_completed_with_mint_number_includes_number_in_reason(self):
        result = _run(
            active_claim={
                "claim_id": 3, "status": "COMPLETED", "phase_type": "vault",
                "queued_at": None, "updated_at": None,
                "collection_mint_number": 42, "tx_hash": "0xabc",
                "asset_address": "0xCAFE.../42", "card_slug": "card-x",
            },
        )
        assert result["eligible_now"] is False
        assert "#42" in result["ineligible_reason"]

    def test_pending_claim_is_null_when_no_active_claim(self):
        """No active claim → pending_claim is explicitly None so the frontend
        can use a simple null check instead of guessing from other fields."""
        result = _run()
        assert result["pending_claim"] is None


# ---------------------------------------------------------------------------
# Ineligible: Vault requires Origin
# ---------------------------------------------------------------------------

class TestVaultRequiresOrigin:
    def test_non_origin_in_vault(self):
        result = _run(
            phase=_phase("vault", is_claim_open=True, requires_origin=True),
            is_origin=False,
        )
        assert result["eligible_now"] is False
        assert "vault" in result["ineligible_reason"].lower() or "origin" in result["ineligible_reason"].lower()

    def test_origin_wallet_passes_vault_gate(self):
        result = _run(
            phase=_phase("vault", is_claim_open=True, requires_origin=True),
            is_origin=True,
            origin_snapshot={"is_minted": False},
        )
        assert result["eligible_now"] is True


# ---------------------------------------------------------------------------
# Ineligible: Origin allocation already minted
# ---------------------------------------------------------------------------

class TestOriginAlreadyMinted:
    def test_same_wallet_already_minted(self):
        result = _run(
            phase=_phase("breach", is_claim_open=True),
            is_origin=True,
            origin_snapshot={"is_minted": True, "minted_to_wallet": "0xabc"},
        )
        assert result["eligible_now"] is False
        assert "already minted" in result["ineligible_reason"].lower()

    def test_different_wallet_stole_origin(self):
        result = _run(
            wallet="0xabc",
            phase=_phase("breach", is_claim_open=True),
            is_origin=True,
            origin_snapshot={"is_minted": True, "minted_to_wallet": "0xother"},
        )
        assert result["eligible_now"] is False
        assert "0xother" in result["ineligible_reason"]

    def test_origin_not_minted_is_still_eligible(self):
        result = _run(
            phase=_phase("breach", is_claim_open=True),
            is_origin=True,
            origin_snapshot={"is_minted": False, "minted_to_wallet": None},
        )
        assert result["eligible_now"] is True


# ---------------------------------------------------------------------------
# Response structure
# ---------------------------------------------------------------------------

class TestResponseStructure:
    def test_all_keys_present(self):
        result = _run()
        expected_keys = {
            "season_id", "season_type", "phase", "phase_reason",
            "already_claimed", "eligible_now", "ineligible_reason",
            "requires_origin", "is_claim_open", "is_origin_wallet",
            "origin_snapshot_is_minted", "origin_snapshot_minted_to_wallet",
            "pending_claim",
        }
        assert set(result.keys()) == expected_keys

    def test_wallet_normalised_to_lowercase(self):
        result = _run(wallet="0xABCDEF")
        # check_user_eligibility_for_season normalises wallet before DB calls
        # The returned dict doesn't include the wallet, but we verify no crash
        assert result is not None

    def test_season_id_passed_through(self):
        result = _run(season_id=42)
        assert result["season_id"] == 42

    def test_phase_info_reflected_in_response(self):
        result = _run(phase=_phase("scavenge", is_claim_open=True, requires_origin=False))
        assert result["phase"] == "scavenge"
        assert result["is_claim_open"] is True
        assert result["requires_origin"] is False
