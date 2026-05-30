"""Tests for the pack-opening animation backend helpers added for the /me
mint flow:

* ``_cancel_active_claims_for_dev_bypass`` — dev-only escape hatch that
  releases the ``ux_claims_active_season_user_wallet_lower`` slot before a
  retry, so UX-testing wallets don't permanently wedge themselves on a
  stale QUEUED row from a prior crashed worker run.
* ``_try_finalize_processing_claim`` — per-poll receipt check that drives
  ``PROCESSING → COMPLETED / FAILED`` without waiting on the 30-minute cron
  recovery window.
* ``_fetch_turntable_image`` — the URL-wrap fix that stopped the 502 from
  ``urlopen_after_ssrf_check`` (which expects a ``urllib.request.Request``).
* ``_delete_turntable_cache`` / ``_sweep_stale_turntable_cache`` — local-disk
  cleanup so abandoned reveals don't accumulate (turntable frames are
  local-only, the on-chain R2 assets they're composed from stay forever).
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import user_web_backend.main as main


# ---------------------------------------------------------------------------
# Shared DB cursor stub
# ---------------------------------------------------------------------------


def _make_db_stub(rowcount: int = 0, returning: object = None):
    """Build a fake (connection, cursor) pair where ``execute`` records the SQL
    + params and ``fetchone`` returns ``returning``. Mirrors the cursor protocol
    used across user_web_backend.main."""
    cursor = MagicMock()
    cursor.rowcount = rowcount
    cursor.fetchone.return_value = returning
    cursor.__enter__ = lambda s: s
    cursor.__exit__ = MagicMock(return_value=False)

    conn = MagicMock()
    conn.cursor.return_value = cursor
    conn.__enter__ = lambda s: s
    conn.__exit__ = MagicMock(return_value=False)
    return conn, cursor


# ---------------------------------------------------------------------------
# _cancel_active_claims_for_dev_bypass
# ---------------------------------------------------------------------------


def test_cancel_active_claims_for_dev_bypass_updates_only_active_statuses(monkeypatch):
    conn, cursor = _make_db_stub(rowcount=2)
    monkeypatch.setattr(main, "_get_connection", lambda: conn)

    cancelled = main._cancel_active_claims_for_dev_bypass(
        "0xdc65dff7eed4c1c05511395ccf19cf507066ace1", season_id=1,
    )

    assert cancelled == 2
    conn.commit.assert_called_once()
    conn.close.assert_called_once()
    sql, params = cursor.execute.call_args[0]
    # Sanity: hits the right table + status set, and binds (season_id, wallet).
    assert "UPDATE claims" in sql
    assert "status = 'FAILED'" in sql
    # FAILED is deliberately NOT in the WHERE — re-failing a FAILED row would
    # bump updated_at and confuse the cron stale-PROCESSING heuristics.
    assert "'QUEUED'" in sql and "'PENDING'" in sql and "'PROCESSING'" in sql
    assert "'COMPLETED'" not in sql or "'COMPLETED'" not in sql.split("WHERE", 1)[1]
    assert params == (1, "0xdc65dff7eed4c1c05511395ccf19cf507066ace1")


def test_cancel_active_claims_for_dev_bypass_zero_rows_is_silent(monkeypatch):
    conn, _ = _make_db_stub(rowcount=0)
    monkeypatch.setattr(main, "_get_connection", lambda: conn)
    assert main._cancel_active_claims_for_dev_bypass("0xabc", 1) == 0
    conn.commit.assert_called_once()


# ---------------------------------------------------------------------------
# _try_finalize_processing_claim
# ---------------------------------------------------------------------------


def _patch_verifier(monkeypatch, fake_verifier):
    """Inject a fake mint-receipt verifier into mint_service.scheduler so the
    per-poll finalize uses it instead of real EvmClient (which needs a key)."""
    fake_scheduler = MagicMock()
    fake_scheduler._make_mint_receipt_verifier.return_value = fake_verifier
    monkeypatch.setattr(main.mint_service, "scheduler", fake_scheduler, raising=False)
    return fake_scheduler


def test_finalize_processing_claim_flips_completed_on_success(monkeypatch):
    verifier = MagicMock()
    verifier.fetch_mint_receipt_status.return_value = (
        "success", 42, "0xcontract/42",
    )
    scheduler = _patch_verifier(monkeypatch, verifier)

    conn, cursor = _make_db_stub(rowcount=1, returning={"metadata_uri": "ipfs://X"})
    monkeypatch.setattr(main, "_get_connection", lambda: conn)

    new_status = main._try_finalize_processing_claim(
        claim_id=99, tx_hash="0xabcdef",
    )

    assert new_status == "COMPLETED"
    sql, params = cursor.execute.call_args[0]
    assert "status        = 'COMPLETED'" in sql
    assert params == ("0xcontract/42", 99)
    # Post-recovery hooks (denormalize + token_id backfill) get called with the
    # data extracted from the UPDATE ... RETURNING + receipt.
    scheduler._run_post_recovery_hooks.assert_called_once_with(
        claim_id=99, metadata_uri="ipfs://X", token_id=42,
    )


def test_finalize_processing_claim_flips_failed_on_revert(monkeypatch):
    verifier = MagicMock()
    verifier.fetch_mint_receipt_status.return_value = ("reverted", None, None)
    scheduler = _patch_verifier(monkeypatch, verifier)

    conn, cursor = _make_db_stub(rowcount=1)
    monkeypatch.setattr(main, "_get_connection", lambda: conn)

    assert main._try_finalize_processing_claim(7, "0xdead") == "FAILED"
    sql, params = cursor.execute.call_args[0]
    assert "status                 = 'FAILED'" in sql
    assert "collection_mint_number = NULL" in sql
    assert params == (7,)
    # No post-recovery hooks on revert — the mint never produced an NFT.
    scheduler._run_post_recovery_hooks.assert_not_called()


@pytest.mark.parametrize("onchain_status", ["pending", "not_found"])
def test_finalize_processing_claim_leaves_pending_alone(monkeypatch, onchain_status):
    verifier = MagicMock()
    verifier.fetch_mint_receipt_status.return_value = (onchain_status, None, None)
    _patch_verifier(monkeypatch, verifier)

    db_calls = []
    monkeypatch.setattr(
        main, "_get_connection",
        lambda: pytest.fail(f"DB must not be touched for status={onchain_status}"),
    )
    assert main._try_finalize_processing_claim(11, "0xfeed") is None


def test_finalize_processing_claim_no_op_on_empty_hash(monkeypatch):
    monkeypatch.setattr(
        main, "_get_connection",
        lambda: pytest.fail("DB must not be touched for empty tx_hash"),
    )
    fake_scheduler = MagicMock()
    monkeypatch.setattr(main.mint_service, "scheduler", fake_scheduler, raising=False)
    assert main._try_finalize_processing_claim(1, "") is None
    assert main._try_finalize_processing_claim(1, "   ") is None
    fake_scheduler._make_mint_receipt_verifier.assert_not_called()


def test_finalize_processing_claim_swallows_verifier_init_failure(monkeypatch):
    fake_scheduler = MagicMock()
    fake_scheduler._make_mint_receipt_verifier.side_effect = RuntimeError("no key")
    monkeypatch.setattr(main.mint_service, "scheduler", fake_scheduler, raising=False)
    monkeypatch.setattr(
        main, "_get_connection",
        lambda: pytest.fail("DB must not be touched when verifier init fails"),
    )
    # Must NOT raise — receipt verification is best-effort and a failure here
    # should leave the row in PROCESSING so the next poll retries cleanly.
    assert main._try_finalize_processing_claim(5, "0xabc") is None


def test_finalize_processing_claim_swallows_receipt_lookup_failure(monkeypatch):
    verifier = MagicMock()
    verifier.fetch_mint_receipt_status.side_effect = ConnectionError("rpc blip")
    _patch_verifier(monkeypatch, verifier)
    monkeypatch.setattr(
        main, "_get_connection",
        lambda: pytest.fail("DB must not be touched when receipt lookup fails"),
    )
    assert main._try_finalize_processing_claim(5, "0xabc") is None


def test_finalize_processing_claim_lock_prevents_concurrent_runs(monkeypatch):
    """Two parallel polls on the same claim must not both run the verifier —
    the second one bails out instantly so we don't pile up RPC calls."""
    verifier = MagicMock()
    verifier.fetch_mint_receipt_status.return_value = ("pending", None, None)
    _patch_verifier(monkeypatch, verifier)

    # Grab the per-claim lock externally so the next call sees it held.
    lock = main._finalize_lock(123)
    lock.acquire()
    try:
        assert main._try_finalize_processing_claim(123, "0xabc") is None
    finally:
        lock.release()
    # Verifier never even constructed — we short-circuited on the lock.
    verifier.fetch_mint_receipt_status.assert_not_called()


