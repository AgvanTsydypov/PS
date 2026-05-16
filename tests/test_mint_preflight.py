"""
Tests for the ERC-721 recipient preflight gate in ``user_web_backend.main``.

The gate refuses ``/api/me/mint`` when ``eth_getCode`` plus a probe of
``onERC721Received`` shows the recipient cannot accept a ``_safeMint``.
Three distinct user-facing messages map to three distinct on-chain signals:

  * EIP-7702 delegation (Pectra ``0xef0100`` prefix)
  * Contract whose receiver probe reverts
  * Contract whose receiver probe returns the wrong magic value

RPC failures must fail-OPEN so a degraded EVM_RPC_URL does not block every
mint.
"""

from __future__ import annotations

import unittest.mock as mock
from typing import Any, Optional

import pytest


def _user_web_module():
    """Same import-stub trick used by ``tests/test_ip_gate.py``: prevent the
    DataLoadingManager from hitting a real DB during module import."""
    with mock.patch("scripts.data_loading_manager.DataLoadingManager._ensure_tables"):
        import user_web_backend.main as m
    return m


@pytest.fixture()
def m():
    return _user_web_module()


def _fake_w3(*, code: bytes, call_result: Optional[bytes] = None,
             call_exc: Optional[BaseException] = None,
             get_code_exc: Optional[BaseException] = None) -> Any:
    """Build a minimal Web3-shaped mock that satisfies the preflight helper.

    ``code`` is what ``w3.eth.get_code`` returns; ``call_result`` /
    ``call_exc`` control ``w3.eth.call``. Raising from ``get_code`` is also
    supported so we can exercise the fail-open path."""
    w3 = mock.MagicMock()
    if get_code_exc is not None:
        w3.eth.get_code.side_effect = get_code_exc
    else:
        w3.eth.get_code.return_value = code
    if call_exc is not None:
        w3.eth.call.side_effect = call_exc
    else:
        w3.eth.call.return_value = call_result if call_result is not None else b""
    return w3


# A valid checksum address (any address — preflight doesn't care which).
_RECIPIENT = "0x4A015d006d5A4337D099C5d0341F39E66b1B2568"


# ─────────────────────────────────────────────────────────────────────────────
# 1. Calldata shape — guards against accidental changes to the probe payload
# ─────────────────────────────────────────────────────────────────────────────
class TestProbeCalldataShape:
    """The probe calldata is hand-built rather than ABI-encoded by web3.py
    so we can avoid a contract handle. Lock its exact shape so a typo in
    the offset/length encoding would fail loudly rather than silently
    sending a malformed call (which most nodes happily decode as a revert
    and would skew preflight diagnoses)."""

    def test_selector_is_onERC721Received(self, m):
        # bytes4(keccak256("onERC721Received(address,address,uint256,bytes)"))
        assert m._ERC721_RECEIVER_SELECTOR_HEX == "150b7a02"

    def test_calldata_starts_with_selector(self, m):
        assert m._ERC721_RECEIVER_PROBE_CALLDATA.startswith(
            "0x" + m._ERC721_RECEIVER_SELECTOR_HEX
        )

    def test_calldata_length_is_selector_plus_five_words(self, m):
        # 0x + 4-byte selector + 5 * 32-byte words = 2 + 8 + 320 = 330 chars.
        assert len(m._ERC721_RECEIVER_PROBE_CALLDATA) == 2 + 8 + 5 * 64

    def test_bytes_offset_word_is_0x80(self, m):
        # Word 4 (0-indexed: operator, from, tokenId, offset, length) must
        # be 0x80 — anything else and the empty bytes blob is interpreted
        # from the wrong position.
        body = m._ERC721_RECEIVER_PROBE_CALLDATA[2 + 8:]  # strip "0x" + selector
        word4 = body[3 * 64:4 * 64]
        assert int(word4, 16) == 0x80

    def test_eip7702_magic_is_three_bytes(self, m):
        assert m._EIP7702_DELEGATION_MAGIC == b"\xef\x01\x00"


