"""
Unit tests for _infer_archetype_from_metrics in polystars_card_payload.py.

Pure function — no DB, no network, no external services required.
Covers all 18 decision branches including pnl-first checks, bracket-specific
archetypes, and the default OPERATOR fallback.
"""

import pytest
from scripts.polystars_card_payload import _infer_archetype_from_metrics

B00 = "[0.00 - 0.20]"
B20 = "[0.20 - 0.40]"
B40 = "[0.40 - 0.60]"
B60 = "[0.60 - 0.80]"
B80 = "[0.80 - 0.97]"
B97 = "[0.97 - 1.00]"


def infer(bracket, edge="BASE", yld="BASE", grav="BASE", cwap=None, volume=None, pnl=None):
    return _infer_archetype_from_metrics(bracket, edge, yld, grav, cwap, volume, pnl)


# ---------------------------------------------------------------------------
# PnL-based archetypes (checked first, before bracket logic)
# ---------------------------------------------------------------------------

class TestIcarusAndBurner:
    def test_negative_pnl_low_cwap_is_icarus(self):
        assert infer(B60, pnl=-100, cwap=0.30) == "ICARUS"

    def test_negative_pnl_cwap_just_below_threshold_is_icarus(self):
        assert infer(B20, pnl=-1, cwap=0.599) == "ICARUS"

    def test_negative_pnl_cwap_at_threshold_is_burner(self):
        assert infer(B80, pnl=-50, cwap=0.60) == "BURNER"

    def test_negative_pnl_high_cwap_is_burner(self):
        assert infer(B80, pnl=-200, cwap=0.95) == "BURNER"

    def test_negative_pnl_cwap_zero_is_icarus(self):
        assert infer(B00, pnl=-1, cwap=0.0) == "ICARUS"

    def test_negative_pnl_no_cwap_skips_to_bracket_logic(self):
        # Without cwap the pnl<0 branch does not fire; falls to B97 logic
        assert infer(B97, volume=10_000, pnl=-50, cwap=None) == "EXTRACTOR"


class TestBot:
    def test_zero_pnl_is_bot(self):
        assert infer(B00, pnl=0) == "BOT"

    def test_zero_pnl_overrides_perfect_tier_combo(self):
        assert infer(B00, edge="P99", yld="P99", grav="P99", pnl=0) == "BOT"

    def test_zero_pnl_on_every_bracket(self):
        for bracket in (B00, B20, B40, B60, B80, B97):
            assert infer(bracket, pnl=0) == "BOT", f"Expected BOT for {bracket}"

    def test_zero_float_pnl_is_bot(self):
        assert infer(B40, pnl=0.0) == "BOT"


# ---------------------------------------------------------------------------
# [0.97 - 1.00] bracket — EXTRACTOR / PASSENGER / SUBSTRATE
# ---------------------------------------------------------------------------

class TestExtractorBracket:
    def test_high_volume_is_extractor(self):
        assert infer(B97, volume=5000) == "EXTRACTOR"

    def test_very_high_volume_is_extractor(self):
        assert infer(B97, volume=1_000_000) == "EXTRACTOR"

    def test_exact_threshold_is_extractor(self):
        assert infer(B97, volume=5000.0) == "EXTRACTOR"

    def test_mid_volume_is_passenger(self):
        assert infer(B97, volume=50) == "PASSENGER"

    def test_mid_volume_just_below_extractor_is_passenger(self):
        assert infer(B97, volume=4999) == "PASSENGER"

    def test_low_volume_is_substrate(self):
        assert infer(B97, volume=10) == "SUBSTRATE"

    def test_volume_none_is_substrate(self):
        assert infer(B97, volume=None) == "SUBSTRATE"

    def test_zero_volume_is_substrate(self):
        assert infer(B97, volume=0) == "SUBSTRATE"

    def test_volume_49_is_passenger(self):
        assert infer(B97, volume=49) == "SUBSTRATE"

    def test_volume_50_is_passenger(self):
        assert infer(B97, volume=50) == "PASSENGER"


# ---------------------------------------------------------------------------
# ANOMALY — perfect tier matches per bracket
# ---------------------------------------------------------------------------

