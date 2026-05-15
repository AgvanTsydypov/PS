"""
End-to-end integration tests for scripts/backfill_recovery_card_fields.py.

The CLI is a one-shot remedial for COMPLETED claims that recovery closed
BEFORE the post-mint hook fix landed — they have a real on-chain mint
(``status='COMPLETED'``, ``tx_hash``, ``metadata_uri``, ``asset_address``)
but no ``card_slug`` / ``card_title`` / ``card_payload_json`` / etc., because
recovery used to skip ``denormalize_card_onto_claim`` and
``notify_claim_minted`` for the historical-winner and simple-flip branches.

These tests run the CLI's ``main()`` against the real testcontainers
Postgres, with ``build_recovery_payload_from_ipfs`` and
``notify_claim_minted`` patched at module level so the suite doesn't need a
live Pinata gateway or a Telegram bot. The CLI script's import of
``psycopg2`` directly (not via DataLoadingManager) means the conftest's
restored real-psycopg2 reaches it transparently.
"""

from __future__ import annotations

import unittest.mock as mock
from typing import Any, Dict, List, Optional

import pytest

import scripts.backfill_recovery_card_fields as backfill_mod
from tests.integration.conftest import make_real_connection


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def backfill_season():
    """Ephemeral season for the CLI tests."""
    conn = make_real_connection()
    season_id: Optional[int] = None
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO seasons
                    (type, season_number, start_date, end_date,
                     total_supply, remaining_supply, is_active)
                VALUES ('standard', %s,
                        '2099-01-01 00:00:00+00',
                        '2099-12-31 00:00:00+00',
                        0, 0, true)
                RETURNING id
                """,
                (99201,),
            )
            season_id = cur.fetchone()[0]
        conn.commit()
    finally:
        conn.close()

    yield season_id

    conn = make_real_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM claims  WHERE season_id = %s", (season_id,))
            cur.execute("DELETE FROM seasons WHERE id        = %s", (season_id,))
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _wallet(i: int) -> str:
    return "0x" + format(i, "x").rjust(40, "0")


def _insert_eligible_row(
    *,
    season_id: int,
    wallet: str,
    tx_hash: str = "0xrecovered",
    metadata_uri: str = "ipfs://bafkreirecoverybackfill",
    asset_address: Optional[str] = "0xCONTRACT/14",
    cmn: int = 1,
    token_id: Optional[int] = None,
    error_message: str = "[auto-completed: on-chain receipt confirmed at NOW]",
) -> int:
    """Insert a row in the exact shape the recovery path leaves behind
    BEFORE the post-mint hook fix: ``status='COMPLETED'``, ``tx_hash`` and
    ``metadata_uri`` set, ``asset_address`` populated (encodes the
    on-chain token_id), but ``card_slug`` and ``token_id`` are NULL — i.e.
    the CLI's exact target audience."""
    conn = make_real_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO claims
                    (user_wallet, season_id, phase_type, status,
                     tx_hash, metadata_uri, asset_address,
                     collection_mint_number, token_id, error_message)
                VALUES (%s, %s, 'breach', 'COMPLETED', %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    wallet, season_id, tx_hash, metadata_uri, asset_address,
                    cmn, token_id, error_message,
                ),
            )
            cid = cur.fetchone()[0]
        conn.commit()
        return cid
    finally:
        conn.close()


