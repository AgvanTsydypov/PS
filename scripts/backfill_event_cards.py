"""
Backfill event cards for already processed events.

Usage examples:
  python scripts/backfill_event_cards.py --dry-run
  python scripts/backfill_event_cards.py --limit 500 --batch-size 25
  python scripts/backfill_event_cards.py --retry-errors
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any, Dict, List, Optional

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

# Add project root to path for direct script execution.
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from scripts.ai import Agent1QuantCardGenerator
from scripts.data_loading_manager import GENESIS_END_DATE, GENESIS_START_DATE

load_dotenv()


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


def _ensure_event_cards_schema(conn: Any) -> None:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS event_cards (
                event_id TEXT PRIMARY KEY,
                card_title TEXT,
                card_lore TEXT,
                primary_tag TEXT,
                secondary_tag TEXT,
                agent_name TEXT NOT NULL DEFAULT 'agent_1_quant',
                model_name TEXT NOT NULL DEFAULT 'gemini-2.5-flash',
                prompt_version TEXT NOT NULL DEFAULT 'v1',
                status TEXT NOT NULL DEFAULT 'ok'
                    CHECK (status IN ('ok', 'error')),
                error_text TEXT,
                generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT fk_event_cards_event
                    FOREIGN KEY (event_id) REFERENCES events(id) ON DELETE CASCADE
            )
            """
        )
        cursor.execute("ALTER TABLE event_cards ALTER COLUMN card_title DROP NOT NULL")
        cursor.execute("ALTER TABLE event_cards ALTER COLUMN card_lore DROP NOT NULL")
        cursor.execute("ALTER TABLE event_cards ALTER COLUMN primary_tag DROP NOT NULL")
        cursor.execute("ALTER TABLE event_cards ADD COLUMN IF NOT EXISTS secondary_tag TEXT")
        cursor.execute("ALTER TABLE event_cards ADD COLUMN IF NOT EXISTS agent_name TEXT NOT NULL DEFAULT 'agent_1_quant'")
        cursor.execute("ALTER TABLE event_cards ADD COLUMN IF NOT EXISTS model_name TEXT NOT NULL DEFAULT 'gemini-2.5-flash'")
        cursor.execute("ALTER TABLE event_cards ADD COLUMN IF NOT EXISTS prompt_version TEXT NOT NULL DEFAULT 'v1'")
        cursor.execute("ALTER TABLE event_cards ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'ok'")
        cursor.execute("ALTER TABLE event_cards ADD COLUMN IF NOT EXISTS error_text TEXT")
        cursor.execute("ALTER TABLE event_cards ADD COLUMN IF NOT EXISTS generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()")
        cursor.execute("ALTER TABLE event_cards ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()")
        cursor.execute(
            """
            ALTER TABLE event_cards
            DROP CONSTRAINT IF EXISTS event_cards_status_check
            """
        )
        cursor.execute(
            """
            ALTER TABLE event_cards
            ADD CONSTRAINT event_cards_status_check
            CHECK (status IN ('ok', 'error'))
            """
        )
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_event_cards_status ON event_cards(status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_event_cards_prompt_version ON event_cards(prompt_version)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_event_cards_generated_at ON event_cards(generated_at DESC)")


def _select_candidate_event_ids(
    conn: Any,
    batch_size: int,
    retry_errors: bool,
    include_genesis: bool,
) -> List[str]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
        cursor.execute(
            """
            SELECT e.id
            FROM events e
            LEFT JOIN event_resolution_queue q
                ON q.event_id = e.id
            LEFT JOIN event_cards ec
                ON ec.event_id = e.id
            WHERE (ec.status IS NULL OR ec.status <> 'ok')
              AND (%s OR ec.status IS DISTINCT FROM 'error')
              AND (
                  q.status = 'processed'
                  OR (
                      %s
                      AND COALESCE(e.end_date::date, e.creation_date::date, e.start_date::date)
                          BETWEEN %s AND %s
                  )
              )
            ORDER BY COALESCE(q.processed_at, q.updated_at, q.created_at, e.end_date, e.creation_date, e.start_date) ASC, e.id ASC
            LIMIT %s
            """,
            (retry_errors, include_genesis, GENESIS_START_DATE, GENESIS_END_DATE, batch_size),
        )
        rows = cursor.fetchall()
    return [str(row["id"]) for row in rows]


def _fetch_payloads(conn: Any, event_ids: List[str]) -> List[Dict[str, Any]]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
        cursor.execute(
            """
            SELECT
                e.id AS event_id,
                e.title,
                e.description,
                s.title AS series_title,
                s.recurrence AS series_recurrence,
                COALESCE(
                    ARRAY_REMOVE(ARRAY_AGG(DISTINCT t.label), NULL),
                    ARRAY[]::TEXT[]
                ) AS tags
            FROM events e
            LEFT JOIN series s
                ON s.id = e.series_id
            LEFT JOIN event_tags et
                ON et.event_id = e.id
            LEFT JOIN tags t
                ON t.id = et.tag_id
            WHERE e.id = ANY(%s)
            GROUP BY
                e.id,
                e.title,
                e.description,
                s.title,
                s.recurrence
            ORDER BY e.id ASC
            """,
            (event_ids,),
        )
        rows = cursor.fetchall()
    return [dict(row) for row in rows]


def _upsert_ok(
    conn: Any,
    event_id: str,
    generated: Dict[str, Any],
    model_name: str,
    prompt_version: str,
    agent_name: str,
) -> None:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO event_cards (
                event_id,
                card_title,
                card_lore,
                primary_tag,
                secondary_tag,
                agent_name,
                model_name,
                prompt_version,
                status,
                error_text,
                generated_at,
                updated_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, 'ok', NULL, NOW(), NOW()
            )
            ON CONFLICT (event_id) DO UPDATE SET
                card_title = EXCLUDED.card_title,
                card_lore = EXCLUDED.card_lore,
                primary_tag = EXCLUDED.primary_tag,
                secondary_tag = EXCLUDED.secondary_tag,
                agent_name = EXCLUDED.agent_name,
                model_name = EXCLUDED.model_name,
                prompt_version = EXCLUDED.prompt_version,
                status = 'ok',
                error_text = NULL,
                generated_at = NOW(),
                updated_at = NOW()
            """,
            (
                event_id,
                generated.get("card_title"),
                generated.get("card_lore"),
                generated.get("primary_tag"),
                generated.get("secondary_tag"),
                agent_name,
                model_name,
                prompt_version,
            ),
        )
    conn.commit()


def _upsert_error(
    conn: Any,
    event_id: str,
    error_text: str,
    model_name: str,
    prompt_version: str,
    agent_name: str,
) -> None:
    err = (error_text or "unknown error").strip()
    if len(err) > 2000:
        err = err[:2000]
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO event_cards (
                event_id,
                card_title,
                card_lore,
                primary_tag,
                secondary_tag,
                agent_name,
                model_name,
                prompt_version,
                status,
                error_text,
                generated_at,
                updated_at
            ) VALUES (
                %s, NULL, NULL, NULL, NULL, %s, %s, %s, 'error', %s, NOW(), NOW()
            )
            ON CONFLICT (event_id) DO UPDATE SET
                status = 'error',
                error_text = EXCLUDED.error_text,
                agent_name = EXCLUDED.agent_name,
                model_name = EXCLUDED.model_name,
                prompt_version = EXCLUDED.prompt_version,
                updated_at = NOW()
            """,
            (event_id, agent_name, model_name, prompt_version, err),
        )
    conn.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill event cards for processed events.")
    parser.add_argument("--batch-size", type=int, default=25, help="Batch size per DB fetch/generation cycle.")
    parser.add_argument("--limit", type=int, default=0, help="Max events to process. 0 means unlimited.")
    parser.add_argument("--dry-run", action="store_true", help="Only print candidate counts; do not generate.")
    parser.add_argument("--retry-errors", action="store_true", help="Re-try rows that already have status=error.")
    parser.add_argument("--skip-genesis", action="store_true", help="Exclude Genesis-period events from candidate selection.")
    parser.add_argument("--local", action="store_true", help="Use LOCAL_DB_* env settings first.")
    parser.add_argument("--model", type=str, default="", help="Override model for this run.")
    parser.add_argument("--prompt-version", type=str, default="v1", help="Prompt version to store in event_cards.")
    parser.add_argument("--agent-name", type=str, default="agent_1_quant", help="Agent name to store in event_cards.")
    args = parser.parse_args()

    if args.batch_size <= 0:
        raise ValueError("--batch-size must be > 0")
    if args.limit < 0:
        raise ValueError("--limit must be >= 0")
    include_genesis = not args.skip_genesis

    conn = psycopg2.connect(**_db_params(use_local_db=args.local))
    try:
        _ensure_event_cards_schema(conn)
        if args.dry_run:
            sample = _select_candidate_event_ids(
                conn,
                batch_size=max(args.batch_size, 50),
                retry_errors=args.retry_errors,
                include_genesis=include_genesis,
            )
            print(f"dry-run candidates_sample_count={len(sample)} sample={sample[:10]}")
            return

        generator = Agent1QuantCardGenerator(
            model=args.model.strip() or None,
            prompt_version=args.prompt_version,
        )
        model_name = generator.model
        prompt_version = generator.prompt_version

        total_processed = 0
        total_success = 0
        total_failed = 0

        print(
            f"Starting event cards backfill: model={model_name}, batch_size={args.batch_size}, "
            f"limit={args.limit or 'unlimited'}, retry_errors={args.retry_errors}, "
            f"include_genesis={include_genesis}"
        )

        while True:
            if args.limit and total_processed >= args.limit:
                break

            remaining = (args.limit - total_processed) if args.limit else args.batch_size
            batch_size = min(args.batch_size, remaining) if args.limit else args.batch_size
            event_ids = _select_candidate_event_ids(
                conn,
                batch_size=batch_size,
                retry_errors=args.retry_errors,
                include_genesis=include_genesis,
            )
            if not event_ids:
                break

            rows = _fetch_payloads(conn, event_ids)
            by_id = {str(row.get("event_id")): row for row in rows}

            for event_id in event_ids:
                payload_row = by_id.get(event_id)
                if not payload_row:
                    _upsert_error(
                        conn=conn,
                        event_id=event_id,
                        error_text="Event payload not found for selected event_id",
                        model_name=model_name,
                        prompt_version=prompt_version,
                        agent_name=args.agent_name,
                    )
                    total_failed += 1
                    total_processed += 1
                    continue

                payload = {
                    "title": payload_row.get("title"),
                    "description": payload_row.get("description"),
                    "series": {
                        "title": payload_row.get("series_title"),
                        "recurrence": payload_row.get("series_recurrence"),
                    },
                    "tags": payload_row.get("tags") or [],
                }
                try:
                    card = generator.generate(payload)
                    _upsert_ok(
                        conn=conn,
                        event_id=event_id,
                        generated=card.model_dump(),
                        model_name=model_name,
                        prompt_version=prompt_version,
                        agent_name=args.agent_name,
                    )
                    total_success += 1
                except Exception as exc:
                    _upsert_error(
                        conn=conn,
                        event_id=event_id,
                        error_text=str(exc),
                        model_name=model_name,
                        prompt_version=prompt_version,
                        agent_name=args.agent_name,
                    )
                    total_failed += 1
                total_processed += 1

            print(
                f"progress processed={total_processed} success={total_success} failed={total_failed}"
            )

        print(
            f"done processed={total_processed} success={total_success} failed={total_failed} model={model_name}"
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
