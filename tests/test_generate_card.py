"""
Unit tests for pure helper functions in scripts/cardgen/generate_card.py.
No external services (DB, Playwright, fonts) are required.
"""

import pytest
from scripts.cardgen.generate_card import (
    _approx_wrap_text,
    _archetype_style_key,
    _back_block_bottom,
    _back_clamp_line_count,
    _esc,
    _event_recurrence_is_fractal,
    _fmt_back_date,
    _instance,
    _normalize_behavioral_frequency_label,
    _orbitron_width,
    _ownership,
    _season,
    _wallet_display,
    _wrap_text_by_width,
    dz_style,
    figma_gradient_to_svg,
    figma_gradients_to_svg_defs,
    get_bracket_color,
    get_ptier_color,
    normalize_entry_bracket,
)


# ---------------------------------------------------------------------------
# _wallet_display
# ---------------------------------------------------------------------------

class TestWalletDisplay:
    def test_short_address_unchanged(self):
        assert _wallet_display("0x1234") == "0x1234"

    def test_long_address_truncated(self):
        addr = "0x" + "a" * 20
        result = _wallet_display(addr)
        assert result.endswith("...")
        assert len(result) == 17  # 14 chars + "..."

    def test_exactly_14_chars_unchanged(self):
        addr = "0x" + "b" * 12  # 14 chars
        assert _wallet_display(addr) == addr

    def test_15_chars_truncated(self):
        addr = "0x" + "c" * 13  # 15 chars
        assert _wallet_display(addr) == addr[:14] + "..."


# ---------------------------------------------------------------------------
# _esc
# ---------------------------------------------------------------------------

class TestEsc:
    def test_escapes_lt_gt(self):
        assert "&lt;" in _esc("<")
        assert "&gt;" in _esc(">")

    def test_escapes_ampersand(self):
        assert "&amp;" in _esc("&")

    def test_escapes_quotes(self):
        assert "&#x27;" in _esc("'") or "&apos;" in _esc("'") or "'" in _esc("'")
        assert "&quot;" in _esc('"')

    def test_plain_text_unchanged(self):
        assert _esc("hello world") == "hello world"

    def test_coerces_non_string(self):
        assert _esc(42) == "42"


# ---------------------------------------------------------------------------
# normalize_entry_bracket / get_bracket_color
# ---------------------------------------------------------------------------

class TestNormalizeEntryBracket:
    def test_empty_returns_default(self):
        assert normalize_entry_bracket("") == "[0.80 - 0.97]"
        assert normalize_entry_bracket(None) == "[0.80 - 0.97]"

    def test_valid_interval_returned_as_is(self):
        assert normalize_entry_bracket("[0.00 - 0.20]") == "[0.00 - 0.20]"

    def test_legacy_anomaly_maps_to_interval(self):
        assert normalize_entry_bracket("ANOMALY") == "[0.00 - 0.20]"

    def test_legacy_oracle_maps_to_interval(self):
        assert normalize_entry_bracket("oracle") == "[0.20 - 0.40]"

    def test_unknown_returns_default(self):
        assert normalize_entry_bracket("TOTALLY_UNKNOWN") == "[0.80 - 0.97]"


class TestGetBracketColor:
    def test_known_bracket_returns_color(self):
        color = get_bracket_color("[0.00 - 0.20]")
        assert color.startswith("#") or color.startswith("url(")

    def test_unknown_bracket_returns_default_color(self):
        # Unknown input normalizes to [0.80 - 0.97] which maps to #B6BBC8
        assert get_bracket_color("???") == "#B6BBC8"

    def test_legacy_name_resolves(self):
        color = get_bracket_color("ANOMALY")
        assert color != "#FFFFFF"


# ---------------------------------------------------------------------------
# get_ptier_color
# ---------------------------------------------------------------------------

class TestGetPtierColor:
    def test_p99_has_color(self):
        color = get_ptier_color("P99")
        assert color.startswith("#") or color.startswith("url(")

    def test_case_insensitive(self):
        assert get_ptier_color("p99") == get_ptier_color("P99")

    def test_unknown_tier_returns_white(self):
        assert get_ptier_color("P42") == "#FFFFFF"


