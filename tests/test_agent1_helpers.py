"""
Unit tests for Agent1QuantCardGenerator static helpers.

All methods tested here are pure / static — no API key, no network calls.
"""

import pytest
from scripts.ai.event_card_agent1 import (
    FOUNDATIONAL_TAG_PRIORITY,
    NOISE_TAGS,
    USER_PROMPT_REQUIRED_MARKERS,
    Agent1CardResponse,
    Agent1QuantCardGenerator,
)

gen = Agent1QuantCardGenerator  # alias for brevity


# ---------------------------------------------------------------------------
# _normalize_tags
# ---------------------------------------------------------------------------

class TestNormalizeTags:
    def test_empty_list_returns_empty(self):
        assert gen._normalize_tags([]) == []

    def test_non_list_returns_empty(self):
        assert gen._normalize_tags(None) == []
        assert gen._normalize_tags("Crypto") == []
        assert gen._normalize_tags({"tag": "Crypto"}) == []

    def test_basic_tags_preserved(self):
        assert gen._normalize_tags(["Crypto", "Politics"]) == ["Crypto", "Politics"]

    def test_none_items_skipped(self):
        assert gen._normalize_tags([None, "Crypto", None]) == ["Crypto"]

    def test_empty_string_items_skipped(self):
        assert gen._normalize_tags(["", "Crypto", "  "]) == ["Crypto"]

    def test_whitespace_stripped(self):
        result = gen._normalize_tags(["  Crypto  ", "  Politics  "])
        assert result == ["Crypto", "Politics"]

    def test_case_insensitive_dedup_keeps_first(self):
        result = gen._normalize_tags(["Crypto", "crypto", "CRYPTO"])
        assert result == ["Crypto"]

    def test_original_case_preserved(self):
        result = gen._normalize_tags(["Pop Culture", "pop culture"])
        assert result == ["Pop Culture"]

    def test_mixed_valid_and_invalid(self):
        result = gen._normalize_tags([None, "", "  ", "Finance", "Sports"])
        assert result == ["Finance", "Sports"]

    def test_ordering_preserved(self):
        tags = ["World", "Politics", "Crypto"]
        assert gen._normalize_tags(tags) == tags


# ---------------------------------------------------------------------------
# _is_recurring
# ---------------------------------------------------------------------------

class TestIsRecurring:
    def test_none_is_not_recurring(self):
        assert gen._is_recurring(None) is False

    def test_empty_string_is_not_recurring(self):
        assert gen._is_recurring("") is False

    def test_string_daily_is_recurring(self):
        assert gen._is_recurring("daily") is True

    def test_string_weekly_is_recurring(self):
        assert gen._is_recurring("weekly") is True

    def test_string_biweekly_is_recurring(self):
        # "biweekly" contains "weekly"
        assert gen._is_recurring("biweekly") is True

    def test_string_monthly_is_not_recurring(self):
        assert gen._is_recurring("monthly") is False

    def test_string_case_insensitive(self):
        assert gen._is_recurring("DAILY Updates") is True
        assert gen._is_recurring("Weekly Tracker") is True

    def test_dict_with_recurrence_key(self):
        assert gen._is_recurring({"recurrence": "daily"}) is True

    def test_dict_with_title_key(self):
        assert gen._is_recurring({"title": "US Weekly Tracker"}) is True

    def test_dict_with_slug_key(self):
        assert gen._is_recurring({"slug": "btc-price-daily"}) is True

    def test_empty_dict_is_not_recurring(self):
        assert gen._is_recurring({}) is False

    def test_dict_monthly_is_not_recurring(self):
        assert gen._is_recurring({"recurrence": "monthly"}) is False

    def test_list_of_strings(self):
        assert gen._is_recurring(["daily updates", "market"]) is True

    def test_list_of_dicts(self):
        assert gen._is_recurring([{"recurrence": "weekly"}]) is True

    def test_list_no_recurring_keywords(self):
        assert gen._is_recurring(["US Election 2024", "Politics"]) is False

    def test_list_empty(self):
        assert gen._is_recurring([]) is False

    def test_series_subtitle_weekly(self):
        assert gen._is_recurring({"subtitle": "Every Week"}) is False  # "week" != "weekly"
        assert gen._is_recurring({"subtitle": "weekly recap"}) is True


# ---------------------------------------------------------------------------
# _clean_title
# ---------------------------------------------------------------------------

