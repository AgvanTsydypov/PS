"""
Unit tests for the pure helpers in scripts/backfill_recovery_card_fields.py.

The CLI's end-to-end flow (DB I/O + IPFS fetch + Telegram dispatch) is
covered by tests/integration/test_backfill_recovery_card_fields_cli.py
against the live testcontainers Postgres. This file isolates the bits
that don't need a database — namely the ``asset_address`` → ``token_id``
parser used as the fallback when the on-chain receipt verifier didn't
capture the token id at recovery time.
"""

from __future__ import annotations

import pytest

from scripts.backfill_recovery_card_fields import (
    _parse_token_id_from_asset_address,
)


class TestParseTokenIdFromAssetAddress:
    """``asset_address`` is the canonical ``<contract>/<tokenId>`` join the
    EVM client writes after a successful mint (``evm_service.py:391``).
    The backfill CLI uses this parser to recover ``token_id`` for rows
    where the recovery path completed BEFORE the post-mint hook fix
    captured ``_verified_token_id`` — i.e. legacy COMPLETED rows where
    ``asset_address`` is the only on-disk record of the token number."""

    def test_standard_shape_returns_int(self):
        assert _parse_token_id_from_asset_address("0xABC/42") == 42

    def test_realistic_mainnet_shape(self):
        # Real row from id=18 in the incident: contract is mixed-case,
        # token_id is multi-digit. Parser must not normalize the contract
        # half (other callers may compare it case-sensitively).
        assert (
            _parse_token_id_from_asset_address(
                "0x692107D5962d0A3bb968c2DcD11Fb43C05907F0B/14"
            )
            == 14
        )

    def test_large_token_id(self):
        # uint256 is huge; the parser uses Python int so this works.
        big = 2**60 + 17
        assert _parse_token_id_from_asset_address(f"0xCAFE/{big}") == big

    def test_empty_string_returns_none(self):
        assert _parse_token_id_from_asset_address("") is None

    def test_none_returns_none(self):
        assert _parse_token_id_from_asset_address(None) is None  # type: ignore[arg-type]

    def test_no_slash_returns_none(self):
        # Contract-only — token_id is unknown, must NOT guess "0".
        assert _parse_token_id_from_asset_address("0xCAFE") is None

    def test_trailing_slash_returns_none(self):
        # "<contract>/" — token half is empty, can't parse to int.
        assert _parse_token_id_from_asset_address("0xCAFE/") is None

    def test_non_integer_token_returns_none(self):
        assert _parse_token_id_from_asset_address("0xCAFE/not-a-number") is None

    def test_negative_token_id_returns_negative_int(self):
        # Python's int() accepts "-1". This is unreachable in production
        # (uint256 can't be negative) but the parser doesn't manufacture
        # rules the source data doesn't enforce — we just exercise the
        # documented behaviour of int().
        assert _parse_token_id_from_asset_address("0xCAFE/-1") == -1

    def test_token_with_whitespace_returns_none(self):
        # int() rejects strings with internal spaces; the canonical writer
        # never produces them, but if a row was tampered with we'd rather
        # return None than swallow garbage.
        assert _parse_token_id_from_asset_address("0xCAFE/ 42 ") == 42  # int() strips

    def test_multiple_slashes_uses_last_segment_as_token(self):
        # Defensive: rsplit("/", 1) means a stray prefix can't fool the
        # parser. "<scheme>://<contract>/<tokenId>" is what evm_service
        # explicitly DOESN'T produce, but we make the parser robust to it.
        assert _parse_token_id_from_asset_address("ipfs://0xCAFE/7") == 7

    def test_only_slash_returns_none(self):
        assert _parse_token_id_from_asset_address("/") is None
