"""
EVM (Ethereum) NFT minting service — ERC-721 on Sepolia / mainnet.
Drop-in replacement for scripts/solana_service.py: same MintedNftResult +
EvmClient that mirrors SolanaClient.mint_user_nft().
"""

from __future__ import annotations

import json
import os
import re
import socket
import time
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import httpx
from dotenv import load_dotenv
from web3 import Web3
from web3.exceptions import ContractLogicError

load_dotenv()

# ── env key names ──────────────────────────────────────────────────────────────
EVM_RPC_URL_ENV_KEY = "EVM_RPC_URL"
EVM_PRIVATE_KEY_ENV_KEY = "EVM_PRIVATE_KEY"
EVM_CONTRACT_ADDRESS_ENV_KEY = "EVM_CONTRACT_ADDRESS"
EVM_CHAIN_ID_ENV_KEY = "EVM_CHAIN_ID"

# Alchemy's NFT API lives at ``/nft/v3/<KEY>`` on the same hostname as the
# JSON-RPC endpoint at ``/v2/<KEY>``. Extracting the key from EVM_RPC_URL
# avoids requiring a second secret in env. ``ALCHEMY_NFT_API_BASE_URL`` is
# an explicit override for non-standard deployments.
ALCHEMY_NFT_API_BASE_URL_ENV_KEY = "ALCHEMY_NFT_API_BASE_URL"
_ALCHEMY_RPC_PATH_RE = re.compile(r"^(?P<scheme>https?://)(?P<host>[^/]+)/v2/(?P<key>[^/?#]+)/?$")
PINATA_JWT_ENV_KEY = "PINATA_JWT"
PINATA_API_URL = "https://api.pinata.cloud/pinning/pinJSONToIPFS"
PINATA_UNPIN_API_URL = "https://api.pinata.cloud/pinning/unpin"
PINATA_GATEWAY_PREFIX = "https://gateway.pinata.cloud/ipfs/"

