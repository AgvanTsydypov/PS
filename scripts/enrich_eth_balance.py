"""
Enrich csv_with_eoa.csv with Ethereum mainnet ETH balance for each EOA.

Reads the output of enrich_eoa_from_polygonscan.py, collects unique EOA
addresses, and queries Etherscan V2 (chainid=1) ``account/balancemulti``
to fetch ETH balances 20 addresses at a time. Writes a new CSV with an
extra ``eth_balance`` column (ETH, decimal string).

Rows whose ``eoa_wallet`` is empty or starts with ``ERROR:`` get an empty
``eth_balance``.

Usage
-----
    set ETHERSCAN_API_KEY=...
    venv\\Scripts\\python.exe scripts\\enrich_eth_balance.py \\
        --input csv_with_eoa.csv --output csv_with_balance.csv

    # smoke test on first 50 unique EOAs
    ... --limit 50 --verbose
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from decimal import Decimal
from pathlib import Path
from typing import Optional

import requests


def _load_dotenv_into_environ(start: Path) -> None:
    """Tiny .env loader (no python-dotenv dependency)."""
    here = start.resolve()
    for parent in [here, *here.parents]:
        candidate = parent / ".env"
        if not candidate.is_file():
            continue
        try:
            for raw in candidate.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
        except OSError:
            pass
        return


_load_dotenv_into_environ(Path(__file__).parent)


ETHERSCAN_API = "https://api.etherscan.io/v2/api"
ETH_CHAINID = 1
BALANCEMULTI_BATCH = 20  # Etherscan limit for account/balancemulti
HTTP_TIMEOUT = 20
MAX_RETRIES = 3
DEFAULT_SLEEP_SEC = 0.22  # ~4.5 req/s, safely under the 5 req/s free tier
WEI_PER_ETH = Decimal(10) ** 18


def normalize_addr(addr: str) -> str:
    return addr.lower().strip()


def wei_to_eth_str(wei_str: str) -> str:
    """Convert a wei integer string to an ETH decimal string. Strips trailing
    zeros so '1500000000000000000' -> '1.5', '0' -> '0', tiny dust keeps
    precision: '1' -> '0.000000000000000001'."""
    try:
        wei = Decimal(wei_str)
    except Exception:
        return ""
    if wei == 0:
        return "0"
    eth = wei / WEI_PER_ETH
    # 18-decimal fixed -> trim trailing zeros and dangling dot
    text = format(eth, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def etherscan_get(
    session: requests.Session, params: dict, api_key: str, verbose: bool = False
) -> dict:
    """GET against Etherscan V2 with rate-limit-aware retries."""
    params = {"chainid": ETH_CHAINID, **params, "apikey": api_key}
    last_err: Optional[str] = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.get(ETHERSCAN_API, params=params, timeout=HTTP_TIMEOUT)
            if resp.status_code >= 500:
                last_err = f"HTTP {resp.status_code}"
            else:
                resp.raise_for_status()
                data = resp.json()
                # Etherscan logical errors are 200 + status=0 + string result.
                result_val = data.get("result")
                if isinstance(result_val, str):
                    low = result_val.lower()
                    if "rate limit" in low or "max calls" in low:
                        last_err = f"rate_limited: {result_val}"
                        if attempt < MAX_RETRIES:
                            if verbose:
                                print(
                                    f"      ⚠ rate-limited, backing off ({attempt})",
                                    file=sys.stderr,
                                )
                            time.sleep(1.0 * attempt)
                        continue
                return data
        except (requests.Timeout, requests.ConnectionError) as e:
            last_err = f"{type(e).__name__}: {e}"
        if attempt < MAX_RETRIES:
            time.sleep(0.5 * attempt)
    raise RuntimeError(f"etherscan_get failed after {MAX_RETRIES} retries: {last_err}")


def fetch_balances_batch(
    session: requests.Session,
    addresses: list[str],
    api_key: str,
    verbose: bool = False,
) -> dict[str, str]:
    """Return {address_lower: wei_str} for a batch of up to 20 addresses."""
    if not addresses:
        return {}
    data = etherscan_get(
        session,
        {
            "module": "account",
            "action": "balancemulti",
            "address": ",".join(addresses),
            "tag": "latest",
        },
        api_key,
        verbose=verbose,
    )
    result = data.get("result")
    if not isinstance(result, list):
        raise RuntimeError(f"unexpected balancemulti payload: {data!r}")
    out: dict[str, str] = {}
    for entry in result:
        if not isinstance(entry, dict):
            continue
        addr = normalize_addr(str(entry.get("account") or ""))
        wei = str(entry.get("balance") or "")
        if addr:
            out[addr] = wei
    return out


def load_existing_balances(output_path: str) -> dict[str, str]:
    """Return {eoa_lower: eth_balance_str} from a prior output file. Empty or
    missing balances are not cached (so resume retries them)."""
    if not os.path.exists(output_path):
        return {}
    out: dict[str, str] = {}
    with open(output_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            eoa = normalize_addr(row.get("eoa_wallet") or "")
            bal = (row.get("eth_balance") or "").strip()
            if eoa and bal and not eoa.startswith("error:"):
                out[eoa] = bal
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", default="csv_with_eoa.csv")
    ap.add_argument("--output", default="csv_with_balance.csv")
    ap.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Process at most N unique EOAs (0 = all). For smoke testing.",
    )
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument(
        "--sleep",
        type=float,
        default=DEFAULT_SLEEP_SEC,
        help=f"Sleep between Etherscan batches (default {DEFAULT_SLEEP_SEC}s).",
    )
    args = ap.parse_args()

    api_key = (
        os.environ.get("ETHERSCAN_API_KEY")
        or os.environ.get("POLYGONSCAN_API_KEY")  # same Etherscan account works for V2
        or ""
    ).strip()
    if not api_key:
        print(
            "ERROR: set ETHERSCAN_API_KEY (or POLYGONSCAN_API_KEY) in the environment.",
            file=sys.stderr,
        )
        return 2

    if not os.path.exists(args.input):
        print(f"ERROR: input file not found: {args.input}", file=sys.stderr)
        return 2

    with open(args.input, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = reader.fieldnames or []

    if "eoa_wallet" not in fieldnames:
        print(
            f"ERROR: input CSV must have an 'eoa_wallet' column, got {fieldnames}",
            file=sys.stderr,
        )
        return 2

    output_fields = list(fieldnames)
    if "eth_balance" not in output_fields:
        output_fields.append("eth_balance")

    # Collect unique, lookup-able EOAs.
    unique_eoas: list[str] = []
    seen: set[str] = set()
    for row in rows:
        raw = (row.get("eoa_wallet") or "").strip()
        if not raw or raw.startswith("ERROR:"):
            continue
        eoa = normalize_addr(raw)
        if eoa in seen:
            continue
        seen.add(eoa)
        unique_eoas.append(eoa)

    cached = load_existing_balances(args.output)
    to_fetch = [a for a in unique_eoas if a not in cached]
    if args.limit and args.limit < len(to_fetch):
        to_fetch = to_fetch[: args.limit]

    print(
        f"📋 input rows: {len(rows)} | unique EOAs: {len(unique_eoas)} | "
        f"cached: {len(cached)} | to fetch: {len(to_fetch)}",
        file=sys.stderr,
    )

    session = requests.Session()
    balances: dict[str, str] = dict(cached)
    fetch_errors = 0
    batches_total = (len(to_fetch) + BALANCEMULTI_BATCH - 1) // BALANCEMULTI_BATCH
    for batch_idx in range(batches_total):
        start = batch_idx * BALANCEMULTI_BATCH
        chunk = to_fetch[start : start + BALANCEMULTI_BATCH]
        print(
            f"[batch {batch_idx + 1}/{batches_total}] fetching {len(chunk)} addresses ...",
            file=sys.stderr,
        )
        try:
            result = fetch_balances_batch(session, chunk, api_key, verbose=args.verbose)
        except Exception as e:
            print(f"   ❌ batch failed: {e}", file=sys.stderr)
            fetch_errors += len(chunk)
            time.sleep(args.sleep)
            continue
        # Etherscan returns one entry per requested address. Anything missing
        # is treated as an empty result for this run (will retry next time).
        for addr in chunk:
            wei = result.get(addr)
            if wei is None:
                fetch_errors += 1
                continue
            eth = wei_to_eth_str(wei)
            balances[addr] = eth
            if args.verbose:
                print(f"   {addr}  {eth} ETH  ({wei} wei)", file=sys.stderr)
        time.sleep(args.sleep)

    # Write output: every row of the input, with eth_balance attached.
    with open(args.output, "w", encoding="utf-8", newline="") as out_f:
        writer = csv.DictWriter(out_f, fieldnames=output_fields)
        writer.writeheader()
        for row in rows:
            raw = (row.get("eoa_wallet") or "").strip()
            if not raw or raw.startswith("ERROR:"):
                writer.writerow({**row, "eth_balance": ""})
                continue
            eoa = normalize_addr(raw)
            writer.writerow({**row, "eth_balance": balances.get(eoa, "")})

    # Summary
    resolved = sum(1 for r in rows if balances.get(normalize_addr(r.get("eoa_wallet") or "")))
    print(
        f"\n✅ done: {len(balances)} unique EOAs have balances, "
        f"{resolved}/{len(rows)} rows enriched, {fetch_errors} fetch error(s)",
        file=sys.stderr,
    )
    if fetch_errors:
        print(
            "   Re-run the same command to retry only the missing balances.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
