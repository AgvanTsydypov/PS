"""
Backfill closed positions for events that have winning redemptions but no rows
in user_closed_positions.

Typical cause: events force-included via GENESIS_INCLUDE_EVENT_IDS were loaded
into events/redemptions during the historical Genesis run, but never reached
the closed-time downstream pipeline (no entry in event_resolution_queue), so
fetch_user_closed_positions_parallel.py was never invoked for them.

This wrapper:
  1. Finds those events (or accepts an explicit list via --event-ids).
  2. Sets POLYSTARS_EVENT_IDS + POLYSTARS_MIN_VOLUME=0.
  3. Invokes the existing closed-positions fetcher as a subprocess.
  4. Reports how many rows landed per event.

Usage:
  python scripts/backfill_closed_positions.py --dry-run
  python scripts/backfill_closed_positions.py
  python scripts/backfill_closed_positions.py --event-ids 238474,38884 --workers 8
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv()

FETCHER = "scripts/fetch/fetch_user_closed_positions_parallel.py"


def _db_params(use_local_db: bool) -> Dict[str, object]:
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


def _find_gap_events(conn) -> List[Dict[str, object]]:
    """Events with winning redemptions and zero closed positions."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT e.id, e.title, e.volume, e.end_date
            FROM public.events e
            WHERE EXISTS (
                SELECT 1 FROM public.redemptions r
                WHERE r.event_id = e.id AND r.payout_usdc > 0
            )
              AND NOT EXISTS (
                SELECT 1 FROM public.user_closed_positions p
                WHERE p.event_id = e.id
              )
            ORDER BY e.volume DESC NULLS LAST
            """
        )
        return list(cur.fetchall())


def _positions_per_event(conn, event_ids: List[str]) -> Dict[str, int]:
    if not event_ids:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT event_id, COUNT(*)
            FROM public.user_closed_positions
            WHERE event_id = ANY(%s)
            GROUP BY event_id
            """,
            (event_ids,),
        )
        return {row[0]: int(row[1]) for row in cur.fetchall()}


def _run_fetcher(
    event_ids: List[str],
    workers: int,
    positions_per_user: Optional[int],
    use_local_db: bool,
) -> int:
    env = os.environ.copy()
    env["POLYSTARS_EVENT_IDS"] = ",".join(event_ids)
    env["POLYSTARS_MIN_VOLUME"] = "0"
    if positions_per_user is not None:
        env["POLYSTARS_TEST_MAX_CLOSED_POSITIONS_PER_USER"] = str(positions_per_user)

    cmd = [sys.executable, FETCHER, "--upload", "--workers", str(workers)]
    if use_local_db:
        cmd.append("--local")

    print(f"▶️  {' '.join(cmd)}")
    print(f"   POLYSTARS_EVENT_IDS={env['POLYSTARS_EVENT_IDS'][:120]}"
          f"{'...' if len(env['POLYSTARS_EVENT_IDS']) > 120 else ''}")
    print(f"   POLYSTARS_MIN_VOLUME=0")

    result = subprocess.run(cmd, env=env, cwd=str(PROJECT_ROOT))
    return result.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--event-ids",
        type=str,
        default=None,
        help="Comma-separated event IDs. Skips auto-detection.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=int(os.getenv("POLYSTARS_BACKFILL_WORKERS", "8")),
        help="Parallel workers for the fetcher (default: 8).",
    )
    parser.add_argument(
        "--positions-per-user",
        type=int,
        default=200,
        help="Max positions per (user, market) pair (default: 200).",
    )
    parser.add_argument(
        "--use-remote-db",
        action="store_true",
        help="Do NOT pass --local to the fetcher (use DB_* envs directly).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only list gap events, do not run the fetcher.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Cap the number of events to backfill (after auto-detection).",
    )
    args = parser.parse_args()

    use_local_db = not args.use_remote_db
    db_params = _db_params(use_local_db)

    print(f"🔌 Connecting to {db_params['host']}:{db_params['port']}/{db_params['database']}")
    conn = psycopg2.connect(**db_params)
    conn.autocommit = True

    try:
        if args.event_ids:
            event_ids = [s.strip() for s in args.event_ids.split(",") if s.strip()]
            print(f"📋 Using explicit list: {len(event_ids)} event(s)")
            events: List[Dict[str, object]] = [{"id": eid, "title": "?", "volume": None} for eid in event_ids]
        else:
            print("🔎 Detecting events with winning redemptions but no closed positions...")
            events = _find_gap_events(conn)
            print(f"   Found {len(events)} gap event(s)")

        if args.limit:
            events = events[: args.limit]
            print(f"   Limited to first {len(events)}")

        if not events:
            print("✅ Nothing to backfill.")
            return 0

        for ev in events:
            vol = ev.get("volume")
            vol_str = f"{float(vol):,.0f}" if vol is not None else "—"
            print(f"   • {ev['id']:<8}  volume={vol_str:>15}  {str(ev.get('title') or '')[:80]}")

        if args.dry_run:
            print("\n🛈 Dry run — fetcher not invoked.")
            return 0

        event_ids = [str(ev["id"]) for ev in events]
        before = _positions_per_event(conn, event_ids)

        rc = _run_fetcher(
            event_ids=event_ids,
            workers=args.workers,
            positions_per_user=args.positions_per_user,
            use_local_db=use_local_db,
        )
        if rc != 0:
            print(f"❌ Fetcher exited with code {rc}")
            return rc

        after = _positions_per_event(conn, event_ids)
        print("\n📊 Backfill result (rows in user_closed_positions):")
        total_added = 0
        for eid in event_ids:
            b = before.get(eid, 0)
            a = after.get(eid, 0)
            delta = a - b
            total_added += delta
            flag = "✅" if a > 0 else "⚠️ "
            print(f"   {flag} {eid:<8}  before={b:>7}  after={a:>7}  +{delta}")
        print(f"\n🏁 Total rows added: {total_added:,}")

        still_empty = [eid for eid in event_ids if after.get(eid, 0) == 0]
        if still_empty:
            print(f"⚠️  {len(still_empty)} event(s) still have 0 positions: {','.join(still_empty)}")
            print("   Check logs/positions_fetch_*.log for API/retry failures.")
            return 2
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