class TestCleanTitle:
    def test_short_title_unchanged(self):
        assert gen._clean_title("Bitcoin Price", "fallback") == "Bitcoin Price"

    def test_question_mark_removed(self):
        assert gen._clean_title("Will BTC hit 100k?", "fb") == "Will BTC hit 100k"

    def test_ellipsis_removed(self):
        assert gen._clean_title("BTC price update...", "fb") == "BTC price update"

    def test_long_title_truncated_to_7_words(self):
        title = "Will Bitcoin price hit one hundred thousand dollars this year"
        result = gen._clean_title(title, "fallback")
        assert len(result.split()) == 7

    def test_exactly_7_words_unchanged(self):
        title = "Bitcoin Price Rally Hits New All-Time"
        assert gen._clean_title(title, "fb") == title

    def test_multiple_spaces_collapsed(self):
        result = gen._clean_title("Bitcoin   Price   Update", "fb")
        assert "  " not in result

    def test_empty_title_uses_fallback(self):
        result = gen._clean_title("", "US Election 2024 Outcome")
        assert "US" in result

    def test_none_title_uses_fallback(self):
        result = gen._clean_title(None, "Fallback Title")
        assert result == "Fallback Title"

    def test_empty_fallback_uses_default(self):
        result = gen._clean_title("", "")
        assert result == "Oracle Signal Card"

    def test_fallback_also_truncated_to_7_words(self):
        long_fallback = "one two three four five six seven eight nine ten"
        result = gen._clean_title("", long_fallback)
        assert len(result.split()) == 7


# ---------------------------------------------------------------------------
# _clean_lore
# ---------------------------------------------------------------------------

class TestCleanLore:
    def test_valid_short_lore_unchanged(self):
        lore = "Short lore text."
        assert gen._clean_lore(lore, recurring=False) == lore

    def test_max_3_sentences(self):
        lore = "Sentence one. Sentence two. Sentence three. Sentence four."
        result = gen._clean_lore(lore, recurring=False)
        assert result.count(".") <= 3

    def test_exactly_3_sentences_preserved(self):
        lore = "First sentence. Second sentence. Third sentence."
        result = gen._clean_lore(lore, recurring=False)
        assert result == lore

    def test_max_49_words(self):
        lore = " ".join(["word"] * 60) + "."
        result = gen._clean_lore(lore, recurring=False)
        assert len(result.split()) <= 50  # 49 + potential period

    def test_truncated_lore_ends_with_period(self):
        lore = " ".join(["word"] * 60)
        result = gen._clean_lore(lore, recurring=False)
        assert result.endswith(".")

    def test_empty_lore_uses_non_recurring_default(self):
        result = gen._clean_lore("", recurring=False)
        assert "single" in result.lower() or "resolution" in result.lower() or "contract" in result.lower()

    def test_empty_lore_uses_recurring_default(self):
        result = gen._clean_lore("", recurring=True)
        assert "recurring" in result.lower() or "daily" in result.lower() or "weekly" in result.lower()

    def test_none_lore_uses_default(self):
        result = gen._clean_lore(None, recurring=False)
        assert result  # non-empty

    def test_multiple_spaces_collapsed(self):
        lore = "Word   word    word."
        result = gen._clean_lore(lore, recurring=False)
        assert "  " not in result

    def test_lore_ending_in_period_not_double_period(self):
        lore = "Valid lore sentence."
        result = gen._clean_lore(lore, recurring=False)
        assert not result.endswith("..")


# ---------------------------------------------------------------------------
# _choose_primary_tag
# ---------------------------------------------------------------------------

class TestChoosePrimaryTag:
    def test_empty_tags_raises(self):
        with pytest.raises(ValueError, match="empty"):
            gen._choose_primary_tag([])

    def test_priority_tag_chosen_first(self):
        tags = ["Memes", "Crypto", "Finance"]
        assert gen._choose_primary_tag(tags) == "Crypto"

    def test_priority_order_respected(self):
        # Politics > Crypto per FOUNDATIONAL_TAG_PRIORITY
        tags = ["Crypto", "Politics"]
        assert gen._choose_primary_tag(tags) == "Politics"

    def test_case_insensitive_priority_match(self):
        tags = ["crypto"]
        assert gen._choose_primary_tag(tags) == "crypto"

    def test_no_priority_match_returns_first_non_noise(self):
        tags = ["recurring", "Football League"]
        result = gen._choose_primary_tag(tags)
        assert result == "Football League"

    def test_all_noise_returns_first_tag(self):
        noise = list(NOISE_TAGS)[:3]
        result = gen._choose_primary_tag(noise)
        assert result == noise[0]

    def test_single_tag_returned(self):
        assert gen._choose_primary_tag(["Sports"]) == "Sports"

    def test_preserves_original_case(self):
        tags = ["SPORTS"]
        assert gen._choose_primary_tag(tags) == "SPORTS"


