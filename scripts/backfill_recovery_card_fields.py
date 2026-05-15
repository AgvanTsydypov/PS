"""
Backfill ``card_slug`` / ``card_title`` / ``front_image_url`` / ``back_image_url`` /
``primary_tag`` / ``secondary_tag`` / ``pattern`` / ``card_payload_json`` / ``token_id``
for ``claims`` rows that were closed by the recovery path BEFORE the
``_run_post_recovery_hooks`` fix landed. Optionally fires the late
Telegram notification too.

Such rows are recognisable as: ``status = 'COMPLETED'`` AND ``card_slug IS NULL``
AND ``error_message LIKE '[auto-completed%'`` (or ``[auto-renumbered%``) —
i.e. recovery promoted them to COMPLETED but the post-mint side-effects
that normally fire in the QUEUE pickup loop never ran for them.

The fix re-derives the missing fields from the on-chain ``metadata_uri`` —
specifically the ``card_display_data`` block we publish in every NFT's
metadata JSON. Nothing is re-pinned to Pinata; no SVG/PNG rendering; no
mint is replayed.

Usage:
  # Dry run — print what would be backfilled, no writes.
  python scripts/backfill_recovery_card_fields.py --dry-run

  # Specific claim ids (comma-separated).
  python scripts/backfill_recovery_card_fields.py --claim-ids 18,21

  # All eligible rows.
  python scripts/backfill_recovery_card_fields.py

  # Also send the Telegram "NEW CLAIM" announcement for each row backfilled.
  # Default is OFF so re-running this script doesn't double-notify if you
  # already let users know out-of-band.
  python scripts/backfill_recovery_card_fields.py --notify
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import List

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv()

from scripts.polystars_card_payload import (  # noqa: E402
    build_recovery_payload_from_ipfs,
    denormalize_card_onto_claim,
)


def _db_params() -> dict:
    ssl_mode = os.getenv("DB_SSLMODE", "require")
    return {
        "host": os.getenv("LOCAL_DB_HOST", os.getenv("DB_HOST")),
        "port": int(os.getenv("LOCAL_DB_PORT", os.getenv("DB_PORT", 5432))),
        "database": os.getenv("LOCAL_DB_NAME", os.getenv("DB_NAME")),
        "user": os.getenv("LOCAL_DB_USER", os.getenv("DB_USER")),
        "password": os.getenv("LOCAL_DB_PASSWORD", os.getenv("DB_PASSWORD")),
        "sslmode": ssl_mode,
    }


def _parse_token_id_from_asset_address(asset_address: str) -> int | None:
    """``asset_address`` is stored as ``"<contract>/<tokenId>"`` after a
    successful mint. The fallback lets us recover ``token_id`` even when the
    receipt verifier couldn't (legacy rows whose recovery ran before this
    fix captured ``_verified_token_id``)."""
    if not asset_address or "/" not in asset_address:
        return None
    try:
        return int(asset_address.rsplit("/", 1)[1])
    except (ValueError, IndexError):
        return None


def _select_rows(conn, claim_ids: List[int] | None) -> list[dict]:
    where_id_clause = "AND id = ANY(%s)" if claim_ids else ""
    params: tuple = (claim_ids,) if claim_ids else ()
    sql = f"""
        SELECT id, tx_hash, metadata_uri, asset_address, token_id, card_slug,
               collection_mint_number, season_id, error_message
        FROM   claims
        WHERE  status = 'COMPLETED'
          AND  metadata_uri IS NOT NULL
          AND  card_slug IS NULL
          {where_id_clause}
        ORDER  BY id
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
        cursor.execute(sql, params)
        return list(cursor.fetchall())


class _LightManager:
    """Minimal stand-in for the production ``DataLoadingManager`` —
    ``denormalize_card_onto_claim`` only calls ``.get_connection()``."""

    def __init__(self, params: dict) -> None:
        self._params = params

    def get_connection(self):
        return psycopg2.connect(**self._params)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--claim-ids",
        type=str,
        default="",
        help="Comma-separated claim ids to backfill. Default: all eligible.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would change, write nothing.",
    )
    parser.add_argument(
        "--notify",
        action="store_true",
        help="Send a late Telegram 'NEW CLAIM' announcement for each row backfilled.",
    )
    args = parser.parse_args()

    claim_ids: List[int] | None = None
    if args.claim_ids.strip():
        claim_ids = [int(s) for s in args.claim_ids.split(",") if s.strip()]

    params = _db_params()
    select_conn = psycopg2.connect(**params)
    try:
        rows = _select_rows(select_conn, claim_ids)
    finally:
        select_conn.close()

    if not rows:
        print("No eligible rows.")
        return 0

    print(f"Eligible rows: {len(rows)}")
    manager = _LightManager(params)

    backfilled = 0
    skipped = 0
    notify_sent = 0

    for row in rows:
        claim_id = int(row["id"])
        metadata_uri = str(row["metadata_uri"] or "").strip()
        token_id_from_asset = (
            row.get("token_id")
            or _parse_token_id_from_asset_address(str(row.get("asset_address") or ""))
        )

        print(f"\n--- claim {claim_id} (metadata_uri={metadata_uri[:40]}…) ---")
        payload = build_recovery_payload_from_ipfs(metadata_uri)
        if payload is None:
            print("  ⚠️  Could not fetch / parse metadata — skipped")
            skipped += 1
            continue

        slug_preview = payload.get("qr_payload", "")
        print(f"  qr_payload={slug_preview}")
        print(f"  front={payload.get('front_image_url')}")
        print(f"  back={payload.get('back_image_url')}")
        print(f"  token_id (from asset_address)={token_id_from_asset}")

        if args.dry_run:
            print("  [dry-run] would denormalize + token_id backfill")
            continue

        # token_id backfill
        if token_id_from_asset is not None and row.get("token_id") is None:
            tok_conn = psycopg2.connect(**params)
            try:
                with tok_conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE claims
                        SET    token_id   = %s,
                               updated_at = NOW()
                        WHERE  id = %s
                          AND  token_id IS NULL
                        """,
                        (int(token_id_from_asset), claim_id),
                    )
                tok_conn.commit()
                print(f"  ✅ token_id={token_id_from_asset} written")
            finally:
                tok_conn.close()

        # Card-fields backfill
        try:
            denormalize_card_onto_claim(
                manager, claim_id=claim_id, polystars_card=payload,
            )
            print("  ✅ card fields denormalized")
            backfilled += 1
        except Exception as exc:
            print(f"  ⚠️  denormalize failed: {type(exc).__name__}: {exc}")
            skipped += 1
            continue

        # Optional late TG announcement
        if args.notify:
            try:
                from scripts.telegram_notifier import notify_claim_minted
                notify_claim_minted(
                    front_image_url=str(payload.get("front_image_url") or ""),
                    season_type=payload.get("season_type"),
                    collection_mint_number=payload.get("collection_mint_number"),
                    season_capacity=payload.get("season_size"),
                    card_url=str(payload.get("qr_payload") or ""),
                    archetype=payload.get("archetype"),
                )
                notify_sent += 1
                print("  ✅ telegram notify dispatched")
            except Exception as exc:
                print(f"  ⚠️  telegram notify failed: {type(exc).__name__}: {exc}")

    print(
        f"\nDone. backfilled={backfilled} skipped={skipped}"
        f" notify_sent={notify_sent} dry_run={args.dry_run}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
