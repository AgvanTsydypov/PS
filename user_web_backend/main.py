from __future__ import annotations

import base64
import json
import logging
import mimetypes
import os
import re
import secrets
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import monotonic
from typing import Any, Dict, List, Optional, Tuple

import jwt
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
from eth_account import Account
from eth_account.messages import encode_defunct
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from web3 import Web3


def _load_environment() -> None:
    env_file = os.getenv("ENV_FILE", ".env")
    repo_root = Path(__file__).resolve().parent.parent
    env_path = Path(env_file)
    if not env_path.is_absolute():
        env_path = repo_root / env_file
    load_dotenv(dotenv_path=env_path, override=False)


_load_environment()

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from scripts.season_manager import SeasonManager
from admin_backend.main import MintClaimRequest, SeasonWorkbenchService
from scripts.cardgen.assets import (
    delete_r2_object_by_key,
    extract_r2_key_from_public_url,
    r2_public_base_url,
    render_card_pngs,
    upload_card_assets_to_r2,
)

try:
    from solders.pubkey import Pubkey  # type: ignore
except Exception:  # pragma: no cover - solders should be installed alongside admin
    Pubkey = None  # type: ignore

logger = logging.getLogger(__name__)


def _is_running_in_docker() -> bool:
    return Path("/.dockerenv").exists() or os.getenv("RUNNING_IN_DOCKER") == "1"


def _db_params() -> Dict[str, object]:
    host = os.getenv("DB_HOST", os.getenv("LOCAL_DB_HOST", "127.0.0.1"))
    if _is_running_in_docker() and host in {"localhost", "127.0.0.1"}:
        host = os.getenv("DOCKER_DB_HOST", "host.docker.internal")
    if not _is_running_in_docker() and host == "host.docker.internal":
        host = "127.0.0.1"

    return {
        "host": host,
        "port": int(os.getenv("DB_PORT", os.getenv("LOCAL_DB_PORT", "5432"))),
        "database": os.getenv("DB_NAME", os.getenv("LOCAL_DB_NAME")),
        "user": os.getenv("DB_USER", os.getenv("LOCAL_DB_USER")),
        "password": os.getenv("DB_PASSWORD", os.getenv("LOCAL_DB_PASSWORD")),
        "sslmode": os.getenv("DB_SSLMODE", "require"),
    }


def _get_connection():
    params = _db_params()
    return psycopg2.connect(**params)


@dataclass(frozen=True)
class ChallengeRecord:
    wallet_address: str
    message: str
    expires_at: datetime


class ChallengeRequest(BaseModel):
    wallet_address: str = Field(min_length=42, max_length=42)


class VerifyRequest(BaseModel):
    challenge_id: str
    wallet_address: str = Field(min_length=42, max_length=42)
    signature: str


class UserGeneratedCardResponse(BaseModel):
    status: str
    message: str
    card: Dict[str, Any]
    remaining_available: int
    total_available: int


class RateLimitConfig(BaseModel):
    window_seconds: int
    max_requests: int


class SolanaWalletUpdateRequest(BaseModel):
    """Body for PUT /api/me/solana-wallet — empty/null clears the saved wallet."""

    solana_wallet: Optional[str] = None


class MintMyNftRequest(BaseModel):
    """Body for POST /api/me/mint — recipient comes from the saved Solana wallet on the session."""

    season_id: int


app = FastAPI(title="PolyStars User Web API", version="1.0.0")


def _allowed_origins() -> List[str]:
    """Explicit origins for credentialed CORS (HttpOnly cookie). Set USER_WEB_CORS_ORIGINS in production."""
    raw = os.getenv("USER_WEB_CORS_ORIGINS", "").strip()
    if raw:
        return [origin.strip() for origin in raw.split(",") if origin.strip()]
    if os.getenv("NODE_ENV", "development") == "development":
        return ["http://localhost:3001", "http://127.0.0.1:3001"]
    return []


app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_challenge_store: Dict[str, ChallengeRecord] = {}
_challenge_lock = threading.Lock()
CHALLENGE_TTL_SECONDS = int(os.getenv("USER_WEB_CHALLENGE_TTL_SECONDS", "300"))
season_manager = SeasonManager(use_local_db=True)
mint_service = SeasonWorkbenchService()
JWT_ALG = "HS256"
JWT_TTL_SECONDS = int(os.getenv("USER_WEB_JWT_TTL_SECONDS", "3600"))
JWT_ISSUER = os.getenv("USER_WEB_JWT_ISSUER", "polystars-user-web-backend")
JWT_AUDIENCE = os.getenv("USER_WEB_JWT_AUDIENCE", "polystars-user-web")


def _access_cookie_name() -> str:
    return os.getenv("USER_WEB_ACCESS_COOKIE_NAME", "polystars_user_access").strip() or "polystars_user_access"


def _cookie_secure_flag() -> bool:
    explicit = os.getenv("USER_WEB_COOKIE_SECURE", "").strip().lower()
    if explicit in ("1", "true", "yes", "on"):
        return True
    if explicit in ("0", "false", "no", "off"):
        return False
    return os.getenv("NODE_ENV", "development") != "development"


def _cookie_domain_value() -> Optional[str]:
    domain = os.getenv("USER_WEB_COOKIE_DOMAIN", "").strip()
    return domain or None


CARD_BASE_URL = (
    os.getenv("CARD_BASE_URL")
    or os.getenv("NEXT_PUBLIC_APP_URL")
    or "https://polystars.app"
).strip().rstrip("/")
GENESIS_START_DATE: Optional[str] = os.getenv("GENESIS_START_DATE", "").strip() or None
GENESIS_END_DATE: Optional[str] = os.getenv("GENESIS_END_DATE", "").strip() or None
RATE_LIMITS: Dict[str, RateLimitConfig] = {
    "/api/auth/wallet/challenge": RateLimitConfig(
        window_seconds=int(os.getenv("USER_WEB_RATE_LIMIT_WINDOW_SECONDS", "60")),
        max_requests=int(os.getenv("USER_WEB_RATE_LIMIT_CHALLENGE_MAX", "20")),
    ),
    "/api/auth/wallet/verify": RateLimitConfig(
        window_seconds=int(os.getenv("USER_WEB_RATE_LIMIT_WINDOW_SECONDS", "60")),
        max_requests=int(os.getenv("USER_WEB_RATE_LIMIT_VERIFY_MAX", "20")),
    ),
    "/api/auth/wallet/session": RateLimitConfig(
        window_seconds=int(os.getenv("USER_WEB_RATE_LIMIT_WINDOW_SECONDS", "60")),
        max_requests=int(os.getenv("USER_WEB_RATE_LIMIT_SESSION_MAX", "90")),
    ),
    "/api/auth/wallet/logout": RateLimitConfig(
        window_seconds=int(os.getenv("USER_WEB_RATE_LIMIT_WINDOW_SECONDS", "60")),
        max_requests=int(os.getenv("USER_WEB_RATE_LIMIT_LOGOUT_MAX", "40")),
    ),
    "/api/me/cards": RateLimitConfig(
        window_seconds=int(os.getenv("USER_WEB_RATE_LIMIT_WINDOW_SECONDS", "60")),
        max_requests=int(os.getenv("USER_WEB_RATE_LIMIT_USER_CARDS_MAX", "30")),
    ),
    "/api/cards/get": RateLimitConfig(
        window_seconds=int(os.getenv("USER_WEB_RATE_LIMIT_WINDOW_SECONDS", "60")),
        max_requests=int(os.getenv("USER_WEB_RATE_LIMIT_GET_CARD_MAX", "20")),
    ),
    "/api/me/solana-wallet": RateLimitConfig(
        window_seconds=int(os.getenv("USER_WEB_RATE_LIMIT_WINDOW_SECONDS", "60")),
        max_requests=int(os.getenv("USER_WEB_RATE_LIMIT_SOLANA_WALLET_MAX", "30")),
    ),
    "/api/me/eligibility": RateLimitConfig(
        window_seconds=int(os.getenv("USER_WEB_RATE_LIMIT_WINDOW_SECONDS", "60")),
        max_requests=int(os.getenv("USER_WEB_RATE_LIMIT_ELIGIBILITY_MAX", "30")),
    ),
    "/api/me/mint": RateLimitConfig(
        window_seconds=int(os.getenv("USER_WEB_RATE_LIMIT_WINDOW_SECONDS", "60")),
        max_requests=int(os.getenv("USER_WEB_RATE_LIMIT_MINT_MAX", "10")),
    ),
    "/api/cards/ticker": RateLimitConfig(
        window_seconds=int(os.getenv("USER_WEB_RATE_LIMIT_WINDOW_SECONDS", "60")),
        max_requests=int(os.getenv("USER_WEB_RATE_LIMIT_CARDS_TICKER_MAX", "40")),
    ),
    "/api/wallet-ticker": RateLimitConfig(
        window_seconds=int(os.getenv("USER_WEB_RATE_LIMIT_WINDOW_SECONDS", "60")),
        max_requests=int(os.getenv("USER_WEB_RATE_LIMIT_TICKER_MAX", "30")),
    ),
}
_rate_limit_lock = threading.Lock()
_rate_limit_store: Dict[str, deque[float]] = defaultdict(deque)
_wallet_actions_db_cache_lock = threading.Lock()
_wallet_actions_db_cache: Optional[Tuple[float, bool]] = None
_WALLET_ACTIONS_DB_CACHE_TTL_SECONDS = float(os.getenv("USER_WEB_WALLET_ACTIONS_DB_CACHE_TTL", "2"))
_cards_ticker_cache_lock = threading.Lock()
_cards_ticker_cache: Dict[Tuple[int, str], Tuple[float, Dict[str, Any]]] = {}
USER_WEB_CARDS_TICKER_CACHE_TTL_SECONDS = float(
    os.getenv("USER_WEB_CARDS_TICKER_CACHE_TTL_SECONDS", "30")
)
_winner_catalog_join_total_lock = threading.Lock()
_winner_catalog_join_total_cache: Tuple[float, int] = (0.0, 0)

# Rows that can ever produce a card (manual image present). Used for supply totals.
_WINNER_CATALOG_JOIN = """
FROM winner_wallets_nft_to_claim w
JOIN event_cards ec ON ec.event_id = w.event_id
WHERE ec.manual_image_url IS NOT NULL
  AND BTRIM(ec.manual_image_url) <> ''
"""

# Subset: catalog rows not yet used for a generated card (pick target for /api/cards/get).
_ELIGIBLE_WINNER_BASE = """
FROM winner_wallets_nft_to_claim w
JOIN event_cards ec ON ec.event_id = w.event_id
LEFT JOIN user_generated_cards gc ON gc.winner_row_id = w.id
WHERE ec.manual_image_url IS NOT NULL
  AND BTRIM(ec.manual_image_url) <> ''
  AND gc.id IS NULL
"""
POLYMARKET_GAMMA_API_BASE = os.getenv("POLYMARKET_GAMMA_API_BASE", "https://gamma-api.polymarket.com").rstrip("/")
POLYMARKET_DATA_API_BASE = os.getenv("POLYMARKET_DATA_API_BASE", "https://data-api.polymarket.com").rstrip("/")
POLYMARKET_REQUEST_TIMEOUT_SECONDS = float(os.getenv("POLYMARKET_REQUEST_TIMEOUT_SECONDS", "8"))
# Card SVG embeds remote manual_image_url — keep this tight so generation does not hang on slow CDNs.
USER_WEB_CARD_IMAGE_TIMEOUT_SECONDS = float(os.getenv("USER_WEB_CARD_IMAGE_TIMEOUT_SECONDS", "6"))
# Set USER_WEB_CARD_GET_TIMING=1 to log per-phase durations for POST /api/cards/get (find slow I/O).
USER_WEB_CARD_GET_TIMING = os.getenv("USER_WEB_CARD_GET_TIMING", "").strip().lower() in ("1", "true", "yes")
_user_web_timing_log_handler_installed = False
# COUNT(w JOIN ec) for “total claimable rows” is expensive; safe to cache briefly for UI supply fields.
USER_WEB_CARD_SUPPLY_JOIN_TOTAL_CACHE_TTL = float(os.getenv("USER_WEB_CARD_SUPPLY_JOIN_TOTAL_CACHE_TTL", "60"))
PM_NOT_REGISTERED_VALUE = "Not registered in PM"
NO_TRADES_YET_VALUE = "No trades yet"
# Sentinel trader_rank values that mean "no real Polymarket leaderboard rank yet".
# Used by both the eligibility response and the /api/me/mint guard so the rank
# requirement can be enforced consistently on client and server.
POLYMARKET_RANK_SENTINEL_VALUES = frozenset({
    PM_NOT_REGISTERED_VALUE.casefold(),
    NO_TRADES_YET_VALUE.casefold(),
})
# How often /api/auth/wallet/session is allowed to re-query Polymarket per wallet
# when the cached proxy_wallet is the "Not registered in PM" sentinel. Users who
# sign in BEFORE registering with Polymarket (or during a transient PM outage)
# get the sentinel cached in the DB; without this opportunistic refresh they
# would have to log out + log back in to see their real proxy wallet.
PM_SESSION_REFRESH_TTL_SECONDS = float(
    os.getenv("PM_SESSION_REFRESH_TTL_SECONDS", "60")
)
_pm_session_refresh_attempts: Dict[str, float] = {}
_pm_session_refresh_lock = threading.Lock()
# Solana / minted NFT lookup configuration. Used by /api/me/cards to render the
# user's actual on-chain NFTs (claims rows that finalized on Solana) instead of
# previewed/generated cards from user_generated_cards.
SOLANA_RPC_URL_FOR_EXPLORER = os.getenv("SOLANA_RPC_URL", "").strip()
SOLANA_NFT_METADATA_FETCH_TIMEOUT_SECONDS = float(
    os.getenv("USER_WEB_NFT_METADATA_FETCH_TIMEOUT_SECONDS", "6")
)
SOLANA_NFT_METADATA_CACHE_MAX_ENTRIES = int(
    os.getenv("USER_WEB_NFT_METADATA_CACHE_MAX_ENTRIES", "2000")
)
SOLANA_NFT_METADATA_PARALLEL_FETCHES = max(
    1, int(os.getenv("USER_WEB_NFT_METADATA_PARALLEL_FETCHES", "8"))
)
_nft_metadata_cache_lock = threading.Lock()
_nft_metadata_cache: "Dict[str, Dict[str, Optional[str]]]" = {}
# Full off-chain JSON metadata cache, keyed by URI. Mirrors
# ``_nft_metadata_cache`` but stores the entire parsed JSON object so the
# DAS-backed /api/me/cards endpoint can read ``polystars_card`` and any
# custom top-level fields without re-fetching from IPFS. Each entry is a
# (cached_at_monotonic, payload) tuple — payload is the parsed JSON for
# successful fetches and an empty dict for negative results. Negative
# entries are kept only for ``USER_WEB_NFT_METADATA_NEGATIVE_TTL_SECONDS``
# so a transient gateway 429/timeout does not permanently hide a card's
# image and slug until the container restarts.
_nft_metadata_full_cache_lock = threading.Lock()
_nft_metadata_full_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
NFT_METADATA_NEGATIVE_TTL_SECONDS = float(
    os.getenv("USER_WEB_NFT_METADATA_NEGATIVE_TTL_SECONDS", "60")
)
# Public IPFS gateways used as automatic fallbacks when the primary URL is
# rate-limited / down. Order matters — cheapest/fastest first. Pinata's
# public gateway is intentionally NOT in this list because it's the most
# common primary URL we want to fall AWAY from.
_IPFS_FALLBACK_GATEWAYS: Tuple[str, ...] = (
    "https://cloudflare-ipfs.com/ipfs/",
    "https://ipfs.io/ipfs/",
    "https://dweb.link/ipfs/",
    "https://nftstorage.link/ipfs/",
)
# Solana DAS (Digital Asset Standard) configuration. Used by /api/me/cards to
# enumerate the user's currently-owned NFTs from MASTER_COLLECTION_ADDRESS
# directly from on-chain state, so transferred/sold NFTs disappear from the
# dashboard automatically. SOLANA_DAS_RPC_URL must point to a DAS-compatible
# provider (Helius, Triton, Quicknode, Shyft, …); plain mainnet-beta does not
# implement searchAssets / getAssetsByOwner.
SOLANA_DAS_RPC_URL = (
    os.getenv("SOLANA_DAS_RPC_URL", "").strip()
    or os.getenv("SOLANA_RPC_URL", "").strip()
)
SOLANA_DAS_REQUEST_TIMEOUT_SECONDS = float(
    os.getenv("USER_WEB_SOLANA_DAS_REQUEST_TIMEOUT_SECONDS", "8")
)
SOLANA_DAS_PAGE_LIMIT = max(
    1, min(int(os.getenv("USER_WEB_SOLANA_DAS_PAGE_LIMIT", "200")), 1000)
)
SOLANA_DAS_MAX_PAGES = max(
    1, int(os.getenv("USER_WEB_SOLANA_DAS_MAX_PAGES", "10"))
)
ME_CARDS_ONCHAIN_CACHE_TTL_SECONDS = float(
    os.getenv("USER_WEB_ME_CARDS_ONCHAIN_CACHE_TTL_SECONDS", "20")
)
_me_cards_onchain_cache_lock = threading.Lock()
# (owner_solana_wallet_lower, collection_lower) -> (expires_at_monotonic, payload)
_me_cards_onchain_cache: Dict[Tuple[str, str], Tuple[float, Dict[str, Any]]] = {}
CLAIMS_UNIQUENESS_INDEX_NAME = "ux_claims_active_season_user_wallet_lower"

