"""
Unit tests for remaining pure helpers in scripts/polystars_card_payload.py:
  _border_css_color, _generated_card_slug, _build_render_payload,
  _metadata_uri_to_https, build_recovery_payload_from_ipfs.
"""

import json
import re
from unittest.mock import patch, MagicMock

import pytest

from scripts.polystars_card_payload import (
    _PTIER_CSS_COLORS,
    _border_css_color,
    _build_render_payload,
    _generated_card_slug,
    _metadata_uri_to_https,
    build_recovery_payload_from_ipfs,
)


# ---------------------------------------------------------------------------
# _border_css_color
# ---------------------------------------------------------------------------

class TestBorderCssColor:
    def test_p99_returns_gold(self):
        assert _border_css_color("P99") == "#FFD700"

    def test_p90_returns_amber(self):
        assert _border_css_color("P90") == "#FFBF00"

    def test_p70_returns_blue(self):
        assert _border_css_color("P70") == "#265DD2"

    def test_p50_returns_green(self):
        assert _border_css_color("P50") == "#38BE50"

    def test_base_returns_grey(self):
        assert _border_css_color("BASE") == "#B6BBC8"

    def test_unknown_tier_returns_fallback_grey(self):
        assert _border_css_color("MYTHIC") == "#B6BBC8"

    def test_none_treated_as_base(self):
        # str(None or "BASE") = "BASE"
        assert _border_css_color(None) == "#B6BBC8"

    def test_empty_string_treated_as_base(self):
        assert _border_css_color("") == "#B6BBC8"

    def test_lowercase_tier_normalized(self):
        assert _border_css_color("p99") == "#FFD700"
        assert _border_css_color("p50") == "#38BE50"

    def test_all_known_tiers_have_valid_hex_color(self):
        hex_pattern = re.compile(r"^#[0-9A-Fa-f]{6}$")
        for tier in _PTIER_CSS_COLORS:
            color = _border_css_color(tier)
            assert hex_pattern.match(color), f"Invalid color for {tier}: {color}"


# ---------------------------------------------------------------------------
# _generated_card_slug
# ---------------------------------------------------------------------------

class TestGeneratedCardSlug:
    def test_standard_season_1_prefix(self):
        slug = _generated_card_slug("standard", 1)
        assert slug.startswith("card-standard-s1-")

    def test_genesis_season_2_prefix(self):
        slug = _generated_card_slug("genesis", 2)
        assert slug.startswith("card-genesis-s2-")

    def test_none_type_defaults_to_season(self):
        slug = _generated_card_slug(None, 1)
        assert slug.startswith("card-season-s1-")

    def test_none_number_defaults_to_0(self):
        slug = _generated_card_slug("standard", None)
        assert slug.startswith("card-standard-s0-")

    def test_both_none_use_defaults(self):
        slug = _generated_card_slug(None, None)
        assert slug.startswith("card-season-s0-")

    def test_special_chars_in_type_replaced_with_dash(self):
        slug = _generated_card_slug("my season!", 1)
        assert "!" not in slug
        assert slug.startswith("card-my-season-s1-")

    def test_slug_is_unique_per_call(self):
        s1 = _generated_card_slug("standard", 1)
        s2 = _generated_card_slug("standard", 1)
        assert s1 != s2

    def test_slug_has_expected_segment_count(self):
        # format: card-{type}-s{n}-{token_hex}-{uuid_hex}
        slug = _generated_card_slug("standard", 1)
        parts = slug.split("-")
        assert parts[0] == "card"
        assert parts[1] == "standard"
        assert parts[2] == "s1"

    def test_slug_contains_only_safe_url_chars(self):
        slug = _generated_card_slug("genesis", 3)
        assert re.match(r"^[a-z0-9\-]+$", slug), f"Unsafe chars in slug: {slug}"

    def test_uppercase_type_lowercased(self):
        slug = _generated_card_slug("GENESIS", 1)
        assert "GENESIS" not in slug
        assert slug.startswith("card-genesis-s1-") or "genesis" in slug


# ---------------------------------------------------------------------------
# _build_render_payload
# ---------------------------------------------------------------------------

def _mock_urlopen(content=b"img", content_type="image/png"):
    mock_resp = MagicMock()
    mock_resp.read.return_value = content
    mock_resp.headers.get_content_type.return_value = content_type
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


