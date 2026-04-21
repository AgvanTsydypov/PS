"""
Unit tests for remaining pure helpers in scripts/polystars_card_payload.py:
  _border_css_color, _generated_card_slug, _build_render_payload.
"""

import re
from unittest.mock import patch, MagicMock

import pytest

from scripts.polystars_card_payload import (
    _PTIER_CSS_COLORS,
    _border_css_color,
    _build_render_payload,
    _generated_card_slug,
)


# ---------------------------------------------------------------------------
# _border_css_color
# ---------------------------------------------------------------------------

class TestBorderCssColor:
    def test_p99_returns_gold(self):
        assert _border_css_color("P99") == "#FFD700"

    def test_p999_returns_gold(self):
        assert _border_css_color("P999") == "#FFD700"

    def test_p90_returns_amber(self):
        assert _border_css_color("P90") == "#FFBF00"

    def test_p95_returns_amber(self):
        assert _border_css_color("P95") == "#FFBF00"

    def test_p70_returns_blue(self):
        assert _border_css_color("P70") == "#265DD2"

    def test_p80_returns_blue(self):
        assert _border_css_color("P80") == "#265DD2"

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
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(b"bytes", "image/jpeg")):
            result = _build_render_payload(payload)
        assert result["image_url"].startswith("data:image/jpeg;base64,")

    def test_https_url_converted_to_data_uri(self):
        payload = {"image_url": "https://cdn.example.com/img.png"}
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(b"pngbytes", "image/png")):
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