CARD_SEASON_TYPE_OPTIONS = ("standard", "genesis")
CARD_ENTRY_BRACKET_OPTIONS = (
    "[0.00 - 0.20]",
    "[0.20 - 0.40]",
    "[0.40 - 0.60]",
    "[0.60 - 0.80]",
    "[0.80 - 0.97]",
    "[0.97 - 1.00]",
)
CARD_TIER_OPTIONS = ("P99", "P90", "P70", "P50", "BASE")
CARD_ARCHETYPE_OPTIONS = (
    "ANOMALY",
    "ICARUS",
    "BOT",
    "BURNER",
    "SIGNAL",
    "VECTOR",
    "EQUILIBRIUM",
    "AMASSER",
    "EXTRACTOR",
    "PASSENGER",
    "SUBSTRATE",
    "OPERATOR",
)
LEGACY_ENTRY_BRACKET_MAP: Dict[str, str] = {
    "ANOMALY": "[0.00 - 0.20]",
    "ORACLE": "[0.20 - 0.40]",
    "OUTLIER": "[0.40 - 0.60]",
    "VECTOR": "[0.60 - 0.80]",
    "HARVESTER": "[0.80 - 0.97]",
    "EXTRACTOR": "[0.97 - 1.00]",
    "PASSENGER": "[0.97 - 1.00]",
}
LEGACY_ARCHETYPE_MAP: Dict[str, str] = {
    "HARVESTER": "EXTRACTOR",
    "MARTYR": "ICARUS",
}


def _cleanup_expired_challenges() -> None:
    while True:
        threading.Event().wait(60)
        now = datetime.now(timezone.utc)
        with _challenge_lock:
            expired = [cid for cid, rec in _challenge_store.items() if rec.expires_at < now]
            for cid in expired:
                del _challenge_store[cid]


threading.Thread(target=_cleanup_expired_challenges, daemon=True).start()


def _normalize_evm_address(value: str) -> str:
    if not Web3.is_address(value):
        raise HTTPException(status_code=400, detail="Invalid wallet address")
    return Web3.to_checksum_address(value)


def _build_challenge_message(wallet_address: str, nonce: str) -> str:
    issued_at = datetime.now(timezone.utc).isoformat()
    return (
        "Sign in to PolyStars user site\n"
        f"Wallet: {wallet_address}\n"
        f"Nonce: {nonce}\n"
        f"Issued At: {issued_at}"
    )


def _jwt_secret() -> str:
    secret = os.getenv("USER_WEB_JWT_SECRET", "").strip()
    if secret:
        return secret
    if os.getenv("NODE_ENV", "development") == "development":
        return "dev-only-insecure-secret-change-me"
    raise RuntimeError("USER_WEB_JWT_SECRET is required in non-development mode")


def _issue_access_token(wallet_address: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": wallet_address.lower(),
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=JWT_TTL_SECONDS)).timestamp()),
        "iss": JWT_ISSUER,
        "aud": JWT_AUDIENCE,
    }
    return jwt.encode(payload, _jwt_secret(), algorithm=JWT_ALG)


def _user_web_wallet_actions_env_disabled() -> bool:
    truthy = {"1", "true", "yes"}
    return (
        os.getenv("USER_WEB_WALLET_ACTIONS_DISABLED", "").strip().lower() in truthy
        or os.getenv("USER_WEB_DISABLE_ME_API", "").strip().lower() in truthy
    )


def _wallet_actions_disabled_from_db_cached() -> bool:
    """Reads polystars_user_web_controls; failures fall back to False (actions allowed)."""
    global _wallet_actions_db_cache
    if _WALLET_ACTIONS_DB_CACHE_TTL_SECONDS <= 0:
        _wallet_actions_db_cache = None
    now = monotonic()
    with _wallet_actions_db_cache_lock:
        if _wallet_actions_db_cache is not None:
            ts, val = _wallet_actions_db_cache
            if now - ts < _WALLET_ACTIONS_DB_CACHE_TTL_SECONDS:
                return val
    disabled = False
    try:
        conn = _get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT wallet_actions_disabled FROM polystars_user_web_controls WHERE singleton_id = 1"
                )
                row = cursor.fetchone()
                disabled = bool(row and row[0])
        finally:
            conn.close()
    except Exception:
        logger.exception("Could not read polystars_user_web_controls; allowing wallet actions")
        disabled = False
    with _wallet_actions_db_cache_lock:
        _wallet_actions_db_cache = (now, disabled)
    return disabled


def _wallet_actions_effective_disabled() -> bool:
    if _user_web_wallet_actions_env_disabled():
        return True
    return _wallet_actions_disabled_from_db_cached()


def _require_wallet_actions_enabled() -> None:
    if _wallet_actions_effective_disabled():
        raise HTTPException(
            status_code=503,
            detail="Wallet-linked actions are temporarily disabled",
        )


def _raw_access_token_from_request(request: Request) -> Optional[str]:
    """Prefer HttpOnly session cookie; fall back to Authorization Bearer (e.g. tooling)."""
    cookie_token = request.cookies.get(_access_cookie_name())
    if cookie_token:
        cookie_token = cookie_token.strip()
        if cookie_token:
            return cookie_token
    auth_header = request.headers.get("authorization", "").strip()
    if auth_header.lower().startswith("bearer "):
        bearer = auth_header.split(" ", 1)[1].strip()
        if bearer:
            return bearer
    return None


def _decode_access_token_to_wallet(token: str) -> Optional[str]:
    try:
        decoded = jwt.decode(
            token,
            _jwt_secret(),
            algorithms=[JWT_ALG],
            audience=JWT_AUDIENCE,
            issuer=JWT_ISSUER,
        )
    except Exception:
        return None
    token_type = str(decoded.get("type", ""))
    subject = str(decoded.get("sub", "")).strip().lower()
    if token_type != "access" or not subject:
        return None
    return subject


def _try_extract_wallet_from_cookie_or_bearer(request: Request) -> Optional[str]:
    raw = _raw_access_token_from_request(request)
    if not raw:
        return None
    return _decode_access_token_to_wallet(raw)


def _extract_wallet_from_request(request: Request) -> str:
    raw = _raw_access_token_from_request(request)
    if not raw:
        raise HTTPException(status_code=401, detail="Unauthorized")
    wallet = _decode_access_token_to_wallet(raw)
    if not wallet:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return wallet


def _warn_if_claims_uniqueness_index_missing() -> None:
    conn = _get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT to_regclass(%s)", (f"public.{CLAIMS_UNIQUENESS_INDEX_NAME}",))
            row = cursor.fetchone()
            exists = bool(row and row[0])
        if not exists:
            logger.warning(
                "Critical DB index %s is missing. Mint duplicate protection is weaker without it.",
                CLAIMS_UNIQUENESS_INDEX_NAME,
            )
    except Exception:
        logger.exception("Could not verify presence of index %s", CLAIMS_UNIQUENESS_INDEX_NAME)
    finally:
        conn.close()


def _ensure_user_solana_wallet_column() -> None:
    """Adds the optional solana_wallet column to user_wallet_signins for existing DBs."""
    conn = _get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                ALTER TABLE user_wallet_signins
                    ADD COLUMN IF NOT EXISTS solana_wallet TEXT
                """
            )
        conn.commit()
    except Exception:
        conn.rollback()
        logger.exception("Failed to ensure user_wallet_signins.solana_wallet column")
    finally:
        conn.close()


def _ensure_user_generated_cards_schema() -> None:
    conn = _get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS user_generated_cards (
                    id BIGSERIAL PRIMARY KEY,
                    slug TEXT NOT NULL UNIQUE,
                    owner_wallet VARCHAR(42) NOT NULL,
                    owner_proxy_wallet TEXT,
                    winner_row_id BIGINT NOT NULL UNIQUE,
                    season_id INTEGER NOT NULL,
                    event_id TEXT,
                    event_slug TEXT,
                    card_title TEXT,
                    primary_tag TEXT,
                    secondary_tag TEXT,
                    pattern TEXT,
                    front_image_path TEXT NOT NULL,
                    back_image_path TEXT NOT NULL,
                    card_payload_json JSONB NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    CONSTRAINT fk_generated_card_winner_row
                        FOREIGN KEY (winner_row_id) REFERENCES winner_wallets_nft_to_claim(id) ON DELETE CASCADE,
                    CONSTRAINT fk_generated_card_season
                        FOREIGN KEY (season_id) REFERENCES seasons(id) ON DELETE CASCADE,
                    CONSTRAINT generated_card_owner_wallet_format_check
                        CHECK (owner_wallet ~* '^0x[a-f0-9]{40}$')
                )
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_generated_cards_owner_wallet_lower
                ON user_generated_cards(LOWER(owner_wallet), created_at DESC)
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_generated_cards_created_at
                ON user_generated_cards(created_at DESC)
                """
            )
        conn.commit()
    except Exception:
        conn.rollback()
        logger.exception("Failed to ensure user_generated_cards schema")
        raise
    finally:
        conn.close()


