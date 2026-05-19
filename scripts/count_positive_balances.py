"""Count how many unique EOA wallets in csv_with_balance.csv have ETH > 0.

Usage:
    venv\\Scripts\\python.exe scripts\\count_positive_balances.py
    venv\\Scripts\\python.exe scripts\\count_positive_balances.py --threshold 0.01
"""

from __future__ import annotations

import argparse
import csv
from decimal import Decimal


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", default="csv_with_balance.csv")
    ap.add_argument(
        "--threshold",
        default="0",
        help="Lower bound (exclusive). Default 0 — count any positive balance.",
    )
    ap.add_argument(
        "--top",
        type=int,
        default=10,
        help="Show top N wallets by balance (0 = skip).",
    )
    args = ap.parse_args()

    threshold = Decimal(args.threshold)

    with open(args.input, "r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))

    # Collect unique EOAs with parsed balances.
    uniq: dict[str, Decimal] = {}
    for r in rows:
        eoa = (r.get("eoa_wallet") or "").strip().lower()
        bal_str = (r.get("eth_balance") or "").strip()
        if not eoa or not bal_str or eoa.startswith("error:"):
            continue
        try:
            uniq[eoa] = Decimal(bal_str)
        except Exception:
            continue

    pos = {a: b for a, b in uniq.items() if b > threshold}
    zero = len(uniq) - len(pos)

    rows_pos = 0
    for r in rows:
        bal_str = (r.get("eth_balance") or "").strip()
        if not bal_str:
            continue
        try:
            if Decimal(bal_str) > threshold:
                rows_pos += 1
        except Exception:
            pass

    total_eth = sum(uniq.values(), Decimal(0))
    pos_eth = sum(pos.values(), Decimal(0))

    print(f"input rows:                          {len(rows)}")
    print(f"unique EOAs with balance fetched:    {len(uniq)}")
    if uniq:
        print(
            f"unique EOAs with balance > {threshold}:   {len(pos)}  "
            f"({len(pos) * 100 / len(uniq):.1f}%)"
        )
    print(f"unique EOAs with balance <= {threshold}:  {zero}")
    print(f"CSV rows with balance > {threshold}:      {rows_pos}/{len(rows)}")
    print(f"total ETH across unique EOAs:        {total_eth} ETH")
    print(f"total ETH across positive EOAs:      {pos_eth} ETH")

    if args.top and pos:
        print(f"\ntop {args.top} by balance:")
        for addr, bal in sorted(pos.items(), key=lambda x: -x[1])[: args.top]:
            print(f"  {addr}  {bal} ETH")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
