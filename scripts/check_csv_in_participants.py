"""Check (and optionally enqueue Origin mints for) rows from csv_with_eoa.csv.

Two modes:

* default — read-only check: for each CSV row, verify there is a matching
  ``participants`` row (LOWER(proxy_wallet), event_slug) and print a summary.

* ``--enqueue`` — for every CSV row that has a matching participants row in
  the given ``--season-id``, INSERT a QUEUED Origin claim **carrying the exact
  snapshot of that participant row** (the row at the (proxy_wallet, event_slug)
  pair from the CSV, NOT a best-archetype self-row picked by the backend).
  Done with direct DB writes — bypasses the admin HTTP endpoint.

Per-claim semantics mirror ``admin_backend.claims_mint._insert_queued_claim``:
- ``user_wallet`` = ``proxy_wallet`` = CSV ``proxy_wallet`` (lowercased; the
  Origin "is" the wallet they're minting for).
- ``recipient_address`` = checksummed CSV ``eoa_wallet``.
- ``claim_type`` = ``'origin'``.
- ``status`` = ``'QUEUED'``.
- ``signature`` = computed via ``compute_structural_signature``.
- ``primary_tag`` and ``recurrence`` come from ``event_cards``.
- ``season_type`` / ``season_number`` come from ``seasons`` (for the signature).
- ``mint_chain`` = ``'ethereum'``.

Usage (check only):
    venv\\Scripts\\python.exe scripts\\check_csv_in_participants.py
    venv\\Scripts\\python.exe scripts\\check_csv_in_participants.py --season-id 5

Usage (enqueue origin mints):
    venv\\Scripts\\python.exe scripts\\check_csv_in_participants.py \\
        --enqueue --season-id 5 --phase breach
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from typing import Any, Dict, List, Optional, Set, Tuple

import psycopg2
import psycopg2.errors
import psycopg2.extras
from dotenv import load_dotenv

# Allow importing scripts.cardgen.* whether invoked from project root
# (host: venv\\Scripts\\python.exe scripts\\check_csv_in_participants.py) or
# from inside the scheduler container (cwd=/app).
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_THIS_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from scripts.cardgen.generate_card import compute_structural_signature  # noqa: E402

load_dotenv()

MINT_CHAIN = "ethereum"
CLAIM_TYPE_ORIGIN = "origin"
ALLOWED_PHASES = ("breach", "vault", "scavenge")


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
            if key in pairs:
                hits.setdefault(key, []).append(int(sid))
    return hits


def _active_claim_wallets(cursor, season_id: int) -> Set[str]:
    """Return {LOWER(user_wallet), LOWER(proxy_wallet)} that already hold an
    active claim in this season. Lets us skip candidates without paying for an
    INSERT + UniqueViolation rollback on a repeat run."""
    cursor.execute(
        """
        SELECT LOWER(user_wallet)  AS w FROM claims
        WHERE season_id = %s
          AND status IN ('QUEUED', 'PENDING', 'PROCESSING', 'COMPLETED')
        UNION
        SELECT LOWER(proxy_wallet) AS w FROM claims
        WHERE season_id = %s
          AND proxy_wallet IS NOT NULL
          AND status IN ('QUEUED', 'PENDING', 'PROCESSING', 'COMPLETED')
        """,
        (season_id, season_id),
    )
    out: Set[str] = set()
    for row in cursor.fetchall():
        w = row.get("w") if isinstance(row, dict) else row[0]
        if w:
            out.add(str(w))
    return out


def _fetch_participant_row(
    cursor,
    proxy_wallet: str,
    event_slug: str,
    season_id: int,
) -> Optional[Dict[str, Any]]:
    """Return the exact participants row for this (wallet, slug, season)."""
    cursor.execute(
        """
        SELECT proxy_wallet, event_id, event_slug,
               entry_cwap, total_volume, total_pnl, roi_percentage,
               entry_bracket, edge, yield, gravity,
               archetype, archetype_description, archetype_math,
               rarity_bracket, rank
        FROM participants
        WHERE season_id = %s
          AND LOWER(proxy_wallet) = %s
          AND event_slug = %s
        LIMIT 1
        """,
        (season_id, proxy_wallet.lower(), event_slug),
    )
    return cursor.fetchone()


def _fetch_event_card_meta(
    cursor, event_id: Optional[str], event_slug: Optional[str]
) -> Dict[str, Optional[str]]:
    for column, value in (("event_id", event_id), ("event_slug", event_slug)):
        if not value:
            continue
        cursor.execute(
            f"SELECT primary_tag, reccurence FROM event_cards WHERE {column} = %s LIMIT 1",
            (value,),
        )
        row = cursor.fetchone()
        if not row:
            continue
        tag = (row.get("primary_tag") or "").strip() or None
        rec = (row.get("reccurence") or "").strip() or None
        if tag or rec:
            return {"primary_tag": tag, "recurrence": rec}
    return {"primary_tag": None, "recurrence": None}


def _fetch_season_meta(cursor, season_id: int) -> Dict[str, Any]:
    cursor.execute(
        "SELECT type AS season_type, season_number FROM seasons WHERE id = %s LIMIT 1",
        (season_id,),
    )
    row = cursor.fetchone() or {}
    return {
        "season_type": (str(row.get("season_type") or "").strip() or None),
        "season_number": row.get("season_number"),
    }


def _checksum_address(addr: str) -> str:
    """EIP-55 checksum via web3 (already a project dep)."""
    from web3 import Web3
    return Web3.to_checksum_address(addr)


def _insert_origin_claim(
    cursor,
    *,
    proxy_wallet_lower: str,
    recipient_address: str,
    season_id: int,
    phase: str,
    participant_row: Dict[str, Any],
    event_card_meta: Dict[str, Optional[str]],
    season_meta: Dict[str, Any],
) -> Dict[str, Any]:
    """Mirror admin_backend.claims_mint._insert_queued_claim, but Origin-only and
    sourced from a participants row chosen by the caller (not auto-allocated)."""
    snap = participant_row  # alias for readability

    signature = compute_structural_signature({
        "archetype":     snap.get("archetype"),
        "entry_bracket": snap.get("entry_bracket"),
        "edge":          snap.get("edge"),
        "yield":         snap.get("yield"),
        "gravity":       snap.get("gravity"),
        "claim_type":    CLAIM_TYPE_ORIGIN,
        "event_id":      snap.get("event_id"),
        "recurrence":    event_card_meta.get("recurrence"),
        "season_type":   season_meta.get("season_type"),
        "season_number": season_meta.get("season_number"),
    })

    cursor.execute(
        """
        INSERT INTO claims (
            user_wallet, recipient_address, season_id, phase_type, status,
            proxy_wallet, event_id, event_slug, primary_tag,
            snapshot_at,
            entry_cwap, total_volume, total_pnl, roi_percentage,
            entry_bracket, edge, yield, gravity,
            archetype, archetype_description, archetype_math, rarity_bracket,
            participant_rank, claim_type,
            signature,
            mint_chain,
            created_at, updated_at
        )
        VALUES (
            %s, %s, %s, %s, 'QUEUED',
            %s, %s, %s, %s,
            NOW(),
            %s, %s, %s, %s,
            %s, %s, %s, %s,
            %s, %s, %s, %s,
            %s, %s,
            %s,
            %s,
            NOW(), NOW()
        )
        RETURNING id, collection_mint_number
        """,
        (
            proxy_wallet_lower, recipient_address, season_id, phase,
            snap.get("proxy_wallet"),
            snap.get("event_id"),
            snap.get("event_slug"),
            event_card_meta.get("primary_tag"),
            snap.get("entry_cwap"), snap.get("total_volume"),
            snap.get("total_pnl"),  snap.get("roi_percentage"),
            snap.get("entry_bracket"), snap.get("edge"),
            snap.get("yield"),         snap.get("gravity"),
            snap.get("archetype"),     snap.get("archetype_description"),
            snap.get("archetype_math"), snap.get("rarity_bracket"),
            snap.get("rank"), CLAIM_TYPE_ORIGIN,
            signature,
            MINT_CHAIN,
        ),
    )
    row = cursor.fetchone()
    if not row:
        raise RuntimeError("INSERT INTO claims returned no row")
    return {
        "claim_id": int(row["id"]),
        "collection_mint_number": (
            int(row["collection_mint_number"])
            if row.get("collection_mint_number") is not None else None
        ),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", default="csv_with_eoa.csv")
    ap.add_argument("--local", action="store_true",
                    help="Use LOCAL_DB_* env overrides instead of DB_*.")
    ap.add_argument("--season-id", type=int, default=None,
                    help="Required with --enqueue. Restricts participants lookup.")
    ap.add_argument("--output", default=None,
                    help="Optional per-row CSV report (adds participants_exists "
                         "and queue_* columns).")
    ap.add_argument("--enqueue", action="store_true",
                    help="INSERT a QUEUED Origin claim per CSV row matched in participants.")
    ap.add_argument("--phase", choices=ALLOWED_PHASES, default="breach",
                    help="Claim phase_type for inserted rows (default: breach).")
    ap.add_argument("--dry-run", action="store_true",
                    help="With --enqueue: print what would be inserted, write nothing.")
    ap.add_argument("--limit", type=int, default=None,
                    help="With --enqueue: stop after N successful inserts.")
    ap.add_argument("--sleep-ms", type=int, default=0,
                    help="With --enqueue: pause between inserts (ms).")
    args = ap.parse_args()

    if args.enqueue and args.season_id is None:
        ap.error("--enqueue requires --season-id")

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
    queue_results: Dict[Tuple[str, str], Dict[str, Any]] = {}
    try:
        hits = _lookup_participants(conn, pairs, args.season_id)

        # ── check summary ───────────────────────────────────────────────────
        found_rows = missing_rows = invalid_rows = 0
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
        print("=== check summary ===")
        print(f"season filter:           {args.season_id if args.season_id is not None else 'any'}")
        print(f"CSV rows total:          {len(rows)}")
        print(f"  empty wallet/slug:     {invalid_rows}")
        print(f"  found in participants: {found_rows}")
        print(f"  missing:               {missing_rows}")
        print(f"unique pairs found:      {len(hits)} / {len(pairs)}")

        # ── enqueue ────────────────────────────────────────────────────────
        if args.enqueue:
            season_meta_cache: Optional[Dict[str, Any]] = None
            event_meta_cache: Dict[str, Dict[str, Optional[str]]] = {}

            # Dedupe at (proxy_wallet, event_slug) — the (wallet, slug) pair
            # *is* the participants PK component we want to mint from. If a
            # wallet appears in CSV with multiple slugs, each becomes its own
            # candidate; the per-season unique active claim constraint will
            # then reject all but one (caller decides which by CSV ordering).
            candidates: List[Tuple[str, str, str]] = []
            seen: Set[Tuple[str, str]] = set()
            for r in rows:
                w = (r.get("proxy_wallet") or "").strip().lower()
                s = (r.get("event_slug") or "").strip()
                e = (r.get("eoa_wallet") or "").strip()
                if not (w and s and e):
                    continue
                if (w, s) not in hits:
                    continue  # not in this season's participants → skip
                if (w, s) in seen:
                    continue
                seen.add((w, s))
                candidates.append((w, s, e))

            # Pre-fetch already-active wallets so a repeat run can skip them
            # without paying for an INSERT + UniqueViolation per row.
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                pre_active = _active_claim_wallets(cur, args.season_id)  # type: ignore[arg-type]

            pre_skipped = sum(1 for w, _, _ in candidates if w in pre_active)

            print()
            print("=== enqueue mode ===")
            print(f"season_id:              {args.season_id}")
            print(f"phase:                  {args.phase}")
            print(f"candidates to insert:   {len(candidates)}")
            print(f"pre-skipped (already active in season): {pre_skipped}")
            if args.limit:
                print(f"limit:                  {args.limit}")
            if args.dry_run:
                print("dry-run: no DB writes will be made")
            print()

            ok = 0
            already = 0
            failed = 0

            for i, (wallet, slug, eoa) in enumerate(candidates, 1):
                if args.limit and ok >= args.limit:
                    print(f"reached --limit {args.limit}, stopping")
                    break

                if wallet in pre_active:
                    # Already has an active claim — silent skip (no SQL).
                    already += 1
                    queue_results[(wallet, slug)] = {
                        "status": "duplicate",
                        "detail": "already active in season (pre-filtered)",
                    }
                    continue

                try:
                    recipient = _checksum_address(eoa)
                except Exception as exc:
                    print(f"[{i:>4}] ERR {wallet} {slug}: bad EOA '{eoa}': {exc}")
                    failed += 1
                    queue_results[(wallet, slug)] = {
                        "status": "error", "detail": f"bad EOA: {exc}",
                    }
                    continue

                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    try:
                        prow = _fetch_participant_row(
                            cur, wallet, slug, args.season_id  # type: ignore[arg-type]
                        )
                        if not prow:
                            # Should not happen — we already filtered by hits.
                            print(f"[{i:>4}] ERR {wallet} {slug}: participant row vanished")
                            failed += 1
                            queue_results[(wallet, slug)] = {
                                "status": "error",
                                "detail": "participant row not found",
                            }
                            conn.rollback()
                            continue

                        event_id = prow.get("event_id") or ""
                        evk = f"{event_id}|{slug}"
                        if evk not in event_meta_cache:
                            event_meta_cache[evk] = _fetch_event_card_meta(
                                cur, prow.get("event_id"), prow.get("event_slug")
                            )
                        event_meta = event_meta_cache[evk]

                        if season_meta_cache is None:
                            season_meta_cache = _fetch_season_meta(
                                cur, args.season_id  # type: ignore[arg-type]
                            )
                        season_meta = season_meta_cache

                        if args.dry_run:
                            print(
                                f"[{i:>4}] DRY {wallet} slug={slug} eoa={recipient} "
                                f"archetype={prow.get('archetype')} "
                                f"rank={prow.get('rank')}"
                            )
                            queue_results[(wallet, slug)] = {
                                "status": "dry_run",
                                "claim_id": None,
                            }
                            conn.rollback()
                            continue

                        inserted = _insert_origin_claim(
                            cur,
                            proxy_wallet_lower=wallet,
                            recipient_address=recipient,
                            season_id=args.season_id,  # type: ignore[arg-type]
                            phase=args.phase,
                            participant_row=prow,
                            event_card_meta=event_meta,
                            season_meta=season_meta,
                        )
                        conn.commit()
                        print(
                            f"[{i:>4}] OK  {wallet} slug={slug} -> {recipient}  "
                            f"claim_id={inserted['claim_id']}  "
                            f"mint#={inserted['collection_mint_number']}"
                        )
                        ok += 1
                        # Lock this wallet out for the rest of THIS run so we
                        # don't try inserting a second CSV slug for the same
                        # wallet only to eat a UniqueViolation.
                        pre_active.add(wallet)
                        queue_results[(wallet, slug)] = {
                            "status": "queued",
                            "claim_id": inserted["claim_id"],
                            "collection_mint_number": inserted["collection_mint_number"],
                        }

                    except psycopg2.errors.UniqueViolation as exc:
                        conn.rollback()
                        constraint = (
                            getattr(getattr(exc, "diag", None), "constraint_name", None)
                            or ""
                        )
                        msg = f"unique violation ({constraint or 'unknown constraint'})"
                        print(f"[{i:>4}] DUP {wallet} slug={slug}: {msg}")
                        already += 1
                        queue_results[(wallet, slug)] = {
                            "status": "duplicate", "detail": msg,
                        }
                    except psycopg2.errors.CheckViolation as exc:
                        conn.rollback()
                        # Cap trigger refused.
                        print(f"[{i:>4}] CAP {wallet} slug={slug}: {exc}".rstrip())
                        failed += 1
                        queue_results[(wallet, slug)] = {
                            "status": "cap_violation",
                            "detail": str(exc).strip(),
                        }
                    except Exception as exc:
                        conn.rollback()
                        print(f"[{i:>4}] ERR {wallet} slug={slug}: "
                              f"{type(exc).__name__}: {exc}")
                        failed += 1
                        queue_results[(wallet, slug)] = {
                            "status": "error",
                            "detail": f"{type(exc).__name__}: {exc}",
                        }

                if args.sleep_ms:
                    time.sleep(args.sleep_ms / 1000.0)

            print()
            print("=== enqueue summary ===")
            print(f"queued:        {ok}")
            print(f"duplicates:    {already}")
            print(f"failed/capped: {failed}")
    finally:
        conn.close()

    if args.output:
        fieldnames = list(rows[0].keys()) if rows else []
        for extra in (
            "participants_exists",
            "participants_season_ids",
            "queue_status",
            "queue_claim_id",
            "queue_detail",
        ):
            if extra not in fieldnames:
                fieldnames.append(extra)
        with open(args.output, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            for r in rows:
                wallet = (r.get("proxy_wallet") or "").strip().lower()
                slug = (r.get("event_slug") or "").strip()
                seasons = hits.get((wallet, slug), [])
                qr = queue_results.get((wallet, slug), {})
                out = dict(r)
                out["participants_exists"] = "1" if seasons else "0"
                out["participants_season_ids"] = ",".join(str(s) for s in sorted(seasons))
                out["queue_status"] = qr.get("status", "")
                out["queue_claim_id"] = (
                    str(qr.get("claim_id")) if qr.get("claim_id") is not None else ""
                )
                out["queue_detail"] = qr.get("detail", "")
                w.writerow(out)
        print(f"wrote per-row report: {args.output}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
