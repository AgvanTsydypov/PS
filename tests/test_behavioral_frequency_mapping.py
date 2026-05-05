"""
Lock-in tests for the archetype -> rarity_bracket mapping in the
participants_analytics view (sql/schemas/init-db.sql).

These tests parse the SQL file directly so they run without a live DB.
They exist to catch unintended drift in the BEHAVIORAL FREQUENCY copy
or in the per-archetype frequency numbers.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = REPO_ROOT / "sql" / "schemas" / "init-db.sql"

# Canonical mapping: archetype -> exact rarity_bracket string emitted by the view.
EXPECTED_FREQUENCIES: dict[str, str] = {
    "INSIDER":     "BEHAVIORAL FREQUENCY: ~ 0.2%",
    "ANOMALY":     "BEHAVIORAL FREQUENCY: ~ 0.5%",
    "EXTRACTOR":   "BEHAVIORAL FREQUENCY: ~ 1.0%",
    "ICARUS":      "BEHAVIORAL FREQUENCY: ~ 1.0%",
    "SIGNAL":      "BEHAVIORAL FREQUENCY: ~ 2.0%",
    "GRAVITON":    "BEHAVIORAL FREQUENCY: ~ 2.0%",
    "VECTOR":      "BEHAVIORAL FREQUENCY: ~ 2.0%",
    "BURNER":      "BEHAVIORAL FREQUENCY: ~ 3.0%",
    "EQUILIBRIUM": "BEHAVIORAL FREQUENCY: ~ 4.0%",
    "BOT":         "BEHAVIORAL FREQUENCY: ~ 7.0%",
    "PASSENGER":   "BEHAVIORAL FREQUENCY: ~ 14.0%",
    "OPERATOR":    "BEHAVIORAL FREQUENCY: ~ 17.0%",
    "SUBSTRATE":   "BEHAVIORAL FREQUENCY: ~ 44.0%",
}


@pytest.fixture(scope="module")
def rarity_case_block() -> str:
    """Extract the CASE expression that produces rarity_bracket."""
    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    match = re.search(
        r"CASE at\.archetype\s+(.*?)\s+END AS rarity_bracket",
        sql,
        flags=re.DOTALL,
    )
    assert match, "Failed to locate the rarity_bracket CASE block in init-db.sql"
    return match.group(1)


def _extract_mapping(case_block: str) -> dict[str, str]:
    """Parse `WHEN '<archetype>'::text THEN '<label>'::text` lines."""
    pattern = re.compile(
        r"WHEN\s+'([A-Z]+)'::text\s+THEN\s+'([^']+)'::text",
        re.IGNORECASE,
    )
    return {m.group(1): m.group(2) for m in pattern.finditer(case_block)}


class TestBehavioralFrequencyMapping:
    def test_all_thirteen_archetypes_present(self, rarity_case_block):
        mapping = _extract_mapping(rarity_case_block)
        assert set(mapping.keys()) == set(EXPECTED_FREQUENCIES.keys()), (
            f"Archetype set drift. Got: {sorted(mapping.keys())}"
        )

    @pytest.mark.parametrize(
        "archetype,expected_label",
        list(EXPECTED_FREQUENCIES.items()),
    )
    def test_archetype_maps_to_expected_label(
        self, rarity_case_block, archetype, expected_label
    ):
        mapping = _extract_mapping(rarity_case_block)
        assert mapping[archetype] == expected_label

    def test_every_label_uses_behavioral_frequency_prefix(self, rarity_case_block):
        mapping = _extract_mapping(rarity_case_block)
        bad = {k: v for k, v in mapping.items() if not v.startswith("BEHAVIORAL FREQUENCY:")}
        assert not bad, f"Stale prefix(es) detected: {bad}"

    def test_no_legacy_probability_cohort_remnants(self):
        """The whole schema file should not reference the old label anymore."""
        sql = SCHEMA_PATH.read_text(encoding="utf-8")
        assert "PROBABILITY COHORT" not in sql
        assert "OCCURRENCE" not in sql.upper().replace("RECURRENCE", "")

    def test_frequencies_sum_to_roughly_one_hundred(self):
        """Point estimates are calibrated against observed distribution; sanity-check the total."""
        total = 0.0
        for label in EXPECTED_FREQUENCIES.values():
            m = re.search(r"~\s*([\d.]+)%", label)
            assert m, f"Cannot parse percentage from {label}"
            total += float(m.group(1))
        # 97.7 today; allow [95, 105] slack for future minor recalibration.
        assert 95.0 <= total <= 105.0, f"Frequency sum drifted to {total}"
