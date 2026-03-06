"""
Solana RPC service helpers for Devnet operations.
"""

from __future__ import annotations

import json
import os
import socket
import struct
import time
import base64
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

import httpx
from dotenv import load_dotenv
from solana.rpc.api import Client
from solana.rpc.commitment import Confirmed
from solders.instruction import AccountMeta, Instruction
from solders.keypair import Keypair
from solders.message import MessageV0
from solders.pubkey import Pubkey
from solders.system_program import ID as SYSTEM_PROGRAM_ID
from solders.transaction import VersionedTransaction

load_dotenv()


MPL_CORE_PROGRAM_ID = Pubkey.from_string(
    "CoREENxT6tW1HoK8ypY1SxRMZTcVPm7R94rH4PZNhX7d"
)
SPL_NOOP_PROGRAM_ID = Pubkey.from_string(
    "noopb9bkMVfRPU8AsbpTUg8AQkHtKwMYZiFUjNRtMmV"
)
MASTER_COLLECTION_ENV_KEY = "MASTER_COLLECTION_ADDRESS"
PINATA_JWT_ENV_KEY = "PINATA_JWT"
PINATA_API_URL = "https://api.pinata.cloud/pinning/pinJSONToIPFS"
LAMPORTS_PER_SOL = 1_000_000_000


@dataclass(frozen=True)
class MintedNftResult:
    claim_id: int
    asset_address: str
    tx_hash: str
    nft_name: str
    metadata_uri: str
    explorer_tx_url: str
    explorer_asset_url: str


