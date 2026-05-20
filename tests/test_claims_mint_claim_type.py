"""
Unit tests for the operator-chosen card type ("auto" | "origin" | "looter")
on the admin mint-queue path (``ClaimsMintMixin.run_queue_mint_request``).

The DB layer is fully stubbed — ``_allocate_for_origin`` / ``_allocate_for_looter``
/ ``_insert_queued_claim`` / ``_resolve_*`` are replaced so the test exercises
only the branching logic introduced by the ``claim_type`` field, not psycopg2.
"""

from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest

import admin_backend.claims_mint as claims_mint
from admin_backend.claims_mint import (
    ClaimsMintMixin,
    MintClaimRequest,
    ParticipantAllocation,
)

# A valid EVM-shaped address (lower-case; Web3 will checksum it internally).
_WALLET = "0x" + "1" * 40
_RECIPIENT = "0x" + "2" * 40


@pytest.fixture(autouse=True)
def _stub_polymarket_profile(monkeypatch):
    """Keep these unit tests offline: the insert path now best-effort fetches
    the proxy_wallet's Polymarket profile, which would otherwise hit the
    network. Stub it to a deterministic identity."""
    monkeypatch.setattr(
        claims_mint,
        "fetch_proxy_profile_identity",
        lambda proxy_wallet: ("sat0shi", "Satoshi"),
    )


def _origin_alloc() -> ParticipantAllocation:
    return ParticipantAllocation(
        proxy_wallet="0xproxyorigin",
        event_id="evt-1",
        event_slug="evt-1-slug",
        claim_type="origin",
        snapshot={"proxy_wallet": "0xproxyorigin", "event_id": "evt-1",
                  "event_slug": "evt-1-slug", "archetype": "INSIDER"},
    )


def _looter_alloc() -> ParticipantAllocation:
    return ParticipantAllocation(
        proxy_wallet="0xproxylooter",
        event_id="evt-9",
        event_slug="evt-9-slug",
        claim_type="looter",
        snapshot={"proxy_wallet": "0xproxylooter", "event_id": "evt-9",
                  "event_slug": "evt-9-slug", "archetype": "PASSENGER"},
    )


class _FakeService(ClaimsMintMixin):
    """Minimal host satisfying the mixin's collaborator contract."""

    def __init__(self, *, wallet_is_origin: bool):
        self._wallet_is_origin = wallet_is_origin
        self.origin_calls = 0
        self.looter_calls = 0
        self.inserted: List[Dict[str, Any]] = []
        self.cleared_cache = 0

        conn = MagicMock()
        conn.cursor.return_value.__enter__ = lambda s: MagicMock()
        conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        self.manager = MagicMock()
        self.manager.get_connection.return_value = conn

    # --- collaborators the mixin expects -----------------------------------
    def clear_wallets_cache(self) -> None:
        self.cleared_cache += 1

    def fmt_dt(self, value: Any) -> str:  # pragma: no cover - unused here
        return str(value)

    def get_claim_phase_enum_values(self) -> List[str]:
        return ["breach", "vault", "scavenge"]

    # --- DB-touching internals, stubbed ------------------------------------
    def _allocate_for_origin(self, cursor: Any, wallet: str, season_id: int):
        self.origin_calls += 1
        return _origin_alloc() if self._wallet_is_origin else None

    def _allocate_for_looter(self, cursor: Any, season_id: int) -> ParticipantAllocation:
        self.looter_calls += 1
        return _looter_alloc()

    def _resolve_event_card_meta(self, cursor: Any, event_id, event_slug) -> Dict[str, Optional[str]]:
        return {"primary_tag": None, "recurrence": None}

    def _resolve_season_meta(self, cursor: Any, season_id: int) -> Dict[str, Any]:
        return {"season_type": "genesis", "season_number": 0}

    def _insert_queued_claim(self, cursor: Any, **kwargs: Any) -> Dict[str, Any]:
        self.inserted.append(kwargs)
        return {"claim_id": 1, "collection_mint_number": 1}


def _req(**overrides: Any) -> MintClaimRequest:
    base: Dict[str, Any] = dict(
        wallet=_WALLET,
        recipient_address=_RECIPIENT,
        season_id=7,
        phase="breach",
        auto_phase=False,  # skip phase auto-detection (no DB)
    )
    base.update(overrides)
    return MintClaimRequest(**base)


# ---------------------------------------------------------------------------
# claim_type == "looter"
# ---------------------------------------------------------------------------

def test_looter_choice_skips_origin_allocation_even_for_origin_wallet():
    svc = _FakeService(wallet_is_origin=True)  # wallet *is* an origin...
    result = svc.run_queue_mint_request(_req(claim_type="looter"))

    assert result["status"] == "queued"
    assert result["claim_type"] == "looter"
    assert svc.origin_calls == 0, "looter choice must not consult the participants partition"
    assert svc.looter_calls == 1
    # The queued row carries the looter allocation's snapshot, not the wallet's own.
    assert svc.inserted[0]["allocation"].claim_type == "looter"
    assert svc.inserted[0]["phase"] == "breach"
    # The best-effort Polymarket identity is threaded into the insert.
    assert svc.inserted[0]["x_username"] == "sat0shi"
    assert svc.inserted[0]["profile_name"] == "Satoshi"
    assert svc.cleared_cache == 1


def test_looter_choice_works_for_non_origin_wallet():
    svc = _FakeService(wallet_is_origin=False)
    result = svc.run_queue_mint_request(_req(claim_type="looter"))
    assert result["claim_type"] == "looter"
    assert svc.origin_calls == 0
    assert svc.looter_calls == 1


def test_looter_choice_rejected_in_vault_phase():
    svc = _FakeService(wallet_is_origin=True)
    with pytest.raises(ValueError, match="[Vv]ault.*Origins-only"):
        svc.run_queue_mint_request(_req(claim_type="looter", phase="vault"))
    assert svc.looter_calls == 0
    assert svc.origin_calls == 0


# ---------------------------------------------------------------------------
# claim_type == "origin"
# ---------------------------------------------------------------------------

def test_origin_choice_for_origin_wallet_picks_own_row():
    svc = _FakeService(wallet_is_origin=True)
    result = svc.run_queue_mint_request(_req(claim_type="origin"))
    assert result["claim_type"] == "origin"
    assert svc.origin_calls == 1
    assert svc.looter_calls == 0, "forced origin must never fall back to the looter pool"


def test_origin_choice_for_non_origin_wallet_raises():
    svc = _FakeService(wallet_is_origin=False)
    with pytest.raises(ValueError, match="not an Origin"):
        svc.run_queue_mint_request(_req(claim_type="origin"))
    assert svc.looter_calls == 0


# ---------------------------------------------------------------------------
# validation + back-compat
# ---------------------------------------------------------------------------

def test_invalid_claim_type_rejected():
    svc = _FakeService(wallet_is_origin=True)
    with pytest.raises(ValueError, match="Invalid claim_type"):
        svc.run_queue_mint_request(_req(claim_type="banana"))


def test_default_auto_uses_origin_when_available():
    svc = _FakeService(wallet_is_origin=True)
    result = svc.run_queue_mint_request(_req())  # claim_type defaults to "auto"
    assert result["claim_type"] == "origin"
    assert svc.origin_calls == 1
    assert svc.looter_calls == 0


def test_default_auto_falls_back_to_looter_for_non_origin():
    svc = _FakeService(wallet_is_origin=False)
    result = svc.run_queue_mint_request(_req())
    assert result["claim_type"] == "looter"
    assert svc.origin_calls == 1
    assert svc.looter_calls == 1
