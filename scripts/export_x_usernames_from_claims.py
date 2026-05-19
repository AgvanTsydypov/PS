"""Export X (Twitter) usernames of minted PolyStars card owners.

Walks the ``claims`` table, takes each distinct ``proxy_wallet`` that already
has a public card page (``card_slug`` filled in by the cron mint worker),
queries Polymarket's public-profile endpoint, and — when the profile carries
a non-empty ``xUsername`` — appends a line to the output file in the form:

    xUsername,https://polystars.app/cards/<card_slug>

Usage:
    venv\\Scripts\\python.exe scripts\\export_x_usernames_from_claims.py
    venv\\Scripts\\python.exe scripts\\export_x_usernames_from_claims.py --output x_usernames.csv
    venv\\Scripts\\python.exe scripts\\export_x_usernames_from_claims.py --local --delay 0.4
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

import psycopg2
from dotenv import load_dotenv


POLYMARKET_PROFILE_API = "https://gamma-api.polymarket.com/public-profile"
POLYSTARS_CARD_URL = "https://polystars.app/cards/{slug}"
REQUEST_TIMEOUT_SECONDS = 15
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

logger = logging.getLogger("export_x_usernames")


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


def _fetch_claim_rows(
    use_local_db: bool,
    limit: Optional[int],
    status_filter: Optional[str],
) -> List[Tuple[str, str]]:
    """Return distinct (proxy_wallet, card_slug) pairs from claims.

    One card per proxy_wallet — the most recent COMPLETED mint wins. We need
    a card_slug because that's the only thing that produces a working public
    permalink; rows still in the queue (no slug yet) are skipped.
    """
    query = """
        SELECT DISTINCT ON (LOWER(proxy_wallet))
               proxy_wallet,
               card_slug
        FROM claims
        WHERE proxy_wallet IS NOT NULL
          AND card_slug IS NOT NULL
          AND TRIM(card_slug) <> ''
    """
    params: List[Any] = []
    if status_filter:
        query += " AND status = %s"
        params.append(status_filter)
    query += " ORDER BY LOWER(proxy_wallet), timestamp DESC"
    if limit is not None and limit > 0:
        query += " LIMIT %s"
        params.append(limit)

    conn = psycopg2.connect(**_db_params(use_local_db=use_local_db))
    try:
        with conn.cursor() as cur:
            cur.execute(query, params)
            return [(row[0], row[1]) for row in cur.fetchall()]
    finally:
        conn.close()


def _fetch_public_profile(wallet_address: str) -> Optional[Dict[str, Any]]:
    """Call Polymarket's public-profile endpoint. Returns ``None`` on 4xx
    (no profile registered) and re-raises on other failures so transient
    network problems aren't silently swallowed."""
    url = f"{POLYMARKET_PROFILE_API}?{urllib.parse.urlencode({'address': wallet_address})}"
    req = urllib.request.Request(
        url,
        method="GET",
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
    )
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            payload = json.loads(body or "{}")
            return payload if isinstance(payload, dict) else None
    except urllib.error.HTTPError as exc:
        if exc.code in {403, 404}:
            return None
        logger.warning("HTTP %s for wallet %s", exc.code, wallet_address)
        return None
    except (urllib.error.URLError, TimeoutError) as exc:
        logger.warning("Network error for wallet %s: %s", wallet_address, exc)
        return None
    except json.JSONDecodeError:
        logger.warning("Bad JSON for wallet %s", wallet_address)
        return None


def main() -> int:
    load_dotenv()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--output",
        default="x_usernames.csv",
        help="Path of the output CSV. Default: x_usernames.csv",
    )
    ap.add_argument(
        "--local",
        action="store_true",
        help="Use LOCAL_DB_* env vars instead of the production DB_* set.",
    )
    ap.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit how many distinct wallets to process (debug).",
    )
    ap.add_argument(
        "--status",
        default="COMPLETED",
        help="claims.status to filter on (default COMPLETED). "
             "Pass empty string to disable the filter.",
    )
    ap.add_argument(
        "--delay",
        type=float,
        default=0.25,
        help="Seconds to sleep between profile requests (rate-limit guard).",
    )
    args = ap.parse_args()

    status_filter = args.status.strip() or None

    logger.info(
        "Loading claims (status=%s, limit=%s, db=%s)…",
        status_filter or "<any>",
        args.limit if args.limit else "<all>",
        "local" if args.local else "remote",
    )
    try:
        rows = _fetch_claim_rows(
            use_local_db=args.local,
            limit=args.limit,
            status_filter=status_filter,
        )
    except psycopg2.Error as exc:
        logger.error("DB query failed: %s", exc)
        return 2

    logger.info("Found %d distinct (proxy_wallet, card_slug) pairs", len(rows))
    if not rows:
        return 0

    written = 0
    skipped_no_username = 0
    skipped_no_profile = 0

    with open(args.output, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        for index, (proxy_wallet, card_slug) in enumerate(rows, start=1):
            profile = _fetch_public_profile(proxy_wallet)
            if profile is None:
                skipped_no_profile += 1
            else:
                x_username = str(profile.get("xUsername") or "").strip()
                if x_username:
                    writer.writerow(
                        [x_username, POLYSTARS_CARD_URL.format(slug=card_slug)]
                    )
                    fh.flush()
                    written += 1
                else:
                    skipped_no_username += 1

            if index % 25 == 0 or index == len(rows):
                logger.info(
                    "Progress %d/%d — written=%d, no_username=%d, no_profile=%d",
                    index,
                    len(rows),
                    written,
                    skipped_no_username,
                    skipped_no_profile,
                )

            if args.delay > 0 and index < len(rows):
                time.sleep(args.delay)

    logger.info(
        "Done. Wrote %d rows to %s (no_username=%d, no_profile=%d)",
        written,
        args.output,
        skipped_no_username,
        skipped_no_profile,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
