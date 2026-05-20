"""
Unit tests for ``admin_backend.claims_mint.fetch_proxy_profile_identity`` — the
best-effort Polymarket public-profile lookup that stamps a claim's
``x_username`` / ``profile_name`` at queue-insert time.

The network is fully stubbed (``urllib.request.urlopen`` is monkeypatched), so
these tests are hermetic and never touch gamma-api.polymarket.com. The contract
under test is: a clean 2xx hit yields the (xUsername, name) pair; everything
else — empty wallet, missing profile (403/404), network error, timeout, bad
JSON, non-dict body — degrades to ``(None, None)`` WITHOUT raising, so a flaky
profile lookup can never block a mint.
"""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

import admin_backend.claims_mint as claims_mint
from admin_backend.claims_mint import fetch_proxy_profile_identity

_WALLET = "0x" + "ab" * 20


class _FakeResponse:
    """Context-manager stand-in for the object urlopen returns."""

    def __init__(self, body: str):
        self._buf = io.BytesIO(body.encode("utf-8"))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._buf.read()


def _patch_urlopen(monkeypatch, *, body=None, exc=None):
    """Make claims_mint's urlopen return ``body`` or raise ``exc``."""

    def fake_urlopen(req, timeout=None):
        if exc is not None:
            raise exc
        return _FakeResponse(body)

    monkeypatch.setattr(claims_mint.urllib.request, "urlopen", fake_urlopen)


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------

def test_full_profile_returns_both_fields(monkeypatch):
    _patch_urlopen(monkeypatch, body=json.dumps({
        "proxyWallet": _WALLET, "xUsername": "sat0shi", "name": "Satoshi",
    }))
    assert fetch_proxy_profile_identity(_WALLET) == ("sat0shi", "Satoshi")


def test_only_name_present(monkeypatch):
    _patch_urlopen(monkeypatch, body=json.dumps({"name": "Hal"}))
    assert fetch_proxy_profile_identity(_WALLET) == (None, "Hal")


def test_only_x_username_present(monkeypatch):
    _patch_urlopen(monkeypatch, body=json.dumps({"xUsername": "hal"}))
    assert fetch_proxy_profile_identity(_WALLET) == ("hal", None)


def test_blank_and_whitespace_fields_become_none(monkeypatch):
    _patch_urlopen(monkeypatch, body=json.dumps({"xUsername": "  ", "name": ""}))
    assert fetch_proxy_profile_identity(_WALLET) == (None, None)


def test_values_are_stripped(monkeypatch):
    _patch_urlopen(monkeypatch, body=json.dumps({"xUsername": "  ace ", "name": " Ada "}))
    assert fetch_proxy_profile_identity(_WALLET) == ("ace", "Ada")


def test_empty_object_returns_none_pair(monkeypatch):
    _patch_urlopen(monkeypatch, body="{}")
    assert fetch_proxy_profile_identity(_WALLET) == (None, None)


# ---------------------------------------------------------------------------
# Input guards — no network call at all for an empty wallet
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("wallet", [None, "", "   "])
def test_empty_wallet_short_circuits_without_network(monkeypatch, wallet):
    def explode(*a, **k):  # pragma: no cover - must never run
        raise AssertionError("urlopen must not be called for an empty wallet")

    monkeypatch.setattr(claims_mint.urllib.request, "urlopen", explode)
    assert fetch_proxy_profile_identity(wallet) == (None, None)


# ---------------------------------------------------------------------------
# Failure modes — every one degrades to (None, None), never raises
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("code", [403, 404])
def test_missing_profile_4xx_returns_none_pair(monkeypatch, code):
    err = urllib.error.HTTPError(
        url="http://x", code=code, msg="nope", hdrs=None, fp=io.BytesIO(b"")
    )
    _patch_urlopen(monkeypatch, exc=err)
    assert fetch_proxy_profile_identity(_WALLET) == (None, None)


@pytest.mark.parametrize("code", [429, 500, 502, 503])
def test_other_http_errors_return_none_pair(monkeypatch, code):
    err = urllib.error.HTTPError(
        url="http://x", code=code, msg="boom", hdrs=None, fp=io.BytesIO(b"")
    )
    _patch_urlopen(monkeypatch, exc=err)
    assert fetch_proxy_profile_identity(_WALLET) == (None, None)


def test_url_error_returns_none_pair(monkeypatch):
    _patch_urlopen(monkeypatch, exc=urllib.error.URLError("dns boom"))
    assert fetch_proxy_profile_identity(_WALLET) == (None, None)


def test_timeout_returns_none_pair(monkeypatch):
    _patch_urlopen(monkeypatch, exc=TimeoutError("slow"))
    assert fetch_proxy_profile_identity(_WALLET) == (None, None)


def test_bad_json_returns_none_pair(monkeypatch):
    _patch_urlopen(monkeypatch, body="<html>not json</html>")
    assert fetch_proxy_profile_identity(_WALLET) == (None, None)


def test_non_dict_json_returns_none_pair(monkeypatch):
    _patch_urlopen(monkeypatch, body=json.dumps(["unexpected", "list"]))
    assert fetch_proxy_profile_identity(_WALLET) == (None, None)


def test_empty_body_returns_none_pair(monkeypatch):
    # urlopen succeeds but returns no bytes — json.loads("" or "{}") -> {}.
    _patch_urlopen(monkeypatch, body="")
    assert fetch_proxy_profile_identity(_WALLET) == (None, None)