# ─────────────────────────────────────────────────────────────────────────────
# 2. _check_recipient_can_receive_nft — the decision tree
# ─────────────────────────────────────────────────────────────────────────────
class TestCheckRecipientDecisionTree:
    """One test per branch of the helper. We mock ``_mint_preflight_w3`` so
    the tests are deterministic and don't depend on EVM_RPC_URL."""

    def test_no_rpc_configured_allows_mint(self, m):
        """Fail-open: if EVM_RPC_URL isn't set, ``_mint_preflight_w3``
        returns ``None`` and the helper must NOT block — otherwise a
        misconfigured dev env locks out every mint."""
        with mock.patch.object(m, "_mint_preflight_w3", return_value=None):
            assert m._check_recipient_can_receive_nft(_RECIPIENT) is None

    def test_eoa_with_empty_code_is_allowed(self, m):
        w3 = _fake_w3(code=b"")
        with mock.patch.object(m, "_mint_preflight_w3", return_value=w3):
            assert m._check_recipient_can_receive_nft(_RECIPIENT) is None
        w3.eth.call.assert_not_called()  # no probe needed for an EOA

    def test_get_code_rpc_failure_fails_open(self, m):
        """A transient ``eth_getCode`` failure must not block legitimate
        users. The cron worker's ``estimate_gas`` is the final safety net,
        not this preflight."""
        w3 = _fake_w3(code=b"", get_code_exc=RuntimeError("rpc 503"))
        with mock.patch.object(m, "_mint_preflight_w3", return_value=w3):
            assert m._check_recipient_can_receive_nft(_RECIPIENT) is None

    def test_eip7702_delegation_returns_msg1_with_delegate(self, m):
        delegate_bytes = bytes.fromhex("535023ed14a1862444ec29d0fe07cf3092c73bc2")
        code = b"\xef\x01\x00" + delegate_bytes
        assert len(code) == 23
        w3 = _fake_w3(code=code)
        with mock.patch.object(m, "_mint_preflight_w3", return_value=w3):
            reason = m._check_recipient_can_receive_nft(_RECIPIENT)
        assert reason is not None
        assert "0x" + delegate_bytes.hex() in reason
        assert reason == m.MINT_PREFLIGHT_MSG_7702.format(
            delegate="0x" + delegate_bytes.hex()
        )
        # The probe call must NOT be made for a 7702 path — we already know
        # the answer from the code prefix and the probe would only confuse
        # logs by recording an extra revert.
        w3.eth.call.assert_not_called()

    def test_non7702_code_starting_with_ef0100_but_wrong_length_falls_through(self, m):
        """``0xef0100`` is only a delegation marker when the deployed code
        is exactly 23 bytes. A regular contract whose runtime bytecode
        happens to start with those bytes must NOT be misclassified as a
        7702 EOA — fall through to the receiver probe instead."""
        code = b"\xef\x01\x00" + b"\x00" * 50  # contract, not delegation
        w3 = _fake_w3(code=code, call_exc=RuntimeError("revert"))
        with mock.patch.object(m, "_mint_preflight_w3", return_value=w3):
            reason = m._check_recipient_can_receive_nft(_RECIPIENT)
        assert reason == m.MINT_PREFLIGHT_MSG_CONTRACT_REVERTS

    def test_contract_probe_reverts_returns_msg2(self, m):
        w3 = _fake_w3(code=b"\x60\x60\x60", call_exc=RuntimeError("execution reverted"))
        with mock.patch.object(m, "_mint_preflight_w3", return_value=w3):
            assert m._check_recipient_can_receive_nft(_RECIPIENT) == (
                m.MINT_PREFLIGHT_MSG_CONTRACT_REVERTS
            )

    def test_contract_probe_returns_wrong_magic_returns_msg3(self, m):
        """The probe answered without reverting, but the first 4 bytes are
        not the ERC-721 receiver selector. A common shape for fallback /
        proxy contracts that accept any selector but return zeros."""
        wrong_response = b"\x00" * 32
        w3 = _fake_w3(code=b"\x60\x60\x60", call_result=wrong_response)
        with mock.patch.object(m, "_mint_preflight_w3", return_value=w3):
            assert m._check_recipient_can_receive_nft(_RECIPIENT) == (
                m.MINT_PREFLIGHT_MSG_CONTRACT_BAD_RECEIVER
            )

    def test_contract_probe_returns_empty_result_returns_msg3(self, m):
        """A bare empty return is also not the magic value. Make sure we
        treat it as msg3 rather than crashing on the slice."""
        w3 = _fake_w3(code=b"\x60\x60\x60", call_result=b"")
        with mock.patch.object(m, "_mint_preflight_w3", return_value=w3):
            assert m._check_recipient_can_receive_nft(_RECIPIENT) == (
                m.MINT_PREFLIGHT_MSG_CONTRACT_BAD_RECEIVER
            )

    def test_contract_probe_returns_correct_magic_is_allowed(self, m):
        """A conforming ``IERC721Receiver`` — e.g. a Gnosis Safe with a
        receiver fallback — must NOT be blocked. The magic value is left-
        aligned in a 32-byte word, padded with zeros."""
        magic_word = bytes.fromhex("150b7a02") + b"\x00" * 28
        w3 = _fake_w3(code=b"\x60\x60\x60", call_result=magic_word)
        with mock.patch.object(m, "_mint_preflight_w3", return_value=w3):
            assert m._check_recipient_can_receive_nft(_RECIPIENT) is None


# ─────────────────────────────────────────────────────────────────────────────
# 3. Message content sanity — guards against accidental rewording that
#    would drop the actionable bits users rely on.
# ─────────────────────────────────────────────────────────────────────────────
class TestMessageContent:
    def test_msg_7702_contains_delegate_placeholder(self, m):
        # Must accept the format kwarg the helper uses.
        formatted = m.MINT_PREFLIGHT_MSG_7702.format(delegate="0xDEADBEEF")
        assert "0xDEADBEEF" in formatted
        # Two anchors the user actually needs:
        assert "EIP-7702" in formatted
        assert "compromised" in formatted

    def test_msg_contract_reverts_suggests_eoa(self, m):
        assert "EOA" in m.MINT_PREFLIGHT_MSG_CONTRACT_REVERTS

    def test_msg_contract_bad_receiver_suggests_eoa(self, m):
        assert "EOA" in m.MINT_PREFLIGHT_MSG_CONTRACT_BAD_RECEIVER