# ---------------------------------------------------------------------------
# _fetch_turntable_image — URL wrap regression test
# ---------------------------------------------------------------------------


def test_fetch_turntable_image_wraps_url_in_request_object(monkeypatch):
    """Regression: a bare string would crash inside ``urlopen_after_ssrf_check``
    with ``'str' object has no attribute 'get_full_url'`` (it's the urllib
    Request method the SSRF guard calls)."""
    captured = {}

    @contextmanager
    def fake_opener(req, *, timeout):
        captured["req"] = req
        captured["timeout"] = timeout
        resp = MagicMock()
        resp.read.return_value = b"PNG-bytes"
        yield resp

    monkeypatch.setattr(main, "urlopen_after_ssrf_check", fake_opener)
    monkeypatch.setattr(
        main, "_absolute_asset_url",
        lambda request, url: f"https://cdn.example.test{url}",
    )

    request = MagicMock()
    out = main._fetch_turntable_image(request, "/img/front.png")

    assert out == b"PNG-bytes"
    # MUST be a urllib.request.Request, not a str — that's the bug we're
    # locking down.
    import urllib.request
    assert isinstance(captured["req"], urllib.request.Request)
    assert captured["req"].get_full_url() == "https://cdn.example.test/img/front.png"
    assert captured["timeout"] == 20


