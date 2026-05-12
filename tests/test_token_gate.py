"""
Tests for the token-holder mint gate in ``user_web_backend.main``.

The gate lets a wallet with no Polymarket trader rank mint anyway if it holds
at least ``TOKEN_GATE_MIN_BALANCE`` whole tokens of the PolyStars *project*
ERC-20 token (``0x9e68096675578CCcf6eb7AD01350f731DDe633eD``, "POLYSTARS",
18 decimals) on Ethereum mainnet — NOT the NFT collection contract.

The deterministic tests mock the on-chain ``balanceOf`` call. A separate
opt-in test does a *live* RPC read against ``EVM_RPC_URL`` and asserts the
known whale wallet ``0xA7AC0001F6F2D87c04580B878f0Bec9B49F9F2E6`` still clears
the threshold (it held ~2,004,353 POLYSTARS at the time this test was written).
"""

import os
import unittest.mock as mock

import pytest

# Canonical example wallet used throughout these tests: a real mainnet holder
# of >> 50,000 POLYSTARS.
WHALE_WALLET = "0xA7AC0001F6F2D87c04580B878f0Bec9B49F9F2E6"


def _user_web_module():
    with mock.patch("scripts.data_loading_manager.DataLoadingManager._ensure_tables"):
        import user_web_backend.main as m
    return m


def _fake_contract(balance_raw: int | Exception):
    """A stand-in for a web3 contract whose ``balanceOf(addr).call()`` returns
    ``balance_raw`` (or raises it, if an Exception instance is passed)."""
    call = mock.MagicMock()
    if isinstance(balance_raw, Exception):
        call.call.side_effect = balance_raw
    else:
        call.call.return_value = balance_raw
    functions = mock.MagicMock()
    functions.balanceOf.return_value = call
    contract = mock.MagicMock()
    contract.functions = functions
    return contract


@pytest.fixture()
def m():
    module = _user_web_module()
    # Each test starts with an empty per-wallet balance cache.
    module._token_gate_balance_cache.clear()
    yield module
    module._token_gate_balance_cache.clear()


class TestWalletHoldsGateToken:
    def test_threshold_is_fifty_thousand_whole_tokens_18dp(self, m):
        assert m.TOKEN_GATE_DECIMALS == 18
        assert m.TOKEN_GATE_MIN_BALANCE_RAW == 50_000 * 10 ** 18
        # The configured contract is the project token, not the NFT collection.
        assert m.TOKEN_GATE_CONTRACT_ADDRESS.lower() == "0x9e68096675578cccf6eb7ad01350f731dde633ed"

    def test_balance_above_threshold_qualifies(self, m):
        with mock.patch.object(m, "_get_token_gate_contract", return_value=_fake_contract(2_004_353 * 10 ** 18)):
            assert m._wallet_holds_gate_token(WHALE_WALLET) is True

    def test_balance_exactly_at_threshold_qualifies(self, m):
        with mock.patch.object(m, "_get_token_gate_contract", return_value=_fake_contract(50_000 * 10 ** 18)):
            assert m._wallet_holds_gate_token(WHALE_WALLET) is True

    def test_balance_one_wei_below_threshold_does_not_qualify(self, m):
        with mock.patch.object(m, "_get_token_gate_contract", return_value=_fake_contract(50_000 * 10 ** 18 - 1)):
            assert m._wallet_holds_gate_token(WHALE_WALLET) is False

    def test_zero_balance_does_not_qualify(self, m):
        with mock.patch.object(m, "_get_token_gate_contract", return_value=_fake_contract(0)):
            assert m._wallet_holds_gate_token("0x000000000000000000000000000000000000dEaD") is False

    def test_contract_not_configured_returns_false(self, m):
        with mock.patch.object(m, "_get_token_gate_contract", return_value=None):
            assert m._wallet_holds_gate_token(WHALE_WALLET) is False

    def test_rpc_failure_returns_false(self, m):
        with mock.patch.object(m, "_get_token_gate_contract", return_value=_fake_contract(RuntimeError("rpc down"))):
            assert m._wallet_holds_gate_token(WHALE_WALLET) is False

    def test_result_is_cached_per_wallet(self, m):
        contract = _fake_contract(2_004_353 * 10 ** 18)
        with mock.patch.object(m, "_get_token_gate_contract", return_value=contract) as get_contract:
            assert m._wallet_holds_gate_token(WHALE_WALLET) is True
            assert m._wallet_holds_gate_token(WHALE_WALLET) is True
        # Second call must have been served from the cache (TTL > 0 by default).
        assert get_contract.call_count == 1
        contract.functions.balanceOf.assert_called_once()

    def test_case_insensitive_wallet_caching(self, m):
        contract = _fake_contract(2_004_353 * 10 ** 18)
        with mock.patch.object(m, "_get_token_gate_contract", return_value=contract) as get_contract:
            assert m._wallet_holds_gate_token(WHALE_WALLET.lower()) is True
            assert m._wallet_holds_gate_token(WHALE_WALLET.upper()) is True
        assert get_contract.call_count == 1


def test_live_whale_wallet_clears_threshold(m):
    """Live sanity check against mainnet: the known whale wallet still holds
    >= 50,000 POLYSTARS, so the token gate would let it mint without a rank.

    Uses ``EVM_RPC_URL`` (loaded from ``.env`` at import time). Skipped when
    that points at a non-mainnet RPC or is unset — there the token simply
    isn't deployed and the gate degrades to "rank only".
    """
    rpc_url = (m.TOKEN_GATE_RPC_URL or os.environ.get("EVM_RPC_URL", "")).strip()
    if not rpc_url:
        pytest.skip("EVM_RPC_URL not configured — live token-gate test skipped")
    # Point the gate at the live RPC and drop the lazily-built Web3 client so
    # it is rebuilt against EVM_RPC_URL.
    m.TOKEN_GATE_RPC_URL = rpc_url
    m._token_gate_w3 = None
    m._token_gate_balance_cache.clear()
    assert m._wallet_holds_gate_token(WHALE_WALLET) is True