# ── contract ABI (PolyStarsNFT: mintTo + Transfer event + ownerOf) ────────────
_MINT_ABI: list[dict] = [
    {
        "inputs": [
            {"internalType": "address", "name": "to", "type": "address"},
            {"internalType": "string", "name": "uri", "type": "string"},
        ],
        "name": "mintTo",
        "outputs": [{"internalType": "uint256", "name": "tokenId", "type": "uint256"}],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "internalType": "address",  "name": "from",    "type": "address"},
            {"indexed": True, "internalType": "address",  "name": "to",      "type": "address"},
            {"indexed": True, "internalType": "uint256",  "name": "tokenId", "type": "uint256"},
        ],
        "name": "Transfer",
        "type": "event",
    },
    {
        "inputs": [{"internalType": "uint256", "name": "tokenId", "type": "uint256"}],
        "name": "ownerOf",
        "outputs": [{"internalType": "address", "name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
]


# ── module-level helpers (reusable from read-only callers) ────────────────────

def parse_asset_address(asset_address: str) -> tuple[str | None, int | None]:
    """Split ``"<contract>/<tokenId>"`` into ``(contract_address, token_id)``.

    Returns ``(None, None)`` for empty / malformed input. ``token_id`` is
    ``None`` when the trailing segment isn't a valid integer (the contract
    part may still be returned for diagnostics).
    """
    if not asset_address:
        return None, None
    parts = asset_address.strip().rsplit("/", 1)
    if len(parts) != 2:
        return None, None
    contract = parts[0].strip() or None
    try:
        token_id = int(parts[1].strip())
    except (ValueError, TypeError):
        return contract, None
    return contract, token_id


def etherscan_base_url(chain_id: int) -> str:
    return {
        1: "https://etherscan.io",
        11155111: "https://sepolia.etherscan.io",
        8453: "https://basescan.org",
        84532: "https://sepolia.basescan.org",
    }.get(int(chain_id), "https://etherscan.io")


def etherscan_tx_url(tx_hash: str, chain_id: int) -> str:
    return f"{etherscan_base_url(chain_id)}/tx/{tx_hash}"


def etherscan_nft_url(contract_address: str, token_id: int, chain_id: int) -> str:
    return f"{etherscan_base_url(chain_id)}/nft/{contract_address}/{token_id}"


# OpenSea is the primary NFT marketplace for PolyStars STARs. Item URLs are
# ``https://opensea.io/item/<chain-slug>/<contract>/<tokenId>`` on mainnets and
# ``https://testnets.opensea.io/item/<chain-slug>/...`` on testnets.
_OPENSEA_CHAIN: dict[int, tuple[str, str]] = {
    1:        ("https://opensea.io",          "ethereum"),
    8453:     ("https://opensea.io",          "base"),
    11155111: ("https://testnets.opensea.io", "sepolia"),
    84532:    ("https://testnets.opensea.io", "base-sepolia"),
}


def opensea_nft_url(contract_address: str, token_id: int, chain_id: int) -> str | None:
    """OpenSea item page for a token, or ``None`` if the chain isn't on OpenSea."""
    entry = _OPENSEA_CHAIN.get(int(chain_id))
    if not entry:
        return None
    base, slug = entry
    return f"{base}/item/{slug}/{contract_address}/{token_id}"


# ── Alchemy NFT response parsers ──────────────────────────────────────────────
# Alchemy NFT API v3 returns metadata in a layered shape. We prefer the most
# CDN-friendly URL (``image.cachedUrl``), then less-processed variants, and
# finally fall back to the original ``raw.metadata.image`` so a wallet can
# render *something* even for tokens whose metadata Alchemy didn't fully
# normalize.

def _first_nonempty_str(*candidates: Any) -> str | None:
    for candidate in candidates:
        if candidate is None:
            continue
        s = str(candidate).strip()
        if s:
            return s
    return None


def _extract_alchemy_image_url(nft: dict[str, Any]) -> str | None:
    image = nft.get("image") or {}
    if isinstance(image, dict):
        cached = _first_nonempty_str(
            image.get("cachedUrl"),
            image.get("pngUrl"),
            image.get("thumbnailUrl"),
            image.get("originalUrl"),
        )
        if cached:
            return cached
    raw_metadata = (nft.get("raw") or {}).get("metadata") if isinstance(nft.get("raw"), dict) else None
    if isinstance(raw_metadata, dict):
        return _first_nonempty_str(raw_metadata.get("image"), raw_metadata.get("image_url"))
    return None


def _extract_alchemy_back_image_url(nft: dict[str, Any], front_url: str | None) -> str | None:
    """Read the back-side image from on-chain metadata's ``properties.files``.

    Our :class:`EvmClient` writes ``files[0]`` = front, ``files[1]`` = back.
    To be tolerant of file-order variation, we pick the first ``files[].uri``
    that differs from the front image rather than blindly indexing ``[1]``.
    """
    raw = nft.get("raw") if isinstance(nft.get("raw"), dict) else None
    metadata = raw.get("metadata") if isinstance(raw, dict) else None
    properties = metadata.get("properties") if isinstance(metadata, dict) else None
    files = properties.get("files") if isinstance(properties, dict) else None
    if not isinstance(files, list):
        return None
    front_norm = (front_url or "").strip()
    for entry in files:
        if not isinstance(entry, dict):
            continue
        candidate = _first_nonempty_str(entry.get("uri"))
        if candidate and candidate != front_norm:
            return candidate
    return None


def _extract_alchemy_name(nft: dict[str, Any]) -> str | None:
    raw_metadata = (nft.get("raw") or {}).get("metadata") if isinstance(nft.get("raw"), dict) else None
    raw_name = raw_metadata.get("name") if isinstance(raw_metadata, dict) else None
    return _first_nonempty_str(nft.get("name"), raw_name)


def _extract_alchemy_token_uri(nft: dict[str, Any]) -> str | None:
    token_uri = nft.get("tokenUri")
    if isinstance(token_uri, dict):
        gateway = _first_nonempty_str(token_uri.get("gateway"), token_uri.get("raw"))
        if gateway:
            return gateway
    return _first_nonempty_str(token_uri) if not isinstance(token_uri, dict) else None


@dataclass(frozen=True)
class MintedNftResult:
    claim_id: int
    asset_address: str   # "<contract_address>/<tokenId>", e.g. "0xABC.../42"
    tx_hash: str         # "0x..."
    nft_name: str
    metadata_uri: str
    explorer_tx_url: str
    explorer_asset_url: str


@dataclass(frozen=True)
class OwnedNft:
    """A single ERC-721 owned by a wallet, enriched with indexer metadata.

    ``image_url`` prefers Alchemy's CDN-cached URL (``image.cachedUrl``) and
    falls back to the original URI; this URL is what the user-web frontend
    renders when no local ``claims`` row carries a ``front_image_url``.

    ``back_image_url`` is read from the on-chain metadata's
    ``properties.files`` array — our :class:`EvmClient` writes the front as
    ``files[0]`` and the back as ``files[1]`` at mint time
    (see :meth:`EvmClient._metadata_image_properties`). Tokens minted by
    other tools without this convention will have ``None``.
    """
    token_id: int
    name: str | None
    image_url: str | None
    back_image_url: str | None
    metadata_uri: str | None


class EvmClient:
    """Mint ERC-721 NFTs on Ethereum via POLYSTARS.mintTo(address, string)."""

    DEFAULT_RPC_URL = "https://rpc.sepolia.org"
    GAS_MULTIPLIER = 1.3

    def __init__(
        self,
        rpc_url: str | None = None,
        private_key: str | None = None,
        contract_address: str | None = None,
        chain_id: int | None = None,
        max_retries: int = 3,
        retry_delay_seconds: float = 2.0,
    ) -> None:
        env_rpc = os.environ.get(EVM_RPC_URL_ENV_KEY, "").strip()
        self.rpc_url = rpc_url or env_rpc or self.DEFAULT_RPC_URL

        env_key = os.environ.get(EVM_PRIVATE_KEY_ENV_KEY, "").strip()
        self._private_key = private_key or env_key
        if not self._private_key:
            raise ValueError(f"{EVM_PRIVATE_KEY_ENV_KEY} is not set")

        env_contract = os.environ.get(EVM_CONTRACT_ADDRESS_ENV_KEY, "").strip()
        raw_contract = contract_address or env_contract
        if not raw_contract:
            raise ValueError(f"{EVM_CONTRACT_ADDRESS_ENV_KEY} is not set")
        self._contract_address = Web3.to_checksum_address(raw_contract)

        env_chain_id = os.environ.get(EVM_CHAIN_ID_ENV_KEY, "").strip()
        self._chain_id: int | None = chain_id or (int(env_chain_id) if env_chain_id else None)

        self.max_retries = max_retries
        self.retry_delay_seconds = retry_delay_seconds

        self.w3 = Web3(Web3.HTTPProvider(self.rpc_url))
        self._account = self.w3.eth.account.from_key(self._private_key)
        self._contract = self.w3.eth.contract(address=self._contract_address, abi=_MINT_ABI)

    @property
    def public_key(self) -> str:
        return self._account.address

    def validate_contract(self) -> None:
        """Raise if the contract address has no deployed bytecode on the configured RPC."""
        code = self.w3.eth.get_code(self._contract_address)
        if not code or code in (b"", b"\x00"):
            raise RuntimeError(
                f"EVM_CONTRACT_ADDRESS {self._contract_address!r} has no bytecode. "
                "Deploy the contract before minting."
            )

    def mint_user_nft(
        self,
        user_wallet_address: str,
        season_name: str,
        claim_id: int | None = None,
        collection_mint_number: int | None = None,
        winner_context: dict[str, Any] | None = None,
        polystars_card: dict[str, Any] | None = None,
        gas_price_gwei: float | None = None,
    ) -> MintedNftResult:
        """Mint one ERC-721 NFT to user_wallet_address and return on-chain artefacts.

        ``gas_price_gwei``: when provided, pin EIP-1559 ``maxFeePerGas`` to this
        total (gwei). Used by the cron mint queue to pay the SafeGasPrice tier
        once the rapid-tier price gate has confirmed the network is cheap.
        """
        recipient = Web3.to_checksum_address(user_wallet_address.strip())
        self.validate_contract()

        resolved_claim_id = claim_id if claim_id is not None else int(time.time())
        nft_number = (
            int(collection_mint_number)
            if collection_mint_number is not None
            else (int(claim_id) if claim_id is not None else resolved_claim_id)
        )
        nft_name = f"STAR {season_name} #{nft_number}"

        metadata_uri = self._build_metadata_uri(
            nft_name=nft_name,
            season_name=season_name,
            winner_context=winner_context,
            polystars_card=polystars_card,
        )

        try:
            token_id, tx_hash = self._send_mint_tx(
                recipient=recipient,
                metadata_uri=metadata_uri,
                gas_price_gwei=gas_price_gwei,
            )
        except Exception:
            self._unpin_pinata_url(metadata_uri)
            raise

        chain_id = self._chain_id or self.w3.eth.chain_id
        asset_address = f"{self._contract_address}/{token_id}"
        return MintedNftResult(
            claim_id=resolved_claim_id,
            asset_address=asset_address,
            tx_hash=tx_hash,
            nft_name=nft_name,
            metadata_uri=metadata_uri,
            explorer_tx_url=self._etherscan_tx_url(tx_hash, chain_id),
            explorer_asset_url=self._etherscan_nft_url(token_id, chain_id),
        )

    # ── transaction ────────────────────────────────────────────────────────────

    def _send_mint_tx(
        self,
        recipient: str,
        metadata_uri: str,
        gas_price_gwei: float | None = None,
    ) -> tuple[int, str]:
        """Build, sign, broadcast and confirm a mintTo tx. Returns (tokenId, tx_hash)."""
        chain_id = self._chain_id or self.w3.eth.chain_id
        nonce = self.w3.eth.get_transaction_count(self._account.address, "pending")
        fn = self._contract.functions.mintTo(recipient, metadata_uri)

        try:
            gas_estimate = fn.estimate_gas({"from": self._account.address})
        except ContractLogicError as exc:
            raise RuntimeError(f"mintTo call would revert: {exc}") from exc

        gas_limit = int(gas_estimate * self.GAS_MULTIPLIER)
        tx = self._build_tx(
            fn=fn,
            nonce=nonce,
            gas_limit=gas_limit,
            chain_id=chain_id,
            gas_price_gwei=gas_price_gwei,
        )
        signed = self._account.sign_transaction(tx)

        for attempt in range(self.max_retries):
            try:
                tx_hash_bytes = self.w3.eth.send_raw_transaction(signed.raw_transaction)
                break
            except Exception as exc:
                if attempt >= self.max_retries - 1 or not self._is_timeout_error(exc):
                    raise
                time.sleep(self.retry_delay_seconds * (2 ** attempt))

        receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash_bytes, timeout=120)
        if receipt["status"] != 1:
            raise RuntimeError(f"Mint transaction reverted. tx={receipt['transactionHash'].hex()}")

        token_id = self._extract_token_id(receipt)
        tx_hash_hex = receipt["transactionHash"].hex()
        return token_id, tx_hash_hex if tx_hash_hex.startswith("0x") else f"0x{tx_hash_hex}"

    def _build_tx(
        self,
        *,
        fn: Any,
        nonce: int,
        gas_limit: int,
        chain_id: int,
        gas_price_gwei: float | None = None,
    ) -> dict:
        """EIP-1559 preferred; falls back to legacy gasPrice.

        When ``gas_price_gwei`` is set, ``maxFeePerGas`` is pinned to that
        total (Etherscan SafeGasPrice already includes priority+base) and the
        priority tip is clamped to never exceed the cap. The legacy fallback
        uses the same value as the flat ``gasPrice``.
        """
        pinned_max_fee_wei = (
            int(gas_price_gwei * 1e9) if gas_price_gwei is not None else None
        )
        try:
            base_fee = self.w3.eth.get_block("latest").get("baseFeePerGas")
            if base_fee is None:
                raise ValueError("baseFeePerGas absent")
            default_priority = self.w3.to_wei(2, "gwei")
            if pinned_max_fee_wei is not None:
                max_fee = pinned_max_fee_wei
                max_priority = min(default_priority, max_fee)
            else:
                max_priority = default_priority
                max_fee = base_fee * 2 + max_priority
            return fn.build_transaction({
                "from": self._account.address,
                "nonce": nonce,
                "gas": gas_limit,
                "maxFeePerGas": max_fee,
                "maxPriorityFeePerGas": max_priority,
                "chainId": chain_id,
            })
        except Exception:
            legacy_price = (
                pinned_max_fee_wei
                if pinned_max_fee_wei is not None
                else self.w3.eth.gas_price
            )
            return fn.build_transaction({
                "from": self._account.address,
                "nonce": nonce,
                "gas": gas_limit,
                "gasPrice": legacy_price,
                "chainId": chain_id,
            })

    def _extract_token_id(self, receipt: Any) -> int:
        """Parse Transfer(from=0x0, to=recipient, tokenId=N) to get the minted tokenId."""
        zero = "0x" + "0" * 40
        for event in self._contract.events.Transfer().process_receipt(receipt):
            if event["args"]["from"].lower() == zero:
                return int(event["args"]["tokenId"])
        raise RuntimeError("Transfer(mint) event not found in transaction receipt")

    # ── explorer URLs ──────────────────────────────────────────────────────────

    def _etherscan_tx_url(self, tx_hash: str, chain_id: int) -> str:
        return f"{self._etherscan_base(chain_id)}/tx/{tx_hash}"

    def _etherscan_nft_url(self, token_id: int, chain_id: int) -> str:
        return f"{self._etherscan_base(chain_id)}/nft/{self._contract_address}/{token_id}"

    @staticmethod
    def _etherscan_base(chain_id: int) -> str:
        return {
            1: "https://etherscan.io",
            11155111: "https://sepolia.etherscan.io",
            8453: "https://basescan.org",
            84532: "https://sepolia.basescan.org",
        }.get(chain_id, "https://etherscan.io")

    # ── metadata + Pinata ──────────────────────────────────────────────────────

    def _build_metadata_uri(
        self,
        nft_name: str,
        season_name: str,
        winner_context: dict[str, Any] | None = None,
        polystars_card: dict[str, Any] | None = None,
    ) -> str:
        card_payload = dict(polystars_card or {})
        metadata_card_payload = self._build_card_display_data_payload(card_payload)
        front_image_url = str(card_payload.get("front_image_url") or "").strip()
        back_image_url = str(card_payload.get("back_image_url") or "").strip()
        front_image_mime = str(card_payload.get("front_image_mime") or "").strip()
        back_image_mime = str(card_payload.get("back_image_mime") or "").strip()
        attributes = self._build_card_attributes(card_payload)

        metadata: dict[str, Any] = {
            "name": nft_name,
            "symbol": "STAR",
            "description": f"Collectible STAR of {season_name}",
            "attributes": attributes,
        }
        if front_image_url:
            metadata["image"] = self._to_ipfs_uri(front_image_url)
            metadata["properties"] = self._metadata_image_properties(
                front_url=self._to_ipfs_uri(front_image_url),
                back_url=self._to_ipfs_uri(back_image_url),
                front_mime=front_image_mime,
                back_mime=back_image_mime,
            )
        if winner_context:
            metadata["winner_source_data"] = self._build_winner_source_data(winner_context)
        if metadata_card_payload:
            metadata["card_display_data"] = metadata_card_payload

        uploaded_uri = self._upload_metadata_to_pinata(metadata)
        if uploaded_uri:
            return uploaded_uri
        raise RuntimeError(
            "Failed to upload NFT metadata to Pinata; aborting mint to avoid non-indexable inline metadata."
        )

    @staticmethod
    def _metadata_image_properties(
        *,
        front_url: str,
        back_url: str,
        front_mime: str,
        back_mime: str,
    ) -> dict[str, Any]:
        files: list[dict[str, str]] = [
            {"uri": front_url, "type": front_mime or EvmClient._guess_media_type(front_url)}
        ]
        if back_url:
            files.append(
                {"uri": back_url, "type": back_mime or EvmClient._guess_media_type(back_url)}
            )
        return {"category": "image", "files": files}

    @staticmethod
    def _to_ipfs_uri(url: str) -> str:
        """Normalize a Pinata/IPFS gateway URL to a canonical ``ipfs://<CID>`` URI.

        On-chain metadata (``image``, ``properties.files[].uri``, ``tokenURI``)
        should reference content by CID, not a single gateway host, so it keeps
        resolving even if a particular gateway goes away.
        """
        s = str(url or "").strip()
        if not s or s.startswith("ipfs://"):
            return s
        if s.startswith(PINATA_GATEWAY_PREFIX):
            cid = s[len(PINATA_GATEWAY_PREFIX):].lstrip("/")
            return f"ipfs://{cid}" if cid else s
        marker = "/ipfs/"
        idx = s.find(marker)
        if idx != -1:
            cid = s[idx + len(marker):].lstrip("/")
            return f"ipfs://{cid}" if cid else s
        return s

    @staticmethod
    def _guess_media_type(url: str) -> str:
        lower = url.lower()
        if lower.endswith(".png"):
            return "image/png"
        if lower.endswith(".jpg") or lower.endswith(".jpeg"):
            return "image/jpeg"
        if lower.endswith(".webp"):
            return "image/webp"
        if lower.endswith(".gif"):
            return "image/gif"
        if lower.endswith(".svg"):
            return "image/svg+xml"
        return "image/*"

    @staticmethod
    def _recurrence_implies_fractal(recurrence_value: Any) -> bool:
        s = str(recurrence_value or "").strip().lower()
        if not s or s in ("null", "none", "-", "unique"):
            return False
        return True

    @staticmethod
    def _format_trait_season_type(raw: Any) -> str | None:
        s = str(raw or "").strip().lower()
        if not s:
            return None
        if s == "genesis":
            return "Genesis"
        if s == "standard":
            return "Standard"
        return s[:1].upper() + s[1:]

    @staticmethod
    def _format_trait_string_title(raw: Any) -> str | None:
        if raw is None:
            return None
        s = str(raw).strip()
        return s or None

    @staticmethod
    def _format_trait_participant_class(raw: Any) -> str | None:
        s = str(raw or "").strip()
        return s.title() if s else None

    @staticmethod
    def _format_trait_archetype(raw: Any) -> str | None:
        s = str(raw or "").strip()
        return s.upper() if s else None

    @staticmethod
    def _build_card_attributes(card_payload: dict[str, Any]) -> list[dict[str, Any]]:
        season_type = EvmClient._format_trait_season_type(card_payload.get("season_type"))
        if season_type == "Genesis":
            season_number_str: str | None = "0"
        else:
            season_num = card_payload.get("season_number")
            season_number_str = str(season_num).strip() or None if season_num is not None else None

        instance_val = (
            "Fractal"
            if EvmClient._recurrence_implies_fractal(card_payload.get("recurrence"))
            else "Singular"
        )

        trait_specs: list[tuple[str | None, str]] = [
            (season_type, "Season Type"),
            (season_number_str, "Season Number"),
            (instance_val, "Instance"),
            (EvmClient._format_trait_participant_class(card_payload.get("claim_type")), "Participant Class"),
            (EvmClient._format_trait_string_title(card_payload.get("primary_tag")), "Sector"),
            (EvmClient._format_trait_archetype(card_payload.get("archetype")), "Archetype"),
            (EvmClient._format_trait_string_title(card_payload.get("entry_bracket")), "P(E)"),
            (EvmClient._format_trait_string_title(card_payload.get("edge")), "Edge"),
            (EvmClient._format_trait_string_title(card_payload.get("yield")), "Yield"),
            (EvmClient._format_trait_string_title(card_payload.get("gravity")), "Gravity"),
        ]
        attributes: list[dict[str, Any]] = []
        for value, trait_type in trait_specs:
            if value is None:
                continue
            v = value.strip() if isinstance(value, str) else value
            if isinstance(v, str) and not v:
                continue
            attributes.append({"trait_type": trait_type, "value": v})
        return attributes

    @staticmethod
    def _sanitize_snapshot(snapshot: Any) -> Any:
        if not isinstance(snapshot, dict):
            return snapshot
        copy = dict(snapshot)
        copy.pop("event_image_url", None)
        copy.pop("event_image_source_url", None)
        return copy

    @staticmethod
    def _build_winner_source_data(winner_context: dict[str, Any]) -> dict[str, Any]:
        snapshot = EvmClient._sanitize_snapshot(winner_context.get("snapshot"))
        chain = str(winner_context.get("blockchain") or "ethereum").strip().lower() or "ethereum"
        return {
            "winner_wallet_address": winner_context.get("winner_wallet_address"),
            "claimer_wallet_address": winner_context.get("claimer_wallet_address"),
            "season_id": winner_context.get("season_id"),
            "snapshot": snapshot,
            "blockchain": chain,
        }

    @staticmethod
    def _build_card_display_data_payload(card_payload: dict[str, Any]) -> dict[str, Any]:
        allowed = (
            "season_type", "season_number", "recurrence", "claim_type",
            "card_title", "card_lore", "primary_tag", "secondary_tag",
            "entry_bracket", "archetype", "archetype_description", "archetype_math",
            "proxy_wallet", "edge", "yield", "gravity",
            "leaderboard_rank", "season_start_date", "season_end_date",
            "season_size", "collection_mint_number", "front_image_url",
            "back_image_url", "qr_payload",
        )
        return {k: card_payload[k] for k in allowed if k in card_payload}

    @staticmethod
    def _unpin_pinata_url(url: str) -> None:
        if not url:
            return
        if url.startswith("ipfs://"):
            cid = url[len("ipfs://"):].lstrip("/")
        elif url.startswith(PINATA_GATEWAY_PREFIX):
            cid = url[len(PINATA_GATEWAY_PREFIX):]
        else:
            return
        if not cid:
            return
        jwt = os.environ.get(PINATA_JWT_ENV_KEY, "").strip()
        if not jwt:
            return
        try:
            httpx.delete(
                f"{PINATA_UNPIN_API_URL}/{cid}",
                headers={"Authorization": f"Bearer {jwt}"},
                timeout=10.0,
            )
        except Exception:
            pass

    @staticmethod
    def _upload_metadata_to_pinata(metadata: dict[str, Any]) -> str | None:
        jwt = os.environ.get(PINATA_JWT_ENV_KEY, "").strip()
        if not jwt:
            return None
        metadata = EvmClient._make_json_safe(metadata)
        headers = {
            "Authorization": f"Bearer {jwt}",
            "Content-Type": "application/json",
        }
        payload = {
            "pinataContent": metadata,
            "pinataMetadata": {"name": f"{metadata.get('name', 'ps-nft')}.json"},
        }
        for _attempt in range(2):
            try:
                response = httpx.post(PINATA_API_URL, headers=headers, json=payload, timeout=20.0)
                response.raise_for_status()
                ipfs_hash = response.json().get("IpfsHash")
                if ipfs_hash:
                    return f"ipfs://{ipfs_hash}"
            except Exception:
                continue
        return None

    @staticmethod
    def _make_json_safe(value: Any) -> Any:
        if isinstance(value, dict):
            return {str(k): EvmClient._make_json_safe(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [EvmClient._make_json_safe(item) for item in value]
        if isinstance(value, Decimal):
            return int(value) if value == value.to_integral_value() else float(value)
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        return value

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
        return "timeout" in str(exc).lower() or "timed out" in str(exc).lower()


class EvmReader:
    """Read-only ERC-721 client for ownership / metadata lookups.

    Unlike :class:`EvmClient`, this requires no private key — it never sends
    transactions. Used by the user-web backend to verify on-chain ownership
    of minted PolyStars NFTs without granting that service mint authority.
    """

    def __init__(
        self,
        rpc_url: str | None = None,
        contract_address: str | None = None,
        chain_id: int | None = None,
        request_timeout_seconds: float = 15.0,
    ) -> None:
        env_rpc = os.environ.get(EVM_RPC_URL_ENV_KEY, "").strip()
        self.rpc_url = rpc_url or env_rpc or EvmClient.DEFAULT_RPC_URL

        env_contract = os.environ.get(EVM_CONTRACT_ADDRESS_ENV_KEY, "").strip()
        raw_contract = contract_address or env_contract
        if not raw_contract:
            raise ValueError(f"{EVM_CONTRACT_ADDRESS_ENV_KEY} is not set")
        self.contract_address = Web3.to_checksum_address(raw_contract)

        env_chain_id = os.environ.get(EVM_CHAIN_ID_ENV_KEY, "").strip()
        self._chain_id_override: int | None = (
            chain_id if chain_id is not None else (int(env_chain_id) if env_chain_id else None)
        )
        self._chain_id_cached: int | None = None

        self._w3 = Web3(
            Web3.HTTPProvider(self.rpc_url, request_kwargs={"timeout": request_timeout_seconds})
        )
        self._contract = self._w3.eth.contract(address=self.contract_address, abi=_MINT_ABI)

    @property
    def chain_id(self) -> int:
        if self._chain_id_override is not None:
            return int(self._chain_id_override)
        if self._chain_id_cached is None:
            self._chain_id_cached = int(self._w3.eth.chain_id)
        return self._chain_id_cached

    def owner_of(self, token_id: int) -> str | None:
        """Return the current owner's checksum address, or ``None`` if the
        token does not exist (``ownerOf`` reverts for unminted/burnt) or the
        RPC call fails. Never raises — callers can treat ``None`` as
        "not owned by anyone the caller cares about"."""
        try:
            addr = self._contract.functions.ownerOf(int(token_id)).call()
        except ContractLogicError:
            return None
        except Exception:
            return None
        try:
            return Web3.to_checksum_address(str(addr))
        except Exception:
            return None

    def _alchemy_nft_base_url(self) -> str:
        """Return the Alchemy NFT API base URL (``.../nft/v3/<KEY>``).

        Prefers the explicit ``ALCHEMY_NFT_API_BASE_URL`` override; otherwise
        derives it from ``EVM_RPC_URL`` by replacing the ``/v2/<KEY>`` path
        with ``/nft/v3/<KEY>`` on the same Alchemy hostname.
        """
        override = os.environ.get(ALCHEMY_NFT_API_BASE_URL_ENV_KEY, "").strip()
        if override:
            return override.rstrip("/")
        match = _ALCHEMY_RPC_PATH_RE.match(self.rpc_url.strip())
        if not match:
            raise RuntimeError(
                f"Cannot derive Alchemy NFT API base URL from {EVM_RPC_URL_ENV_KEY!r}. "
                f"Expected ``https://<host>/v2/<KEY>``; set {ALCHEMY_NFT_API_BASE_URL_ENV_KEY!r} "
                f"explicitly if your provider uses a non-standard URL."
            )
        return f"{match.group('scheme')}{match.group('host')}/nft/v3/{match.group('key')}"

    def tokens_owned_by(self, wallet: str) -> list[OwnedNft]:
        """Return all NFTs in this collection currently owned by ``wallet``.

        Uses Alchemy's NFT API (``getNFTsForOwner``) as the indexer of
        record. Alchemy resolves "what does this wallet currently hold on
        contract X" in one paginated call — we don't have to scan
        ``Transfer`` logs or call ``ownerOf`` per-token.

        ``withMetadata=true`` makes Alchemy return the parsed metadata
        (name, image, tokenUri) in the same response, which the user-web
        backend uses as a fallback when no local ``claims`` row carries the
        denormalized image URL (e.g. NFTs received via secondary transfer).

        The endpoint is reached on the same Alchemy hostname / API key as
        ``EVM_RPC_URL`` (path ``/nft/v3/<KEY>``); see
        ``_alchemy_nft_base_url`` for derivation.
        """
        try:
            wallet_checksum = Web3.to_checksum_address(wallet)
        except Exception:
            return []

        base_url = self._alchemy_nft_base_url()
        url = f"{base_url}/getNFTsForOwner"

        items: list[OwnedNft] = []
        seen: set[int] = set()
        page_key: str | None = None
        # Hard cap on pagination loops as a defense against an indexer bug
        # returning the same pageKey forever. 100 pages × 100 tokens = 10k
        # held NFTs is well above any realistic dashboard scenario.
        max_pages = 100
        for _ in range(max_pages):
            params: list[tuple[str, str]] = [
                ("owner", wallet_checksum),
                ("contractAddresses[]", self.contract_address),
                ("withMetadata", "true"),
                ("pageSize", "100"),
            ]
            if page_key:
                params.append(("pageKey", page_key))
            try:
                response = httpx.get(url, params=params, timeout=15.0)
                response.raise_for_status()
                payload = response.json()
            except Exception as exc:
                raise RuntimeError(f"Alchemy NFT API request failed: {exc}") from exc

            for nft in payload.get("ownedNfts", []) or []:
                raw_token_id = nft.get("tokenId")
                if raw_token_id is None:
                    continue
                try:
                    # Alchemy returns tokenId as a decimal string for ERC-721;
                    # historical responses occasionally used hex ("0x..."), so
                    # accept both via base=0.
                    tid = int(str(raw_token_id), 0) if str(raw_token_id).startswith("0x") else int(raw_token_id)
                except (TypeError, ValueError):
                    continue
                if tid in seen:
                    continue
                seen.add(tid)
                front_url = _extract_alchemy_image_url(nft)
                items.append(OwnedNft(
                    token_id=tid,
                    name=_extract_alchemy_name(nft),
                    image_url=front_url,
                    back_image_url=_extract_alchemy_back_image_url(nft, front_url),
                    metadata_uri=_extract_alchemy_token_uri(nft),
                ))

            page_key = payload.get("pageKey") or None
            if not page_key:
                break

        return items