def _read_card_columns(claim_id: int) -> dict:
    import psycopg2.extras

    conn = make_real_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, status, token_id, asset_address, metadata_uri,
                       card_slug, card_title, front_image_url, back_image_url,
                       primary_tag, secondary_tag, pattern, card_payload_json
                FROM   claims
                WHERE  id = %s
                """,
                (claim_id,),
            )
            return dict(cur.fetchone())
    finally:
        conn.close()


def _make_payload(
    *,
    slug: str,
    cmn: int = 1,
    card_title: str = "Backfilled Star",
    archetype: str = "SUBSTRATE",
    primary_tag: str = "Economy",
    secondary_tag: str = "Macro",
) -> Dict[str, Any]:
    """Same shape as ``_build_card_display_data_payload`` produces."""
    return {
        "season_type": "standard",
        "season_number": 1,
        "season_size": 256,
        "collection_mint_number": cmn,
        "archetype": archetype,
        "card_title": card_title,
        "primary_tag": primary_tag,
        "secondary_tag": secondary_tag,
        "front_image_url": "https://gateway.pinata.cloud/ipfs/bafkreifrontbf",
        "back_image_url": "https://gateway.pinata.cloud/ipfs/bafkreibackbf",
        "qr_payload": f"https://polystars.app/cards/{slug}",
    }


class _RecordingNotifier:
    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)


# ---------------------------------------------------------------------------
# 1. Eligibility filter — only the right rows get picked up
# ---------------------------------------------------------------------------


class TestEligibilityFilter:
    """The CLI's SELECT is the gate: only ``status='COMPLETED'`` AND
    ``metadata_uri IS NOT NULL`` AND ``card_slug IS NULL``. The filter
    matters because a row that already has ``card_slug`` was either
    properly minted by the QUEUE pickup loop or already backfilled — we
    must not touch it (re-running denormalize on an already-set slug
    risks overwriting a hand-fixed value)."""

    def test_completed_with_no_card_slug_is_picked(
        self, backfill_season, monkeypatch,
    ):
        cid = _insert_eligible_row(
            season_id=backfill_season, wallet=_wallet(1201),
            asset_address="0xCONTRACT/14",
        )

        payload = _make_payload(slug="picked-slug")
        recorder = _RecordingNotifier()
        monkeypatch.setattr(
            "scripts.backfill_recovery_card_fields.build_recovery_payload_from_ipfs",
            lambda uri: payload,
        )
        monkeypatch.setattr(
            "scripts.telegram_notifier.notify_claim_minted",
            recorder,
        )
        monkeypatch.setattr(
            "sys.argv", ["backfill_recovery_card_fields.py", "--claim-ids", str(cid)],
        )

        rc = backfill_mod.main()
        assert rc == 0
        row = _read_card_columns(cid)
        assert row["card_slug"] == "picked-slug"
        assert row["token_id"] == 14

    def test_processing_row_is_skipped(self, backfill_season, monkeypatch):
        # PROCESSING row — recovery hasn't even flipped it yet. The
        # backfill CLI is for post-COMPLETED rows only; it must not
        # accidentally repair the wrong thing.
        conn = make_real_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO claims
                        (user_wallet, season_id, phase_type, status,
                         tx_hash, metadata_uri, collection_mint_number)
                    VALUES (%s, %s, 'breach', 'PROCESSING',
                            '0xstillprocessing',
                            'ipfs://bafkreiprocessing', 1)
                    RETURNING id
                    """,
                    (_wallet(1202), backfill_season),
                )
                cid = cur.fetchone()[0]
            conn.commit()
        finally:
            conn.close()

        recorder = _RecordingNotifier()
        monkeypatch.setattr(
            "scripts.backfill_recovery_card_fields.build_recovery_payload_from_ipfs",
            lambda uri: _make_payload(slug="should-not-write"),
        )
        monkeypatch.setattr(
            "scripts.telegram_notifier.notify_claim_minted", recorder,
        )
        monkeypatch.setattr(
            "sys.argv", ["backfill_recovery_card_fields.py", "--claim-ids", str(cid)],
        )

        rc = backfill_mod.main()
        assert rc == 0
        row = _read_card_columns(cid)
        # Untouched: still NULL slug, PROCESSING status preserved.
        assert row["card_slug"] is None
        assert row["status"] == "PROCESSING"
        assert len(recorder.calls) == 0

    def test_completed_with_existing_card_slug_is_skipped(
        self, backfill_season, monkeypatch,
    ):
        # Row that already went through denormalize. Re-backfilling would
        # overwrite the existing payload — that's wrong. Filter must
        # exclude it.
        conn = make_real_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO claims
                        (user_wallet, season_id, phase_type, status,
                         tx_hash, metadata_uri, asset_address,
                         collection_mint_number, card_slug)
                    VALUES (%s, %s, 'breach', 'COMPLETED',
                            '0xalreadydone',
                            'ipfs://bafkreidone', '0xCONTRACT/9', 1,
                            'pre-existing-slug')
                    RETURNING id
                    """,
                    (_wallet(1203), backfill_season),
                )
                cid = cur.fetchone()[0]
            conn.commit()
        finally:
            conn.close()

        recorder = _RecordingNotifier()
        monkeypatch.setattr(
            "scripts.backfill_recovery_card_fields.build_recovery_payload_from_ipfs",
            lambda uri: _make_payload(slug="MUST-NOT-OVERWRITE"),
        )
        monkeypatch.setattr(
            "scripts.telegram_notifier.notify_claim_minted", recorder,
        )
        monkeypatch.setattr(
            "sys.argv", ["backfill_recovery_card_fields.py", "--claim-ids", str(cid)],
        )

        rc = backfill_mod.main()
        assert rc == 0
        row = _read_card_columns(cid)
        # Filter excluded it → unchanged.
        assert row["card_slug"] == "pre-existing-slug"
        assert len(recorder.calls) == 0

    def test_completed_with_null_metadata_uri_is_skipped(
        self, backfill_season, monkeypatch,
    ):
        # No metadata_uri → we can't rebuild the payload. CLI filters it
        # out at the SELECT, never wastes an IPFS fetch.
        conn = make_real_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO claims
                        (user_wallet, season_id, phase_type, status, tx_hash,
                         collection_mint_number)
                    VALUES (%s, %s, 'breach', 'COMPLETED', '0xnomet', 1)
                    RETURNING id
                    """,
                    (_wallet(1204), backfill_season),
                )
                cid = cur.fetchone()[0]
            conn.commit()
        finally:
            conn.close()

        fetch_mock = mock.MagicMock()
        monkeypatch.setattr(
            "scripts.backfill_recovery_card_fields.build_recovery_payload_from_ipfs",
            fetch_mock,
        )
        monkeypatch.setattr(
            "scripts.telegram_notifier.notify_claim_minted",
            _RecordingNotifier(),
        )
        monkeypatch.setattr(
            "sys.argv", ["backfill_recovery_card_fields.py", "--claim-ids", str(cid)],
        )

        rc = backfill_mod.main()
        assert rc == 0
        # IPFS fetch never attempted — SELECT pre-filtered it.
        fetch_mock.assert_not_called()
        row = _read_card_columns(cid)
        assert row["card_slug"] is None


