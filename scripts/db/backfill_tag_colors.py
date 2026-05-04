"""
Backfill missing tag colors in `tags.hex_color` using Agent 2 (The Colorist).

This script:
1) Ensures `tags.hex_color` schema exists
2) Selects tags where `is_primary = TRUE AND hex_color IS NULL`
3) Generates a distinct color for each tag with Agent 2
4) Updates only rows that are still NULL (idempotent)

Usage examples:
    python scripts/db/backfill_tag_colors.py
    python scripts/db/backfill_tag_colors.py --limit 200
    python scripts/db/backfill_tag_colors.py --batch-size 50 --sleep-ms 150
    python scripts/db/backfill_tag_colors.py --dry-run
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from typing import TYPE_CHECKING, List, Optional, Tuple

import psycopg2
from dotenv import load_dotenv

# Add project root to path for direct script execution.
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

if TYPE_CHECKING:
    from scripts.ai import Agent2ColoristGenerator

load_dotenv()


class TagColorBackfiller:
    def __init__(
        self,
        *,
        sleep_ms: int = 0,
    ) -> None:
        self.sleep_seconds = max(0, sleep_ms) / 1000.0
        self.connection_params = self._build_connection_params()

        self.tag_colors_model = os.getenv("POLYSTARS_TAG_COLORS_MODEL", "").strip()
        self.tag_colors_prompt_version = (
            os.getenv("POLYSTARS_TAG_COLORS_PROMPT_VERSION", "v1").strip() or "v1"
        )
        self._generator: Optional["Agent2ColoristGenerator"] = None

        self.stats = {
            "requested": 0,
            "processed": 0,
            "updated": 0,
            "failed": 0,
            "skipped_not_null": 0,
        }

    @staticmethod
    def _build_connection_params() -> dict:
        ssl_mode = os.getenv("DB_SSLMODE", "require")
        return {
            "host": os.getenv("LOCAL_DB_HOST", os.getenv("DB_HOST")),
            "port": os.getenv("LOCAL_DB_PORT", os.getenv("DB_PORT", "5432")),
            "database": os.getenv("LOCAL_DB_NAME", os.getenv("DB_NAME")),
            "user": os.getenv("LOCAL_DB_USER", os.getenv("DB_USER")),
            "password": os.getenv("LOCAL_DB_PASSWORD", os.getenv("DB_PASSWORD")),
            "sslmode": ssl_mode,
        }

    def _get_conn(self):
        return psycopg2.connect(**self.connection_params)

    def _get_generator(self) -> "Agent2ColoristGenerator":
        if self._generator is None:
            from scripts.ai import Agent2ColoristGenerator

            self._generator = Agent2ColoristGenerator(
                model=self.tag_colors_model or None,
                prompt_version=self.tag_colors_prompt_version,
            )
            self.tag_colors_model = self._generator.model
        return self._generator

    @staticmethod
    def _fetch_total_missing(conn) -> int:
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT COUNT(*) FROM tags WHERE is_primary = TRUE AND hex_color IS NULL")
            return int(cursor.fetchone()[0])
        finally:
            cursor.close()

    @staticmethod
    def _fetch_missing_batch(
        conn,
        *,
        limit: int,
    ) -> List[Tuple[str, str]]:
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                SELECT id, COALESCE(NULLIF(BTRIM(label), ''), id) AS effective_label
                FROM tags
                WHERE is_primary = TRUE
                  AND hex_color IS NULL
                ORDER BY id ASC
                LIMIT %s
                """,
                (limit,),
            )
            rows = cursor.fetchall()
            return [(str(row[0]), str(row[1])) for row in rows]
        finally:
            cursor.close()

    @staticmethod
    def _fetch_palette(conn) -> List[dict]:
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                SELECT DISTINCT COALESCE(NULLIF(BTRIM(label), ''), id) AS tag_label, hex_color
                FROM tags
                WHERE is_primary = TRUE
                  AND hex_color IS NOT NULL
                ORDER BY tag_label ASC, hex_color ASC
                """
            )
            palette: List[dict] = []
            for row in cursor.fetchall():
                if not row or not row[1]:
                    continue
                palette.append({"tag_label": str(row[0]), "hex_color": str(row[1])})
            return palette
        finally:
            cursor.close()

    def run(
        self,
        *,
        limit: Optional[int],
        batch_size: int,
        dry_run: bool,
    ) -> None:
        conn = self._get_conn()
        try:
            total_missing = self._fetch_total_missing(conn)
            print(
                f"Connected to db={self.connection_params['database']} "
                f"host={self.connection_params['host']} port={self.connection_params['port']}"
            )
            print(f"Missing colors before run: {total_missing}")
            if total_missing == 0:
                print("Nothing to backfill.")
                return

            to_process = total_missing
            if limit is not None:
                to_process = min(to_process, max(0, limit))
            self.stats["requested"] = to_process
            print(
                f"Start backfill: requested={to_process} batch_size={batch_size} "
                f"dry_run={dry_run} model={self.tag_colors_model or 'default'} "
                f"prompt_version={self.tag_colors_prompt_version}"
            )
            if to_process == 0:
                return

            generator = self._get_generator() if not dry_run else None

            while self.stats["processed"] < to_process:
                remaining = to_process - self.stats["processed"]
                current_batch_size = min(batch_size, remaining)
                batch = self._fetch_missing_batch(
                    conn,
                    limit=current_batch_size,
                )
                if not batch:
                    break

                palette = self._fetch_palette(conn)
                for tag_id, label in batch:
                    self.stats["processed"] += 1
                    if dry_run:
                        print(f"[DRY-RUN] would color tag_id={tag_id} label={label}")
                        continue

                    try:
                        assert generator is not None
                        out = generator.generate(
                            {
                                "new_primary_tag": label,
                                "existing_palette": palette,
                            }
                        )
                    except Exception as exc:
                        self.stats["failed"] += 1
                        print(f"[WARN] tag_id={tag_id} generation failed: {exc}")
                        continue

                    cursor = conn.cursor()
                    try:
                        cursor.execute(
                            """
                            UPDATE tags
                            SET hex_color = %s
                            WHERE id = %s
                              AND hex_color IS NULL
                            """,
                            (out.hex_color, tag_id),
                        )
                        conn.commit()
                        if cursor.rowcount:
                            self.stats["updated"] += 1
                            palette.append({"tag_label": label, "hex_color": out.hex_color})
                        else:
                            self.stats["skipped_not_null"] += 1
                    except Exception as exc:
                        conn.rollback()
                        self.stats["failed"] += 1
                        print(f"[WARN] tag_id={tag_id} update failed: {exc}")
                    finally:
                        cursor.close()

                    if self.sleep_seconds > 0:
                        time.sleep(self.sleep_seconds)

                print(
                    "progress "
                    f"processed={self.stats['processed']} updated={self.stats['updated']} "
                    f"failed={self.stats['failed']} skipped_not_null={self.stats['skipped_not_null']}"
                )

            remaining_missing = self._fetch_total_missing(conn)
            print(
                "done "
                f"requested={self.stats['requested']} processed={self.stats['processed']} "
                f"updated={self.stats['updated']} failed={self.stats['failed']} "
                f"skipped_not_null={self.stats['skipped_not_null']} "
                f"remaining_missing={remaining_missing} model={self.tag_colors_model or 'default'}"
            )
        finally:
            conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill missing colors for tags.hex_color using Agent 2 (The Colorist)."
    )
    parser.add_argument("--limit", type=int, default=None, help="Max number of NULL-color tags to process.")
    parser.add_argument("--batch-size", type=int, default=50, help="Batch size for selection.")
    parser.add_argument("--sleep-ms", type=int, default=0, help="Sleep between successful updates (ms).")
    parser.add_argument("--dry-run", action="store_true", help="Do not call Agent 2 or update DB.")
    args = parser.parse_args()

    if args.limit is not None and args.limit < 0:
        raise ValueError("--limit must be >= 0")
    if args.batch_size <= 0:
        raise ValueError("--batch-size must be > 0")
    if args.sleep_ms < 0:
        raise ValueError("--sleep-ms must be >= 0")

    backfiller = TagColorBackfiller(sleep_ms=args.sleep_ms)
    backfiller.run(
        limit=args.limit,
        batch_size=args.batch_size,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
