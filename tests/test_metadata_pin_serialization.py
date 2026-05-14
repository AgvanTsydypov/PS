"""
Determinism tests for ``EvmClient._serialize_metadata_for_pin``.

Why this test matters
---------------------
The whole disaster-recovery story for IPFS pins relies on the bytes we send
to Pinata being **bit-identical** to the bytes we mirror to R2. If our
serialization drifts (different key order, different float formatting,
escaped vs unescaped unicode), the R2 mirror restores a different CID than
what's on-chain — and the on-chain ``tokenURI`` stays broken.

So this file pins three properties:

1. **Stable across calls** — same dict in, same bytes out, no run-to-run noise.
2. **Insensitive to dict key order** — Python dicts are insertion-ordered, so
   reshuffling the input must not change the output (otherwise upstream code
   reordering attributes would silently produce different CIDs).
3. **Unicode-safe** — non-ASCII content (card titles, archetype names with
   accented chars) survives round-trip without escaping.

The CID-computation tests at the bottom assert that, given our bytes,
locally-computed CIDv1 (raw codec, single-block) matches Pinata's expected
format. That covers the *layout* of the CID we'll get back — not its exact
value against a live API call (which would require network access). For a
true end-to-end check, run ``scripts.evm_service._upload_metadata_to_pinata``
against a real Pinata account once and compare the returned ``IpfsHash`` with
``_compute_cidv1_raw`` over the same bytes.
"""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from scripts.evm_service import EvmClient


# ---------------------------------------------------------------------------
# Bit-level determinism
# ---------------------------------------------------------------------------


def test_same_dict_produces_same_bytes_across_calls():
    """Two calls with the same dict must yield byte-identical output. Without
    this, R2 mirror and Pinata pin would race to different CIDs on retries."""
    metadata = {"name": "STAR genesis #1", "symbol": "STAR", "value": 42}
    a = EvmClient._serialize_metadata_for_pin(metadata)
    b = EvmClient._serialize_metadata_for_pin(metadata)
    assert a == b
    assert isinstance(a, bytes)


def test_dict_key_order_does_not_affect_bytes():
    """Python dicts are insertion-ordered. If we ever reorder attributes
    upstream (e.g. swap the order of ``image`` and ``description``), the
    serialized bytes — and therefore the CID — must stay the same."""
    a = EvmClient._serialize_metadata_for_pin(
        {"a": 1, "b": 2, "c": 3, "nested": {"x": 1, "y": 2}}
    )
    b = EvmClient._serialize_metadata_for_pin(
        {"c": 3, "nested": {"y": 2, "x": 1}, "b": 2, "a": 1}
    )
    assert a == b


def test_compact_separators_no_whitespace():
    """The bytes must use compact separators — any added whitespace would
    change the CID. Defends against someone "prettifying" the output later."""
    out = EvmClient._serialize_metadata_for_pin({"a": 1, "b": [1, 2, 3]})
    assert b" " not in out
    assert b"\n" not in out
    assert out == b'{"a":1,"b":[1,2,3]}'


# ---------------------------------------------------------------------------
# Unicode preservation
# ---------------------------------------------------------------------------


def test_non_ascii_preserved_not_escaped():
    """``ensure_ascii=False`` keeps the bytes shorter and matches what JS
    clients (OpenSea, wallets) emit for the same string. Escaping to
    ``\\uXXXX`` would still be valid JSON but would produce a different CID."""
    out = EvmClient._serialize_metadata_for_pin({"title": "Аномалия — Σ★"})
    text = out.decode("utf-8")
    assert "Аномалия" in text
    assert "Σ★" in text
    assert "\\u" not in text


def test_emoji_round_trips_through_serialization():
    """Card titles can technically contain emoji. They must not be escaped
    or normalized — same byte representation in and out of the JSON layer."""
    out = EvmClient._serialize_metadata_for_pin({"name": "STAR ⭐ #42"})
    assert json.loads(out)["name"] == "STAR ⭐ #42"
    assert "⭐".encode("utf-8") in out


# ---------------------------------------------------------------------------
# JSON-safe coercion (via _make_json_safe)
# ---------------------------------------------------------------------------


def test_decimal_integer_value_serializes_as_int():
    """Pricing / numeric attributes can arrive as ``Decimal`` from psycopg2.
    Whole-number Decimals must serialize as JSON ints, not strings, so the
    on-chain ``attributes`` array stays numeric for marketplace filters."""
    out = EvmClient._serialize_metadata_for_pin({"v": Decimal("42")})
    assert out == b'{"v":42}'


def test_decimal_fractional_value_serializes_as_float():
    out = EvmClient._serialize_metadata_for_pin({"v": Decimal("0.25")})
    # Fractional Decimal goes through float, so we get JSON's float repr.
    assert json.loads(out)["v"] == 0.25


