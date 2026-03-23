"""
Print event IDs whose Polymarket API volume is below a threshold.
Optionally delete matching events from DB (with cascading deletes).

Flow:
1) Read event IDs from local `events` table
2) Call Gamma API: GET /events/{id}
3) Print events where `volume` < threshold
   (and `volume=UNKNOWN` when deletion mode is enabled)
4) Optional: delete matching rows from `events` (cascades to related rows)

Usage examples:
  python scripts/db/print_low_volume_events.py
  python scripts/db/print_low_volume_events.py --threshold 5000000
  python scripts/db/print_low_volume_events.py --limit 300 --local
  python scripts/db/print_low_volume_events.py --delete-matched --local
"""

from __future__ import annotations

import argparse
import os
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

import psycopg2
import requests
from dotenv import load_dotenv

load_dotenv()

API_BASE_URL = "https://gamma-api.polymarket.com"
DEFAULT_THRESHOLD = Decimal("5000000")


def _db_params(use_local_db: bool) -> Dict[str, Any]:
    if use_local_db:
        return {
            "host": os.getenv("LOCAL_DB_HOST", os.getenv("DB_HOST")),
            "port": int(os.getenv("LOCAL_DB_PORT", os.getenv("DB_PORT", "5432"))),
            "database": os.getenv("LOCAL_DB_NAME", os.getenv("DB_NAME")),
            "user": os.getenv("LOCAL_DB_USER", os.getenv("DB_USER")),
            "password": os.getenv("LOCAL_DB_PASSWORD", os.getenv("DB_PASSWORD")),
            "sslmode": os.getenv("DB_SSLMODE", "require"),
        }
    return {
        "host": os.getenv("DB_HOST"),
        "port": int(os.getenv("DB_PORT", "5432")),
        "database": os.getenv("DB_NAME"),
        "user": os.getenv("DB_USER"),
        "password": os.getenv("DB_PASSWORD"),
        "sslmode": os.getenv("DB_SSLMODE", "require"),
    }


def _fetch_event_ids(use_local_db: bool, limit: Optional[int]) -> List[str]:
    conn = psycopg2.connect(**_db_params(use_local_db=use_local_db))
    try:
        with conn.cursor() as cursor:
            query = "SELECT id FROM events ORDER BY id"
            params: List[Any] = []
            if limit is not None:
                query += " LIMIT %s"
                params.append(limit)
            cursor.execute(query, tuple(params))
            return [str(row[0]) for row in cursor.fetchall()]
    finally:
        conn.close()


def _parse_decimal(value: Any) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _fetch_event_volume(session: requests.Session, event_id: str, timeout: int) -> Optional[Decimal]:
    url = f"{API_BASE_URL}/events/{event_id}"
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    payload = response.json()
    return _parse_decimal(payload.get("volume"))


def _delete_event_by_id(conn: Any, event_id: str) -> bool:
    with conn.cursor() as cursor:
        cursor.execute("DELETE FROM events WHERE id = %s", (event_id,))
        deleted = cursor.rowcount > 0
    conn.commit()
    return deleted


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Print events whose API volume is below the configured threshold "
            "and optionally delete them."
        )
    )
    parser.add_argument(
        "--threshold",
        type=str,
        default=str(DEFAULT_THRESHOLD),
        help="Volume threshold. Events with volume < threshold will be printed.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional limit for number of event IDs loaded from DB.",
    )
    parser.add_argument(
        "--local",
        action="store_true",
        help="Use LOCAL_DB_* env vars first (falls back to DB_*).",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=20,
        help="HTTP timeout in seconds for each API call.",
    )
    parser.add_argument(
        "--delete-matched",
        action="store_true",
        help=(
            "Delete events from `events` when volume < threshold. "
            "Related rows are removed by ON DELETE CASCADE constraints."
        ),
    )
    args = parser.parse_args()

    try:
        threshold = Decimal(str(args.threshold))
    except InvalidOperation as exc:
        raise ValueError(f"Invalid --threshold value: {args.threshold}") from exc

    if args.limit is not None and args.limit <= 0:
        raise ValueError("--limit must be > 0 when provided")
    if args.timeout <= 0:
        raise ValueError("--timeout must be > 0")

    event_ids = _fetch_event_ids(use_local_db=args.local, limit=args.limit)
    total = len(event_ids)
    if total == 0:
        print("No events found in DB.")
        return

    print(f"Loaded {total} event IDs from DB.")
    print(f"Checking API volume threshold: {threshold:,}")
    print("-" * 80)

    low_volume_count = 0
    unknown_volume_count = 0
    errors_count = 0
    deleted_count = 0
    deleted_unknown_count = 0
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})
    db_conn = psycopg2.connect(**_db_params(use_local_db=args.local)) if args.delete_matched else None

    try:
        for index, event_id in enumerate(event_ids, start=1):
            try:
                volume = _fetch_event_volume(session=session, event_id=event_id, timeout=args.timeout)
                if volume is None:
                    unknown_volume_count += 1
                    print(f"[{index}/{total}] event_id={event_id} volume=UNKNOWN")
                    if db_conn is None:
                        print("           -> skipped (run with --delete-matched to remove UNKNOWN)")
                        continue
                    try:
                        if _delete_event_by_id(conn=db_conn, event_id=event_id):
                            deleted_count += 1
                            deleted_unknown_count += 1
                            print("           -> deleted from events (cascade applied, UNKNOWN volume)")
                        else:
                            print("           -> not found in events at delete time")
                    except psycopg2.Error as exc:
                        db_conn.rollback()
                        errors_count += 1
                        print(f"           -> delete ERROR: {exc}")
                    continue

                if volume < threshold:
                    low_volume_count += 1
                    print(f"[{index}/{total}] event_id={event_id} volume={volume:,} (< {threshold:,})")

                    if db_conn is not None:
                        try:
                            if _delete_event_by_id(conn=db_conn, event_id=event_id):
                                deleted_count += 1
                                print(f"           -> deleted from events (cascade applied)")
                            else:
                                print(f"           -> not found in events at delete time")
                        except psycopg2.Error as exc:
                            db_conn.rollback()
                            errors_count += 1
                            print(f"           -> delete ERROR: {exc}")
            except requests.RequestException as exc:
                errors_count += 1
                print(f"[{index}/{total}] event_id={event_id} ERROR: {exc}")
    finally:
        if db_conn is not None:
            db_conn.close()

    print("-" * 80)
    print(
        "Done. "
        f"Checked: {total}, "
        f"below_threshold: {low_volume_count}, "
        f"unknown_volume: {unknown_volume_count}, "
        f"deleted: {deleted_count}, "
        f"deleted_unknown: {deleted_unknown_count}, "
        f"errors: {errors_count}"
    )


if __name__ == "__main__":
    main()