class TestBuildRenderPayload:
    def test_non_http_url_not_converted(self):
        payload = {"image_url": "/static/img.png", "other": "value"}
        result = _build_render_payload(payload)
        assert result["image_url"] == "/static/img.png"

    def test_data_uri_not_converted(self):
        payload = {"image_url": "data:image/png;base64,abc"}
        result = _build_render_payload(payload)
        assert result["image_url"].startswith("data:image/png")

    def test_empty_image_url_not_converted(self):
        payload = {"image_url": ""}
        result = _build_render_payload(payload)
        assert result["image_url"] == ""

    def test_none_image_url_not_converted(self):
        payload = {"image_url": None}
        result = _build_render_payload(payload)
        # None is not an http URL so it's left as-is in the dict
        assert result["image_url"] is None

    def test_http_url_converted_to_data_uri(self):
        payload = {"image_url": "http://example.com/img.jpg"}
        with patch(
            "scripts.polystars_card_payload.urlopen_after_ssrf_check",
            return_value=_mock_urlopen(b"bytes", "image/jpeg"),
        ):
            result = _build_render_payload(payload)
        assert result["image_url"].startswith("data:image/jpeg;base64,")

    def test_https_url_converted_to_data_uri(self):
        payload = {"image_url": "https://cdn.example.com/img.png"}
        with patch(
            "scripts.polystars_card_payload.urlopen_after_ssrf_check",
            return_value=_mock_urlopen(b"pngbytes", "image/png"),
        ):
            result = _build_render_payload(payload)
        assert result["image_url"].startswith("data:image/png;base64,")

    def test_other_fields_preserved_unchanged(self):
        payload = {
            "image_url": "/static/img.png",
            "card_title": "Test",
            "archetype": "SIGNAL",
        }
        result = _build_render_payload(payload)
        assert result["card_title"] == "Test"
        assert result["archetype"] == "SIGNAL"

    def test_original_payload_not_mutated(self):
        payload = {"image_url": "/static/img.png", "key": "val"}
        original = dict(payload)
        _build_render_payload(payload)
        assert payload == original

    def test_none_payload_returns_empty_dict(self):
        result = _build_render_payload(None)
        assert result == {}

    def test_empty_payload_returns_empty_dict(self):
        result = _build_render_payload({})
        assert result == {}


# ---------------------------------------------------------------------------
# _metadata_uri_to_https
# ---------------------------------------------------------------------------

class TestMetadataUriToHttps:
    def test_ipfs_uri_converted_to_pinata_gateway(self):
        url = _metadata_uri_to_https("ipfs://bafkreitest")
        assert url == "https://gateway.pinata.cloud/ipfs/bafkreitest"

    def test_ipfs_uri_with_redundant_ipfs_prefix_stripped(self):
        url = _metadata_uri_to_https("ipfs://ipfs/bafkreitest")
        assert url == "https://gateway.pinata.cloud/ipfs/bafkreitest"

    def test_pinata_gateway_url_passthrough(self):
        url = _metadata_uri_to_https("https://gateway.pinata.cloud/ipfs/bafkreitest")
        assert url == "https://gateway.pinata.cloud/ipfs/bafkreitest"

    def test_empty_string_returns_none(self):
        assert _metadata_uri_to_https("") is None

    def test_none_returns_none(self):
        assert _metadata_uri_to_https(None) is None

    def test_bare_ipfs_prefix_returns_none(self):
        assert _metadata_uri_to_https("ipfs://") is None

    def test_other_https_host_rejected(self):
        # SSRF defence: we only fetch from the Pinata gateway we control.
        assert _metadata_uri_to_https("https://evil.example.com/metadata.json") is None

    def test_http_rejected(self):
        assert _metadata_uri_to_https("http://gateway.pinata.cloud/ipfs/bafkreitest") is None


# ---------------------------------------------------------------------------
# build_recovery_payload_from_ipfs
# ---------------------------------------------------------------------------

def _mock_json_urlopen(payload: dict):
    raw = json.dumps(payload).encode("utf-8")
    mock_resp = MagicMock()
    mock_resp.read.return_value = raw
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


_VALID_CARD_DISPLAY_DATA = {
    "season_type": "standard",
    "season_number": 1,
    "season_size": 256,
    "collection_mint_number": 15,
    "archetype": "SUBSTRATE",
    "card_title": "Test Star",
    "primary_tag": "Economy",
    "secondary_tag": "Macro",
    "front_image_url": "https://gateway.pinata.cloud/ipfs/bafkreifront",
    "back_image_url": "https://gateway.pinata.cloud/ipfs/bafkreiback",
    "qr_payload": "https://polystars.app/cards/test-slug-abc",
}


