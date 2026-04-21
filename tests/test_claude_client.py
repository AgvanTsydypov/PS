"""
Unit tests for ClaudeJsonClient in scripts/ai/claude_client.py.

Covers static parsing helpers, JSON extraction, and __init__ validation.
No actual API calls are made.
"""

import json
import os
from unittest.mock import patch, MagicMock

import pytest
from pydantic import BaseModel

from scripts.ai.claude_client import ClaudeJsonClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_client(model="claude-haiku-4-5-20251001", rpm=15):
    """Instantiate ClaudeJsonClient with a fake API key."""
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key-abc"}):
        return ClaudeJsonClient(model=model, requests_per_minute=rpm)


def _response_payload(*text_blocks):
    """Build a minimal Claude Messages API response dict."""
    return {
        "content": [{"type": "text", "text": t} for t in text_blocks]
    }


# ---------------------------------------------------------------------------
# __init__ validation
# ---------------------------------------------------------------------------

class TestInit:
    def test_missing_api_key_raises(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("ANTHROPIC_API_KEY", None)
            with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
                ClaudeJsonClient()

    def test_zero_rpm_raises(self):
        with pytest.raises(ValueError, match="requests_per_minute"):
            with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "key"}):
                ClaudeJsonClient(requests_per_minute=0)

    def test_negative_rpm_raises(self):
        with pytest.raises(ValueError):
            with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "key"}):
                ClaudeJsonClient(requests_per_minute=-5)

    def test_zero_timeout_raises(self):
        with pytest.raises(ValueError, match="timeout"):
            with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "key"}):
                ClaudeJsonClient(timeout_seconds=0)

    def test_custom_env_var_name(self):
        with patch.dict(os.environ, {"MY_KEY": "custom-key"}):
            client = ClaudeJsonClient(api_key_env_var="MY_KEY")
        assert client.api_key == "custom-key"

    def test_model_stored(self):
        client = _make_client(model="claude-opus-4-7")
        assert client.model == "claude-opus-4-7"

    def test_multiple_clients_same_model_share_lock(self):
        c1 = _make_client()
        c2 = _make_client()
        assert ClaudeJsonClient._model_locks[c1.model] is ClaudeJsonClient._model_locks[c2.model]


# ---------------------------------------------------------------------------
# _extract_text_from_response
# ---------------------------------------------------------------------------

class TestExtractTextFromResponse:
    def test_single_text_block(self):
        payload = _response_payload("hello world")
        result = ClaudeJsonClient._extract_text_from_response(payload)
        assert result == "hello world"

    def test_multiple_text_blocks_joined(self):
        payload = _response_payload("first", "second")
        result = ClaudeJsonClient._extract_text_from_response(payload)
        assert "first" in result
        assert "second" in result

    def test_empty_text_blocks_skipped(self):
        payload = {"content": [
            {"type": "text", "text": ""},
            {"type": "text", "text": "real"},
        ]}
        result = ClaudeJsonClient._extract_text_from_response(payload)
        assert result == "real"

    def test_non_text_block_types_ignored(self):
        payload = {"content": [
            {"type": "tool_use", "id": "tool_1"},
            {"type": "text", "text": "answer"},
        ]}
        result = ClaudeJsonClient._extract_text_from_response(payload)
        assert result == "answer"

    def test_missing_content_raises(self):
        with pytest.raises(ValueError, match="content blocks"):
            ClaudeJsonClient._extract_text_from_response({})

    def test_content_not_list_raises(self):
        with pytest.raises(ValueError, match="content blocks"):
            ClaudeJsonClient._extract_text_from_response({"content": "text"})

    def test_all_empty_blocks_raises(self):
        with pytest.raises(ValueError, match="empty text"):
            ClaudeJsonClient._extract_text_from_response({"content": [
                {"type": "text", "text": ""},
                {"type": "text", "text": "   "},
            ]})

    def test_empty_content_list_raises(self):
        with pytest.raises(ValueError):
            ClaudeJsonClient._extract_text_from_response({"content": []})


# ---------------------------------------------------------------------------
# _strip_code_fences
# ---------------------------------------------------------------------------

class TestStripCodeFences:
    def test_plain_json_unchanged(self):
        text = '{"key": "value"}'
        assert ClaudeJsonClient._strip_code_fences(text) == text

    def test_json_code_fence_stripped(self):
        text = '```json\n{"key": "value"}\n```'
        result = ClaudeJsonClient._strip_code_fences(text)
        assert result == '{"key": "value"}'

    def test_plain_code_fence_stripped(self):
        text = '```\n{"key": "value"}\n```'
        result = ClaudeJsonClient._strip_code_fences(text)
        assert result == '{"key": "value"}'

    def test_uppercase_json_fence_stripped(self):
        text = '```JSON\n{"k": 1}\n```'
        result = ClaudeJsonClient._strip_code_fences(text)
        assert result == '{"k": 1}'

    def test_empty_string_returns_empty(self):
        assert ClaudeJsonClient._strip_code_fences("") == ""

    def test_no_fence_returned_as_is(self):
        text = "some text without fences"
        assert ClaudeJsonClient._strip_code_fences(text) == text

    def test_multiline_json_in_fence(self):
        text = '```json\n{\n  "a": 1,\n  "b": 2\n}\n```'
        result = ClaudeJsonClient._strip_code_fences(text)
        assert result == '{\n  "a": 1,\n  "b": 2\n}'


