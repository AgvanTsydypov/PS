"""Regression test for the texture-independent turntable compositor.

`scripts.cardgen.turntable_compose._selftest` builds a synthetic 2-frame
template + two solid textures, runs the homography warp + multiply pipeline,
and asserts the centre pixel of frame 1 is the (red) front texture and is
opaque. The same check ships as a CLI selftest; this wrapper makes pytest
catch any future regression in the math.
"""
import pytest


def test_turntable_compose_selftest():
    pytest.importorskip("numpy")
    pytest.importorskip("PIL")
    from scripts.cardgen import turntable_compose
    turntable_compose._selftest()