class TestBuildRecoveryPayloadFromIpfs:
    def test_happy_path_returns_card_display_data(self):
        metadata = {
            "name": "STAR Genesis #15",
            "image": "ipfs://bafkreifront",
            "card_display_data": dict(_VALID_CARD_DISPLAY_DATA),
        }
        with patch(
            "scripts.polystars_card_payload.urlopen_after_ssrf_check",
            return_value=_mock_json_urlopen(metadata),
        ):
            payload = build_recovery_payload_from_ipfs("ipfs://bafkreimeta")
        assert payload is not None
        assert payload["qr_payload"] == "https://polystars.app/cards/test-slug-abc"
        assert payload["front_image_url"].endswith("/bafkreifront")
        assert payload["archetype"] == "SUBSTRATE"

    def test_non_pinata_uri_returns_none_without_fetching(self):
        # SSRF defence: must not attempt to fetch arbitrary hosts.
        with patch(
            "scripts.polystars_card_payload.urlopen_after_ssrf_check",
        ) as mock_fetch:
            payload = build_recovery_payload_from_ipfs(
                "https://evil.example.com/m.json"
            )
        assert payload is None
        mock_fetch.assert_not_called()

    def test_empty_metadata_uri_returns_none(self):
        assert build_recovery_payload_from_ipfs("") is None
        assert build_recovery_payload_from_ipfs(None) is None

    def test_missing_card_display_data_returns_none(self):
        metadata = {"name": "STAR Genesis #15"}  # legacy mint, no card_display_data
        with patch(
            "scripts.polystars_card_payload.urlopen_after_ssrf_check",
            return_value=_mock_json_urlopen(metadata),
        ):
            assert build_recovery_payload_from_ipfs("ipfs://bafkreimeta") is None

    def test_missing_qr_payload_returns_none(self):
        bad = dict(_VALID_CARD_DISPLAY_DATA)
        bad["qr_payload"] = ""
        with patch(
            "scripts.polystars_card_payload.urlopen_after_ssrf_check",
            return_value=_mock_json_urlopen({"card_display_data": bad}),
        ):
            assert build_recovery_payload_from_ipfs("ipfs://bafkreimeta") is None

    def test_missing_front_image_returns_none(self):
        bad = dict(_VALID_CARD_DISPLAY_DATA)
        bad["front_image_url"] = ""
        with patch(
            "scripts.polystars_card_payload.urlopen_after_ssrf_check",
            return_value=_mock_json_urlopen({"card_display_data": bad}),
        ):
            assert build_recovery_payload_from_ipfs("ipfs://bafkreimeta") is None

    def test_missing_back_image_returns_none(self):
        bad = dict(_VALID_CARD_DISPLAY_DATA)
        bad["back_image_url"] = ""
        with patch(
            "scripts.polystars_card_payload.urlopen_after_ssrf_check",
            return_value=_mock_json_urlopen({"card_display_data": bad}),
        ):
            assert build_recovery_payload_from_ipfs("ipfs://bafkreimeta") is None

    def test_fetch_failure_returns_none(self):
        with patch(
            "scripts.polystars_card_payload.urlopen_after_ssrf_check",
            side_effect=Exception("gateway timeout"),
        ):
            assert build_recovery_payload_from_ipfs("ipfs://bafkreimeta") is None

    def test_malformed_json_returns_none(self):
        bad_resp = MagicMock()
        bad_resp.read.return_value = b"not json"
        bad_resp.__enter__ = lambda s: s
        bad_resp.__exit__ = MagicMock(return_value=False)
        with patch(
            "scripts.polystars_card_payload.urlopen_after_ssrf_check",
            return_value=bad_resp,
        ):
            assert build_recovery_payload_from_ipfs("ipfs://bafkreimeta") is None

    def test_non_dict_metadata_returns_none(self):
        with patch(
            "scripts.polystars_card_payload.urlopen_after_ssrf_check",
            return_value=_mock_json_urlopen([1, 2, 3]),  # array instead of object
        ):
            assert build_recovery_payload_from_ipfs("ipfs://bafkreimeta") is None

    def test_returned_payload_is_a_copy(self):
        # Mutating the returned dict must not affect the source metadata.
        original = dict(_VALID_CARD_DISPLAY_DATA)
        metadata = {"card_display_data": original}
        with patch(
            "scripts.polystars_card_payload.urlopen_after_ssrf_check",
            return_value=_mock_json_urlopen(metadata),
        ):
            payload = build_recovery_payload_from_ipfs("ipfs://bafkreimeta")
        assert payload is not None
        payload["card_title"] = "MUTATED"
        assert original["card_title"] == "Test Star"
