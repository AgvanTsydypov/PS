"""Check whether rows from csv_with_eoa.csv exist in the ``participants`` table.

For each CSV row, a record is considered to "exist" when there is at least one
``participants`` row with the same ``LOWER(proxy_wallet)`` and ``event_slug``
(across any season). The script prints a summary and, optionally, writes a
detailed per-row report to a CSV.

Usage:
    venv\\Scripts\\python.exe scripts\\check_csv_in_participants.py
    venv\\Scripts\\python.exe scripts\\check_csv_in_participants.py --local
    venv\\Scripts\\python.exe scripts\\check_csv_in_participants.py --season-id 5
    venv\\Scripts\\python.exe scripts\\check_csv_in_participants.py --output report.csv
"""

from __future__ import annotations

import argparse
import csv
import os
from typing import Any, Dict, List, Optional, Set, Tuple

import psycopg2
from dotenv import load_dotenv

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


def _read_csv(path: str) -> List[Dict[str, str]]:
    with open(path, "r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _lookup_participants(
    conn,
    pairs: Set[Tuple[str, str]],
    season_id: Optional[int],
) -> Dict[Tuple[str, str], List[int]]:
    """Return {(lower_wallet, event_slug): [season_ids...]} for hits."""
    if not pairs:
        return {}

    wallets = sorted({w for w, _ in pairs})
    slugs = sorted({s for _, s in pairs})

    sql = """
        SELECT LOWER(proxy_wallet) AS wallet, event_slug, season_id
        FROM participants
        WHERE LOWER(proxy_wallet) = ANY(%s)
          AND event_slug = ANY(%s)
    """
    params: List[Any] = [wallets, slugs]
    if season_id is not None:
        sql += " AND season_id = %s"
        params.append(season_id)

    hits: Dict[Tuple[str, str], List[int]] = {}
    with conn.cursor() as cur:
        cur.execute(sql, params)
        for wallet, slug, sid in cur.fetchall():
            key = (wallet, slug)
            if (wallet, slug) in pairs:
                hits.setdefault(key, []).append(int(sid))
    return hits


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", default="csv_with_eoa.csv")
    ap.add_argument(
        "--local",
        action="store_true",
        help="Use LOCAL_DB_* env overrides instead of DB_*.",
    )
    ap.add_argument(
        "--season-id",
        type=int,
        default=None,
        help="Restrict the lookup to a single season_id (default: any season).",
    )
    ap.add_argument(
        "--output",
        default=None,
        help="Optional path to write a per-row CSV report (adds exists/seasons columns).",
    )
    args = ap.parse_args()

    rows = _read_csv(args.input)
    print(f"loaded {len(rows)} rows from {args.input}")

    pairs: Set[Tuple[str, str]] = set()
    for r in rows:
        wallet = (r.get("proxy_wallet") or "").strip().lower()
        slug = (r.get("event_slug") or "").strip()
        if wallet and slug:
            pairs.add((wallet, slug))
    print(f"unique (proxy_wallet, event_slug) pairs: {len(pairs)}")

    conn = psycopg2.connect(**_db_params(use_local_db=args.local))
    try:
        hits = _lookup_participants(conn, pairs, args.season_id)
    finally:
        conn.close()

    found_rows = 0
    missing_rows = 0
    invalid_rows = 0
    for r in rows:
        wallet = (r.get("proxy_wallet") or "").strip().lower()
        slug = (r.get("event_slug") or "").strip()
        if not wallet or not slug:
            invalid_rows += 1
            continue
        if (wallet, slug) in hits:
            found_rows += 1
        else:
            missing_rows += 1

    print()
    print("=== summary ===")
    print(f"season filter:           {args.season_id if args.season_id is not None else 'any'}")
    print(f"CSV rows total:          {len(rows)}")
    print(f"  with empty wallet/slug: {invalid_rows}")
    print(f"  found in participants:  {found_rows}")
    print(f"  missing:                {missing_rows}")
    print(f"unique pairs found:      {len(hits)} / {len(pairs)}")

    if args.output:
        fieldnames = list(rows[0].keys()) if rows else []
        for extra in ("participants_exists", "participants_season_ids"):
            if extra not in fieldnames:
                fieldnames.append(extra)
        with open(args.output, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            for r in rows:
                wallet = (r.get("proxy_wallet") or "").strip().lower()
                slug = (r.get("event_slug") or "").strip()
                seasons = hits.get((wallet, slug), [])
                out = dict(r)
                out["participants_exists"] = "1" if seasons else "0"
                out["participants_season_ids"] = ",".join(str(s) for s in sorted(seasons))
                w.writerow(out)
        print(f"wrote per-row report: {args.output}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