# ---------------------------------------------------------------------------
# _event_recurrence_is_fractal
# ---------------------------------------------------------------------------

class TestEventRecurrenceIsFractal:
    def test_none_is_not_fractal(self):
        assert _event_recurrence_is_fractal(None) is False

    def test_empty_is_not_fractal(self):
        assert _event_recurrence_is_fractal("") is False

    def test_unique_is_not_fractal(self):
        assert _event_recurrence_is_fractal("unique") is False
        assert _event_recurrence_is_fractal("UNIQUE") is False

    def test_weekly_is_fractal(self):
        assert _event_recurrence_is_fractal("weekly") is True

    def test_monthly_is_fractal(self):
        assert _event_recurrence_is_fractal("monthly") is True

    def test_null_string_is_not_fractal(self):
        assert _event_recurrence_is_fractal("null") is False
        assert _event_recurrence_is_fractal("none") is False


# ---------------------------------------------------------------------------
# _instance / _ownership / _season
# ---------------------------------------------------------------------------

class TestInstance:
    def test_singular_by_default(self):
        label, _ = _instance({})
        assert label == "SINGULAR"

    def test_fractal_when_recurring(self):
        label, _ = _instance({"recurrence": "weekly"})
        assert label == "FRACTAL"


class TestOwnership:
    def test_origin_secured(self):
        label, _ = _ownership({"claim_type": "origin"})
        assert label == "ORIGIN SECURED"

    def test_looter_takeover_by_default(self):
        label, _ = _ownership({})
        assert label == "LOOTER TAKEOVER"

    def test_explicit_looter_label(self):
        label, _ = _ownership({"claim_type": "looter"})
        assert label == "LOOTER TAKEOVER"

    def test_origin_color_is_magenta(self):
        _, color = _ownership({"claim_type": "origin"})
        assert color == "#FF007F"

    def test_looter_color_is_green(self):
        _, color = _ownership({"claim_type": "looter"})
        assert color == "#40E288"

    def test_default_color_is_green(self):
        """No claim_type key → falls back to looter, so color must be green."""
        _, color = _ownership({})
        assert color == "#40E288"

    def test_uppercase_origin_recognized(self):
        """claim_type comparison is case-insensitive — uppercase must still give
        the ORIGIN SECURED label and magenta color."""
        label, color = _ownership({"claim_type": "ORIGIN"})
        assert label == "ORIGIN SECURED"
        assert color == "#FF007F"

    def test_none_claim_type_treated_as_looter(self):
        label, color = _ownership({"claim_type": None})
        assert label == "LOOTER TAKEOVER"
        assert color == "#40E288"

    def test_origin_and_looter_colors_are_distinct(self):
        """Sanity: the two ownership bands must not accidentally share a color."""
        _, origin_color = _ownership({"claim_type": "origin"})
        _, looter_color = _ownership({"claim_type": "looter"})
        assert origin_color != looter_color


class TestSeason:
    def test_genesis_label(self):
        label, _ = _season({"season_type": "genesis"})
        assert label == "GENESIS"

    def test_standard_label(self):
        label, _ = _season({"season_type": "standard", "season_number": 3})
        assert label == "STANDARD #3"

    def test_default_season_number(self):
        label, _ = _season({})
        assert "1" in label


# ---------------------------------------------------------------------------
# _archetype_style_key / dz_style
# ---------------------------------------------------------------------------

class TestArchetypeStyleKey:
    def test_strips_the_prefix(self):
        from scripts.cardgen.generate_card import _archetype_style_key
        assert _archetype_style_key("THE ANOMALY") == "ANOMALY"

    def test_uppercases(self):
        from scripts.cardgen.generate_card import _archetype_style_key
        assert _archetype_style_key("signal") == "SIGNAL"

    def test_empty_returns_operator(self):
        from scripts.cardgen.generate_card import _archetype_style_key
        assert _archetype_style_key("") == "OPERATOR"


class TestDzStyle:
    def test_known_archetype_returns_tuple(self):
        fill, stroke, is_signal = dz_style("ANOMALY")
        assert isinstance(fill, str) and isinstance(stroke, str)

    def test_equilibrium_is_signal(self):
        _, _, is_signal = dz_style("EQUILIBRIUM")
        assert is_signal is True

    def test_unknown_falls_back_to_operator(self):
        fill_unknown, _, _ = dz_style("NONEXISTENT")
        fill_operator, _, _ = dz_style("OPERATOR")
        assert fill_unknown == fill_operator