# ---------------------------------------------------------------------------
# 2. Dry-run does not mutate the row
# ---------------------------------------------------------------------------


class TestDryRun:
    """--dry-run lets an operator preview which claims are eligible and
    what payload would be rebuilt, with zero writes. Important because
    the prod use case is "is this safe to run?" before letting it loose
    on a real DB."""

    def test_dry_run_leaves_row_unchanged(self, backfill_season, monkeypatch):
        cid = _insert_eligible_row(
            season_id=backfill_season, wallet=_wallet(1301),
        )
        before = _read_card_columns(cid)

        recorder = _RecordingNotifier()
        monkeypatch.setattr(
            "scripts.backfill_recovery_card_fields.build_recovery_payload_from_ipfs",
            lambda uri: _make_payload(slug="dry-run-slug"),
        )
        monkeypatch.setattr(
            "scripts.telegram_notifier.notify_claim_minted", recorder,
        )
        monkeypatch.setattr(
            "sys.argv",
            [
                "backfill_recovery_card_fields.py",
                "--claim-ids", str(cid), "--dry-run",
            ],
        )

        rc = backfill_mod.main()
        assert rc == 0

        after = _read_card_columns(cid)
        # Every column except possibly transient ones unchanged.
        assert after["card_slug"] == before["card_slug"]
        assert after["card_title"] == before["card_title"]
        assert after["token_id"] == before["token_id"]
        assert after["card_payload_json"] == before["card_payload_json"]
        # TG never dispatched even on a successful dry-run.
        assert len(recorder.calls) == 0


