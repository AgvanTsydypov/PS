"""
Unit tests for pure env-parsing helpers in scripts/daily_scheduler_simple.py.

All functions are simple env-var readers with bounds/defaults —
no DB, no network, no subprocess.
"""

import os
from unittest.mock import patch

import pytest


def _import_helpers():
    """Import the three pure helpers lazily to avoid top-level import issues."""
    from scripts.daily_scheduler_simple import (
        _env_bool,
        _env_int,
        _snapshot_candidate_fetch_batch_size,
        _snapshot_candidate_pool_multiplier,
        _snapshot_max_single_event_share,
    )
    return (
        _env_bool,
        _env_int,
        _snapshot_max_single_event_share,
        _snapshot_candidate_pool_multiplier,
        _snapshot_candidate_fetch_batch_size,
    )


# ---------------------------------------------------------------------------
# _env_bool
# ---------------------------------------------------------------------------

class TestEnvBool:
    @property
    def fn(self):
        return _import_helpers()[0]

    def test_not_set_returns_default_true(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("_TEST_BOOL_VAR", None)
            assert self.fn("_TEST_BOOL_VAR", True) is True

    def test_not_set_returns_default_false(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("_TEST_BOOL_VAR", None)
            assert self.fn("_TEST_BOOL_VAR", False) is False

    def test_true_values(self):
        for val in ("1", "true", "True", "TRUE", "yes", "YES", "on", "ON"):
            with patch.dict(os.environ, {"_TEST_BOOL_VAR": val}):
                assert self.fn("_TEST_BOOL_VAR", False) is True, f"Expected True for {val!r}"

    def test_false_values(self):
        for val in ("0", "false", "False", "FALSE", "no", "NO", "off", "OFF"):
            with patch.dict(os.environ, {"_TEST_BOOL_VAR": val}):
                assert self.fn("_TEST_BOOL_VAR", True) is False, f"Expected False for {val!r}"

    def test_unknown_value_returns_false(self):
        with patch.dict(os.environ, {"_TEST_BOOL_VAR": "maybe"}):
            assert self.fn("_TEST_BOOL_VAR", True) is False

    def test_whitespace_stripped_before_check(self):
        with patch.dict(os.environ, {"_TEST_BOOL_VAR": "  true  "}):
            assert self.fn("_TEST_BOOL_VAR", False) is True

    def test_empty_string_returns_false(self):
        with patch.dict(os.environ, {"_TEST_BOOL_VAR": ""}):
            assert self.fn("_TEST_BOOL_VAR", True) is False


# ---------------------------------------------------------------------------
# _env_int
# ---------------------------------------------------------------------------

class TestEnvInt:
    @property
    def fn(self):
        return _import_helpers()[1]

    def test_not_set_returns_default(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("_TEST_INT_VAR", None)
            assert self.fn("_TEST_INT_VAR", 42) == 42

    def test_valid_int_returned(self):
        with patch.dict(os.environ, {"_TEST_INT_VAR": "7"}):
            assert self.fn("_TEST_INT_VAR", 0) == 7

    def test_zero_returned(self):
        with patch.dict(os.environ, {"_TEST_INT_VAR": "0"}):
            assert self.fn("_TEST_INT_VAR", 5) == 0

    def test_negative_int_returned(self):
        with patch.dict(os.environ, {"_TEST_INT_VAR": "-3"}):
            assert self.fn("_TEST_INT_VAR", 0) == -3

    def test_empty_string_returns_default(self):
        with patch.dict(os.environ, {"_TEST_INT_VAR": ""}):
            assert self.fn("_TEST_INT_VAR", 99) == 99

    def test_whitespace_only_returns_default(self):
        with patch.dict(os.environ, {"_TEST_INT_VAR": "   "}):
            assert self.fn("_TEST_INT_VAR", 99) == 99

    def test_whitespace_around_number_parsed(self):
        # int("  5  ".strip()) = 5
        with patch.dict(os.environ, {"_TEST_INT_VAR": "  5  "}):
            assert self.fn("_TEST_INT_VAR", 0) == 5


# ---------------------------------------------------------------------------
# _snapshot_max_single_event_share
# ---------------------------------------------------------------------------

class TestSnapshotMaxSingleEventShare:
    @property
    def fn(self):
        return _import_helpers()[2]

    ENV = "POLYSTARS_SNAPSHOT_MAX_SINGLE_EVENT_SHARE"

    def test_not_set_returns_default(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(self.ENV, None)
            assert self.fn() == pytest.approx(0.5)

    def test_valid_float_returned(self):
        with patch.dict(os.environ, {self.ENV: "0.3"}):
            assert self.fn() == pytest.approx(0.3)

    def test_max_value_1_returned(self):
        with patch.dict(os.environ, {self.ENV: "1"}):
            assert self.fn() == pytest.approx(1.0)

    def test_above_1_returns_default(self):
        with patch.dict(os.environ, {self.ENV: "1.5"}):
            assert self.fn() == pytest.approx(0.5)

    def test_zero_returns_default(self):
        with patch.dict(os.environ, {self.ENV: "0"}):
            assert self.fn() == pytest.approx(0.5)

    def test_negative_returns_default(self):
        with patch.dict(os.environ, {self.ENV: "-0.1"}):
            assert self.fn() == pytest.approx(0.5)

    def test_invalid_string_returns_default(self):
        with patch.dict(os.environ, {self.ENV: "not_a_float"}):
            assert self.fn() == pytest.approx(0.5)

    def test_empty_string_returns_default(self):
        with patch.dict(os.environ, {self.ENV: ""}):
            assert self.fn() == pytest.approx(0.5)

    def test_min_valid_value(self):
        with patch.dict(os.environ, {self.ENV: "0.01"}):
            assert self.fn() == pytest.approx(0.01)


# ---------------------------------------------------------------------------
# _snapshot_candidate_pool_multiplier
# ---------------------------------------------------------------------------

class TestSnapshotCandidatePoolMultiplier:
    @property
    def fn(self):
        return _import_helpers()[3]

    ENV = "POLYSTARS_SNAPSHOT_CANDIDATE_POOL_MULTIPLIER"

    def test_not_set_returns_default(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(self.ENV, None)
            assert self.fn() == 8

    def test_valid_value_within_bounds(self):
        with patch.dict(os.environ, {self.ENV: "10"}):
            assert self.fn() == 10

    def test_value_below_min_clamped_to_2(self):
        with patch.dict(os.environ, {self.ENV: "1"}):
            assert self.fn() == 2

    def test_value_at_min_boundary(self):
        with patch.dict(os.environ, {self.ENV: "2"}):
            assert self.fn() == 2

    def test_value_above_max_clamped_to_50(self):
        with patch.dict(os.environ, {self.ENV: "100"}):
            assert self.fn() == 50

    def test_value_at_max_boundary(self):
        with patch.dict(os.environ, {self.ENV: "50"}):
            assert self.fn() == 50

    def test_invalid_string_returns_default(self):
        with patch.dict(os.environ, {self.ENV: "bad"}):
            assert self.fn() == 8

    def test_empty_string_returns_default(self):
        with patch.dict(os.environ, {self.ENV: ""}):
            assert self.fn() == 8

    def test_zero_clamped_to_2(self):
        with patch.dict(os.environ, {self.ENV: "0"}):
            assert self.fn() == 2


# ---------------------------------------------------------------------------
# _snapshot_candidate_fetch_batch_size
# ---------------------------------------------------------------------------

class TestSnapshotCandidateFetchBatchSize:
    @property
    def fn(self):
        return _import_helpers()[4]

    ENV = "POLYSTARS_SNAPSHOT_CANDIDATE_FETCH_BATCH_SIZE"

    def test_not_set_returns_default(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(self.ENV, None)
            assert self.fn() == 2000

    def test_valid_value_within_bounds(self):
        with patch.dict(os.environ, {self.ENV: "500"}):
            assert self.fn() == 500

    def test_value_below_min_clamped_to_100(self):
        with patch.dict(os.environ, {self.ENV: "50"}):
            assert self.fn() == 100

    def test_value_at_min_boundary(self):
        with patch.dict(os.environ, {self.ENV: "100"}):
            assert self.fn() == 100

    def test_value_above_max_clamped_to_20000(self):
        with patch.dict(os.environ, {self.ENV: "99999"}):
            assert self.fn() == 20000

    def test_value_at_max_boundary(self):
        with patch.dict(os.environ, {self.ENV: "20000"}):
            assert self.fn() == 20000

    def test_invalid_string_returns_default(self):
        with patch.dict(os.environ, {self.ENV: "bad"}):
            assert self.fn() == 2000

    def test_empty_string_returns_default(self):
        with patch.dict(os.environ, {self.ENV: ""}):
            assert self.fn() == 2000

    def test_zero_clamped_to_100(self):
        with patch.dict(os.environ, {self.ENV: "0"}):
            assert self.fn() == 100