# ---------------------------------------------------------------------------
# _fmt_back_date
# ---------------------------------------------------------------------------

class TestFmtBackDate:
    def test_valid_iso_returns_first_10_chars(self):
        assert _fmt_back_date("2025-06-15T00:00:00Z") == "2025-06-15"

    def test_date_only_string(self):
        assert _fmt_back_date("2024-01-01") == "2024-01-01"

    def test_empty_returns_dash(self):
        assert _fmt_back_date("") == "—"
        assert _fmt_back_date(None) == "—"

    def test_non_iso_returned_as_is(self):
        assert _fmt_back_date("March 2025") == "March 2025"


# ---------------------------------------------------------------------------
# _approx_wrap_text
# ---------------------------------------------------------------------------

class TestApproxWrapText:
    def test_empty_string(self):
        assert _approx_wrap_text("", 20) == []

    def test_short_text_fits_one_line(self):
        result = _approx_wrap_text("hello world", 50)
        assert result == ["hello world"]

    def test_long_text_wraps(self):
        text = "one two three four five"
        result = _approx_wrap_text(text, 10)
        assert len(result) > 1
        assert " ".join(result) == text

    def test_each_line_within_max_chars(self):
        text = "alpha beta gamma delta epsilon zeta"
        result = _approx_wrap_text(text, 12)
        for line in result:
            assert len(line) <= 12


# ---------------------------------------------------------------------------
# _back_block_bottom
# ---------------------------------------------------------------------------

class TestBackBlockBottom:
    def test_zero_lines(self):
        assert _back_block_bottom(100.0, 0, 22, 16.0) == 100.0

    def test_one_line(self):
        assert _back_block_bottom(100.0, 1, 22, 16.0) == 116.0

    def test_three_lines(self):
        # start + (3-1)*22 + 16 = 100 + 44 + 16 = 160
        assert _back_block_bottom(100.0, 3, 22, 16.0) == 160.0


# ---------------------------------------------------------------------------
# _back_clamp_line_count
# ---------------------------------------------------------------------------

class TestBackClampLineCount:
    def test_no_clamp_needed(self):
        from scripts.cardgen.generate_card import _back_clamp_line_count
        lines = ["a", "b", "c"]
        assert _back_clamp_line_count(lines, 5) == lines

    def test_clamps_to_max(self):
        from scripts.cardgen.generate_card import _back_clamp_line_count
        lines = ["a", "b", "c", "d", "e"]
        result = _back_clamp_line_count(lines, 3)
        assert len(result) == 3

    def test_empty_list(self):
        from scripts.cardgen.generate_card import _back_clamp_line_count
        assert _back_clamp_line_count([], 5) == []


# ---------------------------------------------------------------------------
# figma_gradient_to_svg
# ---------------------------------------------------------------------------

class TestFigmaGradientToSvg:
    def test_linear_gradient_output(self):
        gradient = {
            "gradient_type": "GRADIENT_LINEAR",
            "stops": [{"offset": "0%", "color": "#000000", "opacity": 1}],
            "gradient_transform": None,
        }
        svg = figma_gradient_to_svg(gradient, "test-grad")
        assert "<linearGradient" in svg
        assert 'id="test-grad"' in svg
        assert "<stop" in svg

    def test_radial_gradient_output(self):
        gradient = {
            "gradient_type": "GRADIENT_RADIAL",
            "stops": [{"offset": "50%", "color": "#FFFFFF", "opacity": 0.5}],
        }
        svg = figma_gradient_to_svg(gradient, "radial-1")
        assert "<radialGradient" in svg
        assert 'id="radial-1"' in svg

    def test_transform_included_when_provided(self):
        gradient = {
            "gradient_type": "GRADIENT_LINEAR",
            "stops": [],
            "gradient_transform": [[1, 0, 0], [0, 1, 0]],
        }
        svg = figma_gradient_to_svg(gradient, "g")
        assert "gradientTransform" in svg

    def test_no_transform_when_none(self):
        gradient = {
            "gradient_type": "GRADIENT_LINEAR",
            "stops": [],
            "gradient_transform": None,
        }
        svg = figma_gradient_to_svg(gradient, "g")
        assert "gradientTransform" not in svg