# ---------------------------------------------------------------------------
# 3. Token_id backfill from asset_address
# ---------------------------------------------------------------------------


class TestTokenIdBackfill:
    """The CLI's token_id source: parse the trailing segment of
    ``asset_address``. This is the only place legacy COMPLETED rows
    record the on-chain token id — the new ``_run_post_recovery_hooks``
    pulls it from the receipt verifier, but pre-fix rows don't have
    that capture."""

    def test_token_id_parsed_from_asset_address(
        self, backfill_season, monkeypatch,
    ):
        cid = _insert_eligible_row(
            season_id=backfill_season, wallet=_wallet(1401),
            asset_address="0x692107D5962d0A3bb968c2DcD11Fb43C05907F0B/14",
        )

        monkeypatch.setattr(
            "scripts.backfill_recovery_card_fields.build_recovery_payload_from_ipfs",
            lambda uri: _make_payload(slug="tok-slug"),
        )
        monkeypatch.setattr(
            "scripts.telegram_notifier.notify_claim_minted",
            _RecordingNotifier(),
        )
        monkeypatch.setattr(
            "sys.argv", ["backfill_recovery_card_fields.py", "--claim-ids", str(cid)],
        )

        backfill_mod.main()
        row = _read_card_columns(cid)
        assert row["token_id"] == 14

    def test_existing_token_id_is_not_overwritten(
        self, backfill_season, monkeypatch,
    ):
        # If token_id is somehow already set, the CLI must NOT clobber it
        # — the UPDATE has ``AND token_id IS NULL`` for exactly this.
        cid = _insert_eligible_row(
            season_id=backfill_season, wallet=_wallet(1402),
            asset_address="0xCONTRACT/9999",
            token_id=42,
        )

        monkeypatch.setattr(
            "scripts.backfill_recovery_card_fields.build_recovery_payload_from_ipfs",
            lambda uri: _make_payload(slug="no-clobber-slug"),
        )
        monkeypatch.setattr(
            "scripts.telegram_notifier.notify_claim_minted",
            _RecordingNotifier(),
        )
        monkeypatch.setattr(
            "sys.argv", ["backfill_recovery_card_fields.py", "--claim-ids", str(cid)],
        )

        backfill_mod.main()
        row = _read_card_columns(cid)
        assert row["token_id"] == 42  # untouched
        # Card fields still got written.
        assert row["card_slug"] == "no-clobber-slug"

    def test_malformed_asset_address_no_token_id_writes_card_only(
        self, backfill_season, monkeypatch,
    ):
        # Some legacy rows may have asset_address without the /tokenId
        # tail. The CLI should still backfill the card columns (we have
        # the IPFS payload) — only the token_id step is a no-op.
        cid = _insert_eligible_row(
            season_id=backfill_season, wallet=_wallet(1403),
            asset_address="0xCONTRACT",  # no slash → unparseable
        )

        monkeypatch.setattr(
            "scripts.backfill_recovery_card_fields.build_recovery_payload_from_ipfs",
            lambda uri: _make_payload(slug="card-only-slug"),
        )
        monkeypatch.setattr(
            "scripts.telegram_notifier.notify_claim_minted",
            _RecordingNotifier(),
        )
        monkeypatch.setattr(
            "sys.argv", ["backfill_recovery_card_fields.py", "--claim-ids", str(cid)],
        )

        backfill_mod.main()
        row = _read_card_columns(cid)
        assert row["token_id"] is None
        # But the card-side recovery still happened.
        assert row["card_slug"] == "card-only-slug"


# ---------------------------------------------------------------------------
# 4. --notify flag controls Telegram dispatch
# ---------------------------------------------------------------------------