# ---------------------------------------------------------------------------
# _choose_secondary_tag
# ---------------------------------------------------------------------------

class TestChooseSecondaryTag:
    def test_single_tag_returns_none(self):
        assert gen._choose_secondary_tag(["Crypto"], "Crypto") is None

    def test_two_tags_returns_other(self):
        result = gen._choose_secondary_tag(["Crypto", "Finance"], "Crypto")
        assert result == "Finance"

    def test_excludes_primary_case_insensitive(self):
        result = gen._choose_secondary_tag(["CRYPTO", "Finance"], "crypto")
        assert result == "Finance"

    def test_prefers_longer_non_noise_tag(self):
        tags = ["Crypto", "US Presidential Election", "Finance"]
        result = gen._choose_secondary_tag(tags, "Crypto")
        assert result == "US Presidential Election"

    def test_noise_tags_excluded_from_preference(self):
        tags = ["Crypto", "recurring", "Finance"]
        result = gen._choose_secondary_tag(tags, "Crypto")
        assert result == "Finance"

    def test_all_candidates_noise_returns_first(self):
        noise = list(NOISE_TAGS)
        # primary is something else
        tags = ["Primary"] + noise
        result = gen._choose_secondary_tag(tags, "Primary")
        assert result == noise[0]

    def test_none_when_all_tags_are_primary(self):
        assert gen._choose_secondary_tag(["Crypto", "crypto"], "Crypto") is None


# ---------------------------------------------------------------------------
# _enforce_constraints  (instance method — needs a bare instance)
# ---------------------------------------------------------------------------

def _make_gen():
    from unittest.mock import patch, MagicMock
    with patch("scripts.ai.claude_client.ClaudeJsonClient.__init__", return_value=None):
        g = Agent1QuantCardGenerator.__new__(Agent1QuantCardGenerator)
        g.client = MagicMock()
        g.prompt_version = "v5"
    return g


class TestEnforceConstraints:
    def _raw(self, title="Test Card", lore="Some lore.", primary="Crypto", secondary=None):
        return Agent1CardResponse(
            card_title=title, card_lore=lore, primary_tag=primary, secondary_tag=secondary
        )

    def test_valid_raw_with_matching_tags(self):
        raw = self._raw(primary="Crypto")
        result = _make_gen()._enforce_constraints(raw, "Event Title", ["Crypto", "Finance"], recurring=False)
        assert result.primary_tag == "Crypto"

    def test_primary_not_in_tags_falls_back_to_choose(self):
        raw = self._raw(primary="UNKNOWN_TAG")
        result = _make_gen()._enforce_constraints(raw, "Event", ["Politics", "Crypto"], recurring=False)
        assert result.primary_tag == "Politics"

    def test_clean_title_applied(self):
        raw = self._raw(title="Is this going to happen?")
        result = _make_gen()._enforce_constraints(raw, "Fallback", ["Crypto"], recurring=False)
        assert "?" not in result.card_title

    def test_clean_lore_applied_max_sentences(self):
        lore = "S1. S2. S3. S4."
        raw = self._raw(lore=lore)
        result = _make_gen()._enforce_constraints(raw, "E", ["Crypto"], recurring=False)
        assert result.card_lore.count(".") <= 3

    def test_valid_secondary_in_tags_preserved(self):
        raw = self._raw(primary="Crypto", secondary="Finance")
        result = _make_gen()._enforce_constraints(raw, "E", ["Crypto", "Finance"], recurring=False)
        assert result.secondary_tag == "Finance"

    def test_invalid_secondary_not_in_tags_replaced(self):
        raw = self._raw(primary="Crypto", secondary="NONEXISTENT")
        result = _make_gen()._enforce_constraints(raw, "E", ["Crypto", "Finance"], recurring=False)
        assert result.secondary_tag == "Finance"

    def test_secondary_equals_primary_replaced(self):
        raw = self._raw(primary="Crypto", secondary="Crypto")
        result = _make_gen()._enforce_constraints(raw, "E", ["Crypto", "Finance"], recurring=False)
        assert result.secondary_tag != "Crypto"

    def test_single_tag_secondary_is_none(self):
        raw = self._raw(primary="Crypto", secondary="Finance")
        result = _make_gen()._enforce_constraints(raw, "E", ["Crypto"], recurring=False)
        assert result.secondary_tag is None


