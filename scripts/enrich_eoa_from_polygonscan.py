"""
Enrich a CSV of Polymarket proxy_wallet rows with the underlying EOA wallet.

Approach
--------
Each proxy_wallet is a Gnosis Safe Proxy CONTRACT, not an EOA. Polygonscan's
"Contract Creator" tx is what exposes the creator EOA through its event logs.

For each proxy_wallet:
  1. Look up the contract-creation tx via Etherscan V2 unified API
     (chainid=137, module=contract, action=getcontractcreation).
  2. Fetch that tx's receipt logs
     (module=proxy, action=eth_getTransactionReceipt).
  3. Walk logs in REVERSE order (relevant events are near the end of the tx)
     and pick the EOA candidate via heuristic:
       - Pattern A (Polymarket Relay Hub):
         topics[2] decoded as address, != proxy_wallet, != log emitter, != 0x0.
       - Pattern B (Polymarket Safe Proxy Factory):
         any 32-byte data word that looks address-shaped, same exclusions.
     The first match wins.

Verification
------------
After resolving the EOA, the script calls Polymarket's public-profile API:
    GET https://gamma-api.polymarket.com/public-profile?address={eoa}
and compares ``response.proxyWallet`` against the original proxy_wallet from
the CSV. The result is written into ``eoa_verdict``:
    OK    — proxyWallet matches the CSV row
    WRONG — proxyWallet differs (heuristic picked the wrong log entry)
    (empty when no EOA was resolved at all)

Output
------
Writes <input>.csv -> <output>.csv with two extra columns:
    eoa_wallet  — hex address, or "ERROR: <reason>" on failure
    eoa_verdict — OK | WRONG | "" (empty when eoa_wallet is ERROR)

Resume-safe
-----------
On re-run, rows whose verdict is already ``OK`` are kept as-is. Rows with
``WRONG`` or ``ERROR`` are re-tried — useful when you tweak the heuristic.

Usage
-----
    set POLYGONSCAN_API_KEY=...
    venv\\Scripts\\python.exe scripts\\enrich_eoa_from_polygonscan.py \\
        --input csv.csv --output csv_with_eoa.csv

    # quick smoke test on 5 rows
    ... --limit 5 --verbose
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

import requests


def _load_dotenv_into_environ(start: Path) -> None:
    """Tiny .env loader. Walks up from ``start`` looking for a .env file and
    populates ``os.environ`` for keys that aren't already set. Avoids the
    python-dotenv dependency."""
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

POLYGONSCAN_API = "https://api.etherscan.io/v2/api"
POLYGON_CHAINID = 137
POLYMARKET_PROFILE_API = "https://gamma-api.polymarket.com/public-profile"
ZERO_ADDR = "0x" + "0" * 40

# Logs emitted by these contracts are noise — they appear in every Polygon tx
# and are not Polymarket-related. Excluding them prevents the heuristic from
# latching onto the gas-fee burn log at the very end of each tx, whose
# topics[2] is the validator/relayer wallet (not the user EOA).
NOISE_EMITTERS = {
    "0x0000000000000000000000000000000000001010",  # Polygon MATIC native fee burn
}
DEFAULT_SLEEP_SEC = 0.22  # ~4.5 req/s, safely under the 5 req/s free tier
HTTP_TIMEOUT = 20
MAX_RETRIES = 3


@dataclass
class LookupResult:
    eoa: Optional[str]
    error: Optional[str]


@dataclass
class VerifyResult:
    # One of: "OK", "WRONG", "NOT_FOUND", "ERROR"
    status: str
    # The proxyWallet returned by Polymarket (lowercase) for diagnostics.
    returned_proxy: Optional[str]
    detail: Optional[str]


def is_address_word(word_hex: str) -> bool:
    """A 32-byte topic/data word is "address-shaped" when the upper 12 bytes
    are zero and the lower 20 bytes are non-zero hex."""
    if not word_hex.startswith("0x") or len(word_hex) != 66:
        return False
    if word_hex[2:26] != "0" * 24:
        return False
    return word_hex[26:] != "0" * 40


def word_to_address(word_hex: str) -> str:
    return ("0x" + word_hex[26:]).lower()


def normalize_addr(addr: str) -> str:
    return addr.lower().strip()


def polygonscan_get(session: requests.Session, params: dict, api_key: str) -> dict:
    """GET against the Etherscan V2 unified API (chainid=137 = Polygon).

    Retries on transient errors, including Etherscan's rate-limit responses
    (status=0 with result like "Max calls per sec rate limit reached (3/sec)").
    """
    params = {"chainid": POLYGON_CHAINID, **params, "apikey": api_key}
    last_err: Optional[str] = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.get(POLYGONSCAN_API, params=params, timeout=HTTP_TIMEOUT)
            if resp.status_code >= 500:
                last_err = f"HTTP {resp.status_code}"
            else:
                resp.raise_for_status()
                data = resp.json()
                # Etherscan returns its own logical errors (rate limit, invalid
                # key) as HTTP 200 with status=0 and a string in `result`. Treat
                # rate-limit hits as transient; everything else surfaces normally.
                result_val = data.get("result")
                if isinstance(result_val, str):
                    low = result_val.lower()
                    if "rate limit" in low or "max calls" in low:
                        last_err = f"rate_limited: {result_val}"
                        # exponential-ish backoff that respects the 5 req/s tier
                        if attempt < MAX_RETRIES:
                            time.sleep(1.0 * attempt)
                        continue
                return data
        except (requests.Timeout, requests.ConnectionError) as e:
            last_err = f"{type(e).__name__}: {e}"
        if attempt < MAX_RETRIES:
            time.sleep(0.5 * attempt)
    raise RuntimeError(f"polygonscan_get failed after {MAX_RETRIES} retries: {last_err}")


def fetch_contract_creation_info(
    session: requests.Session, address: str, api_key: str
) -> tuple[Optional[str], Optional[str]]:
    """Return (txHash, contractCreator) for the given contract address.

    Polymarket proxy_wallets are Gnosis Safe Proxy contracts created via a
    factory. The Polygonscan UI exposes this under "Contract Creator → tx".
    Etherscan V2's contract.getcontractcreation returns the same data:

        {"contractAddress":"...","contractCreator":"...","txHash":"..."}

    ``contractCreator`` is the relayer worker (Relay Hub case) or the proxy
    factory contract (Safe Factory case). In either case it is NOT the user
    EOA we want, so it is passed back as an exclusion hint to the log walker.
    """
    data = polygonscan_get(
        session,
        {
            "module": "contract",
            "action": "getcontractcreation",
            "contractaddresses": address,
        },
        api_key,
    )
    result = data.get("result")
    if isinstance(result, list) and result:
        first = result[0] or {}
        tx_hash = str(first.get("txHash") or "").strip() or None
        creator = str(first.get("contractCreator") or "").strip().lower() or None
        return tx_hash, creator
    return None, None


def fetch_receipt_logs(
    session: requests.Session, tx_hash: str, api_key: str
) -> list[dict]:
    data = polygonscan_get(
        session,
        {
            "module": "proxy",
            "action": "eth_getTransactionReceipt",
            "txhash": tx_hash,
        },
        api_key,
    )
    result = data.get("result")
    if not isinstance(result, dict):
        raise RuntimeError(f"unexpected receipt payload: {data!r}")
    logs = result.get("logs")
    if not isinstance(logs, list):
        return []
    return logs


def extract_eoa_from_logs(
    logs: list[dict],
    proxy_wallet: str,
    contract_creator: Optional[str] = None,
    verbose: bool = False,
) -> Optional[str]:
    """Walk logs in reverse, return the first plausible EOA address.

    Pattern A: topics[2] decoded as address.
    Pattern B: any address-shaped 32-byte word in `data`.

    Exclusions: proxy_wallet itself, the log emitter, the zero address,
    contract_creator (relayer worker / factory — never the user EOA), and
    well-known Polygon system contracts that emit noise on every tx.
    """
    proxy_norm = normalize_addr(proxy_wallet)
    creator_norm = normalize_addr(contract_creator) if contract_creator else None

    def is_noisy_emitter(emitter: str) -> bool:
        return normalize_addr(emitter) in NOISE_EMITTERS

    def is_candidate(addr_hex: str, emitter: str) -> bool:
        a = normalize_addr(addr_hex)
        if a == ZERO_ADDR or a == proxy_norm:
            return False
        if a == normalize_addr(emitter):
            return False
        if creator_norm and a == creator_norm:
            return False
        return True

    # Pass 1: prefer topics[2] matches (Relay Hub pattern).
    for log in reversed(logs):
        emitter = str(log.get("address") or "")
        if is_noisy_emitter(emitter):
            continue
        topics = log.get("topics") or []
        if len(topics) >= 3:
            t2 = str(topics[2])
            if is_address_word(t2):
                cand = word_to_address(t2)
                if is_candidate(cand, emitter):
                    if verbose:
                        print(
                            f"      ✓ topics[2] match in log emitter={emitter} -> {cand}",
                            file=sys.stderr,
                        )
                    return cand

    # Pass 2: address-shaped word in data (Safe Proxy Factory pattern).
    for log in reversed(logs):
        emitter = str(log.get("address") or "")
        if is_noisy_emitter(emitter):
            continue
        data_hex = str(log.get("data") or "")
        if not data_hex.startswith("0x") or len(data_hex) < 66:
            continue
        body = data_hex[2:]
        words = [
            "0x" + body[i : i + 64]
            for i in range(0, len(body) - len(body) % 64, 64)
        ]
        for word in words:
            if not is_address_word(word):
                continue
            cand = word_to_address(word)
            if is_candidate(cand, emitter):
                if verbose:
                    print(
                        f"      ✓ data-word match in log emitter={emitter} -> {cand}",
                        file=sys.stderr,
                    )
                return cand

    return None


def polymarket_lookup_profile(
    session: requests.Session,
    address: str,
    verbose: bool = False,
) -> tuple[Optional[str], Optional[str]]:
    """Hit Polymarket's public-profile endpoint for an arbitrary address.

    Returns (returned_proxy_wallet, error). On success, returned_proxy_wallet
    is the lowercase address of ``proxyWallet`` from the response. On HTTP 404
    or empty body, returns (None, "polymarket_profile_404"). On other failures,
    returns (None, "<error_detail>").
    """
    last_err: Optional[str] = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.get(
                POLYMARKET_PROFILE_API,
                params={"address": address},
                timeout=HTTP_TIMEOUT,
            )
            if resp.status_code == 404:
                return None, "polymarket_profile_404"
            if resp.status_code >= 500:
                last_err = f"HTTP {resp.status_code}"
            else:
                resp.raise_for_status()
                payload = resp.json()
                returned = (
                    payload.get("proxyWallet") if isinstance(payload, dict) else None
                )
                if not returned:
                    return None, "no_proxyWallet_in_response"
                if verbose:
                    print(
                        f"      polymarket lookup for {address} -> proxyWallet={returned}",
                        file=sys.stderr,
                    )
                return normalize_addr(str(returned)), None
        except (requests.Timeout, requests.ConnectionError) as e:
            last_err = f"{type(e).__name__}: {e}"
        except ValueError as e:
            return None, f"bad_json: {e}"
        if attempt < MAX_RETRIES:
            time.sleep(0.5 * attempt)
    return None, last_err or "unknown"


def verify_eoa_via_polymarket(
    session: requests.Session,
    eoa: str,
    expected_proxy: str,
    verbose: bool = False,
) -> VerifyResult:
    """Hit Polymarket's public-profile endpoint and compare proxyWallet."""
    last_err: Optional[str] = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.get(
                POLYMARKET_PROFILE_API,
                params={"address": eoa},
                timeout=HTTP_TIMEOUT,
            )
            if resp.status_code == 404:
                return VerifyResult("NOT_FOUND", None, "polymarket_profile_404")
            if resp.status_code >= 500:
                last_err = f"HTTP {resp.status_code}"
            else:
                resp.raise_for_status()
                payload = resp.json()
                returned = payload.get("proxyWallet") if isinstance(payload, dict) else None
                if not returned:
                    return VerifyResult("NOT_FOUND", None, "no_proxyWallet_in_response")
                returned_norm = normalize_addr(str(returned))
                expected_norm = normalize_addr(expected_proxy)
                if verbose:
                    print(
                        f"      polymarket says proxyWallet={returned_norm}",
                        file=sys.stderr,
                    )
                if returned_norm == expected_norm:
                    return VerifyResult("OK", returned_norm, None)
                return VerifyResult("WRONG", returned_norm, None)
        except (requests.Timeout, requests.ConnectionError) as e:
            last_err = f"{type(e).__name__}: {e}"
        except ValueError as e:  # JSON decode
            return VerifyResult("ERROR", None, f"bad_json: {e}")
        if attempt < MAX_RETRIES:
            time.sleep(0.5 * attempt)
    return VerifyResult("ERROR", None, last_err or "unknown")