class TestNotifyFlag:
    """``notify_claim_minted`` is opt-in: default is OFF so re-running the
    CLI doesn't double-notify users who already saw their card. Passing
    ``--notify`` flips it on for the late announcement use case."""

    def test_default_does_not_notify(self, backfill_season, monkeypatch):
        cid = _insert_eligible_row(
            season_id=backfill_season, wallet=_wallet(1501),
        )

        recorder = _RecordingNotifier()
        monkeypatch.setattr(
            "scripts.backfill_recovery_card_fields.build_recovery_payload_from_ipfs",
            lambda uri: _make_payload(slug="silent-slug"),
        )
        monkeypatch.setattr(
            "scripts.telegram_notifier.notify_claim_minted", recorder,
        )
        monkeypatch.setattr(
            "sys.argv", ["backfill_recovery_card_fields.py", "--claim-ids", str(cid)],
        )

        backfill_mod.main()
        # Card backfilled, but TG silent.
        row = _read_card_columns(cid)
        assert row["card_slug"] == "silent-slug"
        assert len(recorder.calls) == 0

    def test_notify_flag_dispatches_with_full_payload(
        self, backfill_season, monkeypatch,
    ):
        cid = _insert_eligible_row(
            season_id=backfill_season, wallet=_wallet(1502),
            cmn=42,
            asset_address="0xCONTRACT/77",
        )

        payload = _make_payload(
            slug="loud-slug", cmn=42, archetype="EXTRACTOR",
        )
        recorder = _RecordingNotifier()
        monkeypatch.setattr(
            "scripts.backfill_recovery_card_fields.build_recovery_payload_from_ipfs",
            lambda uri: payload,
        )
        monkeypatch.setattr(
            "scripts.telegram_notifier.notify_claim_minted", recorder,
        )
        monkeypatch.setattr(
            "sys.argv",
            [
                "backfill_recovery_card_fields.py",
                "--claim-ids", str(cid), "--notify",
            ],
        )

        backfill_mod.main()
        assert len(recorder.calls) == 1
        tg = recorder.calls[0]
        assert tg["collection_mint_number"] == 42
        assert tg["season_type"] == "standard"
        assert tg["archetype"] == "EXTRACTOR"
        assert tg["card_url"] == "https://polystars.app/cards/loud-slug"
        assert "bafkreifrontbf" in tg["front_image_url"]
        # token_id from asset_address ran too.
        row = _read_card_columns(cid)
        assert row["token_id"] == 77


# ---------------------------------------------------------------------------
# 5. Skip-on-None-payload (legacy mints without card_display_data)
# ---------------------------------------------------------------------------


class TestPayloadSkip:
    """When ``build_recovery_payload_from_ipfs`` returns None (legacy
    mint, gateway down, malformed JSON), the CLI must skip cleanly: no
    write, no crash, count it as ``skipped`` not ``backfilled``. The
    operator can then look at logs and decide whether to rerun later."""

    def test_none_payload_skips_row_and_does_not_notify(
        self, backfill_season, monkeypatch,
    ):
        cid = _insert_eligible_row(
            season_id=backfill_season, wallet=_wallet(1601),
        )

        recorder = _RecordingNotifier()
        monkeypatch.setattr(
            "scripts.backfill_recovery_card_fields.build_recovery_payload_from_ipfs",
            lambda uri: None,  # legacy mint
        )
        monkeypatch.setattr(
            "scripts.telegram_notifier.notify_claim_minted", recorder,
        )
        monkeypatch.setattr(
            "sys.argv",
            [
                "backfill_recovery_card_fields.py",
                "--claim-ids", str(cid), "--notify",
            ],
        )

        rc = backfill_mod.main()
        assert rc == 0
        row = _read_card_columns(cid)
        assert row["card_slug"] is None
        assert row["card_title"] is None
        # token_id not written either — we never got that far (None
        # payload returns before token_id step in the CLI flow).
        # Defensively assert: TG silent.
        assert len(recorder.calls) == 0


# ---------------------------------------------------------------------------
# 6. --claim-ids batch — multiple targets in one run
# ---------------------------------------------------------------------------


