"""
Unit tests for the R2-primary / Pinata-fallback image URL rewriting that fixes
the gateway.pinata.cloud 429 problem on the card ticker and detail pages.

Two layers:
  1. The pure URL builder ``scripts.cardgen.assets.ipfs_backup_public_url`` —
     proves the ``{base}/{R2_PREFIX}/ipfs-backup/<cid>.png`` contract the
     backend relies on. The root conftest stubs ``scripts.cardgen.assets`` with
     a MagicMock, so we temporarily swap in the real module and restore it.
  2. The backend branching (``_ipfs_r2_mirror_url`` / ``_image_fallback_url`` /
     ``_image_primary_and_fallback`` / ``_absolute_asset_url``) with the assets
     helper monkeypatched, so we test our logic, not boto3/env.
"""

from __future__ import annotations

import importlib
import sys
from unittest.mock import MagicMock

import pytest

import user_web_backend.main as main

_GATEWAY = "https://gateway.pinata.cloud/ipfs/bafyCID123"
_MIRROR = "https://cdn.example/prod/ipfs-backup/bafyCID123.png"


@pytest.fixture()
def stub_mirror(monkeypatch):
    """Replace the (mocked) assets helper with a deterministic URL builder."""
    monkeypatch.setattr(
        main, "ipfs_backup_public_url",
        lambda cid, ext="png": f"https://cdn.example/prod/ipfs-backup/{cid}.{ext}",
    )


# ---------------------------------------------------------------------------
# Layer 1 — real assets URL builder contract
# ---------------------------------------------------------------------------

def test_ipfs_backup_public_url_real_format(monkeypatch):
    monkeypatch.setenv("R2_ENDPOINT", "https://acc.r2.cloudflarestorage.com")
    monkeypatch.setenv("R2_BUCKET", "bucket")
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "key")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setenv("R2_PUBLIC_BASE_URL", "https://cdn.polystars.app")
    monkeypatch.setenv("R2_PREFIX", "prod")

    saved = sys.modules.get("scripts.cardgen.assets")
    sys.modules.pop("scripts.cardgen.assets", None)
    try:
        real_assets = importlib.import_module("scripts.cardgen.assets")
        url = real_assets.ipfs_backup_public_url("bafyCID123", "png")
        assert url == "https://cdn.polystars.app/prod/ipfs-backup/bafyCID123.png"
    finally:
        # Restore the MagicMock stub so other unit tests stay hermetic.
        if saved is not None:
            sys.modules["scripts.cardgen.assets"] = saved
        else:
            sys.modules.pop("scripts.cardgen.assets", None)


# ---------------------------------------------------------------------------
# Layer 2 — backend branching
# ---------------------------------------------------------------------------

class TestMirrorUrl:
    def test_gateway_url_maps_to_mirror(self, stub_mirror):
        assert main._ipfs_r2_mirror_url(_GATEWAY) == _MIRROR

    def test_ipfs_scheme_maps_to_mirror(self, stub_mirror):
        assert main._ipfs_r2_mirror_url("ipfs://bafyCID123") == _MIRROR

    def test_subpath_is_dropped_to_file_cid(self, stub_mirror):
        # Mirror is keyed by the bare file CID, not any sub-path.
        assert main._ipfs_r2_mirror_url("ipfs://ipfs/bafyCID123/x.png") == _MIRROR

    def test_non_ipfs_returns_none(self, stub_mirror):
        assert main._ipfs_r2_mirror_url("https://cdn.example/cards/x.png") is None

    def test_empty_returns_none(self, stub_mirror):
        assert main._ipfs_r2_mirror_url("") is None

    def test_r2_unconfigured_falls_back_to_none(self, monkeypatch):
        def boom(*a, **k):
            raise ValueError("R2 env vars missing")
        monkeypatch.setattr(main, "ipfs_backup_public_url", boom)
        assert main._ipfs_r2_mirror_url(_GATEWAY) is None


class TestAbsoluteAssetUrl:
    def test_ipfs_prefers_mirror(self, stub_mirror):
        assert main._absolute_asset_url(MagicMock(), _GATEWAY) == _MIRROR

    def test_ipfs_falls_back_to_gateway_when_r2_off(self, monkeypatch):
        monkeypatch.setattr(
            main, "ipfs_backup_public_url",
            MagicMock(side_effect=ValueError("missing")),
        )
        out = main._absolute_asset_url(MagicMock(), _GATEWAY)
        assert out == "https://gateway.pinata.cloud/ipfs/bafyCID123"

    def test_plain_http_passes_through(self, stub_mirror):
        url = "https://cdn.example/cards/x.png"
        assert main._absolute_asset_url(MagicMock(), url) == url


class TestFallbackUrl:
    def test_fallback_is_gateway_when_mirror_used(self, stub_mirror):
        assert main._image_fallback_url(_GATEWAY) == "https://gateway.pinata.cloud/ipfs/bafyCID123"

    def test_no_fallback_for_non_ipfs(self, stub_mirror):
        assert main._image_fallback_url("https://cdn.example/x.png") is None

    def test_no_fallback_when_r2_off(self, monkeypatch):
        monkeypatch.setattr(
            main, "ipfs_backup_public_url",
            MagicMock(side_effect=ValueError("missing")),
        )
        # Primary is already the gateway, so a duplicate fallback is noise.
        assert main._image_fallback_url(_GATEWAY) is None


class TestPrimaryAndFallback:
    def test_ipfs_returns_mirror_and_gateway(self, stub_mirror):
        primary, fallback = main._image_primary_and_fallback(_GATEWAY)
        assert primary == _MIRROR
        assert fallback == "https://gateway.pinata.cloud/ipfs/bafyCID123"

    def test_non_ipfs_returns_url_and_no_fallback(self, stub_mirror):
        primary, fallback = main._image_primary_and_fallback("https://cdn.example/x.png")
        assert primary == "https://cdn.example/x.png"
        assert fallback is None

    def test_empty_returns_none_pair(self, stub_mirror):
        assert main._image_primary_and_fallback("") == (None, None)