def lookup_eoa(
    session: requests.Session,
    proxy_wallet: str,
    api_key: str,
    verbose: bool = False,
) -> LookupResult:
    try:
        tx_hash, contract_creator = fetch_contract_creation_info(
            session, proxy_wallet, api_key
        )
    except Exception as e:
        return LookupResult(None, f"creation_tx_lookup_failed: {e}")
    if not tx_hash:
        return LookupResult(None, "no_contract_creation_tx_found")

    if verbose:
        print(
            f"      creation tx: {tx_hash}  creator: {contract_creator}",
            file=sys.stderr,
        )

    time.sleep(DEFAULT_SLEEP_SEC)
    try:
        logs = fetch_receipt_logs(session, tx_hash, api_key)
    except Exception as e:
        return LookupResult(None, f"receipt_failed: {e}")
    if not logs:
        return LookupResult(None, "no_logs_in_first_tx")

    eoa = extract_eoa_from_logs(
        logs, proxy_wallet, contract_creator=contract_creator, verbose=verbose
    )
    if not eoa:
        return LookupResult(None, "no_eoa_candidate_in_logs")
    return LookupResult(eoa, None)


CACHEABLE_VERDICTS = {"OK", "OK_SELF", "SELF_EOA", "UNVERIFIED_NO_PROFILE"}