# ---------------------------------------------------------------------------
# _delete_turntable_cache  +  _sweep_stale_turntable_cache  (real FS)
# ---------------------------------------------------------------------------


def test_delete_turntable_cache_removes_files_and_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "TURNTABLE_CACHE_DIR", tmp_path)
    claim_dir = tmp_path / "42"
    claim_dir.mkdir()
    for i in range(3):
        (claim_dir / f"000{i+1}.webp").write_bytes(b"x")

    deleted = main._delete_turntable_cache(42)
    assert deleted == 3
    assert not claim_dir.exists()


def test_delete_turntable_cache_is_idempotent_on_missing_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "TURNTABLE_CACHE_DIR", tmp_path)
    assert main._delete_turntable_cache(404) == 0


def test_sweep_stale_turntable_cache_keeps_fresh_drops_stale(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "TURNTABLE_CACHE_DIR", tmp_path)
    # 5-second TTL so we don't need to actually wait days in tests.
    monkeypatch.setattr(main, "TURNTABLE_CACHE_TTL_SECONDS", 5)

    fresh = tmp_path / "100"
    fresh.mkdir()
    (fresh / "f.webp").write_bytes(b"x")  # mtime ~= now

    stale = tmp_path / "200"
    stale.mkdir()
    stale_file = stale / "f.webp"
    stale_file.write_bytes(b"x")
    old = time.time() - 60  # 1 min ago, comfortably past the 5s TTL
    import os as _os
    _os.utime(stale_file, (old, old))
    _os.utime(stale, (old, old))

    foreign = tmp_path / "not-a-claim-id"  # non-numeric, must be skipped
    foreign.mkdir()
    foreign_file = foreign / "f.webp"
    foreign_file.write_bytes(b"x")
    _os.utime(foreign_file, (old, old))
    _os.utime(foreign, (old, old))

    stats = main._sweep_stale_turntable_cache()
    assert stats["scanned"] == 3
    assert stats["deleted_dirs"] == 1
    assert stats["deleted_files"] == 1
    assert fresh.exists()           # fresh dir survives
    assert not stale.exists()       # stale claim dir nuked
    assert foreign.exists()         # foreign-named dir left alone


def test_sweep_stale_turntable_cache_empty_root(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "TURNTABLE_CACHE_DIR", tmp_path)
    monkeypatch.setattr(main, "TURNTABLE_CACHE_TTL_SECONDS", 5)
    stats = main._sweep_stale_turntable_cache()
    assert stats == {"scanned": 0, "deleted_dirs": 0, "deleted_files": 0}