# ---------------------------------------------------------------------------
# _validate_user_prompt_structure
# ---------------------------------------------------------------------------

class TestValidateUserPromptStructure:
    def _valid_prompt(self):
        return "\n".join(USER_PROMPT_REQUIRED_MARKERS)

    def test_valid_prompt_no_exception(self):
        gen._validate_user_prompt_structure(self._valid_prompt())

    def test_missing_one_marker_raises(self):
        prompt = self._valid_prompt().replace(USER_PROMPT_REQUIRED_MARKERS[0], "")
        with pytest.raises(ValueError, match=USER_PROMPT_REQUIRED_MARKERS[0]):
            gen._validate_user_prompt_structure(prompt)

    def test_empty_prompt_raises_with_all_markers_listed(self):
        with pytest.raises(ValueError):
            gen._validate_user_prompt_structure("")

    def test_all_markers_present(self):
        prompt = " ".join(USER_PROMPT_REQUIRED_MARKERS)
        gen._validate_user_prompt_structure(prompt)


# ---------------------------------------------------------------------------
# build_prompt_context
# ---------------------------------------------------------------------------

class TestBuildPromptContext:
    def _payload(self, **kwargs):
        base = {
            "title": "Will BTC hit 100k",
            "description": "Bitcoin price prediction market",
            "series": None,
            "tags": ["Crypto", "Finance"],
        }
        base.update(kwargs)
        return base

    def test_raises_on_missing_tags(self):
        with pytest.raises(ValueError, match="tag"):
            gen._normalize_tags.__func__ if False else None  # dummy
            payload = self._payload(tags=[])
            # build_prompt_context calls _normalize_tags internally → raises
            from scripts.ai.event_card_agent1 import Agent1QuantCardGenerator as G
            G.__new__(G).build_prompt_context(payload)

    def test_required_keys_in_result(self):
        from unittest.mock import patch, MagicMock
        with patch("scripts.ai.claude_client.ClaudeJsonClient.__init__", return_value=None):
            g = Agent1QuantCardGenerator.__new__(Agent1QuantCardGenerator)
            g.client = MagicMock()
            g.prompt_version = "v5"
        result = g.build_prompt_context(self._payload())
        for key in ("event_title", "event_description", "tags", "recurring",
                    "prompt", "system_instruction", "full_prompt"):
            assert key in result, f"Missing key: {key}"

    def test_all_required_markers_in_prompt(self):
        from unittest.mock import patch, MagicMock
        with patch("scripts.ai.claude_client.ClaudeJsonClient.__init__", return_value=None):
            g = Agent1QuantCardGenerator.__new__(Agent1QuantCardGenerator)
            g.client = MagicMock()
            g.prompt_version = "v5"
        result = g.build_prompt_context(self._payload())
        prompt = result["prompt"]
        for marker in USER_PROMPT_REQUIRED_MARKERS:
            assert marker in prompt, f"Marker missing from prompt: {marker!r}"

    def test_recurring_detected_from_series_string(self):
        from unittest.mock import patch, MagicMock
        with patch("scripts.ai.claude_client.ClaudeJsonClient.__init__", return_value=None):
            g = Agent1QuantCardGenerator.__new__(Agent1QuantCardGenerator)
            g.client = MagicMock()
            g.prompt_version = "v5"
        result = g.build_prompt_context(self._payload(series="daily update"))
        assert result["recurring"] is True

    def test_non_recurring_series(self):
        from unittest.mock import patch, MagicMock
        with patch("scripts.ai.claude_client.ClaudeJsonClient.__init__", return_value=None):
            g = Agent1QuantCardGenerator.__new__(Agent1QuantCardGenerator)
            g.client = MagicMock()
            g.prompt_version = "v5"
        result = g.build_prompt_context(self._payload(series="US Election"))
        assert result["recurring"] is False

    def test_tags_normalized_in_result(self):
        from unittest.mock import patch, MagicMock
        with patch("scripts.ai.claude_client.ClaudeJsonClient.__init__", return_value=None):
            g = Agent1QuantCardGenerator.__new__(Agent1QuantCardGenerator)
            g.client = MagicMock()
            g.prompt_version = "v5"
        result = g.build_prompt_context(self._payload(tags=["Crypto", "crypto", None, ""]))
        assert result["tags"] == ["Crypto"]