def _update_generated_card_asset_urls(slug: str, front_url: str, back_url: str) -> None:
    conn = _get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE user_generated_cards
                SET front_image_path = %s,
                    back_image_path = %s
                WHERE slug = %s
                """,
                (front_url, back_url, slug),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        logger.exception("Failed to update generated card asset urls slug=%s", slug)
        raise
    finally:
        conn.close()


def _fmt_date_field(value: Any) -> Optional[str]:
    """Return ISO date string (YYYY-MM-DD) from a datetime/date/str DB value, or None."""
    if value is None:
        return None
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d")
    s = str(value).strip()
    return s[:10] if len(s) >= 10 else s or None


def _normalize_choice(raw: Optional[str], options: Tuple[str, ...], fallback: str) -> str:
    value = str(raw or "").strip().upper()
    for option in options:
        if option.upper() == value:
            return option
    return fallback


def _normalize_archetype(raw: Optional[str], inferred: str) -> str:
    """Map DB / legacy payloads (e.g. 'THE ANOMALY') to canonical CARD_ARCHETYPE_OPTIONS labels."""
    cleaned = str(raw or "").strip().upper()
    if cleaned.startswith("THE "):
        cleaned = cleaned[4:].strip()
    cleaned = LEGACY_ARCHETYPE_MAP.get(cleaned, cleaned)
    return _normalize_choice(cleaned, CARD_ARCHETYPE_OPTIONS, inferred)


def _normalize_entry_bracket(raw: Optional[str]) -> str:
    value = str(raw or "").strip().upper()
    if value in LEGACY_ENTRY_BRACKET_MAP:
        return LEGACY_ENTRY_BRACKET_MAP[value]
    return _normalize_choice(value, CARD_ENTRY_BRACKET_OPTIONS, "[0.80 - 0.97]")


def _to_float(raw: Any) -> Optional[float]:
    try:
        if raw is None or str(raw).strip() == "":
            return None
        return float(raw)
    except (TypeError, ValueError):
        return None


def _infer_archetype_from_metrics(
    entry_bracket: str,
    edge_raw: Optional[str],
    yield_raw: Optional[str],
    gravity_raw: Optional[str],
    entry_cwap_raw: Any = None,
    total_volume_raw: Any = None,
    total_pnl_raw: Any = None,
) -> str:
    edge = _normalize_choice(edge_raw, CARD_TIER_OPTIONS, "BASE")
    yld = _normalize_choice(yield_raw, CARD_TIER_OPTIONS, "BASE")
    grav = _normalize_choice(gravity_raw, CARD_TIER_OPTIONS, "BASE")
    entry_cwap = _to_float(entry_cwap_raw)
    total_volume = _to_float(total_volume_raw)
    total_pnl = _to_float(total_pnl_raw)

    if total_pnl is not None:
        if total_pnl < 0 and entry_cwap is not None:
            return "ICARUS" if entry_cwap < 0.60 else "BURNER"
        if total_pnl == 0:
            return "BOT"
    if entry_bracket == "[0.97 - 1.00]":
        if total_volume is not None and total_volume >= 5000:
            return "EXTRACTOR"
        if total_volume is not None and total_volume >= 50:
            return "PASSENGER"
        return "SUBSTRATE"

    if (
        entry_bracket != "[0.80 - 0.97]"
        and (
            (entry_bracket == "[0.00 - 0.20]" and edge == "P99" and yld == "P99" and grav == "P99")
            or (entry_bracket == "[0.20 - 0.40]" and edge == "P90" and yld == "P90" and grav == "P90")
            or (entry_bracket == "[0.40 - 0.60]" and edge == "P70" and yld == "P70" and grav == "P70")
            or (entry_bracket == "[0.60 - 0.80]" and edge == "P50" and yld == "P50" and grav == "P50")
        )
    ):
        return "ANOMALY"
    if (
        (entry_bracket == "[0.00 - 0.20]" or entry_bracket == "[0.20 - 0.40]")
        and (edge == "P99" or edge == "P90")
        and (yld == "P99" or yld == "P90")
    ):
        return "SIGNAL"
    if entry_bracket == "[0.40 - 0.60]" and (edge == "P99" or edge == "P90") and (yld == "P99" or yld == "P90"):
        return "VECTOR"
    if (
        (edge == "P99" or edge == "P90" or edge == "P70")
        and (yld == "P99" or yld == "P90" or yld == "P70")
        and (grav == "P99" or grav == "P90" or grav == "P70")
    ):
        return "EQUILIBRIUM"
    if grav == "P99" or grav == "P90":
        return "AMASSER"
    if (
        (entry_bracket == "[0.60 - 0.80]" or entry_bracket == "[0.80 - 0.97]")
        and (edge == "BASE" or edge == "P50")
        and (yld == "BASE" or yld == "P50")
        and (grav == "BASE" or grav == "P50" or grav == "P70")
    ):
        return "SUBSTRATE"
    return "OPERATOR"


def _normalize_proxy_wallet_for_compare(addr: Optional[str]) -> Optional[str]:
    """Lowercase 0x address for equality checks; None if not a 20-byte hex address."""
    raw = str(addr or "").strip()
    if not raw.startswith("0x") or len(raw) != 42:
        return None
    body = raw[2:]
    if not all(c in "0123456789abcdefABCDEF" for c in body):
        return None
    return raw.lower()


def _resolve_card_claim_type(winner_proxy_wallet: Optional[str], session_signin_proxy_wallet: Optional[str]) -> str:
    """SVG OWNERSHIP band: ORIGIN SECURED vs LOOTER TAKEOVER.

    - *Winner side*: `winner_wallets_nft_to_claim.proxy_wallet` (trader identity on the allocation row).
    - *Session side*: Polymarket `proxy_wallet` from `user_wallet_signins` for the **dashboard EOA** (JWT `sub`
      from Authorization — the wallet that signed in; never from request body or query params).

    When both are valid 0x addresses and equal (case-insensitive) → ``origin`` (ORIGIN SECURED), else ``looter``.
    """
    w = _normalize_proxy_wallet_for_compare(winner_proxy_wallet)
    c = _normalize_proxy_wallet_for_compare(session_signin_proxy_wallet)
    if w and c and w == c:
        return "origin"
    return "looter"


def _load_signin_proxy_for_session_wallet(cursor: Any, session_wallet_eoa: str) -> str:
    """Return Polymarket proxy bound to this session EOA at sign-in (same row dashboard auth uses).

    ``session_wallet_eoa`` must come only from verified JWT (e.g. ``_extract_wallet_from_request``).
    """
    cursor.execute(
        """
        SELECT proxy_wallet
        FROM user_wallet_signins
        WHERE LOWER(wallet_address) = LOWER(%s)
        LIMIT 1
        """,
        (session_wallet_eoa,),
    )
    row = cursor.fetchone()
    if not row:
        return PM_NOT_REGISTERED_VALUE
    raw = row.get("proxy_wallet") if isinstance(row, dict) else row[0]
    value = str(raw or "").strip()
    return value or PM_NOT_REGISTERED_VALUE


_PTIER_CSS_COLORS: Dict[str, str] = {
    "P999": "#FFD700",
    "P99":  "#FFD700",
    "P95":  "#FFBF00",
    "P90":  "#FFBF00",
    "P80":  "#265DD2",
    "P70":  "#265DD2",
    "P50":  "#38BE50",
    "BASE": "#B6BBC8",
}

def _border_css_color(yield_tier: str) -> str:
    """Return hex CSS color for the card border (resolves gradient tiers to gold fallback)."""
    return _PTIER_CSS_COLORS.get(str(yield_tier or "BASE").upper(), "#B6BBC8")


def _build_card_payload_from_source_row(
    row: Dict[str, Any],
    *,
    session_signin_proxy_wallet: Optional[str] = None,
) -> Dict[str, Any]:
    normalized_entry_bracket = _normalize_entry_bracket(row.get("entry_bracket"))
    normalized_edge = _normalize_choice(row.get("edge"), CARD_TIER_OPTIONS, "BASE")
    normalized_yield = _normalize_choice(row.get("yield"), CARD_TIER_OPTIONS, "BASE")
    normalized_gravity = _normalize_choice(row.get("gravity"), CARD_TIER_OPTIONS, "BASE")
    inferred_archetype = _infer_archetype_from_metrics(
        normalized_entry_bracket,
        normalized_edge,
        normalized_yield,
        normalized_gravity,
        row.get("entry_cwap"),
        row.get("total_volume"),
        row.get("total_pnl"),
    )
    normalized_archetype = _normalize_archetype(row.get("archetype"), inferred_archetype)
    rec_raw = row.get("reccurence")
    recurrence_out: Optional[str]
    if rec_raw is None:
        recurrence_out = None
    else:
        recurrence_out = str(rec_raw).strip() or None

    return {
        "season_type": _normalize_choice(row.get("season_type"), CARD_SEASON_TYPE_OPTIONS, "standard").lower(),
        "season_number": int(row.get("season_number") or 1),
        "recurrence": recurrence_out,
        "claim_type": _resolve_card_claim_type(
            str(row.get("proxy_wallet") or "").strip() or None,
            str(session_signin_proxy_wallet or "").strip() or None,
        ),
        "image_url": str(row.get("manual_image_url") or "").strip(),
        "card_title": str(row.get("card_title") or "").strip(),
        "card_lore": str(row.get("card_lore") or "").strip(),
        "primary_tag": str(row.get("primary_tag") or "UNKNOWN").strip() or "UNKNOWN",
        "primary_tag_color": str(row.get("primary_tag_hex_color") or "#FFFFFF").strip() or "#FFFFFF",
        "secondary_tag": str(row.get("secondary_tag") or "NONE").strip() or "NONE",
        "entry_bracket": normalized_entry_bracket,
        "archetype": normalized_archetype,
        "archetype_description": str(row.get("archetype_description") or "").strip(),
        "archetype_math": str(row.get("archetype_math") or "").strip(),
        "rarity_bracket": str(row.get("rarity_bracket") or "").strip(),
        "proxy_wallet": str(row.get("proxy_wallet") or "").strip(),
        "edge": normalized_edge,
        "yield": normalized_yield,
        "gravity": normalized_gravity,
        "border_color": _border_css_color(normalized_yield),
        "leaderboard_rank": int(row.get("rank") or 0),
        # Season meta for card back (dates, supply).
        # Genesis seasons use canonical dates from env vars (GENESIS_START_DATE / GENESIS_END_DATE).
        "season_start_date": (
            GENESIS_START_DATE
            if _normalize_choice(row.get("season_type"), CARD_SEASON_TYPE_OPTIONS, "standard").lower() == "genesis"
               and GENESIS_START_DATE
            else _fmt_date_field(row.get("season_start_date"))
        ),
        "season_end_date": (
            GENESIS_END_DATE
            if _normalize_choice(row.get("season_type"), CARD_SEASON_TYPE_OPTIONS, "standard").lower() == "genesis"
               and GENESIS_END_DATE
            else _fmt_date_field(row.get("season_end_date"))
        ),
        "season_size":       row.get("season_size"),
    }


def _generated_card_slug(season_type: Optional[str], season_number: Any) -> str:
    normalized_type = "".join(
        ch.lower() if ch.isalnum() else "-"
        for ch in str(season_type or "season").strip()
    ).strip("-") or "season"
    try:
        normalized_number = str(int(season_number))
    except Exception:
        normalized_number = "0"
    random_chunk = secrets.token_hex(16)
    uuid_chunk = uuid.uuid4().hex
    return f"card-{normalized_type}-s{normalized_number}-{random_chunk}-{uuid_chunk}"


def _count_winner_catalog_join(cursor: Any) -> int:
    cursor.execute(
        f"""
        SELECT COUNT(*) AS total_count
        {_WINNER_CATALOG_JOIN}
        """
    )
    row = cursor.fetchone()
    raw = (row.get("total_count") if isinstance(row, dict) else row[0]) if row else 0
    return int(raw or 0)


def _winner_catalog_join_total_cached(cursor: Any) -> int:
    global _winner_catalog_join_total_cache
    now = monotonic()
    with _winner_catalog_join_total_lock:
        ts, cached_total = _winner_catalog_join_total_cache
        if ts > 0 and (now - ts) < USER_WEB_CARD_SUPPLY_JOIN_TOTAL_CACHE_TTL:
            return cached_total
    total = _count_winner_catalog_join(cursor)
    with _winner_catalog_join_total_lock:
        _winner_catalog_join_total_cache = (monotonic(), total)
    return total


def _configure_user_web_timing_logging() -> None:
    """Uvicorn only configures its own loggers; app logger.info would be dropped (root level WARNING)."""
    global _user_web_timing_log_handler_installed
    if not USER_WEB_CARD_GET_TIMING or _user_web_timing_log_handler_installed:
        return
    pkg = logging.getLogger("user_web_backend")
    pkg.setLevel(logging.INFO)
    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter("%(levelname)s:%(name)s:%(message)s"))
    pkg.addHandler(handler)
    pkg.propagate = False
    _user_web_timing_log_handler_installed = True


def _log_card_get_phase(phase: str, phase_start: float) -> float:
    if USER_WEB_CARD_GET_TIMING:
        ms = (monotonic() - phase_start) * 1000.0
        logger.info("POST /api/cards/get phase=%s elapsed_ms=%.1f", phase, ms)
    return monotonic()


def _pick_eligible_winner_row_id(cursor: Any) -> Optional[int]:
    """Uniform random eligible row across all seasons (weights by per-season eligible count).

    Eligible rows are mostly ~333 per season with one larger season (~2k). Probing global
    min/max id skews toward dense id ranges; instead we pick a season with probability
    proportional to its eligible count, then RANDOM() within that season (small sets).
    """
    cursor.execute(
        f"""
        SELECT w.season_id AS season_id, COUNT(*)::bigint AS eligible_count
        {_ELIGIBLE_WINNER_BASE}
        GROUP BY w.season_id
        ORDER BY w.season_id
        """
    )
    season_rows = cursor.fetchall()
    if not season_rows:
        return None

    parsed: List[Tuple[Optional[int], int]] = []
    total = 0
    for r in season_rows:
        sid_raw = r.get("season_id") if isinstance(r, dict) else r[0]
        c_raw = r.get("eligible_count") if isinstance(r, dict) else r[1]
        c = int(c_raw or 0)
        if c <= 0:
            continue
        sid: Optional[int] = int(sid_raw) if sid_raw is not None else None
        parsed.append((sid, c))
        total += c
    if total <= 0 or not parsed:
        return None

    pick = secrets.randbelow(total)
    cumulative = 0
    chosen_season: Optional[int]
    for sid, c in parsed:
        if pick < cumulative + c:
            chosen_season = sid
            break
        cumulative += c
    else:
        return None

    season_filter = "AND w.season_id IS NULL" if chosen_season is None else "AND w.season_id = %s"
    season_params: Tuple[Any, ...] = () if chosen_season is None else (chosen_season,)

    for _ in range(8):
        cursor.execute(
            f"""
            SELECT w.id
            {_ELIGIBLE_WINNER_BASE}
              {season_filter}
            ORDER BY RANDOM()
            LIMIT 1
            FOR UPDATE OF w SKIP LOCKED
            """,
            season_params,
        )
        row = cursor.fetchone()
        if row:
            rid = row.get("id") if isinstance(row, dict) else row[0]
            return int(rid)
    return None


def _absolute_asset_url(request: Request, asset_path: str) -> str:
    if asset_path.startswith("http://") or asset_path.startswith("https://"):
        return asset_path
    return urllib.parse.urljoin(str(request.base_url), asset_path.lstrip("/"))


def _clone_cards_ticker_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "items": [dict(item) for item in list(payload.get("items") or [])],
        "total": int(payload.get("total") or 0),
        "fetched_at": str(payload.get("fetched_at") or ""),
    }


def _cards_ticker_cache_key(request: Request, safe_limit: int) -> Tuple[int, str]:
    return safe_limit, str(request.base_url)


def _get_cards_ticker_cached(request: Request, safe_limit: int) -> Optional[Dict[str, Any]]:
    if USER_WEB_CARDS_TICKER_CACHE_TTL_SECONDS <= 0:
        return None
    cache_key = _cards_ticker_cache_key(request, safe_limit)
    now = monotonic()
    with _cards_ticker_cache_lock:
        cached = _cards_ticker_cache.get(cache_key)
        if cached and (now - cached[0]) < USER_WEB_CARDS_TICKER_CACHE_TTL_SECONDS:
            return _clone_cards_ticker_payload(cached[1])
        if cached is not None:
            _cards_ticker_cache.pop(cache_key, None)
    return None


def _set_cards_ticker_cached(request: Request, safe_limit: int, payload: Dict[str, Any]) -> None:
    if USER_WEB_CARDS_TICKER_CACHE_TTL_SECONDS <= 0:
        return
    cache_key = _cards_ticker_cache_key(request, safe_limit)
    with _cards_ticker_cache_lock:
        _cards_ticker_cache[cache_key] = (monotonic(), _clone_cards_ticker_payload(payload))


def _remote_image_to_data_uri(image_url: str, *, timeout_seconds: Optional[float] = None) -> str:
    normalized = str(image_url or "").strip()
    if not normalized or normalized.startswith("data:"):
        return normalized
    if not normalized.startswith(("http://", "https://")):
        return normalized

    req = urllib.request.Request(
        normalized,
        method="GET",
        headers={
            "Accept": "image/*,*/*;q=0.8",
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
        },
    )
    effective_timeout = (
        POLYMARKET_REQUEST_TIMEOUT_SECONDS if timeout_seconds is None else float(timeout_seconds)
    )
    with urllib.request.urlopen(req, timeout=effective_timeout) as response:
        raw = response.read()
        if not raw:
            raise ValueError("Downloaded image is empty")
        content_type = str(response.headers.get_content_type() or "").strip().lower()

    guessed_type, _ = mimetypes.guess_type(normalized)
    mime_type = content_type if content_type and content_type != "application/octet-stream" else (guessed_type or "image/jpeg")
    encoded = base64.b64encode(raw).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _build_render_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    render_payload = dict(payload or {})
    image_url = str(render_payload.get("image_url") or "").strip()
    if image_url.startswith(("http://", "https://")):
        try:
            render_payload["image_url"] = _remote_image_to_data_uri(
                image_url,
                timeout_seconds=USER_WEB_CARD_IMAGE_TIMEOUT_SECONDS,
            )
        except Exception:
            logger.exception("Failed to embed generated card image url=%s", image_url)
    return render_payload


def _ensure_generated_card_assets_on_r2(row: Dict[str, Any]) -> Dict[str, Any]:
    slug = str(row.get("slug") or "").strip()
    if not slug:
        return row
    current_front = str(row.get("front_image_path") or "").strip()
    current_back = str(row.get("back_image_path") or "").strip()
    # Mint-time rows persist absolute IPFS URLs (Pinata gateway) directly in
    # *_image_path. They don't need to be re-hosted on R2 — the existing URLs
    # are already publicly servable, and re-rendering on every detail-page
    # load would waste a Chromium roundtrip per request.
    if (
        current_front.startswith(("http://", "https://"))
        and current_back.startswith(("http://", "https://"))
    ):
        return row
    base_url = r2_public_base_url()
    front_key = extract_r2_key_from_public_url(base_url, current_front)
    back_key = extract_r2_key_from_public_url(base_url, current_back)
    if front_key and back_key:
        return row

    payload_raw = row.get("card_payload_json")
    if isinstance(payload_raw, str):
        try:
            payload_raw = json.loads(payload_raw)
        except json.JSONDecodeError:
            payload_raw = {}
    if not isinstance(payload_raw, dict):
        return row

    render_payload = _build_render_payload(payload_raw)
    front_png, back_png = render_card_pngs(render_payload)
    front_url, back_url, _, _ = upload_card_assets_to_r2(slug, front_png, back_png)
    _update_generated_card_asset_urls(slug, front_url, back_url)
    row["front_image_path"] = front_url
    row["back_image_path"] = back_url
    return row


def _load_card_source_row(cursor: Any, winner_row_id: int) -> Optional[Dict[str, Any]]:
    cursor.execute(
        """
        SELECT
            w.id AS winner_row_id,
            w.season_id,
            s.type AS season_type,
            s.season_number,
            w.proxy_wallet,
            w.event_id,
            w.event_slug,
            e.title AS event_title,
            COALESCE(w.entry_cwap, p.entry_cwap) AS entry_cwap,
            COALESCE(w.total_volume, p.total_volume) AS total_volume,
            COALESCE(w.total_pnl, p.total_pnl) AS total_pnl,
            w.entry_bracket,
            COALESCE(w.archetype, p.archetype) AS archetype,
            COALESCE(w.archetype_description, p.archetype_description) AS archetype_description,
            COALESCE(w.archetype_math, p.archetype_math) AS archetype_math,
            COALESCE(w.rarity_bracket, p.rarity_bracket) AS rarity_bracket,
            w.edge,
            w.yield,
            w.gravity,
            w.rank,
            ec.reccurence,
            ec.manual_image_url,
            ec.card_title,
            ec.card_lore,
            ec.primary_tag,
            ec.secondary_tag,
            tp.hex_color AS primary_tag_hex_color,
            s.start_date  AS season_start_date,
            s.end_date    AS season_end_date,
            s.total_supply AS season_size
        FROM winner_wallets_nft_to_claim w
        JOIN event_cards ec ON ec.event_id = w.event_id
        LEFT JOIN LATERAL (
            SELECT
                p.entry_cwap,
                p.total_volume,
                p.total_pnl,
                p.archetype,
                p.archetype_description,
                p.archetype_math,
                p.rarity_bracket
            FROM participants p
            WHERE LOWER(p.proxy_wallet) = LOWER(w.proxy_wallet)
              AND (
                  (w.event_id IS NOT NULL AND p.event_id = w.event_id)
                  OR
                  (w.event_slug IS NOT NULL AND p.event_slug = w.event_slug)
              )
            ORDER BY
                CASE
                    WHEN w.event_id IS NOT NULL AND p.event_id = w.event_id THEN 0
                    WHEN w.event_slug IS NOT NULL AND p.event_slug = w.event_slug THEN 1
                    ELSE 2
                END,
                p.rank ASC NULLS LAST
            LIMIT 1
        ) p ON TRUE
        LEFT JOIN events e ON e.id = w.event_id
        LEFT JOIN seasons s ON s.id = w.season_id
        LEFT JOIN tags tp ON LOWER(BTRIM(tp.label)) = LOWER(BTRIM(ec.primary_tag))
        WHERE w.id = %s
          AND ec.manual_image_url IS NOT NULL
          AND BTRIM(ec.manual_image_url) <> ''
        LIMIT 1
        """,
        (winner_row_id,),
    )
    row = cursor.fetchone()
    return dict(row) if row else None


def _generated_cards_supply_counts(
    cursor: Any,
    *,
    use_cached_join_total: bool = True,
) -> Tuple[int, int]:
    if use_cached_join_total:
        total_available = _winner_catalog_join_total_cached(cursor)
    else:
        total_available = _count_winner_catalog_join(cursor)
    cursor.execute("SELECT COUNT(*) AS claimed_count FROM user_generated_cards")
    claimed_row = cursor.fetchone()
    claimed_count = int(
        (claimed_row.get("claimed_count") if isinstance(claimed_row, dict) else claimed_row[0]) if claimed_row else 0
    )
    remaining_available = max(total_available - claimed_count, 0)
    return total_available, remaining_available


def _format_generated_card_row(row: Dict[str, Any], request: Request, include_payload: bool = True) -> Dict[str, Any]:
    payload = _ensure_generated_card_assets_on_r2(dict(row))
    created_at = payload.get("created_at")
    if isinstance(created_at, datetime):
        payload["created_at"] = created_at.astimezone(timezone.utc).isoformat()
    payload["front_image_url"] = _absolute_asset_url(request, str(payload.get("front_image_path") or ""))
    payload["back_image_url"] = _absolute_asset_url(request, str(payload.get("back_image_path") or ""))
    if isinstance(payload.get("card_payload_json"), str):
        try:
            payload["card_payload_json"] = json.loads(payload["card_payload_json"])
        except json.JSONDecodeError:
            payload["card_payload_json"] = {}
    if not include_payload:
        payload.pop("card_payload_json", None)
    return payload


def _solana_explorer_cluster_suffix() -> str:
    """Returns the ?cluster=... suffix matching SOLANA_RPC_URL for explorer links."""
    rpc = SOLANA_RPC_URL_FOR_EXPLORER.lower()
    if "devnet" in rpc:
        return "?cluster=devnet"
    if "testnet" in rpc:
        return "?cluster=testnet"
    return ""


def _build_solana_explorer_asset_url(asset_address: str) -> Optional[str]:
    addr = (asset_address or "").strip()
    if not addr:
        return None
    return f"https://explorer.solana.com/address/{addr}{_solana_explorer_cluster_suffix()}"


def _build_solana_explorer_tx_url(tx_hash: str) -> Optional[str]:
    tx = (tx_hash or "").strip()
    if not tx:
        return None
    return f"https://explorer.solana.com/tx/{tx}{_solana_explorer_cluster_suffix()}"


def _build_magiceden_item_url(asset_address: str) -> Optional[str]:
    """Public Magic Eden item page for a Solana NFT mint/asset address.

    Magic Eden only indexes mainnet assets, so on devnet/testnet the link will
    404 — we still emit it for parity with the Explorer link; the frontend can
    decide whether to surface it based on the cluster.
    """
    addr = (asset_address or "").strip()
    if not addr:
        return None
    return f"https://magiceden.io/item-details/{addr}"


def _decode_data_uri_json(metadata_uri: str) -> Optional[Dict[str, Any]]:
    """Decode a data:application/json[;base64],... metadata URI used as Pinata fallback."""
    if not metadata_uri.startswith("data:"):
        return None
    try:
        header, _, body = metadata_uri.partition(",")
        if not body:
            return None
        if ";base64" in header:
            raw = base64.b64decode(body)
            text = raw.decode("utf-8", errors="replace")
        else:
            text = urllib.parse.unquote(body)
        return json.loads(text)
    except Exception:
        return None


def _extract_card_slug_from_polystars_card(polystars_card: Any) -> Optional[str]:
    """Extract the in-app card slug from the embedded ``polystars_card.qr_payload``.

    Mint-time payload shape (see ``scripts/polystars_card_payload.py``):
    ``qr_payload = "{CARD_BASE_URL}/cards/{slug}"`` — the slug is the last
    non-empty path segment. Returns ``None`` for any malformed input.
    """
    if not isinstance(polystars_card, dict):
        return None
    qr = str(polystars_card.get("qr_payload") or "").strip()
    if not qr:
        return None
    try:
        path = urllib.parse.urlparse(qr).path or qr
    except Exception:
        path = qr
    tail = path.rstrip("/").rsplit("/", 1)[-1]
    tail = tail.strip()
    return tail or None


# Match any Pinata-style IPFS gateway host so URLs written for private
# dedicated gateways (e.g. ``crimson-glamorous-dragon-957.mypinata.cloud``)
# are rewritten to the canonical public gateway. Without this rewrite the
# browser has to call the private gateway directly, which returns 403 unless
# a per-request ``pinataGatewayToken`` is supplied — something we can't
# reasonably bake into NFT metadata for end users.
_PINATA_GATEWAY_HOST_PATTERN = re.compile(
    r"^(?:gateway\.pinata\.cloud|[a-z0-9-]+\.mypinata\.cloud)$",
    re.IGNORECASE,
)
_CANONICAL_PINATA_GATEWAY = "https://gateway.pinata.cloud/ipfs/"


def _normalize_ipfs_gateway_url(value: Any) -> Optional[str]:
    """Rewrite Pinata / ipfs:// URLs to the canonical public Pinata gateway.

    Any ``*.mypinata.cloud/ipfs/<CID>`` URL (including private dedicated
    gateways that require a ``pinataGatewayToken``) and any ``ipfs://<CID>``
    URL is normalized to ``https://gateway.pinata.cloud/ipfs/<CID>`` so the
    browser can load the asset without per-gateway auth. Query strings
    (e.g. expired gateway tokens) are stripped. Non-Pinata / non-IPFS URLs
    are returned unchanged, and empty / non-string inputs return ``None``.
    """
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.startswith("ipfs://"):
        cid_and_path = raw[len("ipfs://"):].lstrip("/")
        return f"{_CANONICAL_PINATA_GATEWAY}{cid_and_path}" if cid_and_path else raw
    try:
        parsed = urllib.parse.urlparse(raw)
    except Exception:
        return raw
    if parsed.scheme not in {"http", "https"}:
        return raw
    if not _PINATA_GATEWAY_HOST_PATTERN.match(parsed.hostname or ""):
        return raw
    path = parsed.path or ""
    ipfs_marker = "/ipfs/"
    idx = path.find(ipfs_marker)
    if idx < 0:
        return raw
    cid_and_path = path[idx + len(ipfs_marker):].lstrip("/")
    if not cid_and_path:
        return raw
    return f"{_CANONICAL_PINATA_GATEWAY}{cid_and_path}"


def _extract_nft_visuals_from_metadata(metadata: Dict[str, Any]) -> Dict[str, Optional[str]]:
    """Pull (name, front, back, card_slug) details out of a Metaplex-style NFT metadata JSON.

    Mirrors the layout produced by ``SolanaClient._build_metadata_uri``: the
    primary front image is in ``image``, and ``properties.files`` lists ``[front, back]``
    so the back image is the first ``files[].uri`` that differs from ``image``.
    The in-app card slug is recovered from the embedded ``polystars_card.qr_payload``
    so minted STARs can deep-link to the same ``/cards/{slug}`` page used by previews.

    All image URLs are normalized through ``_normalize_ipfs_gateway_url`` so
    that metadata written against a private dedicated Pinata gateway still
    resolves on the public gateway for end users.
    """
    name = str(metadata.get("name") or "").strip() or None
    front = _normalize_ipfs_gateway_url(metadata.get("image"))
    back: Optional[str] = None
    properties = metadata.get("properties")
    if isinstance(properties, dict):
        files = properties.get("files")
        if isinstance(files, list):
            for entry in files:
                if not isinstance(entry, dict):
                    continue
                uri_value = _normalize_ipfs_gateway_url(entry.get("uri"))
                if not uri_value:
                    continue
                if front and uri_value == front:
                    continue
                back = uri_value
                break
    card_slug = _extract_card_slug_from_polystars_card(metadata.get("polystars_card"))
    return {
        "name": name,
        "front_image_url": front,
        "back_image_url": back,
        "card_slug": card_slug,
    }


def _resolve_nft_visuals_for_metadata_uri(metadata_uri: str) -> Dict[str, Optional[str]]:
    """Fetch (with caching) NFT image URLs + card slug by metadata URI.

    The cache is keyed by the URI itself; IPFS content is immutable so a single
    successful fetch can be reused across requests. Returns a dict with
    ``name``, ``front_image_url``, ``back_image_url``, ``card_slug`` (any of
    which may be ``None``). A negative result (failed fetch) is also cached as
    empty values to avoid hammering a slow gateway on every page load.
    """
    uri = (metadata_uri or "").strip()
    empty: Dict[str, Optional[str]] = {
        "name": None,
        "front_image_url": None,
        "back_image_url": None,
        "card_slug": None,
    }
    if not uri:
        return empty

    with _nft_metadata_cache_lock:
        cached = _nft_metadata_cache.get(uri)
        if cached is not None:
            return dict(cached)

    metadata: Optional[Dict[str, Any]] = None
    if uri.startswith("data:"):
        metadata = _decode_data_uri_json(uri)
    else:
        try:
            req = urllib.request.Request(
                uri,
                method="GET",
                headers={
                    "Accept": "application/json",
                    "User-Agent": "Mozilla/5.0 (PolyStars user-web)",
                },
            )
            with urllib.request.urlopen(req, timeout=SOLANA_NFT_METADATA_FETCH_TIMEOUT_SECONDS) as response:
                status_code = int(getattr(response, "status", 200))
                if 200 <= status_code < 300:
                    body = response.read().decode("utf-8", errors="replace")
                    if body:
                        try:
                            metadata = json.loads(body)
                        except json.JSONDecodeError:
                            metadata = None
        except Exception:
            metadata = None

    visuals = _extract_nft_visuals_from_metadata(metadata) if isinstance(metadata, dict) else dict(empty)

    with _nft_metadata_cache_lock:
        if len(_nft_metadata_cache) >= SOLANA_NFT_METADATA_CACHE_MAX_ENTRIES:
            # Drop one arbitrary entry; not LRU but bounded — enough for a
            # process-local cache backed by immutable IPFS content.
            try:
                _nft_metadata_cache.pop(next(iter(_nft_metadata_cache)))
            except StopIteration:
                pass
        _nft_metadata_cache[uri] = dict(visuals)

    return visuals


def _extract_ipfs_cid_path(uri: str) -> Optional[str]:
    """Extract the ``<cid>[/...path]`` portion from any IPFS-like URI.

    Recognises ``ipfs://``, ``ipfs://ipfs/``, and HTTPS gateway URLs that
    contain ``/ipfs/<cid>``. Returns ``None`` if the URI does not look like
    IPFS content (e.g. an Arweave URL or a regular HTTPS asset).
    """
    if not uri:
        return None
    text = uri.strip()
    lower = text.lower()
    if lower.startswith("ipfs://"):
        rest = text[len("ipfs://"):]
        if rest.lower().startswith("ipfs/"):
            rest = rest[len("ipfs/"):]
        return rest.lstrip("/") or None
    marker = "/ipfs/"
    idx = lower.find(marker)
    if idx >= 0:
        return text[idx + len(marker):].lstrip("/") or None
    return None


def _candidate_metadata_uris(uri: str) -> List[str]:
    """Return ``uri`` plus any alternative IPFS gateway URLs to try in order.

    Helps recover from transient 429 / 5xx / timeouts on a single gateway
    (most commonly Pinata's public ``gateway.pinata.cloud``). Because IPFS
    content is content-addressed by CID, every gateway returns the same
    bytes — we just need *any* of them to succeed.
    """
    candidates: List[str] = []
    if uri:
        candidates.append(uri)
    cid_path = _extract_ipfs_cid_path(uri)
    if cid_path:
        for gateway in _IPFS_FALLBACK_GATEWAYS:
            alt = gateway + cid_path
            if alt not in candidates:
                candidates.append(alt)
    return candidates


def _try_fetch_metadata_json(uri: str) -> Optional[Dict[str, Any]]:
    """Single-shot HTTP GET that returns parsed JSON or ``None`` on any failure.

    Treats any non-2xx, network error, or non-JSON body as a soft failure so
    the caller can move on to the next fallback gateway.
    """
    if not uri:
        return None
    if uri.startswith("data:"):
        return _decode_data_uri_json(uri)
    try:
        req = urllib.request.Request(
            uri,
            method="GET",
            headers={
                "Accept": "application/json",
                "User-Agent": "Mozilla/5.0 (PolyStars user-web)",
            },
        )
        with urllib.request.urlopen(
            req, timeout=SOLANA_NFT_METADATA_FETCH_TIMEOUT_SECONDS
        ) as response:
            status_code = int(getattr(response, "status", 200))
            if not (200 <= status_code < 300):
                return None
            body = response.read().decode("utf-8", errors="replace")
            if not body:
                return None
            try:
                parsed = json.loads(body)
            except json.JSONDecodeError:
                return None
            return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None


def _fetch_full_nft_metadata_for_uri(metadata_uri: str) -> Optional[Dict[str, Any]]:
    """Returns the parsed off-chain NFT metadata JSON, or ``None`` on failure.

    Unlike ``_resolve_nft_visuals_for_metadata_uri`` (which returns a small
    projection), this returns the full JSON object so callers can read the
    embedded ``polystars_card`` block, attributes, and any other custom
    top-level fields.

    Successful results are cached forever (IPFS content is immutable).
    Negative results are kept only for ``NFT_METADATA_NEGATIVE_TTL_SECONDS``
    so a single transient gateway 429/timeout does not permanently hide a
    card's image and slug. On a cold or expired entry we also try a small
    set of public IPFS fallback gateways before giving up.
    """
    uri = (metadata_uri or "").strip()
    if not uri:
        return None

    now = monotonic()
    with _nft_metadata_full_cache_lock:
        cached = _nft_metadata_full_cache.get(uri)
        if cached is not None:
            cached_at, payload = cached
            if payload:
                return payload
            if now - cached_at < NFT_METADATA_NEGATIVE_TTL_SECONDS:
                return None
            # Negative entry expired — fall through and refetch.

    metadata: Optional[Dict[str, Any]] = None
    for candidate in _candidate_metadata_uris(uri):
        result = _try_fetch_metadata_json(candidate)
        if isinstance(result, dict) and result:
            metadata = result
            break

    with _nft_metadata_full_cache_lock:
        if len(_nft_metadata_full_cache) >= SOLANA_NFT_METADATA_CACHE_MAX_ENTRIES:
            try:
                _nft_metadata_full_cache.pop(next(iter(_nft_metadata_full_cache)))
            except StopIteration:
                pass
        _nft_metadata_full_cache[uri] = (
            now,
            metadata if isinstance(metadata, dict) else {},
        )
    return metadata if isinstance(metadata, dict) else None


def _iso_to_epoch_seconds(value: Optional[str]) -> int:
    """Best-effort ISO-8601 → epoch seconds converter for stable sort ordering.

    Returns ``0`` for any unparseable / empty input so callers can use the
    result in a sort key without branching on ``None``.
    """
    text = str(value or "").strip()
    if not text:
        return 0
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
    except ValueError:
        return 0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    try:
        return int(dt.timestamp())
    except (OverflowError, OSError, ValueError):
        return 0


def _das_search_assets_by_owner_and_collection(
    owner_solana_wallet: str,
    collection_address: str,
) -> List[Dict[str, Any]]:
    """Returns all DAS Asset records owned by ``owner`` that belong to ``collection``.

    Uses the Metaplex DAS ``searchAssets`` RPC method. Iterates pages until
    the provider returns fewer items than the page size or until we hit the
    safety cap ``SOLANA_DAS_MAX_PAGES``. Raises ``HTTPException(503)`` if the
    RPC is misconfigured / unreachable / returns a JSON-RPC error so the
    caller can surface a clean "service unavailable" to the dashboard
    instead of silently rendering an empty list.

    The DAS endpoint must be a provider that implements DAS (Helius, Triton,
    Quicknode, Shyft, …); plain ``api.mainnet-beta.solana.com`` does NOT.
    """
    rpc_url = SOLANA_DAS_RPC_URL.strip()
    if not rpc_url:
        logger.error(
            "SOLANA_DAS_RPC_URL (or SOLANA_RPC_URL) is not configured; cannot list on-chain STARs"
        )
        raise HTTPException(
            status_code=503,
            detail="On-chain NFT lookup is not configured on the server",
        )
    owner = (owner_solana_wallet or "").strip()
    collection = (collection_address or "").strip()
    if not owner or not collection:
        return []

    aggregated: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()
    page = 1
    while page <= SOLANA_DAS_MAX_PAGES:
        body = json.dumps({
            "jsonrpc": "2.0",
            "id": f"polystars-me-cards-p{page}",
            "method": "searchAssets",
            "params": {
                "ownerAddress": owner,
                "grouping": ["collection", collection],
                "burnt": False,
                "page": page,
                "limit": SOLANA_DAS_PAGE_LIMIT,
            },
        }).encode("utf-8")
        req = urllib.request.Request(
            rpc_url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "PolyStars user-web/1.0 (+DAS me-cards)",
            },
        )
        try:
            with urllib.request.urlopen(
                req, timeout=SOLANA_DAS_REQUEST_TIMEOUT_SECONDS
            ) as response:
                status_code = int(getattr(response, "status", 200))
                raw = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            err_body = ""
            try:
                err_body = exc.read().decode("utf-8", errors="replace")[:300]
            except Exception:
                pass
            logger.warning(
                "DAS searchAssets HTTP error code=%s owner=%s collection=%s body=%s",
                exc.code,
                owner,
                collection,
                err_body,
            )
            raise HTTPException(
                status_code=503, detail="On-chain NFT lookup failed"
            ) from exc
        except (urllib.error.URLError, TimeoutError):
            logger.exception(
                "DAS searchAssets transport failure owner=%s collection=%s",
                owner,
                collection,
            )
            raise HTTPException(
                status_code=503, detail="On-chain NFT lookup failed"
            )

        if status_code < 200 or status_code >= 300:
            logger.warning(
                "DAS searchAssets non-2xx status=%s body=%s",
                status_code,
                raw[:300],
            )
            raise HTTPException(
                status_code=503, detail="On-chain NFT lookup failed"
            )

        try:
            envelope = json.loads(raw or "{}")
        except json.JSONDecodeError:
            logger.warning("DAS searchAssets returned invalid JSON: %s", raw[:300])
            raise HTTPException(
                status_code=503, detail="On-chain NFT lookup failed"
            )

        if not isinstance(envelope, dict):
            raise HTTPException(
                status_code=503, detail="On-chain NFT lookup failed"
            )
        if envelope.get("error"):
            logger.warning(
                "DAS searchAssets returned RPC error owner=%s collection=%s error=%s",
                owner,
                collection,
                envelope.get("error"),
            )
            raise HTTPException(
                status_code=503, detail="On-chain NFT lookup failed"
            )
        result = envelope.get("result") or {}
        items = result.get("items") if isinstance(result, dict) else None
        if not isinstance(items, list):
            items = []

        # Defensive de-dup: some providers return overlapping pages.
        for item in items:
            if not isinstance(item, dict):
                continue
            asset_id = str(item.get("id") or "").strip()
            if not asset_id or asset_id in seen_ids:
                continue
            seen_ids.add(asset_id)
            aggregated.append(item)

        if len(items) < SOLANA_DAS_PAGE_LIMIT:
            break
        page += 1

    return aggregated


def _extract_attribute_map(metadata: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Returns a flat ``{trait_type: value}`` mapping from a metadata JSON.

    Mirrors the attribute layout produced by ``SolanaClient._build_card_attributes``
    so the dashboard can hydrate ``season_type`` / ``season_number`` / ``phase``
    purely from on-chain metadata. Unknown trait types are preserved as-is.
    """
    if not isinstance(metadata, dict):
        return {}
    raw_attrs = metadata.get("attributes")
    if not isinstance(raw_attrs, list):
        return {}
    out: Dict[str, Any] = {}
    for entry in raw_attrs:
        if not isinstance(entry, dict):
            continue
        trait = str(entry.get("trait_type") or "").strip()
        if not trait:
            continue
        out[trait] = entry.get("value")
    return out


def _coerce_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        if isinstance(value, bool):
            return int(value)
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            return None


def _build_me_card_item_from_das_asset(
    asset: Dict[str, Any],
    metadata_json: Optional[Dict[str, Any]],
    claims_enrichment: Dict[str, Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Maps a DAS Asset (+ off-chain JSON + DB row) to /api/me/cards item shape.

    On-chain data is the source of truth for the *list* and for the visible
    card content (name, images, slug, season type/number, phase,
    collection_mint_number). The optional ``claims_enrichment`` lookup is
    used solely to add ``claim_id``, ``season_id``, ``tx_hash``, ``minted_at``
    when the same asset_address still has a matching DB row — these are not
    available from DAS and are nice-to-have for explorer/timestamp links.
    """
    asset_address = str(asset.get("id") or "").strip()
    if not asset_address:
        return None

    content = asset.get("content") if isinstance(asset.get("content"), dict) else {}
    metadata_inline = (
        content.get("metadata") if isinstance(content.get("metadata"), dict) else {}
    )
    json_uri = ""
    if isinstance(content, dict):
        json_uri = str(content.get("json_uri") or "").strip()

    # Prefer the freshly-fetched off-chain JSON because it contains the full
    # ``polystars_card`` block and any custom top-level fields. Fall back to
    # the metadata inline-projected by the DAS provider if the URI fetch
    # failed or returned nothing.
    metadata_source: Dict[str, Any] = {}
    if isinstance(metadata_json, dict) and metadata_json:
        metadata_source = metadata_json
    elif isinstance(metadata_inline, dict) and metadata_inline:
        metadata_source = metadata_inline

    visuals = _extract_nft_visuals_from_metadata(metadata_source) if metadata_source else {
        "name": None,
        "front_image_url": None,
        "back_image_url": None,
        "card_slug": None,
    }

    # If the off-chain JSON didn't yield images, fall back to DAS-supplied
    # links/files (provider may host its own image proxy). Normalize Pinata
    # gateway URLs here too so private gateway URLs emitted by the DAS
    # provider are rewritten to the canonical public gateway.
    if not visuals.get("front_image_url"):
        links = content.get("links") if isinstance(content.get("links"), dict) else {}
        if isinstance(links, dict):
            link_image = _normalize_ipfs_gateway_url(links.get("image"))
            if link_image:
                visuals["front_image_url"] = link_image
    if not visuals.get("front_image_url") or not visuals.get("back_image_url"):
        files = content.get("files") if isinstance(content.get("files"), list) else []
        for entry in files:
            if not isinstance(entry, dict):
                continue
            uri_value = _normalize_ipfs_gateway_url(entry.get("uri"))
            if not uri_value:
                continue
            if not visuals.get("front_image_url"):
                visuals["front_image_url"] = uri_value
                continue
            if uri_value != visuals.get("front_image_url") and not visuals.get("back_image_url"):
                visuals["back_image_url"] = uri_value

    if not visuals.get("name"):
        inline_name = str(metadata_inline.get("name") or "").strip() if isinstance(metadata_inline, dict) else ""
        if inline_name:
            visuals["name"] = inline_name

    attributes = _extract_attribute_map(metadata_source)
    season_type = (
        str(attributes.get("season_type") or "").strip().lower() or None
    )
    season_number = _coerce_int(attributes.get("season_number"))
    phase = (
        str(attributes.get("claim_type") or "").strip() or None
    )

    collection_mint_number: Optional[int] = None
    polystars_card = metadata_source.get("polystars_card") if metadata_source else None
    if isinstance(polystars_card, dict):
        collection_mint_number = _coerce_int(polystars_card.get("collection_mint_number"))

    ownership = asset.get("ownership") if isinstance(asset.get("ownership"), dict) else {}
    owner_address = (
        str(ownership.get("owner") or "").strip() or None if isinstance(ownership, dict) else None
    )

    enrichment = claims_enrichment.get(asset_address) or {}
    claim_id = enrichment.get("claim_id")
    season_id = enrichment.get("season_id")
    tx_hash = enrichment.get("tx_hash")
    minted_at_iso = enrichment.get("minted_at_iso")

    return {
        "claim_id": claim_id,
        "asset_address": asset_address,
        "tx_hash": tx_hash,
        "metadata_uri": json_uri or None,
        "recipient_solana_wallet": owner_address,
        "season_id": season_id,
        "season_type": season_type,
        "season_number": season_number,
        "phase": phase,
        "collection_mint_number": collection_mint_number,
        "name": visuals.get("name"),
        "front_image_url": visuals.get("front_image_url"),
        "back_image_url": visuals.get("back_image_url"),
        "card_slug": visuals.get("card_slug"),
        "explorer_asset_url": _build_solana_explorer_asset_url(asset_address),
        "explorer_tx_url": _build_solana_explorer_tx_url(tx_hash) if tx_hash else None,
        "minted_at": minted_at_iso,
    }


def _load_claims_enrichment_for_assets(
    asset_addresses: List[str],
) -> Dict[str, Dict[str, Any]]:
    """Best-effort lookup of DB-only fields (claim_id, season_id, tx_hash, minted_at).

    We intentionally don't filter by ``user_wallet`` here: the source of truth
    for ownership is on-chain. The DB enrichment is keyed purely on
    ``asset_address`` so even an NFT that was transferred to the current owner
    from another PolyStars user gets a tx_hash/minted_at if those exist in our
    claims table.
    """
    cleaned = [str(addr or "").strip() for addr in asset_addresses if str(addr or "").strip()]
    if not cleaned:
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    conn = _get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
            cursor.execute(
                """
                SELECT
                    c.id AS claim_id,
                    c.asset_address,
                    c.season_id,
                    c.tx_hash,
                    c.timestamp AS minted_at
                FROM claims c
                WHERE c.asset_address = ANY(%s)
                  AND c.status = 'COMPLETED'
                """,
                (cleaned,),
            )
            for row in cursor.fetchall():
                addr = str(row.get("asset_address") or "").strip()
                if not addr:
                    continue
                minted_at = row.get("minted_at")
                minted_at_iso: Optional[str] = None
                if isinstance(minted_at, datetime):
                    minted_at_iso = minted_at.astimezone(timezone.utc).isoformat()
                out[addr] = {
                    "claim_id": _coerce_int(row.get("claim_id")),
                    "season_id": _coerce_int(row.get("season_id")),
                    "tx_hash": (str(row.get("tx_hash") or "").strip() or None),
                    "minted_at_iso": minted_at_iso,
                }
    except Exception:
        # Enrichment is best-effort. A DB hiccup must not break the on-chain
        # listing — the caller will simply render those auxiliary fields as
        # null.
        logger.exception("Failed to enrich on-chain STARs from claims table")
        return {}
    finally:
        conn.close()
    return out


def _fetch_polymarket_public_profile(wallet_address: str) -> Dict[str, Any]:
    query = urllib.parse.urlencode({"address": wallet_address})
    url = f"{POLYMARKET_GAMMA_API_BASE}/public-profile?{query}"
    req = urllib.request.Request(
        url,
        method="GET",
        headers={
            "Accept": "application/json",
            # Avoid default Python-urllib UA that is often blocked by WAF/CDN.
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=POLYMARKET_REQUEST_TIMEOUT_SECONDS) as response:
            status_code = int(getattr(response, "status", 200))
            raw_body = response.read().decode("utf-8", errors="replace")
            if status_code < 200 or status_code >= 300:
                raise HTTPException(status_code=502, detail="Polymarket profile request failed")
            payload = json.loads(raw_body or "{}")
            if not isinstance(payload, dict):
                raise HTTPException(status_code=502, detail="Invalid Polymarket profile response")
            return payload
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        if exc.code in {403, 404}:
            logger.warning(
                "Polymarket profile unavailable code=%s wallet=%s; using fallback proxy marker",
                exc.code,
                wallet_address,
            )
            return {}
        logger.warning(
            "Polymarket profile HTTP error code=%s wallet=%s body=%s",
            exc.code,
            wallet_address,
            body[:300],
        )
        raise HTTPException(status_code=502, detail="Polymarket profile request failed")
    except urllib.error.URLError:
        logger.exception("Polymarket profile request failed wallet=%s", wallet_address)
        raise HTTPException(status_code=502, detail="Polymarket profile request failed")
    except TimeoutError:
        logger.exception("Polymarket profile request timeout wallet=%s", wallet_address)
        raise HTTPException(status_code=504, detail="Polymarket profile request timeout")
    except json.JSONDecodeError:
        logger.exception("Polymarket profile response parse failed wallet=%s", wallet_address)
        raise HTTPException(status_code=502, detail="Invalid Polymarket profile response")


def _proxy_wallet_from_profile(profile: Dict[str, Any]) -> Optional[str]:
    value = str(profile.get("proxyWallet") or "").strip()
    return value or None


def _load_proxy_wallet_for_user_wallet(user_wallet_address: str) -> str:
    conn = _get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT proxy_wallet
                FROM user_wallet_signins
                WHERE lower(wallet_address) = lower(%s)
                LIMIT 1
                """,
                (user_wallet_address,),
            )
            row = cursor.fetchone()
            if not row:
                return PM_NOT_REGISTERED_VALUE
            value = str(row[0] or "").strip()
            return value or PM_NOT_REGISTERED_VALUE
    finally:
        conn.close()


def _has_valid_polymarket_rank(trader_rank: Optional[str]) -> bool:
    """Returns True only when ``trader_rank`` is a real Polymarket leaderboard rank.

    Empty/null values, "Not registered in PM" and "No trades yet" all mean the
    user does not have a leaderboard rank yet. Used for UI surfaces only —
    minting eligibility uses ``_is_registered_on_polymarket`` because users
    that are registered with Polymarket but haven't traded yet must still be
    allowed to mint.
    """
    if trader_rank is None:
        return False
    value = str(trader_rank).strip()
    if not value:
        return False
    return value.casefold() not in POLYMARKET_RANK_SENTINEL_VALUES


def _is_registered_on_polymarket(proxy_wallet: Optional[str]) -> bool:
    """Returns True when ``proxy_wallet`` is a real Polymarket proxy address.

    Polymarket assigns each registered EVM wallet a deterministic proxy. We
    persist that value in ``user_wallet_signins.proxy_wallet`` at sign-in; if
    Polymarket has no profile for the wallet we instead persist the sentinel
    string ``PM_NOT_REGISTERED_VALUE``. So "registered" simply means the
    stored value is non-empty and not the sentinel.
    """
    if proxy_wallet is None:
        return False
    value = str(proxy_wallet).strip()
    if not value:
        return False
    return value.casefold() != PM_NOT_REGISTERED_VALUE.casefold()


def _maybe_refresh_polymarket_session_snapshot(
    wallet_address: str,
    cached_proxy_wallet: Optional[str],
    cached_trader_rank: Optional[str],
) -> Tuple[Optional[str], Optional[str]]:
    """Opportunistically re-resolve PM proxy/rank when the DB has the "not registered" sentinel.

    Background: ``/api/auth/wallet/verify`` resolves the Polymarket profile at
    sign-in time and caches the result in ``user_wallet_signins``. If the user
    signed in *before* registering on Polymarket (or while the PM gamma API was
    unavailable), the cached value is the sentinel ``PM_NOT_REGISTERED_VALUE``
    and ``/api/auth/wallet/session`` would keep returning it forever — the only
    way to refresh was to log out and log back in.

    To make this self-healing, we re-fetch the profile here when the cached
    value is the sentinel, throttled per-wallet by
    ``PM_SESSION_REFRESH_TTL_SECONDS`` so we don't hammer the PM API on every
    page hit for users who genuinely aren't registered.

    Returns the (possibly updated) ``(proxy_wallet, trader_rank)`` tuple and,
    on success, persists the new values to ``user_wallet_signins``.
    """
    # Real proxy already cached → nothing to refresh.
    if _is_registered_on_polymarket(cached_proxy_wallet):
        return cached_proxy_wallet, cached_trader_rank

    key = wallet_address.lower()
    now = monotonic()
    with _pm_session_refresh_lock:
        last_attempt = _pm_session_refresh_attempts.get(key, 0.0)
        if now - last_attempt < PM_SESSION_REFRESH_TTL_SECONDS:
            return cached_proxy_wallet, cached_trader_rank
        _pm_session_refresh_attempts[key] = now

    try:
        profile = _fetch_polymarket_public_profile(wallet_address)
    except Exception as exc:
        logger.warning(
            "Session-time PM refresh failed wallet=%s: %s",
            wallet_address,
            str(exc),
        )
        return cached_proxy_wallet, cached_trader_rank

    new_proxy = _proxy_wallet_from_profile(profile)
    if not new_proxy:
        # Still not registered on Polymarket — keep the sentinel as-is and let
        # the throttle window expire before we try again.
        return cached_proxy_wallet, cached_trader_rank

    leaderboard_rank, leaderboard_api_available = _fetch_polymarket_trader_rank(new_proxy)
    if leaderboard_api_available:
        new_trader_rank = leaderboard_rank or NO_TRADES_YET_VALUE
    else:
        new_trader_rank = NO_TRADES_YET_VALUE

    try:
        conn = _get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE user_wallet_signins
                    SET proxy_wallet = %s, trader_rank = %s
                    WHERE lower(wallet_address) = lower(%s)
                    """,
                    (new_proxy, new_trader_rank, wallet_address),
                )
            conn.commit()
        finally:
            conn.close()
        logger.info(
            "Session-time PM refresh updated wallet=%s proxy=%s trader_rank=%s",
            wallet_address,
            new_proxy,
            new_trader_rank,
        )
    except Exception:
        logger.exception(
            "Failed to persist session-time PM refresh wallet=%s", wallet_address
        )

    return new_proxy, new_trader_rank


def _load_wallet_signin_snapshot(user_wallet_address: str) -> Tuple[str, str]:
    conn = _get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT proxy_wallet, trader_rank
                FROM user_wallet_signins
                WHERE lower(wallet_address) = lower(%s)
                LIMIT 1
                """,
                (user_wallet_address,),
            )
            row = cursor.fetchone()
            if not row:
                return PM_NOT_REGISTERED_VALUE, NO_TRADES_YET_VALUE
            proxy_wallet = str(row[0] or "").strip() or PM_NOT_REGISTERED_VALUE
            trader_rank = str(row[1] or "").strip() or NO_TRADES_YET_VALUE
            return proxy_wallet, trader_rank
    finally:
        conn.close()


def _validate_solana_address(raw: str) -> str:
    """Validates a Solana base58 pubkey and returns its canonical form."""
    candidate = (raw or "").strip()
    if not candidate:
        raise HTTPException(status_code=400, detail="Solana wallet is required")
    if Pubkey is None:
        raise HTTPException(
            status_code=500,
            detail="Solana support is unavailable on this server (solders not installed)",
        )
    try:
        pubkey = Pubkey.from_string(candidate)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid Solana wallet address")
    return str(pubkey)


def _load_user_solana_wallet(user_wallet_address: str) -> Optional[str]:
    conn = _get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT solana_wallet
                FROM user_wallet_signins
                WHERE wallet_address = %s
                """,
                (user_wallet_address.lower(),),
            )
            row = cursor.fetchone()
    finally:
        conn.close()
    if not row:
        return None
    value = row[0]
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _save_user_solana_wallet(user_wallet_address: str, solana_wallet: Optional[str]) -> None:
    """Persist (or clear) the Solana recipient for the given EVM session wallet."""
    normalized_evm = user_wallet_address.lower()
    conn = _get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO user_wallet_signins (
                    wallet_address, first_seen_at, last_signed_in_at, sign_in_count, solana_wallet
                ) VALUES (%s, NOW(), NOW(), 1, %s)
                ON CONFLICT (wallet_address)
                DO UPDATE SET solana_wallet = EXCLUDED.solana_wallet
                """,
                (normalized_evm, solana_wallet),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        logger.exception("Failed to save user solana wallet for %s", normalized_evm)
        raise HTTPException(status_code=500, detail="Failed to save Solana wallet")
    finally:
        conn.close()


def _load_wallet_session_row(user_wallet_address: str) -> Optional[Dict[str, Any]]:
    conn = _get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
            cursor.execute(
                """
                SELECT sign_in_count, proxy_wallet, trader_rank, solana_wallet
                FROM user_wallet_signins
                WHERE LOWER(wallet_address) = LOWER(%s)
                LIMIT 1
                """,
                (user_wallet_address,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            return dict(row)
    finally:
        conn.close()


def _load_trader_rank_for_user_wallet(user_wallet_address: str) -> str:
    conn = _get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT trader_rank
                FROM user_wallet_signins
                WHERE lower(wallet_address) = lower(%s)
                LIMIT 1
                """,
                (user_wallet_address,),
            )
            row = cursor.fetchone()
            if not row:
                return NO_TRADES_YET_VALUE
            value = str(row[0] or "").strip()
            return value or NO_TRADES_YET_VALUE
    finally:
        conn.close()


def _fetch_polymarket_trader_rank(proxy_wallet: str) -> Tuple[Optional[str], bool]:
    if not Web3.is_address(proxy_wallet):
        return None, True
    query = urllib.parse.urlencode(
        {
            "category": "OVERALL",
            "timePeriod": "ALL",
            "orderBy": "PNL",
            "user": Web3.to_checksum_address(proxy_wallet),
            "limit": "1",
            "offset": "0",
        }
    )
    url = f"{POLYMARKET_DATA_API_BASE}/v1/leaderboard?{query}"
    req = urllib.request.Request(
        url,
        method="GET",
        headers={
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=POLYMARKET_REQUEST_TIMEOUT_SECONDS) as response:
            status_code = int(getattr(response, "status", 200))
            raw_body = response.read().decode("utf-8", errors="replace")
            if status_code < 200 or status_code >= 300:
                logger.warning(
                    "Polymarket leaderboard non-2xx status=%s proxy_wallet=%s",
                    status_code,
                    proxy_wallet,
                )
                return None, False
            payload = json.loads(raw_body or "[]")
            if not isinstance(payload, list) or len(payload) == 0:
                return None, True
            first_entry = payload[0]
            if not isinstance(first_entry, dict):
                return None, False
            rank = str(first_entry.get("rank") or "").strip()
            return (rank or None), True
    except urllib.error.HTTPError as exc:
        logger.warning(
            "Polymarket leaderboard HTTP error code=%s proxy_wallet=%s",
            exc.code,
            proxy_wallet,
        )
        return None, False
    except urllib.error.URLError:
        logger.warning("Polymarket leaderboard network error proxy_wallet=%s", proxy_wallet)
        return None, False
    except TimeoutError:
        logger.warning("Polymarket leaderboard timeout proxy_wallet=%s", proxy_wallet)
        return None, False
    except json.JSONDecodeError:
        logger.warning("Polymarket leaderboard invalid JSON proxy_wallet=%s", proxy_wallet)
        return None, False
    except Exception:
        logger.warning("Polymarket leaderboard request failed for proxy wallet=%s", proxy_wallet)
        return None, False


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    cfg = RATE_LIMITS.get(request.url.path)
    if cfg is not None:
        client_ip = request.client.host if request.client else "unknown"
        wallet_scope = ""
        bucket_key = f"{request.url.path}:{client_ip}:{wallet_scope}"
        now = monotonic()
        with _rate_limit_lock:
            bucket = _rate_limit_store[bucket_key]
            while bucket and now - bucket[0] > cfg.window_seconds:
                bucket.popleft()
            if len(bucket) >= cfg.max_requests:
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Too many requests. Please retry shortly."},
                )
            bucket.append(now)
    return await call_next(request)


@app.on_event("startup")
def startup_checks() -> None:
    _configure_user_web_timing_logging()
    _warn_if_claims_uniqueness_index_missing()
    _ensure_user_generated_cards_schema()
    _ensure_user_solana_wallet_column()
    if os.getenv("NODE_ENV", "development") != "development" and not _allowed_origins():
        logger.warning(
            "USER_WEB_CORS_ORIGINS is empty: set it to your user web origins (e.g. https://app.example.com) "
            "so browsers can send the session cookie."
        )


@app.get("/api/health")
def health():
    return {"ok": True, "service": "user_web_backend"}


@app.get("/api/server-time")
def server_time() -> Dict[str, str]:
    return {"now_utc_iso": datetime.now(timezone.utc).isoformat()}


@app.get("/api/public/site-status")
def public_site_status() -> Dict[str, bool]:
    """Always available — used by the frontend to show maintenance UI (not subject to wallet-actions lock)."""
    return {"wallet_actions_disabled": _wallet_actions_effective_disabled()}


@app.get("/api/seasons/active")
def active_seasons() -> List[Dict[str, Any]]:
    try:
        conn = _get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, type, season_number, total_supply, remaining_supply, end_date, is_active
                    FROM seasons
                    WHERE is_active = TRUE
                    ORDER BY
                        CASE WHEN type = 'genesis' THEN 0 ELSE 1 END,
                        season_number DESC,
                        id DESC
                    """
                )
                rows = cursor.fetchall()
        finally:
            conn.close()

        result: List[Dict[str, Any]] = []
        for row in rows:
            season_type = str(row[1])
            season_number = int(row[2])
            if season_type == "genesis":
                title = "Genesis"
                short_description = "Genesis season: launch season with no fixed end date."
            else:
                title = f"{season_type.capitalize()} #{season_number}"
                short_description = "Standard season: regular cycle with a scheduled finish."

            end_date = row[5]
            phase = "unknown"
            phase_reason = "Phase unavailable"
            try:
                phase_info = season_manager.get_current_phase(int(row[0]))
                phase = str(phase_info.get("phase", "unknown"))
                phase_reason = str(phase_info.get("reason", ""))
            except Exception:
                # Keep endpoint resilient even if phase resolution fails for a row.
                pass

            result.append(
                {
                    "id": int(row[0]),
                    "type": season_type,
                    "season_number": season_number,
                    "title": title,
                    "short_description": short_description,
                    "total_supply": int(row[3]),
                    "remaining_supply": int(row[4]),
                    "end_date": end_date.isoformat() if end_date else None,
                    "is_active": bool(row[6]),
                    "phase": phase,
                    "phase_reason": phase_reason,
                }
            )
        return result
    except Exception:
        logger.exception("Failed to load active seasons")
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")


@app.get("/api/wallet-ticker")
def wallet_ticker(limit: int = 100) -> Dict[str, Any]:
    safe_limit = max(1, min(int(limit), 200))
    conn = _get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT proxy_wallet AS wallet_address
                FROM (
                    SELECT DISTINCT lower(proxy_wallet) AS proxy_wallet
                    FROM winner_wallets_nft_to_claim
                    WHERE proxy_wallet IS NOT NULL
                ) t
                ORDER BY random()
                LIMIT %s
                """,
                (safe_limit,),
            )
            rows = cursor.fetchall()
    except Exception:
        logger.exception("Failed to load wallet ticker addresses")
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    finally:
        conn.close()

    wallets = [str(row[0]) for row in rows if row and row[0]]
    return {
        "wallets": wallets,
        "total": len(wallets),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/api/auth/wallet/challenge")
def wallet_challenge(payload: ChallengeRequest):
    _require_wallet_actions_enabled()
    wallet_address = _normalize_evm_address(payload.wallet_address)
    nonce = secrets.token_hex(16)
    challenge_id = str(uuid.uuid4())
    message = _build_challenge_message(wallet_address, nonce)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=CHALLENGE_TTL_SECONDS)

    with _challenge_lock:
        _challenge_store[challenge_id] = ChallengeRecord(
            wallet_address=wallet_address,
            message=message,
            expires_at=expires_at,
        )

    return {
        "challenge_id": challenge_id,
        "message": message,
        "expires_at": expires_at.isoformat(),
    }


@app.post("/api/auth/wallet/verify")
def wallet_verify(payload: VerifyRequest):
    _require_wallet_actions_enabled()
    wallet_address = _normalize_evm_address(payload.wallet_address)
    with _challenge_lock:
        challenge = _challenge_store.get(payload.challenge_id)
        if challenge is None:
            raise HTTPException(status_code=400, detail="Unknown challenge")
        if challenge.expires_at < datetime.now(timezone.utc):
            _challenge_store.pop(payload.challenge_id, None)
            raise HTTPException(status_code=400, detail="Challenge expired")
        if challenge.wallet_address.lower() != wallet_address.lower():
            raise HTTPException(status_code=400, detail="Challenge wallet mismatch")

    encoded = encode_defunct(text=challenge.message)
    try:
        recovered_address = Account.recover_message(encoded, signature=payload.signature)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid signature") from exc

    if recovered_address.lower() != wallet_address.lower():
        raise HTTPException(status_code=401, detail="Signature verification failed")

    stored_proxy_wallet, stored_trader_rank = _load_wallet_signin_snapshot(wallet_address)
    proxy_wallet = stored_proxy_wallet
    trader_rank = stored_trader_rank
    profile_freshly_resolved = False
    try:
        profile = _fetch_polymarket_public_profile(wallet_address)
        profile_freshly_resolved = True
        proxy_wallet = _proxy_wallet_from_profile(profile) or PM_NOT_REGISTERED_VALUE
    except Exception as exc:
        # Keep the last known DB snapshot when PM profile API is unavailable.
        logger.warning(
            "Could not resolve Polymarket proxy wallet for sign-in wallet=%s: %s",
            wallet_address,
            str(exc),
        )
    if profile_freshly_resolved and proxy_wallet == PM_NOT_REGISTERED_VALUE:
        trader_rank = NO_TRADES_YET_VALUE
    elif proxy_wallet != PM_NOT_REGISTERED_VALUE:
        leaderboard_rank, leaderboard_api_available = _fetch_polymarket_trader_rank(proxy_wallet)
        if leaderboard_api_available:
            trader_rank = leaderboard_rank or NO_TRADES_YET_VALUE
        else:
            # Keep last known rank from DB when leaderboard API is unavailable.
            trader_rank = stored_trader_rank

    conn = _get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO user_wallet_signins (wallet_address, first_seen_at, last_signed_in_at, sign_in_count, proxy_wallet, trader_rank)
                VALUES (%s, NOW(), NOW(), 1, %s, %s)
                ON CONFLICT (wallet_address)
                DO UPDATE SET
                    last_signed_in_at = NOW(),
                    sign_in_count = user_wallet_signins.sign_in_count + 1,
                    proxy_wallet = EXCLUDED.proxy_wallet,
                    trader_rank = EXCLUDED.trader_rank
                RETURNING wallet_address, first_seen_at, last_signed_in_at, sign_in_count, proxy_wallet, trader_rank
                """,
                (wallet_address, proxy_wallet, trader_rank),
            )
            row = cursor.fetchone()
        conn.commit()
    except Exception:
        conn.rollback()
        logger.exception("Failed to persist wallet sign-in")
        raise
    finally:
        conn.close()

    with _challenge_lock:
        _challenge_store.pop(payload.challenge_id, None)

    access_token = _issue_access_token(wallet_address)
    body: Dict[str, Any] = {
        "signed_in": True,
        "wallet_address": row[0],
        "first_seen_at": row[1].isoformat(),
        "last_signed_in_at": row[2].isoformat(),
        "sign_in_count": row[3],
        "proxy_wallet": row[4],
        "trader_rank": row[5],
        "expires_in": JWT_TTL_SECONDS,
    }
    response = JSONResponse(content=body)
    response.set_cookie(
        key=_access_cookie_name(),
        value=access_token,
        max_age=JWT_TTL_SECONDS,
        httponly=True,
        secure=_cookie_secure_flag(),
        samesite="lax",
        path="/",
        domain=_cookie_domain_value(),
    )
    return response


@app.get("/api/auth/wallet/session")
def wallet_session(request: Request) -> Dict[str, Any]:
    """Restore dashboard session from HttpOnly cookie (or Bearer for tooling)."""
    wallet = _try_extract_wallet_from_cookie_or_bearer(request)
    if not wallet:
        return {"signed_in": False}
    db_row = _load_wallet_session_row(wallet)
    if not db_row:
        return {
            "signed_in": True,
            "wallet_address": wallet,
            "sign_in_count": 0,
            "proxy_wallet": None,
            "trader_rank": None,
            "solana_wallet": None,
        }
    cached_proxy = str(db_row.get("proxy_wallet") or "").strip() or None
    cached_rank = str(db_row.get("trader_rank") or "").strip() or None
    refreshed_proxy, refreshed_rank = _maybe_refresh_polymarket_session_snapshot(
        wallet, cached_proxy, cached_rank
    )
    return {
        "signed_in": True,
        "wallet_address": wallet,
        "sign_in_count": int(db_row.get("sign_in_count") or 0),
        "proxy_wallet": refreshed_proxy,
        "trader_rank": refreshed_rank,
        "solana_wallet": (str(db_row.get("solana_wallet") or "").strip() or None)
        if db_row.get("solana_wallet") is not None
        else None,
    }


@app.post("/api/auth/wallet/logout")
def wallet_logout() -> JSONResponse:
    response = JSONResponse(content={"signed_in": False})
    response.delete_cookie(
        key=_access_cookie_name(),
        path="/",
        domain=_cookie_domain_value(),
        httponly=True,
        secure=_cookie_secure_flag(),
        samesite="lax",
    )
    return response


@app.get("/api/polymarket/public-profile")
def polymarket_public_profile(request: Request) -> Dict[str, Any]:
    _require_wallet_actions_enabled()
    wallet = _extract_wallet_from_request(request)
    profile = _fetch_polymarket_public_profile(wallet)
    proxy_wallet = _proxy_wallet_from_profile(profile) or PM_NOT_REGISTERED_VALUE
    return {
        "wallet_address": wallet,
        "proxy_wallet": proxy_wallet,
        "profile": profile,
    }


@app.get("/api/me/cards")
def me_cards(request: Request) -> Dict[str, Any]:
    """Returns the NFTs the user's linked Solana wallet currently holds on-chain.

    Source of truth is **on-chain ownership** of assets in
    ``MASTER_COLLECTION_ADDRESS`` for the user's saved Solana wallet (set via
    ``PUT /api/me/solana-wallet``). The list is queried through the Metaplex
    DAS ``searchAssets`` RPC method on ``SOLANA_DAS_RPC_URL``. As a result:

    * Selling/transferring an NFT immediately removes it from the dashboard.
    * Receiving someone else's PolyStars NFT immediately shows it.
    * Minting on-chain failures (no asset_address) never appear here even
      if a row exists in the local ``claims`` table.

    Card content (name, images, slug, season type/number, phase,
    collection_mint_number) is hydrated from the asset's off-chain JSON
    metadata (referenced by ``content.json_uri``), which is cached in
    ``_nft_metadata_cache``. Auxiliary DB-only fields (``claim_id``,
    ``season_id``, ``tx_hash``, ``minted_at``) are best-effort joined from
    our ``claims`` table by ``asset_address`` to keep explorer-tx links
    working; they are ``None`` for NFTs that were not minted via PolyStars
    (e.g. transferred in from another wallet).
    """
    _require_wallet_actions_enabled()
    connected_wallet = _extract_wallet_from_request(request).lower()
    if not Web3.is_address(connected_wallet):
        raise HTTPException(status_code=400, detail="Invalid connected wallet")

    linked_solana_wallet = _load_user_solana_wallet(connected_wallet)
    collection_address = ""
    try:
        collection_address = (mint_service.get_master_collection_address() or "").strip()
    except Exception:
        logger.exception("Failed to resolve MASTER_COLLECTION_ADDRESS for /api/me/cards")
        collection_address = ""

    response_envelope_base: Dict[str, Any] = {
        "wallet_address": connected_wallet,
        "linked_solana_wallet": linked_solana_wallet,
        "collection_address": collection_address or None,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }

    # No linked Solana wallet → nothing to query on-chain. Return an empty
    # listing rather than an error so the dashboard can prompt the user to
    # link a Solana wallet via the existing UX.
    if not linked_solana_wallet:
        return {
            **response_envelope_base,
            "items": [],
            "total": 0,
            "source": "onchain",
            "reason": "no_linked_solana_wallet",
        }

    if not collection_address:
        logger.error(
            "MASTER_COLLECTION_ADDRESS is not configured; /api/me/cards cannot list on-chain NFTs"
        )
        raise HTTPException(
            status_code=503,
            detail="Collection address is not configured on the server",
        )

    cache_key = (linked_solana_wallet.lower(), collection_address.lower())
    now_monotonic = monotonic()
    if ME_CARDS_ONCHAIN_CACHE_TTL_SECONDS > 0:
        with _me_cards_onchain_cache_lock:
            cached = _me_cards_onchain_cache.get(cache_key)
            if cached and cached[0] > now_monotonic:
                return {
                    **response_envelope_base,
                    **cached[1],
                    "source": "onchain",
                    "cached": True,
                }

    assets = _das_search_assets_by_owner_and_collection(
        owner_solana_wallet=linked_solana_wallet,
        collection_address=collection_address,
    )

    # Defensive double-check: filter out anything DAS may have leaked through
    # without an owner match (provider bugs / cached responses). Compare
    # case-sensitively because Solana addresses are base58 — case matters.
    owned_assets: List[Dict[str, Any]] = []
    for asset in assets:
        ownership = asset.get("ownership") if isinstance(asset.get("ownership"), dict) else {}
        owner_addr = str(ownership.get("owner") or "").strip() if isinstance(ownership, dict) else ""
        burnt = bool(asset.get("burnt"))
        if burnt:
            continue
        if owner_addr and owner_addr != linked_solana_wallet:
            continue
        owned_assets.append(asset)

    # Hydrate off-chain metadata in parallel (cached → near-instant on warm hits).
    json_uris = [
        str((a.get("content") or {}).get("json_uri") or "").strip() for a in owned_assets
    ]
    metadata_by_uri: Dict[str, Dict[str, Any]] = {}
    unique_uris = [uri for uri in dict.fromkeys(json_uris) if uri]
    if unique_uris:
        max_workers = min(SOLANA_NFT_METADATA_PARALLEL_FETCHES, len(unique_uris))
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            for uri, raw_metadata in zip(
                unique_uris,
                pool.map(_fetch_full_nft_metadata_for_uri, unique_uris),
            ):
                if isinstance(raw_metadata, dict):
                    metadata_by_uri[uri] = raw_metadata

    asset_addresses = [str(a.get("id") or "").strip() for a in owned_assets]
    enrichment = _load_claims_enrichment_for_assets(asset_addresses)

    items: List[Dict[str, Any]] = []
    for asset, json_uri in zip(owned_assets, json_uris):
        item = _build_me_card_item_from_das_asset(
            asset=asset,
            metadata_json=metadata_by_uri.get(json_uri),
            claims_enrichment=enrichment,
        )
        if item is None:
            continue
        items.append(item)

    # Sort newest-first using the best timestamp we have. Items without a
    # known minted_at (no matching DB row) are kept at the end so they remain
    # visible but don't pollute the "recently minted" head of the list.
    items.sort(
        key=lambda entry: (
            0 if entry.get("minted_at") else 1,
            -(_iso_to_epoch_seconds(entry.get("minted_at"))),
            str(entry.get("asset_address") or ""),
        )
    )

    payload_body = {"items": items, "total": len(items)}
    if ME_CARDS_ONCHAIN_CACHE_TTL_SECONDS > 0:
        with _me_cards_onchain_cache_lock:
            _me_cards_onchain_cache[cache_key] = (
                now_monotonic + ME_CARDS_ONCHAIN_CACHE_TTL_SECONDS,
                payload_body,
            )

    return {
        **response_envelope_base,
        **payload_body,
        "source": "onchain",
        "cached": False,
    }


@app.get("/api/cards/ticker")
def generated_cards_ticker(request: Request, limit: Optional[int] = None) -> Dict[str, Any]:
    """Public random sample for the home ticker (no auth). Intentionally not gated by wallet-actions lock."""
    env_default = int(os.getenv("USER_WEB_CARDS_TICKER_SAMPLE_SIZE", "40"))
    ticker_default = max(1, min(env_default, 48))
    if limit is None:
        safe_limit = ticker_default
    else:
        try:
            safe_limit = max(1, min(int(limit), 48))
        except (TypeError, ValueError):
            safe_limit = ticker_default

    cached_payload = _get_cards_ticker_cached(request, safe_limit)
    if cached_payload is not None:
        return cached_payload

    conn = _get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
            cursor.execute(
                """
                SELECT slug, card_title, front_image_path, back_image_path, created_at
                FROM user_generated_cards
                ORDER BY RANDOM()
                LIMIT %s
                """,
                (safe_limit,),
            )
            rows = cursor.fetchall()
    except Exception:
        logger.exception("Failed to load generated cards ticker")
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    finally:
        conn.close()

    items: List[Dict[str, Any]] = []
    for row in rows:
        r = dict(row)
        slug = str(r.get("slug") or "").strip()
        if not slug:
            continue
        front_path = str(r.get("front_image_path") or "").strip()
        back_path = str(r.get("back_image_path") or "").strip()
        title = str(r.get("card_title") or "").strip()
        created_at = r.get("created_at")
        created_iso: Optional[str] = None
        if isinstance(created_at, datetime):
            created_iso = created_at.astimezone(timezone.utc).isoformat()
        items.append(
            {
                "slug": slug,
                "card_title": title,
                "front_image_url": _absolute_asset_url(request, front_path),
                "back_image_url": _absolute_asset_url(request, back_path) if back_path else None,
                "created_at": created_iso,
            }
        )

    payload = {
        "items": items,
        "total": len(items),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    _set_cards_ticker_cached(request, safe_limit, payload)
    return payload


@app.get("/api/cards/{slug}")
def card_by_slug(slug: str, request: Request) -> Dict[str, Any]:
    """Public card detail for showcase links; not gated by wallet-actions lock (same as /api/cards/ticker)."""
    normalized_slug = str(slug or "").strip()
    if not normalized_slug:
        raise HTTPException(status_code=400, detail="Card slug is required")

    conn = _get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
            cursor.execute(
                """
                SELECT
                    gc.id,
                    gc.collection_mint_number,
                    gc.slug,
                    gc.owner_wallet,
                    gc.owner_proxy_wallet,
                    gc.winner_row_id,
                    gc.season_id,
                    gc.event_id,
                    gc.event_slug,
                    gc.card_title,
                    gc.primary_tag,
                    gc.secondary_tag,
                    gc.pattern,
                    gc.front_image_path,
                    gc.back_image_path,
                    gc.card_payload_json,
                    gc.created_at,
                    e.title AS event_title,
                    e.description AS event_description,
                    e.slug AS event_slug_from_events,
                    e.volume AS event_volume,
                    e.volume24hr AS event_volume_24hr,
                    e.volume1wk AS event_volume_1wk,
                    e.volume1mo AS event_volume_1mo,
                    e.liquidity AS event_liquidity,
                    e.open_interest AS event_open_interest,
                    e.comment_count AS event_comment_count,
                    e.active AS event_active,
                    e.closed AS event_closed,
                    e.start_date AS event_start_date,
                    e.end_date AS event_end_date,
                    e.closed_time AS event_closed_time,
                    wwin.proxy_wallet AS winner_proxy_wallet,
                    wwin.minted_asset_address AS minted_asset_address
                FROM user_generated_cards gc
                LEFT JOIN events e ON e.id = gc.event_id
                LEFT JOIN winner_wallets_nft_to_claim wwin ON wwin.id = gc.winner_row_id
                WHERE gc.slug = %s
                LIMIT 1
                """,
                (normalized_slug,),
            )
            row = cursor.fetchone()
    except Exception:
        logger.exception("Failed to load generated card slug=%s", normalized_slug)
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    finally:
        conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="Card not found")
    row_dict = dict(row)
    card = _format_generated_card_row(row_dict, request=request, include_payload=True)
    event_snapshot = {
        "title": row_dict.get("event_title"),
        "description": row_dict.get("event_description"),
        "slug": row_dict.get("event_slug_from_events") or row_dict.get("event_slug"),
        "volume": row_dict.get("event_volume"),
        "volume_24hr": row_dict.get("event_volume_24hr"),
        "volume_1wk": row_dict.get("event_volume_1wk"),
        "volume_1mo": row_dict.get("event_volume_1mo"),
        "liquidity": row_dict.get("event_liquidity"),
        "open_interest": row_dict.get("event_open_interest"),
        "comment_count": row_dict.get("event_comment_count"),
        "active": row_dict.get("event_active"),
        "closed": row_dict.get("event_closed"),
        "start_date": row_dict.get("event_start_date"),
        "end_date": row_dict.get("event_end_date"),
        "closed_time": row_dict.get("event_closed_time"),
    }
    for key in ("start_date", "end_date", "closed_time"):
        value = event_snapshot.get(key)
        if isinstance(value, datetime):
            event_snapshot[key] = value.astimezone(timezone.utc).isoformat()
    card["event_snapshot"] = event_snapshot
    minted_asset_address = str(row_dict.get("minted_asset_address") or "").strip() or None
    card["asset_address"] = minted_asset_address
    card["explorer_asset_url"] = (
        _build_solana_explorer_asset_url(minted_asset_address) if minted_asset_address else None
    )
    card["magiceden_url"] = (
        _build_magiceden_item_url(minted_asset_address) if minted_asset_address else None
    )
    return {"card": card}


@app.post("/api/cards/get")
def get_card(request: Request) -> UserGeneratedCardResponse:
    _require_wallet_actions_enabled()
    phase_t = monotonic()
    # EOA tied to dashboard session (JWT only — same wallet user connected at sign-in).
    session_wallet_eoa = _extract_wallet_from_request(request).lower()
    if not Web3.is_address(session_wallet_eoa):
        raise HTTPException(status_code=400, detail="Invalid connected wallet")

    slug: Optional[str] = None
    uploaded_front_key: Optional[str] = None
    uploaded_back_key: Optional[str] = None
    conn = _get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
            session_signin_proxy_wallet = _load_signin_proxy_for_session_wallet(cursor, session_wallet_eoa)
            phase_t = _log_card_get_phase("load_session_signin_proxy", phase_t)

            winner_row_id = _pick_eligible_winner_row_id(cursor)
            phase_t = _log_card_get_phase("pick_eligible_winner", phase_t)
            if winner_row_id is None:
                total_available, _ = _generated_cards_supply_counts(
                    cursor,
                    use_cached_join_total=False,
                )
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "All cards have already been generated."
                        if total_available > 0
                        else "No card-builder rows with manual images are available yet."
                    ),
                )
            source_row = _load_card_source_row(cursor, winner_row_id=winner_row_id)
            phase_t = _log_card_get_phase("load_card_source_row_db", phase_t)
            if not source_row:
                raise HTTPException(status_code=400, detail="Selected row has no renderable card payload")

            payload = _build_card_payload_from_source_row(
                source_row,
                session_signin_proxy_wallet=session_signin_proxy_wallet,
            )
            slug = _generated_card_slug(payload.get("season_type"), payload.get("season_number"))
            payload["qr_payload"] = f"{CARD_BASE_URL}/cards/{slug}"
            image_url = str(payload.get("image_url") or "").strip()
            if not image_url:
                raise HTTPException(status_code=400, detail="Selected row is missing manual image URL")

            # ── Step 1: INSERT first (placeholder paths) so the DB trigger assigns
            #   collection_mint_number before we render the SVG. ──────────────────
            cursor.execute(
                """
                INSERT INTO user_generated_cards (
                    slug,
                    owner_wallet,
                    owner_proxy_wallet,
                    winner_row_id,
                    season_id,
                    event_id,
                    event_slug,
                    card_title,
                    primary_tag,
                    secondary_tag,
                    pattern,
                    front_image_path,
                    back_image_path,
                    card_payload_json
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb
                )
                RETURNING
                    id,
                    collection_mint_number,
                    slug,
                    owner_wallet,
                    owner_proxy_wallet,
                    winner_row_id,
                    season_id,
                    event_id,
                    event_slug,
                    card_title,
                    primary_tag,
                    secondary_tag,
                    pattern,
                    front_image_path,
                    back_image_path,
                    card_payload_json,
                    created_at
                """,
                (
                    slug,
                    session_wallet_eoa,
                    session_signin_proxy_wallet,
                    winner_row_id,
                    int(source_row.get("season_id") or 0),
                    source_row.get("event_id"),
                    source_row.get("event_slug"),
                    str(payload.get("card_title") or ""),
                    str(payload.get("primary_tag") or ""),
                    str(payload.get("secondary_tag") or ""),
                    None,
                    "",   # placeholder — updated below after SVG render
                    "",   # placeholder — updated below after SVG render
                    json.dumps(payload),
                ),
            )
            created_row = cursor.fetchone()
            phase_t = _log_card_get_phase("insert_generated_card", phase_t)

            # ── Step 2: Inject the assigned mint number into the render payload ──
            payload["collection_mint_number"] = created_row["collection_mint_number"]

            # ── Step 3: Render SVGs + rasterize to PNG now that mint number is known ──
            # Showcase cards share the NFT pipeline: SVG -> PNG via the shared
            # headless-browser pool, then uploaded to R2 as image/png. The only
            # difference from a real mint is the destination (R2 vs Pinata).
            render_payload = _build_render_payload(payload)
            phase_t = _log_card_get_phase("fetch_manual_image_http", phase_t)
            front_png, back_png = render_card_pngs(render_payload)
            phase_t = _log_card_get_phase("rasterize_png_local", phase_t)

            # ── Step 4: Upload to R2 ──────────────────────────────────────────────
            front_image_path, back_image_path, uploaded_front_key, uploaded_back_key = upload_card_assets_to_r2(
                slug,
                front_png,
                back_png,
            )
            phase_t = _log_card_get_phase("r2_put_object_x2", phase_t)

            # ── Step 5: Update record with real paths and final payload ───────────
            cursor.execute(
                """
                UPDATE user_generated_cards
                SET front_image_path = %s,
                    back_image_path  = %s,
                    card_payload_json = %s::jsonb
                WHERE slug = %s
                """,
                (front_image_path, back_image_path, json.dumps(payload), slug),
            )
            # Refresh created_row to reflect updated paths for the response.
            created_row = dict(created_row)
            created_row["front_image_path"] = front_image_path
            created_row["back_image_path"] = back_image_path
            created_row["card_payload_json"] = payload
            total_available, remaining_available = _generated_cards_supply_counts(cursor)
            phase_t = _log_card_get_phase("supply_counts_db", phase_t)
        conn.commit()
        phase_t = _log_card_get_phase("transaction_commit", phase_t)
    except HTTPException:
        conn.rollback()
        delete_r2_object_by_key(uploaded_front_key)
        delete_r2_object_by_key(uploaded_back_key)
        raise
    except Exception:
        conn.rollback()
        delete_r2_object_by_key(uploaded_front_key)
        delete_r2_object_by_key(uploaded_back_key)
        logger.exception("Failed to generate card for wallet=%s", session_wallet_eoa)
        raise HTTPException(status_code=503, detail="Failed to generate card. Please retry shortly.")
    finally:
        conn.close()

    card_payload = _format_generated_card_row(dict(created_row), request=request, include_payload=True)
    phase_t = _log_card_get_phase("format_response_row", phase_t)

    return UserGeneratedCardResponse(
        status="ok",
        message="Card generated successfully",
        card=card_payload,
        remaining_available=remaining_available,
        total_available=total_available,
    )