class SolanaClient:
    """Minimal Solana client with timeout retry logic."""

    DEVNET_RPC_URL = "https://api.devnet.solana.com"

    def __init__(
        self,
        keypair_path: str | Path = "my-keypair.json",
        rpc_url: str = DEVNET_RPC_URL,
        timeout_seconds: float = 10.0,
        max_retries: int = 3,
        retry_delay_seconds: float = 1.0,
    ) -> None:
        self.keypair_path = Path(keypair_path)
        self.rpc_url = rpc_url
        self.max_retries = max_retries
        self.retry_delay_seconds = retry_delay_seconds

        self._keypair = self._load_keypair(self.keypair_path)
        self.client = Client(self.rpc_url, timeout=timeout_seconds)

    @property
    def keypair(self) -> Keypair:
        return self._keypair

    @property
    def public_key(self):
        return self._keypair.pubkey()

    def get_balance(self) -> float:
        """
        Return wallet balance in SOL for the loaded keypair.
        """
        response = self._rpc_call_with_retry(
            lambda: self.client.get_balance(self.public_key)
        )
        lamports = response.value
        return lamports / LAMPORTS_PER_SOL

    def mint_user_nft(
        self,
        user_wallet_address: str,
        pnl_value: float,
        rank: int,
        season_name: str,
        claim_id: int | None = None,
        winner_context: dict[str, Any] | None = None,
    ) -> MintedNftResult:
        """
        Mint a Core NFT for a user wallet and attach it to master collection.
        """
        owner_pubkey = Pubkey.from_string(user_wallet_address.strip())
        collection_pubkey = self._get_master_collection_pubkey()

        resolved_claim_id = claim_id if claim_id is not None else int(time.time())
        nft_name = f"PolyStars {season_name} #{resolved_claim_id}"
        metadata_uri = self._build_metadata_uri(
            nft_name=nft_name,
            season_name=season_name,
            pnl_value=pnl_value,
            rank=rank,
            winner_context=winner_context,
        )

        asset_keypair = Keypair()
        instruction_data = self._build_create_v2_data(name=nft_name, uri=metadata_uri)
        instruction = Instruction(
            MPL_CORE_PROGRAM_ID,
            instruction_data,
            [
                AccountMeta(asset_keypair.pubkey(), True, True),   # asset
                AccountMeta(collection_pubkey, False, True),       # collection
                AccountMeta(self.public_key, True, False),         # authority
                AccountMeta(self.public_key, True, True),          # payer
                AccountMeta(owner_pubkey, False, False),           # owner
                # For CreateV2 with collection, keep this slot present but use
                # MPL Core program id as "none"/sentinel update authority.
                AccountMeta(MPL_CORE_PROGRAM_ID, False, False),    # update authority
                AccountMeta(SYSTEM_PROGRAM_ID, False, False),      # system program
                AccountMeta(SPL_NOOP_PROGRAM_ID, False, False),    # log wrapper
            ],
        )

        latest_blockhash = self._rpc_call_with_retry(
            lambda: self.client.get_latest_blockhash()
        ).value.blockhash

        message = MessageV0.try_compile(
            payer=self.public_key,
            instructions=[instruction],
            address_lookup_table_accounts=[],
            recent_blockhash=latest_blockhash,
        )
        transaction = VersionedTransaction(message, [self._keypair, asset_keypair])

        send_resp = self._rpc_call_with_retry(
            lambda: self.client.send_transaction(transaction)
        )
        signature = str(send_resp.value)

        confirm_resp = self._rpc_call_with_retry(
            lambda: self.client.confirm_transaction(
                send_resp.value,
                commitment=Confirmed,
                sleep_seconds=0.8,
            )
        )
        status = confirm_resp.value[0] if confirm_resp.value else None
        if status is None:
            raise RuntimeError("Transaction confirmation status is empty")
        if status.err is not None:
            raise RuntimeError(f"Mint transaction failed: {status.err}")

        asset_address = str(asset_keypair.pubkey())
        return MintedNftResult(
            claim_id=resolved_claim_id,
            asset_address=asset_address,
            tx_hash=signature,
            nft_name=nft_name,
            metadata_uri=metadata_uri,
            explorer_tx_url=f"https://explorer.solana.com/tx/{signature}?cluster=devnet",
            explorer_asset_url=(
                f"https://explorer.solana.com/address/{asset_address}?cluster=devnet"
            ),
        )

    def _rpc_call_with_retry(self, rpc_call: Callable[[], Any]) -> Any:
        """
        Retry RPC call when timeout-like errors occur.
        """
        attempt = 0
        while True:
            try:
                return rpc_call()
            except Exception as exc:
                if not self._is_timeout_error(exc) or attempt >= self.max_retries:
                    raise

                attempt += 1
                delay = self.retry_delay_seconds * (2 ** (attempt - 1))
                time.sleep(delay)

    @staticmethod
    def _serialize_string(value: str) -> bytes:
        payload = value.encode("utf-8")
        return struct.pack("<I", len(payload)) + payload

    def _build_create_v2_data(self, name: str, uri: str) -> bytes:
        """
        Metaplex Core CreateV2 layout:
        discriminator(20) + data_state(0) + name + uri + plugins + adapters.
        """
        discriminator = bytes([20])
        data_state_account = bytes([0])
        some_empty_vec = bytes([1]) + struct.pack("<I", 0)
        return (
            discriminator
            + data_state_account
            + self._serialize_string(name)
            + self._serialize_string(uri)
            + some_empty_vec   # plugins: Some([])
            + some_empty_vec   # external_plugin_adapters: Some([])
        )

    def _build_metadata_uri(
        self,
        nft_name: str,
        season_name: str,
        pnl_value: float,
        rank: int,
        winner_context: dict[str, Any] | None = None,
    ) -> str:
        metadata = {
            "name": nft_name,
            "symbol": "POLY",
            "description": f"PolyStars reward NFT for season {season_name}",
            "attributes": [
                {"trait_type": "Profit", "value": pnl_value},
                {"trait_type": "Rank", "value": rank},
            ],
        }
        if winner_context:
            metadata["winner_context"] = winner_context

        uploaded_uri = self._upload_metadata_to_pinata(metadata)
        if uploaded_uri:
            return uploaded_uri

        # Fallback when Pinata is unavailable: keep URI minimal to stay below
        # transaction size limits. Full winner snapshot remains in DB records.
        compact_metadata = {
            "name": nft_name,
            "symbol": "POLY",
            "description": f"PolyStars reward NFT for season {season_name}",
            "attributes": [
                {"trait_type": "Profit", "value": pnl_value},
                {"trait_type": "Rank", "value": rank},
            ],
        }
        if winner_context:
            compact_metadata["winner_ref"] = {
                "assignment_type": winner_context.get("assignment_type"),
                "winner_wallet_address": winner_context.get("winner_wallet_address"),
                "season_id": winner_context.get("season_id"),
                "winner_row_id": (winner_context.get("snapshot") or {}).get("winner_row_id"),
            }

        raw_json = json.dumps(compact_metadata, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        encoded = base64.b64encode(raw_json).decode("ascii")
        return f"data:application/json;base64,{encoded}"

    @staticmethod
    def _upload_metadata_to_pinata(metadata: dict[str, Any]) -> str | None:
        jwt = os.environ.get(PINATA_JWT_ENV_KEY, "").strip()
        if not jwt:
            return None

        headers = {
            "Authorization": f"Bearer {jwt}",
            "Content-Type": "application/json",
        }
        payload = {
            "pinataContent": metadata,
            "pinataMetadata": {"name": f"{metadata.get('name', 'polystars-nft')}.json"},
        }

        for _attempt in range(2):
            try:
                response = httpx.post(PINATA_API_URL, headers=headers, json=payload, timeout=20.0)
                response.raise_for_status()
                body = response.json()
                ipfs_hash = body.get("IpfsHash")
                if not ipfs_hash:
                    return None
                return f"https://gateway.pinata.cloud/ipfs/{ipfs_hash}"
            except Exception:
                continue
        return None

    @staticmethod
    def _get_master_collection_pubkey() -> Pubkey:
        collection_address = os.environ.get(MASTER_COLLECTION_ENV_KEY, "").strip()
        if not collection_address:
            raise ValueError(
                f"{MASTER_COLLECTION_ENV_KEY} is empty. Set it in .env before minting."
            )
        return Pubkey.from_string(collection_address)

    @staticmethod
    def _load_keypair(path: Path) -> Keypair:
        if not path.exists():
            raise FileNotFoundError(f"Solana keypair file not found: {path}")

        try:
            with path.open("r", encoding="utf-8") as f:
                secret_data = json.load(f)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid keypair JSON in file: {path}") from exc

        if not isinstance(secret_data, Sequence) or isinstance(secret_data, (str, bytes)):
            raise ValueError("Keypair JSON must contain an array of integers")

        try:
            key_bytes = bytes(secret_data)
        except ValueError as exc:
            raise ValueError("Keypair array must contain integers in range 0..255") from exc

        if len(key_bytes) == 64:
            return Keypair.from_bytes(key_bytes)
        if len(key_bytes) == 32:
            return Keypair.from_seed(key_bytes)

        raise ValueError(
            "Unsupported keypair length. Expected 32 (seed) or 64 (secret key) bytes."
        )

    @staticmethod
    def _is_timeout_error(exc: Exception) -> bool:
        timeout_types = (
            TimeoutError,
            socket.timeout,
            httpx.TimeoutException,
            httpx.ReadTimeout,
            httpx.ConnectTimeout,
            httpx.WriteTimeout,
            httpx.PoolTimeout,
        )
        if isinstance(exc, timeout_types):
            return True

        message = str(exc).lower()
        return "timeout" in message or "timed out" in message