# ---------------------------------------------------------------------------
# _extract_first_json_object
# ---------------------------------------------------------------------------

class TestExtractFirstJsonObject:
    def test_plain_json_returned(self):
        text = '{"key": "val"}'
        assert ClaudeJsonClient._extract_first_json_object(text) == text

    def test_json_with_leading_text_extracted(self):
        text = 'Here is the result: {"key": "val"}'
        result = ClaudeJsonClient._extract_first_json_object(text)
        assert result == '{"key": "val"}'

    def test_json_with_trailing_text_extracted(self):
        text = '{"key": "val"} and some trailing text'
        result = ClaudeJsonClient._extract_first_json_object(text)
        assert result == '{"key": "val"}'

    def test_nested_object_extracted_correctly(self):
        text = '{"outer": {"inner": 1}}'
        result = ClaudeJsonClient._extract_first_json_object(text)
        assert result == '{"outer": {"inner": 1}}'

    def test_no_json_returns_original(self):
        text = "no json here"
        result = ClaudeJsonClient._extract_first_json_object(text)
        assert result == text

    def test_empty_string_returns_empty(self):
        assert ClaudeJsonClient._extract_first_json_object("") == ""

    def test_escaped_braces_in_string_not_counted(self):
        text = '{"key": "has { brace }"}'
        result = ClaudeJsonClient._extract_first_json_object(text)
        parsed = json.loads(result)
        assert parsed["key"] == "has { brace }"

    def test_only_first_object_extracted(self):
        text = '{"first": 1} {"second": 2}'
        result = ClaudeJsonClient._extract_first_json_object(text)
        assert json.loads(result) == {"first": 1}


# ---------------------------------------------------------------------------
# _normalize_json_text (combines strip + extract)
# ---------------------------------------------------------------------------

class TestNormalizeJsonText:
    def test_fenced_with_preamble(self):
        text = 'Sure, here you go:\n```json\n{"a": 1}\n```'
        result = ClaudeJsonClient._normalize_json_text(text)
        assert json.loads(result) == {"a": 1}

    def test_bare_json_unchanged(self):
        text = '{"x": 2}'
        assert json.loads(ClaudeJsonClient._normalize_json_text(text)) == {"x": 2}

    def test_fenced_json_with_trailing_text(self):
        text = '```json\n{"score": 99}\n``` done'
        result = ClaudeJsonClient._normalize_json_text(text)
        # After stripping fence, no fence match → extract_first still finds it
        parsed = json.loads(result)
        assert parsed["score"] == 99

    def test_empty_input_returns_empty(self):
        assert ClaudeJsonClient._normalize_json_text("") == ""


# ---------------------------------------------------------------------------
# generate_json — integration-level (mocked API call)
# ---------------------------------------------------------------------------

class TestGenerateJson:
    def _patched_generate(self, response_text, schema, **kwargs):
        client = _make_client()
        api_response = _response_payload(response_text)
        with patch.object(client, "_call_messages_api", return_value=api_response):
            return client.generate_json("test prompt", schema, **kwargs)

    def test_returns_dict_for_dict_schema(self):
        schema = {"type": "object", "properties": {"score": {"type": "integer"}}}
        result = self._patched_generate('{"score": 5}', schema)
        assert result == {"score": 5}

    def test_returns_model_for_pydantic_schema(self):
        class MyModel(BaseModel):
            name: str
            value: int

        result = self._patched_generate('{"name": "foo", "value": 42}', MyModel)
        assert isinstance(result, MyModel)
        assert result.name == "foo"
        assert result.value == 42

    def test_fenced_json_parsed_correctly(self):
        schema = {"type": "object", "properties": {"k": {"type": "integer"}}}
        result = self._patched_generate('```json\n{"k": 1}\n```', schema)
        assert result == {"k": 1}

    def test_empty_prompt_raises(self):
        client = _make_client()
        with pytest.raises(ValueError, match="empty"):
            client.generate_json("", {"key": str})

    def test_whitespace_prompt_raises(self):
        client = _make_client()
        with pytest.raises(ValueError):
            client.generate_json("   ", {"key": str})

    def test_invalid_json_raises_after_retries(self):
        client = _make_client()
        schema = {"type": "object", "properties": {"key": {"type": "string"}}}
        api_response = _response_payload("not json at all")
        with (
            patch.object(client, "_call_messages_api", return_value=api_response),
            pytest.raises(ValueError, match="JSON"),
        ):
            client.generate_json("prompt", schema)

    def test_pydantic_schema_mismatch_raises(self):
        class StrictModel(BaseModel):
            required_field: str

        client = _make_client()
        api_response = _response_payload('{"wrong_field": "x"}')
        with (
            patch.object(client, "_call_messages_api", return_value=api_response),
            pytest.raises(ValueError, match="schema"),
        ):
            client.generate_json("prompt", StrictModel)