@app.get("/api/master-collection")
def me_master_collection() -> Dict[str, str]:
    """Public master collection address used as the parent for minted user STARs."""
    address = ""
    try:
        address = mint_service.get_master_collection_address()
    except Exception:
        logger.exception("Could not resolve master collection address")
        address = ""
    return {"address": address}


@app.get("/api/me/solana-wallet")
def me_get_solana_wallet(request: Request) -> Dict[str, Optional[str]]:
    """Returns the saved Solana recipient for the signed-in EVM wallet."""
    _require_wallet_actions_enabled()
    wallet = _extract_wallet_from_request(request).lower()
    saved = _load_user_solana_wallet(wallet)
    return {"wallet_address": wallet, "solana_wallet": saved}


@app.put("/api/me/solana-wallet")
def me_put_solana_wallet(payload: SolanaWalletUpdateRequest, request: Request) -> Dict[str, Optional[str]]:
    """Saves (or clears with empty/null) the Solana recipient for the signed-in EVM wallet."""
    _require_wallet_actions_enabled()
    wallet = _extract_wallet_from_request(request).lower()
    raw = (payload.solana_wallet or "").strip()
    if not raw:
        _save_user_solana_wallet(wallet, None)
        return {"wallet_address": wallet, "solana_wallet": None}
    canonical = _validate_solana_address(raw)
    _save_user_solana_wallet(wallet, canonical)
    return {"wallet_address": wallet, "solana_wallet": canonical}


