"""Etherscan V2 gas tracker for Ethereum mainnet mint cost estimation.

Used by:
  - admin_backend/main.py: GET /api/gas-tracker/eth-mint (cached for the UI panel).
  - scripts/daily_scheduler_simple.py: process_mint_queue uses the rapid USD cost
    as a price gate before broadcasting each on-chain mint.

Required env:
  ETHERSCAN_API_KEY        — Etherscan API V2 mandates an API key.

Optional env:
  EVM_MINT_GAS_ESTIMATE    — gas units per mintTo() call (default 165000).
"""

from __future__ import annotations

import os
from dataclasses import dataclass


ETHERSCAN_V2_BASE_URL = "https://api.etherscan.io/v2/api"
DEFAULT_MINT_GAS_ESTIMATE = 165_000


@dataclass(frozen=True)
class GasTrackerSnapshot:
    base_fee_gwei: float
    safe_gwei: float
    propose_gwei: float
    rapid_gwei: float
    eth_usd: float
    gas_estimate: int

    def _cost_eth(self, gwei: float) -> float:
        return self.gas_estimate * gwei * 1e-9

    @property
    def safe_eth(self) -> float:
        return self._cost_eth(self.safe_gwei)

    @property
    def propose_eth(self) -> float:
        return self._cost_eth(self.propose_gwei)

    @property
    def rapid_eth(self) -> float:
        return self._cost_eth(self.rapid_gwei)

    @property
    def safe_usd(self) -> float:
        return self.safe_eth * self.eth_usd

    @property
    def propose_usd(self) -> float:
        return self.propose_eth * self.eth_usd

    @property
    def rapid_usd(self) -> float:
        return self.rapid_eth * self.eth_usd


def _gas_estimate_from_env() -> int:
    raw = (os.environ.get("EVM_MINT_GAS_ESTIMATE") or "").strip()
    if not raw:
        return DEFAULT_MINT_GAS_ESTIMATE
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_MINT_GAS_ESTIMATE
    return value if value > 0 else DEFAULT_MINT_GAS_ESTIMATE


def fetch_eth_mint_gas_tracker(timeout: float = 8.0) -> GasTrackerSnapshot:
    """Fetch mainnet gas oracle + ETH price from Etherscan API V2.

    Raises RuntimeError if ETHERSCAN_API_KEY is missing or either endpoint
    returns a non-success payload.
    """
    import requests

    api_key = (os.environ.get("ETHERSCAN_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("ETHERSCAN_API_KEY is not set (required by Etherscan API V2)")
    common = {"chainid": "1", "apikey": api_key}

    gas_resp = requests.get(
        ETHERSCAN_V2_BASE_URL,
        params={"module": "gastracker", "action": "gasoracle", **common},
        timeout=timeout,
    )
    gas_resp.raise_for_status()
    gas_payload = gas_resp.json()
    if str(gas_payload.get("status")) != "1":
        raise RuntimeError(
            f"gasoracle: {gas_payload.get('result') or gas_payload.get('message')}"
        )
    gas = gas_payload["result"]

    price_resp = requests.get(
        ETHERSCAN_V2_BASE_URL,
        params={"module": "stats", "action": "ethprice", **common},
        timeout=timeout,
    )
    price_resp.raise_for_status()
    price_payload = price_resp.json()
    if str(price_payload.get("status")) != "1":
        raise RuntimeError(
            f"ethprice: {price_payload.get('result') or price_payload.get('message')}"
        )

    return GasTrackerSnapshot(
        base_fee_gwei=float(gas.get("suggestBaseFee", "0") or 0.0),
        safe_gwei=float(gas["SafeGasPrice"]),
        propose_gwei=float(gas["ProposeGasPrice"]),
        rapid_gwei=float(gas["FastGasPrice"]),
        eth_usd=float(price_payload["result"]["ethusd"]),
        gas_estimate=_gas_estimate_from_env(),
    )
