"""
Unit tests for pure helper functions in scripts/fetch/fetch_events_config.py.
"""

import os
from unittest.mock import patch

import pytest

from scripts.fetch.fetch_events_config import _env_bool_optional


class TestEnvBoolOptional:
    def test_returns_default_when_not_set(self):
        os.environ.pop("__TEST_BOOL__", None)
        assert _env_bool_optional("__TEST_BOOL__", True) is True
        assert _env_bool_optional("__TEST_BOOL__", None) is None

    def test_true_values(self):
        for val in ("1", "true", "True", "yes", "y", "on"):
            with patch.dict(os.environ, {"__TEST_BOOL__": val}):
                assert _env_bool_optional("__TEST_BOOL__", None) is True

    def test_false_values(self):
        for val in ("0", "false", "False", "no", "n", "off"):
            with patch.dict(os.environ, {"__TEST_BOOL__": val}):
                assert _env_bool_optional("__TEST_BOOL__", None) is False

    def test_none_values(self):
        for val in ("", "none", "null", "any", "all"):
            with patch.dict(os.environ, {"__TEST_BOOL__": val}):
                assert _env_bool_optional("__TEST_BOOL__", True) is None

    def test_unknown_value_returns_default(self):
        with patch.dict(os.environ, {"__TEST_BOOL__": "maybe"}):
            assert _env_bool_optional("__TEST_BOOL__", True) is True
            assert _env_bool_optional("__TEST_BOOL__", False) is False
