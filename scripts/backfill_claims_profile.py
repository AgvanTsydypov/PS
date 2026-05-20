"""Backfill claims.x_username / claims.profile_name from Polymarket profiles.

Path 2 of the profile-enrichment work. Path 1 stamps these two columns onto a
claim at queue-insert time (admin_backend/claims_mint.py); this script fills in
the rows that predate that change — claims whose ``x_username`` and
``profile_name`` are both still NULL.

For each distinct ``proxy_wallet`` with at least one un-enriched row, it calls
Polymarket's public-profile endpoint
(gamma-api.polymarket.com/public-profile?address={proxy_wallet}) and writes
``xUsername`` -> ``x_username`` and ``name`` -> ``profile_name`` onto *every*
claim row for that wallet whose pair is still NULL. Wallets with no registered
profile (403/404) are skipped and remain NULL, so re-running is safe and
idempotent — it only ever touches rows that are still empty.

Usage:
    venv\\Scripts\\python.exe scripts\\backfill_claims_profile.py
    venv\\Scripts\\python.exe scripts\\backfill_claims_profile.py --local --delay 0.4
    venv\\Scripts\\python.exe scripts\\backfill_claims_profile.py --limit 50 --dry-run
"""

from __future__ import annotations

import argparse
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


POLYMARKET_GAMMA_API_BASE = os.getenv(
    "POLYMARKET_GAMMA_API_BASE", "https://gamma-api.polymarket.com"
).rstrip("/")
REQUEST_TIMEOUT_SECONDS = float(os.getenv("POLYMARKET_PROFILE_TIMEOUT_SECONDS", "15"))
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

logger = logging.getLogger("backfill_claims_profile")


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


def _fetch_unenriched_wallets(
    conn: "psycopg2.extensions.connection",
    limit: Optional[int],
) -> List[str]:
    """Distinct proxy_wallets having >=1 claim with both profile columns NULL."""
    query = """
        SELECT DISTINCT proxy_wallet
        FROM claims
        WHERE proxy_wallet IS NOT NULL
          AND TRIM(proxy_wallet) <> ''
          AND x_username IS NULL
          AND profile_name IS NULL
        ORDER BY proxy_wallet
    """
    params: List[Any] = []
    if limit is not None and limit > 0:
        query += " LIMIT %s"
        params.append(limit)
    with conn.cursor() as cur:
        cur.execute(query, params)
        return [row[0] for row in cur.fetchall()]


def _fetch_public_profile(wallet_address: str) -> Optional[Dict[str, Any]]:
    """Call Polymarket's public-profile endpoint. ``None`` on 4xx / network /
    JSON errors so a single bad wallet never aborts the whole backfill."""
    url = f"{POLYMARKET_GAMMA_API_BASE}/public-profile?{urllib.parse.urlencode({'address': wallet_address})}"
    req = urllib.request.Request(
        url,
        method="GET",
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
    )
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="replace") or "{}")
            return payload if isinstance(payload, dict) else None
    except urllib.error.HTTPError as exc:
        if exc.code not in {403, 404}:
            logger.warning("HTTP %s for wallet %s", exc.code, wallet_address)
        return None
    except (urllib.error.URLError, TimeoutError) as exc:
        logger.warning("Network error for wallet %s: %s", wallet_address, exc)
        return None
    except json.JSONDecodeError:
        logger.warning("Bad JSON for wallet %s", wallet_address)
        return None


def _profile_identity(profile: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    x_username = str(profile.get("xUsername") or "").strip() or None
    profile_name = str(profile.get("name") or "").strip() or None
    return x_username, profile_name


def _update_wallet(
    conn: "psycopg2.extensions.connection",
    proxy_wallet: str,
    x_username: Optional[str],
    profile_name: Optional[str],
) -> int:
    """Write the identity onto every still-NULL claim row for this wallet.

    Matches case-insensitively on proxy_wallet and only overwrites the pair
    while both columns are NULL, so this never clobbers values written by the
    insert-time path. Returns the number of rows updated.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE claims
            SET x_username   = %s,
                profile_name = %s,
                updated_at   = NOW()
            WHERE LOWER(proxy_wallet) = LOWER(%s)
              AND x_username IS NULL
              AND profile_name IS NULL
            """,
            (x_username, profile_name, proxy_wallet),
        )
        return cur.rowcount


def main() -> int:
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--local", action="store_true",
                    help="Use LOCAL_DB_* env vars instead of the production DB_* set.")
    ap.add_argument("--limit", type=int, default=None,
                    help="Process at most N distinct wallets (debug).")
    ap.add_argument("--delay", type=float, default=0.25,
                    help="Seconds to sleep between profile requests (rate-limit guard).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Fetch profiles and log what would change, but write nothing.")
    args = ap.parse_args()

    conn = psycopg2.connect(**_db_params(use_local_db=args.local))
    conn.autocommit = False
    try:
        wallets = _fetch_unenriched_wallets(conn, args.limit)
        logger.info("Found %d distinct wallet(s) with un-enriched claims", len(wallets))
        if not wallets:
            return 0

        updated_rows = 0
        wallets_with_identity = 0
        wallets_no_profile = 0
        wallets_empty_identity = 0

        for index, wallet in enumerate(wallets, start=1):
            profile = _fetch_public_profile(wallet)
            if profile is None:
                wallets_no_profile += 1
            else:
                x_username, profile_name = _profile_identity(profile)
                if x_username is None and profile_name is None:
                    wallets_empty_identity += 1
                else:
                    wallets_with_identity += 1
                    if args.dry_run:
                        logger.info(
                            "[dry-run] %s -> x_username=%r profile_name=%r",
                            wallet, x_username, profile_name,
                        )
                    else:
                        n = _update_wallet(conn, wallet, x_username, profile_name)
                        conn.commit()
                        updated_rows += n

            if index % 25 == 0 or index == len(wallets):
                logger.info(
                    "Progress %d/%d — rows_updated=%d, with_identity=%d, "
                    "empty_identity=%d, no_profile=%d",
                    index, len(wallets), updated_rows, wallets_with_identity,
                    wallets_empty_identity, wallets_no_profile,
                )

            if args.delay > 0 and index < len(wallets):
                time.sleep(args.delay)

        logger.info(
            "Done. %s%d claim row(s) updated across %d wallet(s) "
            "(empty_identity=%d, no_profile=%d).",
            "[dry-run] would update " if args.dry_run else "",
            updated_rows, wallets_with_identity,
            wallets_empty_identity, wallets_no_profile,
        )
        return 0
    except psycopg2.Error as exc:
        conn.rollback()
        logger.error("DB error: %s", exc)
        return 2
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