@app.get("/api/me/eligibility")
def me_eligibility(request: Request) -> Dict[str, Any]:
    """Mint eligibility for the signed-in wallet across the current Genesis and Standard streams."""
    _require_wallet_actions_enabled()
    wallet = _extract_wallet_from_request(request).lower()
    if not Web3.is_address(wallet):
        raise HTTPException(status_code=400, detail="Invalid connected wallet")
    try:
        return season_manager.check_user_eligibility(wallet)
    except Exception as exc:
        logger.exception("Failed to compute eligibility for wallet=%s", wallet)
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/me/mint")
def me_mint(payload: MintMyNftRequest, request: Request) -> Dict[str, Any]:
    """Mints the next eligible NFT to the user's saved Solana wallet for the given season."""
    _require_wallet_actions_enabled()
    wallet = _extract_wallet_from_request(request).lower()
    if not Web3.is_address(wallet):
        raise HTTPException(status_code=400, detail="Invalid connected wallet")

    # Strict server-side gate: only wallets that are registered on Polymarket
    # (i.e. have a real Polymarket proxy wallet) may mint. The trader rank
    # itself is irrelevant — registered users with no trades yet must also be
    # allowed to mint. Frontend mirrors this check, but we MUST also enforce
    # it here so the API cannot be bypassed.
    #
    # We do a *live* lookup against Polymarket's public profile API so the
    # gate does not depend on whatever was cached in ``user_wallet_signins``
    # at sign-in time (which can be stale or empty if the profile API was
    # temporarily unavailable then). If the live call fails, we fall back to
    # the cached snapshot so a transient Polymarket outage does not lock
    # legitimately-registered users out of minting.
    cached_proxy_wallet, _cached_trader_rank = _load_wallet_signin_snapshot(wallet)
    live_proxy_wallet: Optional[str] = None
    live_lookup_succeeded = False
    try:
        profile = _fetch_polymarket_public_profile(wallet)
        live_proxy_wallet = _proxy_wallet_from_profile(profile)
        live_lookup_succeeded = True
    except Exception as exc:
        logger.warning(
            "Live Polymarket profile lookup failed for wallet=%s: %s; "
            "falling back to cached proxy_wallet=%r",
            wallet,
            str(exc),
            cached_proxy_wallet,
        )

    effective_proxy_wallet = (
        live_proxy_wallet if live_lookup_succeeded else cached_proxy_wallet
    )
    is_registered = _is_registered_on_polymarket(effective_proxy_wallet)
    logger.info(
        "Polymarket mint gate check: wallet=%s live_ok=%s live_proxy=%r "
        "cached_proxy=%r effective_proxy=%r is_registered=%s",
        wallet,
        live_lookup_succeeded,
        live_proxy_wallet,
        cached_proxy_wallet,
        effective_proxy_wallet,
        is_registered,
    )
    if not is_registered:
        raise HTTPException(
            status_code=403,
            detail="Wallet is not registered on Polymarket — minting is not allowed.",
        )

    saved_solana = _load_user_solana_wallet(wallet)
    if not saved_solana:
        raise HTTPException(
            status_code=400,
            detail="Set your Solana recipient wallet before minting",
        )
    recipient = _validate_solana_address(saved_solana)

    try:
        season_id = int(payload.season_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid season_id")
    if season_id <= 0:
        raise HTTPException(status_code=400, detail="Invalid season_id")

    mint_request = MintClaimRequest(
        wallet=wallet,
        recipient_address=recipient,
        season_id=season_id,
        phase="breach",
        auto_phase=True,
        db_only=False,
        use_fixed_claim_images=False,
    )
    try:
        return mint_service.run_mint_claim(mint_request)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "Mint failed for wallet=%s season_id=%s recipient=%s",
            wallet,
            season_id,
            recipient,
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc


