"""Dev-only: free stuck pending nonces on the worker wallet by sending a
0-value self-transfer at high gas for each stuck nonce. Used when a previous
mint broadcast went out at sub-1-gwei (mainnet gasoracle tier) and Sepolia
validators ignored it, leaving the pending-nonce counter ahead of the chain
nonce and blocking every subsequent mint.

Usage:
    venv\\Scripts\\python.exe scripts/dev_unstick_nonce.py

Refuses to run on Ethereum mainnet (chain_id=1) — replacement-with-50-gwei is
explicitly a dev shortcut and would be wasteful / risky on prod.
"""
from __future__ import annotations

import sys
from pathlib import Path

# When invoked as ``python scripts/dev_unstick_nonce.py`` Python only puts
# ``scripts/`` on sys.path, so ``from scripts.evm_service`` fails. Prepend the
# project root to make the package import resolve regardless of cwd.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv()

from scripts.evm_service import EvmClient


def main() -> None:
    client = EvmClient()
    addr = client.public_key
    chain_id = client.w3.eth.chain_id
    if chain_id == 1:
        raise SystemExit("Refusing to run on Ethereum mainnet (chain_id=1).")

    chain_n = client.w3.eth.get_transaction_count(addr)
    pending_n = client.w3.eth.get_transaction_count(addr, "pending")
    stuck = pending_n - chain_n
    print(f"wallet={addr}")
    print(f"chain_id={chain_id} chain_nonce={chain_n} pending_nonce={pending_n} stuck={stuck}")

    if stuck <= 0:
        print("Nothing stuck — exiting.")
        return

    max_fee = client.w3.to_wei(50, "gwei")
    max_priority = client.w3.to_wei(5, "gwei")

    for n in range(chain_n, pending_n):
        tx = {
            "from": addr,
            "to": addr,
            "value": 0,
            "nonce": n,
            "gas": 21000,
            "maxFeePerGas": max_fee,
            "maxPriorityFeePerGas": max_priority,
            "chainId": chain_id,
        }
        signed = client._account.sign_transaction(tx)
        h = client.w3.eth.send_raw_transaction(signed.raw_transaction)
        print(f"replaced nonce {n}: {h.hex()}")

    print("Done. Wait ~12-24s, then re-check pending vs chain nonce.")


if __name__ == "__main__":
    main()
