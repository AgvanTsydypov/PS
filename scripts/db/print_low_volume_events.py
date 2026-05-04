"""
Print event IDs whose Polymarket API volume is below a threshold.
Optionally delete matching events from DB (with cascading deletes).

Flow:
1) Read event IDs either from local `events` table OR from `event_resolution_queue`
2) Call Gamma API: GET /events/{id}
3) Print events where `volume` < threshold
   (and `volume=UNKNOWN` when deletion mode is enabled)
4) Optional: write audit record into `event_resolution_trash_log`
   and delete matching rows from `events`

Usage examples:
  python scripts/db/print_low_volume_events.py
  python scripts/db/print_low_volume_events.py --threshold 5000000
  python scripts/db/print_low_volume_events.py --limit 300 --local
  python scripts/db/print_low_volume_events.py --delete-matched --local
  python scripts/db/print_low_volume_events.py --queue-ready-within-minutes 30 --delete-matched
"""

from __future__ import annotations

import argparse
import os
from decimal import Decimal, InvalidOperation
from datetime import datetime
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


def _fetch_ready_queue_event_ids(
    use_local_db: bool,
    ready_within_minutes: int,
    limit: Optional[int],
) -> List[str]:
    conn = psycopg2.connect(**_db_params(use_local_db=use_local_db))
    try:
        with conn.cursor() as cursor:
            query = """
                SELECT event_id
                FROM event_resolution_queue
                WHERE status = 'ready_for_redemptions'
                  AND resolution_ready_at IS NOT NULL
                  AND resolution_ready_at < (NOW() + (%s * INTERVAL '1 minute'))
                ORDER BY resolution_ready_at ASC
            """
            params: List[Any] = [ready_within_minutes]
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


def _fetch_event_data(
    session: requests.Session, event_id: str, timeout: int
) -> tuple[Optional[Decimal], str]:
    url = f"{API_BASE_URL}/events/{event_id}"
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    payload = response.json()
    title = str(payload.get("title") or "").strip() or "<no title>"
    return _parse_decimal(payload.get("volume")), title


def _delete_event_by_id(conn: Any, event_id: str) -> bool:
    with conn.cursor() as cursor:
        cursor.execute("DELETE FROM events WHERE id = %s", (event_id,))
        deleted = cursor.rowcount > 0
    conn.commit()
    return deleted


def _insert_trash_log(
    conn: Any,
    event_id: str,
    reason: str,
    api_volume: Optional[Decimal],
    api_title: str,
    deleted_from_events: bool,
) -> None:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO event_resolution_trash_log (
                event_id,
                queue_status,
                resolution_ready_at,
                api_volume,
                api_title,
                reason,
                deleted_from_events
            )
            SELECT
                %s,
                q.status,
                q.resolution_ready_at,
                %s,
                %s,
                %s,
                %s
            FROM (SELECT 1) s
            LEFT JOIN event_resolution_queue q
              ON q.event_id = %s
            """,
            (
                event_id,
                api_volume,
                api_title,
                reason,
                deleted_from_events,
                event_id,
            ),
        )
    conn.commit()


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
    parser.add_argument(
        "--queue-ready-within-minutes",
        type=int,
        default=None,
        help=(
            "Read candidate event IDs from event_resolution_queue where "
            "status='ready_for_redemptions' and resolution_ready_at < now + N minutes."
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
    if args.queue_ready_within_minutes is not None and args.queue_ready_within_minutes < 0:
        raise ValueError("--queue-ready-within-minutes must be >= 0")

    if args.queue_ready_within_minutes is None:
        event_ids = _fetch_event_ids(use_local_db=args.local, limit=args.limit)
        source_label = "events table"
    else:
        event_ids = _fetch_ready_queue_event_ids(
            use_local_db=args.local,
            ready_within_minutes=args.queue_ready_within_minutes,
            limit=args.limit,
        )
        source_label = (
            "event_resolution_queue "
            f"(ready_for_redemptions, resolution_ready_at < now + {args.queue_ready_within_minutes}m)"
        )
    total = len(event_ids)
    if total == 0:
        print(f"No events found in DB source: {source_label}.")
        return

    print(f"Loaded {total} event IDs from DB source: {source_label}.")
    print(f"Checking API volume threshold: {threshold:,}")
    print(f"Run timestamp: {datetime.utcnow().isoformat()}Z")
    print("-" * 80)

    low_volume_count = 0
    unknown_volume_count = 0
    errors_count = 0
    deleted_count = 0
    deleted_unknown_count = 0
    logged_count = 0
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})
    db_conn = psycopg2.connect(**_db_params(use_local_db=args.local)) if args.delete_matched else None

    try:
        for index, event_id in enumerate(event_ids, start=1):
            try:
                volume, title = _fetch_event_data(
                    session=session,
                    event_id=event_id,
                    timeout=args.timeout,
                )
                if volume is None:
                    unknown_volume_count += 1
                    print(f"[{index}/{total}] event_id={event_id} title={title!r} volume=UNKNOWN")
                    if db_conn is None:
                        continue
                    try:
                        deleted = _delete_event_by_id(conn=db_conn, event_id=event_id)
                        _insert_trash_log(
                            conn=db_conn,
                            event_id=event_id,
                            reason="Auto-trash: UNKNOWN volume in pre-downstream volume check",
                            api_volume=None,
                            api_title=title,
                            deleted_from_events=deleted,
                        )
                        logged_count += 1
                        if deleted:
                            deleted_count += 1
                            deleted_unknown_count += 1
                            print("           -> logged in event_resolution_trash_log, deleted from events")
                        else:
                            print("           -> logged in event_resolution_trash_log, not found in events")
                    except psycopg2.Error as exc:
                        db_conn.rollback()
                        errors_count += 1
                        print(f"           -> delete ERROR: {exc}")
                    continue

                if volume < threshold:
                    low_volume_count += 1
                    print(
                        f"[{index}/{total}] event_id={event_id} title={title!r} "
                        f"volume={volume:,} (< {threshold:,})"
                    )

                    if db_conn is not None:
                        try:
                            deleted = _delete_event_by_id(conn=db_conn, event_id=event_id)
                            _insert_trash_log(
                                conn=db_conn,
                                event_id=event_id,
                                reason=f"Auto-trash: volume {volume} below threshold {threshold}",
                                api_volume=volume,
                                api_title=title,
                                deleted_from_events=deleted,
                            )
                            logged_count += 1
                            if deleted:
                                deleted_count += 1
                                print("           -> logged in event_resolution_trash_log, deleted from events")
                            else:
                                print("           -> logged in event_resolution_trash_log, not found in events")
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
        f"logged: {logged_count}, "
        f"errors: {errors_count}"
    )


if __name__ == "__main__":
    main()