def test_datetime_serializes_as_iso8601_string():
    """Datetimes appear in ``season_start_date`` etc. ISO-8601 keeps them
    sortable and human-readable on Etherscan / OpenSea trait displays."""
    dt = datetime(2026, 5, 14, 12, 0, 0, tzinfo=timezone.utc)
    out = EvmClient._serialize_metadata_for_pin({"d": dt})
    assert json.loads(out)["d"] == dt.isoformat()


def test_date_serializes_as_iso8601_string():
    out = EvmClient._serialize_metadata_for_pin({"d": date(2026, 5, 14)})
    assert json.loads(out)["d"] == "2026-05-14"


# ---------------------------------------------------------------------------
# Golden-bytes regression
# ---------------------------------------------------------------------------


def test_golden_bytes_for_known_metadata():
    """If a future change accidentally tweaks serialization (e.g. someone
    flips ``sort_keys`` off), this catches it as a byte-diff before the CID
    drifts in production."""
    metadata = {
        "name": "STAR genesis #1",
        "symbol": "STAR",
        "description": "Collectible STAR of genesis#1",
        "image": "ipfs://bafyfront",
        "attributes": [
            {"trait_type": "Season Type", "value": "Genesis"},
            {"trait_type": "Archetype", "value": "ICARUS"},
        ],
    }
    out = EvmClient._serialize_metadata_for_pin(metadata)
    expected = (
        b'{"attributes":[{"trait_type":"Season Type","value":"Genesis"},'
        b'{"trait_type":"Archetype","value":"ICARUS"}],'
        b'"description":"Collectible STAR of genesis#1",'
        b'"image":"ipfs://bafyfront",'
        b'"name":"STAR genesis #1","symbol":"STAR"}'
    )
    assert out == expected


# ---------------------------------------------------------------------------
# CIDv1 layout verification (raw codec, single block)
# ---------------------------------------------------------------------------


def _compute_cidv1_raw(data: bytes) -> str:
    """Locally compute CIDv1 for raw-codec, single-block content.

    Matches the format Pinata returns from ``pinFileToIPFS`` with
    ``cidVersion: 1`` for files small enough to fit in one chunk (≤256 KB).
    All our NFT JSON metadata fits comfortably in this regime.

    Layout: ``<version=1><codec=raw=0x55><multihash=sha2-256||32||digest>``,
    base32-lowercase encoded, prefixed with multibase tag ``b``.
    """
    digest = hashlib.sha256(data).digest()
    assert len(digest) == 32
    # Multihash: <code=0x12 sha2-256> <length=0x20=32> <digest>
    multihash = b"\x12\x20" + digest
    # CID: <version=0x01> <codec=0x55 raw> <multihash>
    cid_bytes = b"\x01\x55" + multihash
    encoded = base64.b32encode(cid_bytes).decode("ascii").lower().rstrip("=")
    return "b" + encoded


def test_local_cidv1_known_vector_empty_bytes():
    """Sanity check the local CID computation against a well-known fixture:
    the CIDv1-raw of empty bytes is ``bafkreihdwdcefgh4dqkjv67uzcmw7ojee6xedzdetojuzjevtenxquvyku``.
    If this drifts, the multihash/multibase wiring is wrong and every other
    CID assertion in this file becomes meaningless."""
    expected = "bafkreihdwdcefgh4dqkjv67uzcmw7ojee6xedzdetojuzjevtenxquvyku"
    assert _compute_cidv1_raw(b"") == expected


def test_local_cidv1_format_for_metadata_bytes():
    """The CID Pinata returns should *look like* a CIDv1 raw single-block
    CID for our serialized bytes. Length and prefix are the cheap structural
    checks; full equivalence requires a live Pinata round-trip."""
    out = EvmClient._serialize_metadata_for_pin({"name": "STAR test"})
    cid = _compute_cidv1_raw(out)
    assert cid.startswith("bafkrei")
    # base32(36 bytes) = 60 chars (with padding stripped); plus 'b' multibase.
    assert len(cid) == 59


def test_local_cidv1_is_deterministic_for_same_bytes():
    """Belt-and-suspenders for the local CID helper itself."""
    payload = b'{"name":"STAR"}'
    assert _compute_cidv1_raw(payload) == _compute_cidv1_raw(payload)


def test_serialization_then_cid_is_stable_end_to_end():
    """The contract this whole test file exists to defend: same metadata
    in → same bytes → same CID. If this ever flakes, the disaster-recovery
    story is broken."""
    metadata = {
        "name": "STAR genesis #1",
        "attributes": [
            {"trait_type": "Edge", "value": "P99"},
            {"trait_type": "Yield", "value": "P90"},
        ],
    }
    cid_a = _compute_cidv1_raw(EvmClient._serialize_metadata_for_pin(metadata))
    cid_b = _compute_cidv1_raw(EvmClient._serialize_metadata_for_pin(dict(metadata)))
    assert cid_a == cid_b