class TestAnomaly:
    def test_b00_p99_all_is_anomaly(self):
        assert infer(B00, edge="P99", yld="P99", grav="P99") == "ANOMALY"

    def test_b20_p90_all_is_anomaly(self):
        assert infer(B20, edge="P90", yld="P90", grav="P90") == "ANOMALY"

    def test_b40_p70_all_is_anomaly(self):
        assert infer(B40, edge="P70", yld="P70", grav="P70") == "ANOMALY"

    def test_b60_p50_all_is_anomaly(self):
        assert infer(B60, edge="P50", yld="P50", grav="P50") == "ANOMALY"

    def test_b80_excluded_from_anomaly(self):
        assert infer(B80, edge="P50", yld="P50", grav="P50") != "ANOMALY"

    def test_b00_wrong_gravity_not_anomaly(self):
        assert infer(B00, edge="P99", yld="P99", grav="P90") != "ANOMALY"

    def test_b20_wrong_edge_not_anomaly(self):
        assert infer(B20, edge="P99", yld="P90", grav="P90") != "ANOMALY"

    def test_b40_wrong_yld_not_anomaly(self):
        assert infer(B40, edge="P70", yld="P50", grav="P70") != "ANOMALY"

    def test_b60_higher_tier_not_anomaly(self):
        # B60 ANOMALY requires exactly P50; P70 does not match
        assert infer(B60, edge="P70", yld="P70", grav="P70") != "ANOMALY"


# ---------------------------------------------------------------------------
# SIGNAL — low-bracket high-skill
# ---------------------------------------------------------------------------

class TestSignal:
    def test_b00_p99_yld_diff_grav_is_signal(self):
        # grav=P90 breaks ANOMALY for B00 (needs P99); SIGNAL fires
        assert infer(B00, edge="P99", yld="P99", grav="P90") == "SIGNAL"

    def test_b00_p90_edge_yld_is_signal(self):
        assert infer(B00, edge="P90", yld="P90", grav="BASE") == "SIGNAL"

    def test_b20_p99_edge_yld_is_signal(self):
        # B20 ANOMALY needs P90; P99 edge misses it → SIGNAL
        assert infer(B20, edge="P99", yld="P99", grav="BASE") == "SIGNAL"

    def test_b20_mixed_high_edge_yld_is_signal(self):
        assert infer(B20, edge="P90", yld="P99", grav="BASE") == "SIGNAL"

    def test_b40_high_edge_yld_is_vector_not_signal(self):
        assert infer(B40, edge="P99", yld="P99", grav="BASE") == "VECTOR"

    def test_b60_high_edge_yld_not_signal(self):
        result = infer(B60, edge="P99", yld="P99", grav="BASE")
        assert result != "SIGNAL"

    def test_b00_base_edge_not_signal(self):
        assert infer(B00, edge="BASE", yld="P99", grav="BASE") != "SIGNAL"


# ---------------------------------------------------------------------------
# VECTOR
# ---------------------------------------------------------------------------

class TestVector:
    def test_b40_p99_edge_yld_is_vector(self):
        assert infer(B40, edge="P99", yld="P99", grav="BASE") == "VECTOR"

    def test_b40_p90_edge_yld_is_vector(self):
        assert infer(B40, edge="P90", yld="P90", grav="BASE") == "VECTOR"

    def test_b40_p99_edge_p90_yld_is_vector(self):
        assert infer(B40, edge="P99", yld="P90", grav="BASE") == "VECTOR"

    def test_b40_base_edge_not_vector(self):
        assert infer(B40, edge="BASE", yld="P99", grav="BASE") != "VECTOR"

    def test_b40_base_yld_not_vector(self):
        assert infer(B40, edge="P99", yld="BASE", grav="BASE") != "VECTOR"

    def test_b00_signal_bracket_not_vector(self):
        result = infer(B00, edge="P90", yld="P90", grav="BASE")
        assert result == "SIGNAL"


# ---------------------------------------------------------------------------
# EQUILIBRIUM
# ---------------------------------------------------------------------------

class TestEquilibrium:
    def test_p70_all_tiers_b80_is_equilibrium(self):
        assert infer(B80, edge="P70", yld="P70", grav="P70") == "EQUILIBRIUM"

    def test_p99_all_tiers_b80_is_equilibrium(self):
        assert infer(B80, edge="P99", yld="P99", grav="P99") == "EQUILIBRIUM"

    def test_mixed_p70_p90_p99_is_equilibrium(self):
        assert infer(B80, edge="P99", yld="P70", grav="P90") == "EQUILIBRIUM"

    def test_p50_edge_breaks_equilibrium(self):
        result = infer(B80, edge="P50", yld="P70", grav="P70")
        assert result != "EQUILIBRIUM"

    def test_base_gravity_breaks_equilibrium(self):
        result = infer(B80, edge="P70", yld="P70", grav="BASE")
        assert result != "EQUILIBRIUM"

    def test_b00_all_p70_is_equilibrium(self):
        # B00 with P70 — doesn't match ANOMALY (needs P99), not SIGNAL (P70 not P99/P90), not VECTOR → EQUILIBRIUM
        assert infer(B00, edge="P70", yld="P70", grav="P70") == "EQUILIBRIUM"


