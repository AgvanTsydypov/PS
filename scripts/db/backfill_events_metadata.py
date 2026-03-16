"""
Backfill normalized metadata tables from existing events.

This script:
1) Reads event IDs from local `events` table
2) Calls Gamma API: GET /events/{id}
3) Upserts data into `series`, `tags`, `event_tags`
4) Updates `events.series_id`

Usage examples:
    python scripts/db/backfill_events_metadata.py
    python scripts/db/backfill_events_metadata.py --limit 500
    python scripts/db/backfill_events_metadata.py --missing-only
"""

from __future__ import annotations

import argparse
import os
import time
from typing import Dict, List, Optional, Tuple

import psycopg2
import requests
from dotenv import load_dotenv

load_dotenv()


class EventMetadataBackfiller:
    BASE_URL = "https://gamma-api.polymarket.com"

    def __init__(self, timeout_seconds: int = 20, retries: int = 3, sleep_ms: int = 0):
        ssl_mode = os.getenv("DB_SSLMODE", "require")
        self.connection_params = {
            "host": os.getenv("LOCAL_DB_HOST", os.getenv("DB_HOST")),
            "port": os.getenv("LOCAL_DB_PORT", os.getenv("DB_PORT", "5432")),
            "database": os.getenv("LOCAL_DB_NAME", os.getenv("DB_NAME")),
            "user": os.getenv("LOCAL_DB_USER", os.getenv("DB_USER")),
            "password": os.getenv("LOCAL_DB_PASSWORD", os.getenv("DB_PASSWORD")),
            "sslmode": ssl_mode,
        }
        self.timeout_seconds = timeout_seconds
        self.retries = retries
        self.sleep_seconds = max(sleep_ms, 0) / 1000.0
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})

        self.stats = {
            "events_total": 0,
            "events_processed": 0,
            "events_failed": 0,
            "series_upserted": 0,
            "tags_upserted": 0,
            "event_tags_inserted": 0,
            "events_series_updated": 0,
        }

    def _get_conn(self):
        return psycopg2.connect(**self.connection_params)

    def _fetch_event_ids(
        self,
        conn,
        limit: Optional[int],
        offset: int,
        missing_only: bool,
    ) -> List[str]:
        cursor = conn.cursor()
        try:
            if missing_only:
                sql = """
                    SELECT e.id
                    FROM events e
                    WHERE e.series_id IS NULL
                       OR NOT EXISTS (
                            SELECT 1
                            FROM event_tags et
                            WHERE et.event_id = e.id
                       )
                    ORDER BY e.id
                    OFFSET %s
                """
                params: List[object] = [offset]
                if limit is not None:
                    sql += " LIMIT %s"
                    params.append(limit)
            else:
                sql = "SELECT id FROM events ORDER BY id OFFSET %s"
                params = [offset]
                if limit is not None:
                    sql += " LIMIT %s"
                    params.append(limit)

            cursor.execute(sql, tuple(params))
            return [str(row[0]) for row in cursor.fetchall()]
        finally:
            cursor.close()

    def _read_db_diagnostics(self, conn) -> Dict[str, object]:
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT current_database(), current_schema(), current_setting('search_path')")
            db_name, schema_name, search_path = cursor.fetchone()

            cursor.execute("SELECT COUNT(*) FROM events")
            events_count = int(cursor.fetchone()[0])

            cursor.execute("SELECT COUNT(*) FROM series")
            series_count = int(cursor.fetchone()[0])

            cursor.execute("SELECT COUNT(*) FROM tags")
            tags_count = int(cursor.fetchone()[0])

            cursor.execute("SELECT COUNT(*) FROM event_tags")
            event_tags_count = int(cursor.fetchone()[0])

            return {
                "current_database": db_name,
                "current_schema": schema_name,
                "search_path": search_path,
                "events_count": events_count,
                "series_count": series_count,
                "tags_count": tags_count,
                "event_tags_count": event_tags_count,
            }
        finally:
            cursor.close()

    def _fetch_event_from_api(self, event_id: str) -> Optional[Dict]:
        url = f"{self.BASE_URL}/events/{event_id}"
        last_error: Optional[Exception] = None

        for attempt in range(1, self.retries + 1):
            try:
                response = self.session.get(url, timeout=self.timeout_seconds)
                if response.status_code == 404:
                    return None
                response.raise_for_status()
                payload = response.json()
                if isinstance(payload, dict):
                    return payload
                return None
            except Exception as exc:
                last_error = exc
                if attempt < self.retries:
                    time.sleep(min(2 ** (attempt - 1), 5))

        if last_error:
            raise last_error
        return None

    @staticmethod
    def _extract_series(event_payload: Dict) -> Optional[Dict]:
        series_raw = event_payload.get("series")
        if isinstance(series_raw, dict):
            return series_raw
        if isinstance(series_raw, list) and series_raw and isinstance(series_raw[0], dict):
            return series_raw[0]
        return None

    @staticmethod
    def _extract_tags(event_payload: Dict) -> List[Tuple[str, Optional[str]]]:
        tags_out: List[Tuple[str, Optional[str]]] = []
        for tag in event_payload.get("tags", []) or []:
            if not isinstance(tag, dict):
                continue
            tag_id = tag.get("id") or tag.get("slug") or tag.get("label")
            if not tag_id:
                continue
            tags_out.append((str(tag_id), tag.get("label")))
        return tags_out

    def _upsert_single_event_metadata(self, conn, event_id: str, event_payload: Dict) -> None:
        cursor = conn.cursor()
        try:
            series = self._extract_series(event_payload)
            series_id: Optional[str] = None
            if series and series.get("id"):
                series_id = str(series.get("id"))
                cursor.execute(
                    """
                    INSERT INTO series (
                        id, ticker, slug, title, subtitle, series_type, recurrence, description
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        ticker = EXCLUDED.ticker,
                        slug = EXCLUDED.slug,
                        title = EXCLUDED.title,
                        subtitle = EXCLUDED.subtitle,
                        series_type = EXCLUDED.series_type,
                        recurrence = EXCLUDED.recurrence,
                        description = EXCLUDED.description
                    """,
                    (
                        series_id,
                        series.get("ticker"),
                        series.get("slug"),
                        series.get("title"),
                        series.get("subtitle"),
                        series.get("seriesType") if "seriesType" in series else series.get("series_type"),
                        series.get("recurrence"),
                        series.get("description"),
                    ),
                )
                self.stats["series_upserted"] += 1

            cursor.execute(
                """
                UPDATE events
                SET series_id = %s
                WHERE id = %s
                """,
                (series_id, event_id),
            )
            if cursor.rowcount:
                self.stats["events_series_updated"] += int(cursor.rowcount)

            tags = self._extract_tags(event_payload)
            for tag_id, label in tags:
                cursor.execute(
                    """
                    INSERT INTO tags (id, label)
                    VALUES (%s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        label = EXCLUDED.label
                    """,
                    (tag_id, label),
                )
                self.stats["tags_upserted"] += 1

            # Keep mapping in sync with API payload for this event.
            cursor.execute("DELETE FROM event_tags WHERE event_id = %s", (event_id,))
            for tag_id, _ in tags:
                cursor.execute(
                    """
                    INSERT INTO event_tags (event_id, tag_id)
                    VALUES (%s, %s)
                    ON CONFLICT (event_id, tag_id) DO NOTHING
                    """,
                    (event_id, tag_id),
                )
                self.stats["event_tags_inserted"] += 1

            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()

    def run(self, limit: Optional[int], offset: int, missing_only: bool) -> None:
        print("🔄 Backfilling series/tags/event_tags from /events/{id} ...")
        print(f"   DB: {self.connection_params['database']} @ {self.connection_params['host']}:{self.connection_params['port']}")
        if missing_only:
            print("   Mode: only events with missing series_id or missing tag links")

        conn = self._get_conn()
        try:
            diag = self._read_db_diagnostics(conn)
            print(
                "   Connected as: "
                f"db={diag['current_database']}, schema={diag['current_schema']}, search_path={diag['search_path']}"
            )
            print(
                "   Current counts: "
                f"events={diag['events_count']}, series={diag['series_count']}, "
                f"tags={diag['tags_count']}, event_tags={diag['event_tags_count']}"
            )

            event_ids = self._fetch_event_ids(conn, limit=limit, offset=offset, missing_only=missing_only)
            self.stats["events_total"] = len(event_ids)
            print(f"   Events to process: {len(event_ids)}")

            if not event_ids:
                print("   ℹ️  Nothing to backfill in current database context.")
                print("      If this is unexpected, check DB_NAME/LOCAL_DB_NAME and whether events were loaded here.")
                return

            for idx, event_id in enumerate(event_ids, start=1):
                try:
                    payload = self._fetch_event_from_api(event_id)
                    if payload is None:
                        self.stats["events_failed"] += 1
                        print(f"   [{idx}/{len(event_ids)}] ⚠️  Event {event_id}: not found in API")
                        continue

                    self._upsert_single_event_metadata(conn, event_id, payload)
                    self.stats["events_processed"] += 1

                    if idx % 50 == 0 or idx == len(event_ids):
                        print(
                            f"   [{idx}/{len(event_ids)}] ✅ processed={self.stats['events_processed']} "
                            f"failed={self.stats['events_failed']}"
                        )
                except Exception as exc:
                    self.stats["events_failed"] += 1
                    print(f"   [{idx}/{len(event_ids)}] ❌ Event {event_id}: {exc}")

                if self.sleep_seconds > 0:
                    time.sleep(self.sleep_seconds)
        finally:
            conn.close()

        print("\n📊 Backfill summary")
        print(f"   events_total: {self.stats['events_total']}")
        print(f"   events_processed: {self.stats['events_processed']}")
        print(f"   events_failed: {self.stats['events_failed']}")
        print(f"   series_upserted: {self.stats['series_upserted']}")
        print(f"   tags_upserted: {self.stats['tags_upserted']}")
        print(f"   event_tags_inserted: {self.stats['event_tags_inserted']}")
        print(f"   events_series_updated: {self.stats['events_series_updated']}")


def main():
    parser = argparse.ArgumentParser(
        description="Backfill series/tags/event_tags from existing events via /events/{id}"
    )
    parser.add_argument("--limit", type=int, default=None, help="Max number of events to process")
    parser.add_argument("--offset", type=int, default=0, help="Offset in events table")
    parser.add_argument(
        "--missing-only",
        action="store_true",
        help="Process only events with missing series_id or missing event_tags",
    )
    parser.add_argument("--timeout", type=int, default=20, help="API timeout in seconds")
    parser.add_argument("--retries", type=int, default=3, help="API retries per event")
    parser.add_argument("--sleep-ms", type=int, default=0, help="Sleep between requests (ms)")
    args = parser.parse_args()

    backfiller = EventMetadataBackfiller(
        timeout_seconds=args.timeout,
        retries=args.retries,
        sleep_ms=args.sleep_ms,
    )
    backfiller.run(limit=args.limit, offset=args.offset, missing_only=args.missing_only)


if __name__ == "__main__":
    main()