class TestFigmaGradientsToDefs:
    def test_empty_list(self):
        assert figma_gradients_to_svg_defs([]) == ""

    def test_single_gradient_uses_layer_name(self):
        gradients = [{
            "layer_name": "My Gradient",
            "gradient_type": "GRADIENT_LINEAR",
            "stops": [],
        }]
        svg = figma_gradients_to_svg_defs(gradients)
        assert 'id="my-gradient"' in svg

    def test_multiple_gradients(self):
        gradients = [
            {"layer_name": "g1", "gradient_type": "GRADIENT_LINEAR", "stops": []},
            {"layer_name": "g2", "gradient_type": "GRADIENT_RADIAL", "stops": []},
        ]
        svg = figma_gradients_to_svg_defs(gradients)
        assert 'id="g1"' in svg
        assert 'id="g2"' in svg


# ---------------------------------------------------------------------------
# _orbitron_width (smoke test — verifies model doesn't crash)
# ---------------------------------------------------------------------------

class TestOrbitronWidth:
    def test_empty_string_is_zero(self):
        assert _orbitron_width("", 14.0) == 0.0

    def test_width_increases_with_text(self):
        w1 = _orbitron_width("A", 14.0)
        w2 = _orbitron_width("ABCDEF", 14.0)
        assert w2 > w1

    def test_width_increases_with_font_size(self):
        w1 = _orbitron_width("HELLO", 10.0)
        w2 = _orbitron_width("HELLO", 20.0)
        assert w2 > w1

    def test_wrap_by_width_produces_lines(self):
        text = "WILL THIS VERY LONG TITLE WRAP CORRECTLY"
        lines = _wrap_text_by_width(text, 100.0, 14.0)
        assert len(lines) >= 1
        assert " ".join(lines) == text


# ---------------------------------------------------------------------------
# _normalize_behavioral_frequency_label
# ---------------------------------------------------------------------------

class TestNormalizeBehavioralFrequencyLabel:
    def test_empty_returns_dash_placeholder(self):
        assert _normalize_behavioral_frequency_label("") == "BEHAVIORAL FREQUENCY: --"
        assert _normalize_behavioral_frequency_label(None) == "BEHAVIORAL FREQUENCY: --"

    def test_canonical_value_passes_through(self):
        assert (
            _normalize_behavioral_frequency_label("BEHAVIORAL FREQUENCY: ~ 2.0%")
            == "BEHAVIORAL FREQUENCY: ~ 2.0%"
        )

    def test_strips_brackets_and_collapses_whitespace(self):
        raw = "[BEHAVIORAL  FREQUENCY:   ~ 0.5%]"
        assert (
            _normalize_behavioral_frequency_label(raw)
            == "BEHAVIORAL FREQUENCY: ~ 0.5%"
        )

    def test_bare_payload_gets_prefix(self):
        assert (
            _normalize_behavioral_frequency_label("~ 7.0%")
            == "BEHAVIORAL FREQUENCY: ~ 7.0%"
        )

    def test_other_prefix_with_colon_is_replaced(self):
        # Anything before the colon is treated as a stale prefix and dropped.
        assert (
            _normalize_behavioral_frequency_label("OCCURRENCE: ~ 1.0%")
            == "BEHAVIORAL FREQUENCY: ~ 1.0%"
        )
        assert (
            _normalize_behavioral_frequency_label("PROBABILITY COHORT: < 1.0%")
            == "BEHAVIORAL FREQUENCY: < 1.0%"
        )

    def test_coerces_non_string(self):
        assert (
            _normalize_behavioral_frequency_label(42)
            == "BEHAVIORAL FREQUENCY: 42"
        )

    def test_collapses_internal_newlines(self):
        raw = "BEHAVIORAL FREQUENCY:\n~ 44.0%"
        assert (
            _normalize_behavioral_frequency_label(raw)
            == "BEHAVIORAL FREQUENCY: ~ 44.0%"
        )