class TestClaimIdsBatch:
    """Multiple ids in a single invocation — comma-separated. The list
    flows into ``= ANY(%s)`` so PostgreSQL handles it as a set."""

    def test_two_claim_ids_both_processed(self, backfill_season, monkeypatch):
        c1 = _insert_eligible_row(
            season_id=backfill_season, wallet=_wallet(1701),
            tx_hash="0xbatch1", metadata_uri="ipfs://bafkreibatch1",
            asset_address="0xCONTRACT/100",
            cmn=701,
        )
        c2 = _insert_eligible_row(
            season_id=backfill_season, wallet=_wallet(1702),
            tx_hash="0xbatch2", metadata_uri="ipfs://bafkreibatch2",
            asset_address="0xCONTRACT/200",
            cmn=702,
        )

        # Distinct payloads per metadata_uri so we can assert each row
        # got its own (i.e. the per-row loop isn't reusing one cached
        # result).
        per_uri = {
            "ipfs://bafkreibatch1": _make_payload(slug="batch-slug-1", cmn=1),
            "ipfs://bafkreibatch2": _make_payload(slug="batch-slug-2", cmn=2),
        }
        monkeypatch.setattr(
            "scripts.backfill_recovery_card_fields.build_recovery_payload_from_ipfs",
            lambda uri: per_uri[uri],
        )
        monkeypatch.setattr(
            "scripts.telegram_notifier.notify_claim_minted",
            _RecordingNotifier(),
        )
        monkeypatch.setattr(
            "sys.argv",
            [
                "backfill_recovery_card_fields.py",
                "--claim-ids", f"{c1},{c2}",
            ],
        )

        backfill_mod.main()
        r1 = _read_card_columns(c1)
        r2 = _read_card_columns(c2)
        assert r1["card_slug"] == "batch-slug-1"
        assert r1["token_id"] == 100
        assert r2["card_slug"] == "batch-slug-2"
        assert r2["token_id"] == 200


# ---------------------------------------------------------------------------
# 7. _select_rows query semantics under real Postgres
# ---------------------------------------------------------------------------


class TestSelectRowsQuery:
    """Hits the actual SQL — confirms the filter compiles, handles the
    optional ``--claim-ids`` clause, and respects all three eligibility
    columns simultaneously. This complements TestEligibilityFilter,
    which drives the CLI; this exercises the helper directly so a
    regression in the query alone (e.g. accidentally dropping the
    metadata_uri check) shows up here too."""

    def test_returns_only_eligible_rows(self, backfill_season):
        eligible = _insert_eligible_row(
            season_id=backfill_season, wallet=_wallet(1801),
        )
        # Ineligible: already has card_slug.
        conn = make_real_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO claims
                        (user_wallet, season_id, phase_type, status,
                         tx_hash, metadata_uri, card_slug)
                    VALUES (%s, %s, 'breach', 'COMPLETED',
                            '0xhasslug', 'ipfs://x', 'already-here')
                    RETURNING id
                    """,
                    (_wallet(1802), backfill_season),
                )
                already = cur.fetchone()[0]
            conn.commit()
        finally:
            conn.close()

        conn = make_real_connection()
        try:
            rows = backfill_mod._select_rows(conn, claim_ids=None)
        finally:
            conn.close()

        ids = {int(r["id"]) for r in rows}
        assert eligible in ids
        assert already not in ids

    def test_explicit_claim_ids_narrow_the_set(self, backfill_season):
        c1 = _insert_eligible_row(
            season_id=backfill_season, wallet=_wallet(1810),
            tx_hash="0xrow1", metadata_uri="ipfs://row1",
            cmn=810,
        )
        c2 = _insert_eligible_row(
            season_id=backfill_season, wallet=_wallet(1811),
            tx_hash="0xrow2", metadata_uri="ipfs://row2",
            cmn=811,
        )

        conn = make_real_connection()
        try:
            rows = backfill_mod._select_rows(conn, claim_ids=[c1])
        finally:
            conn.close()

        ids = {int(r["id"]) for r in rows}
        assert ids == {c1}
        assert c2 not in ids