# ---------------------------------------------------------------------------
# AMASSER
# ---------------------------------------------------------------------------

class TestAmasser:
    def test_high_grav_base_edge_is_amasser(self):
        assert infer(B00, edge="BASE", yld="BASE", grav="P90") == "AMASSER"

    def test_p99_grav_base_edge_is_amasser(self):
        assert infer(B20, edge="P50", yld="BASE", grav="P99") == "AMASSER"

    def test_p90_grav_with_low_edge_is_amasser(self):
        assert infer(B40, edge="BASE", yld="P50", grav="P90") == "AMASSER"

    def test_p70_grav_not_amasser(self):
        result = infer(B00, edge="BASE", yld="BASE", grav="P70")
        assert result != "AMASSER"

    def test_amasser_requires_edge_not_meeting_equilibrium(self):
        # If edge/yld also P70+, EQUILIBRIUM fires first
        result = infer(B80, edge="P70", yld="P70", grav="P99")
        assert result == "EQUILIBRIUM"


# ---------------------------------------------------------------------------
# SUBSTRATE
# ---------------------------------------------------------------------------

class TestSubstrate:
    def test_b60_all_base_is_substrate(self):
        assert infer(B60, edge="BASE", yld="BASE", grav="BASE") == "SUBSTRATE"

    def test_b80_all_base_is_substrate(self):
        assert infer(B80, edge="BASE", yld="BASE", grav="BASE") == "SUBSTRATE"

    def test_b80_p50_tiers_is_substrate(self):
        assert infer(B80, edge="P50", yld="P50", grav="P50") == "SUBSTRATE"

    def test_b80_base_edge_p70_grav_is_substrate(self):
        assert infer(B80, edge="BASE", yld="BASE", grav="P70") == "SUBSTRATE"

    def test_b60_p50_edge_p70_grav_is_substrate(self):
        assert infer(B60, edge="P50", yld="P50", grav="P70") == "SUBSTRATE"

    def test_b00_all_base_not_substrate(self):
        # B00 is not in SUBSTRATE bracket list → OPERATOR
        assert infer(B00, edge="BASE", yld="BASE", grav="BASE") == "OPERATOR"

    def test_b80_p70_edge_breaks_substrate(self):
        result = infer(B80, edge="P70", yld="BASE", grav="BASE")
        assert result != "SUBSTRATE"

    def test_b80_p70_yld_breaks_substrate(self):
        result = infer(B80, edge="BASE", yld="P70", grav="BASE")
        assert result != "SUBSTRATE"

    def test_b80_p99_grav_breaks_substrate(self):
        # P99 grav → AMASSER fires before SUBSTRATE
        result = infer(B80, edge="BASE", yld="BASE", grav="P99")
        assert result == "AMASSER"


# ---------------------------------------------------------------------------
# OPERATOR fallback
# ---------------------------------------------------------------------------

class TestOperator:
    def test_b00_all_base_is_operator(self):
        assert infer(B00, edge="BASE", yld="BASE", grav="BASE") == "OPERATOR"

    def test_b20_all_base_is_operator(self):
        assert infer(B20, edge="BASE", yld="BASE", grav="BASE") == "OPERATOR"

    def test_b40_all_base_is_operator(self):
        assert infer(B40, edge="BASE", yld="BASE", grav="BASE") == "OPERATOR"

    def test_b60_p70_edge_base_yld_grav_is_operator(self):
        # P70 edge but BASE yld → not EQUILIBRIUM, not AMASSER, not SUBSTRATE → OPERATOR
        assert infer(B60, edge="P70", yld="BASE", grav="BASE") == "OPERATOR"

    def test_b20_p50_tiers_is_operator(self):
        assert infer(B20, edge="P50", yld="P50", grav="P50") == "OPERATOR"


# ---------------------------------------------------------------------------
# Tier normalization edge cases
# ---------------------------------------------------------------------------

class TestTierNormalization:
    def test_lowercase_tiers_normalized(self):
        assert infer(B00, edge="p99", yld="p99", grav="p99") == "ANOMALY"

    def test_none_tiers_normalize_to_base(self):
        # All None → BASE → OPERATOR for B00
        assert infer(B00, edge=None, yld=None, grav=None) == "OPERATOR"

    def test_unknown_tier_normalizes_to_base(self):
        result = infer(B00, edge="MYTHIC", yld="P99", grav="P99")
        assert result != "ANOMALY"  # edge=BASE after normalization

    def test_whitespace_tier_normalizes_to_base(self):
        result = infer(B00, edge="  ", yld="P99", grav="P99")
        assert result != "ANOMALY"