def load_existing_results(output_path: str) -> dict[str, tuple[str, str]]:
    """Return {proxy_wallet: (eoa_wallet, eoa_verdict)} from an existing
    output file. Successful verdicts (OK / OK_SELF / SELF_EOA /
    UNVERIFIED_NO_PROFILE) are cached so resume doesn't redo work; WRONG
    and ERROR rows are re-tried on the next run."""
    if not os.path.exists(output_path):
        return {}
    out: dict[str, tuple[str, str]] = {}
    with open(output_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            pw = normalize_addr(row.get("proxy_wallet") or "")
            eoa = (row.get("eoa_wallet") or "").strip()
            verdict = (row.get("eoa_verdict") or "").strip().upper()
            if (
                pw
                and eoa
                and verdict in CACHEABLE_VERDICTS
                and not eoa.startswith("ERROR:")
            ):
                out[pw] = (eoa, verdict)
    return out


def iter_input_rows(input_path: str) -> Iterable[tuple[list[str], list[dict]]]:
    with open(input_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = reader.fieldnames or []
    yield fieldnames, rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", default="csv.csv")
    ap.add_argument("--output", default="csv_with_eoa.csv")
    ap.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Process at most N rows (0 = all). Useful for smoke testing.",
    )
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument(
        "--sleep",
        type=float,
        default=DEFAULT_SLEEP_SEC,
        help=f"Sleep between Polygonscan calls (default {DEFAULT_SLEEP_SEC}s).",
    )
    args = ap.parse_args()

    api_key = os.environ.get("POLYGONSCAN_API_KEY", "").strip()
    if not api_key:
        print("ERROR: set POLYGONSCAN_API_KEY in the environment.", file=sys.stderr)
        return 2

    fieldnames, rows = next(iter_input_rows(args.input))
    if "proxy_wallet" not in fieldnames:
        print(
            f"ERROR: input CSV must have a 'proxy_wallet' column, got {fieldnames}",
            file=sys.stderr,
        )
        return 2

    output_fields = list(fieldnames)
    if "eoa_wallet" not in output_fields:
        output_fields.append("eoa_wallet")
    if "eoa_verdict" not in output_fields:
        output_fields.append("eoa_verdict")

    cached = load_existing_results(args.output)
    print(
        f"📋 input: {len(rows)} rows | already-resolved cache: {len(cached)} rows",
        file=sys.stderr,
    )

    session = requests.Session()
    processed = 0
    ok_count = 0
    wrong_count = 0
    errors = 0
    error_rows: list[tuple[str, str]] = []
    wrong_rows: list[tuple[str, str, str]] = []  # (proxy, found_eoa, returned_proxy)

    with open(args.output, "w", encoding="utf-8", newline="") as out_f:
        writer = csv.DictWriter(out_f, fieldnames=output_fields)
        writer.writeheader()

        for idx, row in enumerate(rows, start=1):
            if args.limit and processed >= args.limit:
                # Flush the rest unchanged so the output remains complete.
                writer.writerow(
                    {
                        **row,
                        "eoa_wallet": row.get("eoa_wallet", ""),
                        "eoa_verdict": row.get("eoa_verdict", ""),
                    }
                )
                continue

            proxy = normalize_addr(row.get("proxy_wallet") or "")
            if not proxy:
                writer.writerow(
                    {**row, "eoa_wallet": "ERROR: empty_proxy_wallet", "eoa_verdict": ""}
                )
                errors += 1
                continue

            if proxy in cached:
                cached_eoa, cached_verdict = cached[proxy]
                writer.writerow(
                    {**row, "eoa_wallet": cached_eoa, "eoa_verdict": cached_verdict}
                )
                ok_count += 1
                continue

            print(
                f"[{idx:>4}/{len(rows)}] {proxy} ...",
                file=sys.stderr,
            )
            result = lookup_eoa(session, proxy, api_key, verbose=args.verbose)

            if not result.eoa:
                # Fallback for the "no contract creation tx" case: the
                # proxy_wallet itself might be an EOA registered with
                # Polymarket. Ask Polymarket directly. If it has a profile,
                # treat the proxy_wallet as the EOA (verdict SELF_EOA).
                if result.error == "no_contract_creation_tx_found":
                    time.sleep(args.sleep)
                    pm_proxy, pm_err = polymarket_lookup_profile(
                        session, proxy, verbose=args.verbose
                    )
                    if pm_proxy is not None:
                        verdict = "OK_SELF" if pm_proxy == proxy else "SELF_EOA"
                        writer.writerow(
                            {**row, "eoa_wallet": proxy, "eoa_verdict": verdict}
                        )
                        ok_count += 1
                        print(
                            f"           -> {proxy}  {verdict} "
                            f"(polymarket proxy={pm_proxy})",
                            file=sys.stderr,
                        )
                        out_f.flush()
                        processed += 1
                        time.sleep(args.sleep)
                        continue
                    # Polymarket also has nothing on this address — give up.
                    note = pm_err or "no_polymarket_profile"
                    msg = f"ERROR: no_contract_creation_tx_and_{note}"
                    writer.writerow({**row, "eoa_wallet": msg, "eoa_verdict": ""})
                    errors += 1
                    error_rows.append((proxy, msg[len('ERROR: '):]))
                    print(f"           -> {msg}", file=sys.stderr)
                else:
                    msg = f"ERROR: {result.error or 'unknown'}"
                    writer.writerow({**row, "eoa_wallet": msg, "eoa_verdict": ""})
                    errors += 1
                    error_rows.append((proxy, result.error or "unknown"))
                    print(f"           -> {msg}", file=sys.stderr)
            else:
                # Polite gap between the receipt fetch and the Polymarket call.
                time.sleep(args.sleep)
                verify = verify_eoa_via_polymarket(
                    session, result.eoa, proxy, verbose=args.verbose
                )
                if verify.status == "OK":
                    writer.writerow(
                        {**row, "eoa_wallet": result.eoa, "eoa_verdict": "OK"}
                    )
                    ok_count += 1
                    print(f"           -> {result.eoa}  OK", file=sys.stderr)
                elif verify.status == "WRONG":
                    writer.writerow(
                        {**row, "eoa_wallet": result.eoa, "eoa_verdict": "WRONG"}
                    )
                    wrong_count += 1
                    wrong_rows.append(
                        (proxy, result.eoa, verify.returned_proxy or "")
                    )
                    print(
                        f"           -> {result.eoa}  WRONG "
                        f"(polymarket: {verify.returned_proxy})",
                        file=sys.stderr,
                    )
                elif verify.status == "NOT_FOUND":
                    # Polymarket has no public profile for our EOA. The EOA we
                    # extracted from logs is most likely still correct — the
                    # user just never set up a public profile. Keep it with a
                    # softer verdict so resume doesn't keep retrying.
                    writer.writerow(
                        {
                            **row,
                            "eoa_wallet": result.eoa,
                            "eoa_verdict": "UNVERIFIED_NO_PROFILE",
                        }
                    )
                    ok_count += 1
                    print(
                        f"           -> {result.eoa}  UNVERIFIED_NO_PROFILE",
                        file=sys.stderr,
                    )
                else:
                    # Genuine Polymarket-side error (5xx, bad JSON, timeout).
                    detail = verify.detail or ""
                    writer.writerow(
                        {
                            **row,
                            "eoa_wallet": result.eoa,
                            "eoa_verdict": f"ERROR: {detail}" if detail else "ERROR",
                        }
                    )
                    errors += 1
                    error_rows.append((proxy, f"verify_error: {detail}"))
                    print(
                        f"           -> {result.eoa}  ERROR ({detail})",
                        file=sys.stderr,
                    )

            out_f.flush()
            processed += 1
            time.sleep(args.sleep)

    print(
        f"\n✅ done: OK={ok_count}, WRONG={wrong_count}, errors={errors}, "
        f"new lookups={processed}",
        file=sys.stderr,
    )
    if wrong_rows:
        print(
            f"\n⚠️  {len(wrong_rows)} wallet(s) verified WRONG by Polymarket:",
            file=sys.stderr,
        )
        for pw, found_eoa, returned in wrong_rows[:20]:
            print(
                f"   csv proxy={pw}  our EOA={found_eoa}  polymarket says proxy={returned}",
                file=sys.stderr,
            )
        if len(wrong_rows) > 20:
            print(f"   ... and {len(wrong_rows) - 20} more", file=sys.stderr)
    if error_rows:
        print(f"\n⚠️  {len(error_rows)} wallet(s) failed:", file=sys.stderr)
        for pw, why in error_rows[:20]:
            print(f"   {pw}  {why}", file=sys.stderr)
        if len(error_rows) > 20:
            print(f"   ... and {len(error_rows) - 20} more", file=sys.stderr)
        print(
            "   Re-run the same command to retry only WRONG/ERROR rows.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
