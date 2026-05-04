"""
Unit tests for the Polystars structural signature (Section 4 of the spec).

The signature is the contract between ``claims.signature`` (frozen at
queue-insert time) and the back-of-card render. A regression here will
permanently mis-identify Stars on chain, so every encoding branch must be
guarded.
"""

import pytest

from scripts.cardgen.generate_card import (
    _SIG_ARCH_CODES,
    compute_structural_signature,
)


def _data(**overrides):
    """Minimal valid input; tests override one segment at a time."""
    base = {
        "archetype":     "ICARUS",
        "entry_bracket": "[0.60 - 0.80]",
        "edge":          "BASE",
        "yield":         "P50",
        "gravity":       "BASE",
        "claim_type":    "origin",
        "event_id":      "12345",
        "recurrence":    "unique",
        "season_type":   "genesis",
        "season_number": 1,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# [ARCH] — three-letter archetype code
# ---------------------------------------------------------------------------

class TestArchetypeSegment:
    @pytest.mark.parametrize("archetype,expected", [
        ("ANOMALY",     "ANO"),
        ("ICARUS",      "ICA"),
        ("BOT",         "BOT"),
        ("BURNER",      "BUR"),
        ("EQUILIBRIUM", "EQU"),
        ("GRAVITON",    "GRA"),
        ("VECTOR",      "VEC"),
        ("SIGNAL",      "SIG"),
        ("EXTRACTOR",   "EXT"),
        ("INSIDER",     "INS"),
        ("PASSENGER",   "PAS"),
        ("OPERATOR",    "OPR"),
        ("SUBSTRATE",   "SUB"),
    ])
    def test_all_thirteen_archetypes_have_codes(self, archetype, expected):
        sig = compute_structural_signature(_data(archetype=archetype))
        assert sig.split("-", 1)[0] == expected

    def test_archetype_table_covers_thirteen_entries(self):
        # Spec: "thirteen Behavioral Archetypes". Drift in either direction is
        # a contract break.
        assert len(_SIG_ARCH_CODES) == 13

    def test_archetype_codes_are_unique(self):
        codes = list(_SIG_ARCH_CODES.values())
        assert len(codes) == len(set(codes)), "duplicate ARCH codes would collide"

    def test_archetype_codes_are_three_uppercase_letters(self):
        for code in _SIG_ARCH_CODES.values():
            assert len(code) == 3
            assert code.isupper()
            assert code.isalpha()

    def test_unknown_archetype_falls_back_to_operator(self):
        sig = compute_structural_signature(_data(archetype="MYTHIC_TRADER"))
        assert sig.split("-", 1)[0] == "OPR"

    def test_archetype_with_the_prefix_normalized(self):
        # _archetype_style_key strips a leading "THE " — the signature must
        # see ICARUS, not "THE ICARUS".
        sig = compute_structural_signature(_data(archetype="THE ICARUS"))
        assert sig.split("-", 1)[0] == "ICA"

    def test_archetype_lowercase_normalized(self):
        sig = compute_structural_signature(_data(archetype="icarus"))
        assert sig.split("-", 1)[0] == "ICA"


# ---------------------------------------------------------------------------
# [P(E)] — single-digit bracket lower bound
# ---------------------------------------------------------------------------

class TestProbabilityBracketSegment:
    @pytest.mark.parametrize("bracket,expected_digit", [
        ("[0.00 - 0.20]", "0"),
        ("[0.20 - 0.40]", "2"),
        ("[0.40 - 0.60]", "4"),
        ("[0.60 - 0.80]", "6"),
        ("[0.80 - 0.97]", "8"),
        ("[0.97 - 1.00]", "9"),  # terminal-certainty compression
    ])
    def test_each_bracket_maps_to_correct_digit(self, bracket, expected_digit):
        sig = compute_structural_signature(_data(entry_bracket=bracket))
        assert sig.split("-")[1] == expected_digit

    def test_terminal_certainty_compressed_to_nine(self):
        # Spec: "with the terminal-certainty bracket compressed to 9 to
        # preserve single-digit notation across all six positions."
        sig = compute_structural_signature(_data(entry_bracket="[0.97 - 1.00]"))
        assert sig.split("-")[1] == "9"

    def test_unknown_bracket_uses_default(self):
        sig = compute_structural_signature(_data(entry_bracket="[0.50 - 0.75]"))
        # normalize_entry_bracket falls back to "[0.80 - 0.97]" → "8".
        assert sig.split("-")[1] == "8"


# ---------------------------------------------------------------------------
# [E][Y][G] — tier triplet (Edge, Yield, Gravity)
# ---------------------------------------------------------------------------

class TestTierTripletSegment:
    @pytest.mark.parametrize("tier,expected_char", [
        ("BASE", "B"),
        ("P50",  "5"),
        ("P70",  "7"),
        ("P90",  "9"),
        ("P99",  "X"),
    ])
    def test_each_canonical_tier_maps_to_char(self, tier, expected_char):
        # ``yield`` is a Python keyword, so override via dict literal instead
        # of as a kwarg.
        sig = compute_structural_signature(
            {**_data(), "edge": tier, "yield": tier, "gravity": tier}
        )
        assert sig.split("-")[2] == expected_char * 3

    def test_positional_order_is_edge_yield_gravity(self):
        # Spec: "Position 1: Edge. Position 2: Yield. Position 3: Gravity."
        sig = compute_structural_signature(
            {**_data(), "edge": "P99", "yield": "P50", "gravity": "BASE"}
        )
        eyg = sig.split("-")[2]
        assert eyg[0] == "X"   # Edge=P99
        assert eyg[1] == "5"   # Yield=P50
        assert eyg[2] == "B"   # Gravity=BASE

    def test_legacy_tier_falls_back_to_base(self):
        # P80 / P95 / P999 were dropped; they must collapse to BASE not blow up.
        sig = compute_structural_signature(
            {**_data(), "edge": "P80", "yield": "P95", "gravity": "P999"}
        )
        assert sig.split("-")[2] == "BBB"

    def test_unknown_tier_falls_back_to_base(self):
        sig = compute_structural_signature(
            {**_data(), "edge": "MYTHIC", "yield": "MYTHIC", "gravity": "MYTHIC"}
        )
        assert sig.split("-")[2] == "BBB"

    def test_lowercase_tier_normalized(self):
        sig = compute_structural_signature(
            {**_data(), "edge": "p99", "yield": "p50", "gravity": "base"}
        )
        assert sig.split("-")[2] == "X5B"

    def test_missing_tier_falls_back_to_base(self):
        sig = compute_structural_signature(
            {**_data(), "edge": None, "yield": "", "gravity": None}
        )
        assert sig.split("-")[2] == "BBB"


# ---------------------------------------------------------------------------
# [INST] — event instance / recurrence
# ---------------------------------------------------------------------------

class TestEventInstanceSegment:
    @pytest.mark.parametrize("recurrence,expected", [
        ("unique",  "U"),
        ("UNIQUE",  "U"),
        ("daily",   "D"),
        ("weekly",  "W"),
        ("monthly", "M"),
    ])
    def test_known_recurrences(self, recurrence, expected):
        sig = compute_structural_signature(_data(recurrence=recurrence))
        assert sig.split("-")[3] == expected

    def test_unknown_recurrence_collapses_to_recurring(self):
        sig = compute_structural_signature(_data(recurrence="biweekly"))
        assert sig.split("-")[3] == "R"

    def test_empty_recurrence_defaults_to_unique(self):
        sig = compute_structural_signature(_data(recurrence=""))
        assert sig.split("-")[3] == "U"

    def test_none_recurrence_defaults_to_unique(self):
        sig = compute_structural_signature(_data(recurrence=None))
        assert sig.split("-")[3] == "U"


# ---------------------------------------------------------------------------
# [CLAIM] — claim class
# ---------------------------------------------------------------------------

class TestClaimSegment:
    def test_origin_renders_as_slashed_O(self):
        # Spec: rendered as Ø to eliminate visual collision with digit 0.
        sig = compute_structural_signature(_data(claim_type="origin"))
        assert sig.split("-")[4] == "Ø"

    def test_origin_uppercase_normalized(self):
        sig = compute_structural_signature(_data(claim_type="ORIGIN"))
        assert sig.split("-")[4] == "Ø"

    def test_looter_renders_as_L(self):
        sig = compute_structural_signature(_data(claim_type="looter"))
        assert sig.split("-")[4] == "L"

    def test_unknown_claim_type_defaults_to_looter(self):
        sig = compute_structural_signature(_data(claim_type="ghost"))
        assert sig.split("-")[4] == "L"

    def test_empty_claim_type_defaults_to_looter(self):
        sig = compute_structural_signature(_data(claim_type=""))
        assert sig.split("-")[4] == "L"


# ---------------------------------------------------------------------------
# S[N] — season designator
# ---------------------------------------------------------------------------

class TestSeasonSegment:
    def test_genesis_always_emits_s0(self):
        # Spec: "S0 reservation is permanent. No future Star will ever carry
        # an S0 designator unless minted within the Genesis Epoch."
        sig = compute_structural_signature(_data(season_type="genesis", season_number=1))
        assert sig.split("-")[5] == "S0"

    def test_genesis_ignores_season_number_value(self):
        # Genesis must produce S0 even if season_number is something weird.
        for n in (0, 1, 2, 99, None, "abc"):
            sig = compute_structural_signature(_data(season_type="genesis", season_number=n))
            assert sig.split("-")[5] == "S0", f"genesis produced wrong season for n={n!r}"

    def test_genesis_is_case_insensitive(self):
        sig = compute_structural_signature(_data(season_type="GENESIS"))
        assert sig.split("-")[5] == "S0"

    def test_standard_season_emits_sn(self):
        sig = compute_structural_signature(_data(season_type="standard", season_number=3))
        assert sig.split("-")[5] == "S3"

    def test_standard_season_with_string_number(self):
        sig = compute_structural_signature(_data(season_type="standard", season_number="7"))
        assert sig.split("-")[5] == "S7"

    @pytest.mark.parametrize("bad", [None, "", "abc", -5, 0])
    def test_standard_with_invalid_number_falls_back_to_one(self, bad):
        sig = compute_structural_signature(_data(season_type="standard", season_number=bad))
        assert sig.split("-")[5] == "S1"


# ---------------------------------------------------------------------------
# [PLATFORM][EVENT_ID] — source token
# ---------------------------------------------------------------------------

class TestSourceTokenSegment:
    def test_event_id_is_prefixed_with_pol(self):
        sig = compute_structural_signature(_data(event_id="12345"))
        assert sig.rsplit("-", 1)[1] == "POL12345"

    def test_empty_event_id_emits_bare_pol(self):
        sig = compute_structural_signature(_data(event_id=""))
        assert sig.rsplit("-", 1)[1] == "POL"

    def test_none_event_id_emits_bare_pol(self):
        sig = compute_structural_signature(_data(event_id=None))
        assert sig.rsplit("-", 1)[1] == "POL"

    def test_event_slug_is_used_when_event_id_missing(self):
        # The source token is the last segment but a slug may itself contain
        # dashes ("us-election-2024"), so a naive rsplit("-", 1) misparses it.
        # Use split with a max of 6 splits to isolate the source token.
        data = _data(event_id="")
        data["event_slug"] = "us-election-2024"
        sig = compute_structural_signature(data)
        assert sig.split("-", 6)[6] == "POLus-election-2024"

    def test_event_id_is_preserved_verbatim_no_truncation(self):
        # Spec: "preserved as-is from the source."
        long_id = "21742633143463906290569050155826241533067272736897614950488156847949938836455"
        sig = compute_structural_signature(_data(event_id=long_id))
        assert sig.rsplit("-", 1)[1] == f"POL{long_id}"


# ---------------------------------------------------------------------------
# Composition / shape
# ---------------------------------------------------------------------------

class TestSignatureComposition:
    def test_golden_signature(self):
        # If this test fails, every previously-minted Star with this exact
        # input would no longer match its persisted claims.signature.
        sig = compute_structural_signature(_data())
        assert sig == "ICA-6-B5B-U-Ø-S0-POL12345"

    def test_signature_has_seven_dash_separated_segments(self):
        sig = compute_structural_signature(_data())
        assert sig.count("-") == 6
        assert len(sig.split("-")) == 7

    def test_function_is_deterministic(self):
        data = _data()
        first = compute_structural_signature(data)
        for _ in range(50):
            assert compute_structural_signature(data) == first

    def test_function_does_not_mutate_input(self):
        data = _data()
        snapshot = dict(data)
        compute_structural_signature(data)
        assert data == snapshot

    def test_segment_separators_are_only_between_segments(self):
        # No segment may itself contain "-" (would break parsers that split
        # on the separator). Event_id is the one segment that *could* in
        # theory contain dashes from a slug, and that is allowed by spec
        # (the source token is intentionally the last segment).
        sig = compute_structural_signature(_data())
        head, tail = sig.rsplit("-", 1)
        # First six segments must not contain a dash internally.
        assert head.count("-") == 5
