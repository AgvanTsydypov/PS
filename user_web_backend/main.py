from __future__ import annotations

import json
import logging
import os
import secrets
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import monotonic
from typing import Any, Dict, List, Optional, Tuple

import jwt
import psycopg2
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
from admin_backend.main import BLOCKCHAIN_BASE_ZORA, MintClaimRequest, SeasonWorkbenchService

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


class EligibilityRequest(BaseModel):
    wallet: str
    season_id: Optional[int] = None


class UserMintResponse(BaseModel):
    status: str
    message: str
    wallet_address: str
    proxy_wallet: str
    minted_count: int
    minted_claims: List[Dict[str, Any]]
    failed_claims: List[Dict[str, Any]]


class RateLimitConfig(BaseModel):
    window_seconds: int
    max_requests: int


app = FastAPI(title="PolyStars User Web API", version="1.0.0")


def _allowed_origins() -> List[str]:
    raw = os.getenv("USER_WEB_CORS_ORIGINS", "").strip()
    if raw:
        return [origin.strip() for origin in raw.split(",") if origin.strip()]
    if os.getenv("NODE_ENV", "development") == "development":
        return ["http://localhost:3001", "http://127.0.0.1:3001"]
    return []


app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins(),
    allow_credentials=False,
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
RATE_LIMITS: Dict[str, RateLimitConfig] = {
    "/api/auth/wallet/challenge": RateLimitConfig(
        window_seconds=int(os.getenv("USER_WEB_RATE_LIMIT_WINDOW_SECONDS", "60")),
        max_requests=int(os.getenv("USER_WEB_RATE_LIMIT_CHALLENGE_MAX", "20")),
    ),
    "/api/auth/wallet/verify": RateLimitConfig(
        window_seconds=int(os.getenv("USER_WEB_RATE_LIMIT_WINDOW_SECONDS", "60")),
        max_requests=int(os.getenv("USER_WEB_RATE_LIMIT_VERIFY_MAX", "20")),
    ),
    "/api/eligibility": RateLimitConfig(
        window_seconds=int(os.getenv("USER_WEB_RATE_LIMIT_WINDOW_SECONDS", "60")),
        max_requests=int(os.getenv("USER_WEB_RATE_LIMIT_ELIGIBILITY_MAX", "30")),
    ),
    "/api/mint/base-sepolia": RateLimitConfig(
        window_seconds=int(os.getenv("USER_WEB_RATE_LIMIT_WINDOW_SECONDS", "60")),
        max_requests=int(os.getenv("USER_WEB_RATE_LIMIT_MINT_MAX", "12")),
    ),
    "/api/me/nfts": RateLimitConfig(
        window_seconds=int(os.getenv("USER_WEB_RATE_LIMIT_WINDOW_SECONDS", "60")),
        max_requests=int(os.getenv("USER_WEB_RATE_LIMIT_NFTS_MAX", "30")),
    ),
    "/api/wallet-ticker": RateLimitConfig(
        window_seconds=int(os.getenv("USER_WEB_RATE_LIMIT_WINDOW_SECONDS", "60")),
        max_requests=int(os.getenv("USER_WEB_RATE_LIMIT_TICKER_MAX", "30")),
    ),
}
_rate_limit_lock = threading.Lock()
_rate_limit_store: Dict[str, deque[float]] = defaultdict(deque)
POLYMARKET_GAMMA_API_BASE = os.getenv("POLYMARKET_GAMMA_API_BASE", "https://gamma-api.polymarket.com").rstrip("/")
POLYMARKET_DATA_API_BASE = os.getenv("POLYMARKET_DATA_API_BASE", "https://data-api.polymarket.com").rstrip("/")
POLYMARKET_REQUEST_TIMEOUT_SECONDS = float(os.getenv("POLYMARKET_REQUEST_TIMEOUT_SECONDS", "8"))
PM_NOT_REGISTERED_VALUE = "Not registered in PM"
NO_TRADES_YET_VALUE = "No trades yet"
CLAIMS_UNIQUENESS_INDEX_NAME = "ux_claims_active_season_user_wallet_lower"
BLOCKSCOUT_TIMEOUT_SECONDS = float(os.getenv("USER_WEB_BLOCKSCOUT_TIMEOUT_SECONDS", "12"))
BLOCKSCOUT_RETRY_ATTEMPTS = max(1, int(os.getenv("USER_WEB_BLOCKSCOUT_RETRY_ATTEMPTS", "2")))
BLOCKSCOUT_NFT_CACHE_TTL_SECONDS = int(os.getenv("USER_WEB_BLOCKSCOUT_NFT_CACHE_TTL_SECONDS", "20"))
BLOCKSCOUT_CHAIN_API_BASES: Dict[str, str] = {
    "base-sepolia": "https://base-sepolia.blockscout.com/api/v2",
    "base": "https://base.blockscout.com/api/v2",
}
BLOCKSCOUT_CHAIN_EXPLORER_BASES: Dict[str, str] = {
    "base-sepolia": "https://base-sepolia.blockscout.com",
    "base": "https://base.blockscout.com",
}
BLOCKSCOUT_REQUEST_HEADERS: Dict[str, str] = {
    "accept": "application/json,text/plain,*/*",
    # Blockscout sometimes rejects default Python urllib user-agent with 403.
    "user-agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
}
_nft_cache_lock = threading.Lock()
_nft_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}


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


def _extract_wallet_from_request(request: Request) -> str:
    auth_header = request.headers.get("authorization", "").strip()
    if not auth_header.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")
    token = auth_header.split(" ", 1)[1].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Unauthorized")
    try:
        decoded = jwt.decode(
            token,
            _jwt_secret(),
            algorithms=[JWT_ALG],
            audience=JWT_AUDIENCE,
            issuer=JWT_ISSUER,
        )
    except Exception:
        raise HTTPException(status_code=401, detail="Unauthorized")

    token_type = str(decoded.get("type", ""))
    subject = str(decoded.get("sub", "")).strip().lower()
    if token_type != "access" or not subject:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return subject


def _try_extract_wallet_from_auth_header(request: Request) -> Optional[str]:
    auth_header = request.headers.get("authorization", "").strip()
    if not auth_header.lower().startswith("bearer "):
        return None
    token = auth_header.split(" ", 1)[1].strip()
    if not token:
        return None
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


def _mint_block_reason(proxy_wallet: str, trader_rank: str) -> Optional[str]:
    if proxy_wallet == PM_NOT_REGISTERED_VALUE:
        return "Mint blocked: user has no Polymarket proxy wallet (not registered on Polymarket)."
    rank_value = str(trader_rank or "").strip()
    if not rank_value or rank_value.lower() == NO_TRADES_YET_VALUE.lower():
        return "Mint blocked: user has no trades on Polymarket leaderboard."
    return None


def _blockscout_api_base_url() -> str:
    raw_override = os.getenv("USER_WEB_BLOCKSCOUT_API_BASE", "").strip().rstrip("/")
    if raw_override:
        return raw_override
    chain_name = str(os.getenv("ZORA_CHAIN", "base-sepolia")).strip().lower()
    return BLOCKSCOUT_CHAIN_API_BASES.get(chain_name, BLOCKSCOUT_CHAIN_API_BASES["base-sepolia"])


def _blockscout_explorer_base_url() -> str:
    chain_name = str(os.getenv("ZORA_CHAIN", "base-sepolia")).strip().lower()
    return BLOCKSCOUT_CHAIN_EXPLORER_BASES.get(chain_name, BLOCKSCOUT_CHAIN_EXPLORER_BASES["base-sepolia"])


def _collection_contract_address() -> str:
    address = str(os.getenv("ZORA_1155_CONTRACT_ADDRESS", "")).strip()
    if not address:
        raise HTTPException(status_code=503, detail="Collection contract is not configured")
    if not Web3.is_address(address):
        raise HTTPException(status_code=503, detail="Collection contract address is invalid")
    return Web3.to_checksum_address(address)


def _normalize_nft_item(raw_item: Dict[str, Any]) -> Dict[str, Any]:
    metadata = raw_item.get("metadata") if isinstance(raw_item.get("metadata"), dict) else {}
    token = raw_item.get("token") if isinstance(raw_item.get("token"), dict) else {}
    owner = raw_item.get("owner") if isinstance(raw_item.get("owner"), dict) else {}
    token_id = str(raw_item.get("id") or "")
    contract_address = str(token.get("address_hash") or "")
    instance_url = ""
    if contract_address and token_id:
        instance_url = f"{_blockscout_explorer_base_url()}/token/{contract_address}/instance/{token_id}"

    return {
        "token_id": token_id,
        "name": str(metadata.get("name") or f"Token #{token_id}"),
        "description": str(metadata.get("description") or ""),
        "image_url": str(raw_item.get("image_url") or raw_item.get("media_url") or metadata.get("image") or ""),
        "owner_address": str(owner.get("hash") or ""),
        "collection_name": str(token.get("name") or ""),
        "token_type": str(raw_item.get("token_type") or token.get("type") or ""),
        "amount": str(raw_item.get("value") or "1"),
        "explorer_url": instance_url,
        "metadata": metadata,
    }


def _fetch_blockscout_json(url: str, connected_wallet: str, contract_address: str) -> Dict[str, Any]:
    headers = dict(BLOCKSCOUT_REQUEST_HEADERS)
    base = _blockscout_explorer_base_url().rstrip("/")
    headers["origin"] = base
    headers["referer"] = f"{base}/"
    request_obj = urllib.request.Request(url, headers=headers)
    last_error: Optional[Exception] = None
    for attempt in range(1, BLOCKSCOUT_RETRY_ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(request_obj, timeout=BLOCKSCOUT_TIMEOUT_SECONDS) as response:
                payload = json.load(response)
                if isinstance(payload, dict):
                    return payload
                return {}
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code == 404:
                raise HTTPException(
                    status_code=503,
                    detail="Collection contract is not indexed on explorer yet",
                ) from exc
            logger.warning(
                "Blockscout NFT HTTP error code=%s attempt=%s/%s wallet=%s contract=%s",
                exc.code,
                attempt,
                BLOCKSCOUT_RETRY_ATTEMPTS,
                connected_wallet,
                contract_address,
            )
        except Exception as exc:
            last_error = exc
            logger.warning(
                "Blockscout NFT request failed attempt=%s/%s wallet=%s contract=%s",
                attempt,
                BLOCKSCOUT_RETRY_ATTEMPTS,
                connected_wallet,
                contract_address,
                exc_info=True,
            )
        if attempt < BLOCKSCOUT_RETRY_ATTEMPTS:
            threading.Event().wait(0.5 * attempt)

    raise HTTPException(status_code=502, detail="Failed to load NFTs from explorer") from last_error


def _fetch_user_nfts_onchain(connected_wallet: str) -> Dict[str, Any]:
    contract_address = _collection_contract_address()
    checksum_wallet = Web3.to_checksum_address(connected_wallet)
    blockscout_api_base = _blockscout_api_base_url()
    query = urllib.parse.urlencode({"holder_address_hash": checksum_wallet})
    url = f"{blockscout_api_base}/tokens/{contract_address}/instances?{query}"
    payload = _fetch_blockscout_json(
        url=url,
        connected_wallet=checksum_wallet,
        contract_address=contract_address,
    )

    raw_items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(raw_items, list):
        raw_items = []

    items = [_normalize_nft_item(item) for item in raw_items if isinstance(item, dict)]
    items.sort(key=lambda item: int(item["token_id"]) if str(item.get("token_id", "")).isdigit() else -1, reverse=True)
    return {
        "wallet_address": connected_wallet,
        "contract_address": contract_address,
        "items": items,
        "total": len(items),
        "source": "blockscout_onchain",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def _get_user_nfts_cached(connected_wallet: str) -> Dict[str, Any]:
    cache_key = connected_wallet.lower()
    now = monotonic()
    cached: Optional[Tuple[float, Dict[str, Any]]] = None
    with _nft_cache_lock:
        cached = _nft_cache.get(cache_key)
        if cached and now - cached[0] <= BLOCKSCOUT_NFT_CACHE_TTL_SECONDS:
            return cached[1]

    try:
        payload = _fetch_user_nfts_onchain(connected_wallet)
    except HTTPException:
        if cached is not None:
            stale_payload = dict(cached[1])
            stale_payload["source"] = f"{str(stale_payload.get('source') or 'blockscout_onchain')}:stale_cache"
            stale_payload["stale"] = True
            return stale_payload
        raise

    with _nft_cache_lock:
        _nft_cache[cache_key] = (now, payload)
    return payload


def _blocked_eligibility_payload(
    token_wallet: str,
    proxy_wallet: str,
    trader_rank: str,
    reason: str,
) -> Dict[str, Any]:
    stream_payload = {
        "season_id": None,
        "phase": None,
        "eligible_now": False,
        "ineligible_reason": reason,
    }
    return {
        "wallet_address": token_wallet,
        "proxy_wallet": proxy_wallet,
        "trader_rank": trader_rank,
        "eligibility_wallet": proxy_wallet.lower() if Web3.is_address(proxy_wallet) else proxy_wallet,
        "is_origin_wallet": False,
        "genesis": dict(stream_payload),
        "standard": dict(stream_payload),
        "double_mint": {
            "can_claim_genesis": False,
            "can_claim_standard": False,
            "can_claim_both_now": False,
        },
        "mint_blocked": True,
        "mint_block_reason": reason,
    }


def _build_eligibility_failure_message(payload: Dict[str, Any]) -> str:
    reasons: List[str] = []
    for stream_name in ("genesis", "standard"):
        stream = payload.get(stream_name)
        if not isinstance(stream, dict):
            continue
        if bool(stream.get("eligible_now")):
            continue
        reason = str(stream.get("ineligible_reason") or "not eligible")
        reasons.append(f"{stream_name}: {reason}")
    if reasons:
        return "Mint eligibility failed: " + "; ".join(reasons)
    return "Mint eligibility failed"


def _select_eligible_stream(payload: Dict[str, Any]) -> Dict[str, Any]:
    standard = payload.get("standard")
    if isinstance(standard, dict) and bool(standard.get("eligible_now")):
        return standard
    genesis = payload.get("genesis")
    if isinstance(genesis, dict) and bool(genesis.get("eligible_now")):
        return genesis
    raise ValueError(_build_eligibility_failure_message(payload))


def _collect_eligible_streams(payload: Dict[str, Any]) -> List[tuple[str, Dict[str, Any]]]:
    selected: List[tuple[str, Dict[str, Any]]] = []
    # Prefer standard first, then genesis.
    for stream_name in ("standard", "genesis"):
        stream = payload.get(stream_name)
        if not isinstance(stream, dict):
            continue
        if bool(stream.get("eligible_now")):
            selected.append((stream_name, stream))
    return selected


def _resolve_eligibility_wallet(token_wallet: str, proxy_wallet: str) -> str:
    del token_wallet
    return proxy_wallet.lower()


def _addresses_for_claim_uniqueness(token_wallet: str, proxy_wallet: str) -> List[str]:
    addresses = {token_wallet.lower()}
    proxy = proxy_wallet.strip().lower()
    if proxy and proxy != PM_NOT_REGISTERED_VALUE.lower():
        addresses.add(proxy)
    return sorted(addresses)


def _has_claim_in_season_any_address(season_id: int, addresses: List[str]) -> bool:
    if not addresses:
        return False
    conn = _get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM claims
                    WHERE season_id = %s
                      AND lower(user_wallet) = ANY(%s)
                      AND status IN ('PENDING', 'PROCESSING', 'COMPLETED')
                )
                """,
                (season_id, addresses),
            )
            row = cursor.fetchone()
            return bool(row and row[0])
    finally:
        conn.close()


def _try_acquire_mint_lock(season_id: int, eligibility_wallet: str):
    lock_key = f"mint:{season_id}:{eligibility_wallet.lower()}"
    conn = _get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(hashtext(%s))", (lock_key,))
            row = cursor.fetchone()
            locked = bool(row and row[0])
        if not locked:
            conn.close()
            return None
        return conn
    except Exception:
        conn.close()
        raise


def _release_mint_lock(conn, season_id: int, eligibility_wallet: str) -> None:
    lock_key = f"mint:{season_id}:{eligibility_wallet.lower()}"
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(hashtext(%s))", (lock_key,))
    finally:
        conn.close()


def _apply_claim_uniqueness_guard(
    eligibility_payload: Dict[str, Any],
    season_id: int,
    addresses: List[str],
) -> Dict[str, Any]:
    if not _has_claim_in_season_any_address(season_id=season_id, addresses=addresses):
        return eligibility_payload
    payload = dict(eligibility_payload)
    reason = "Already claimed in this season by connected/proxy wallet linkage"
    payload["already_claimed_via_linked_wallet"] = True
    payload["eligible_now"] = False
    payload["ineligible_reason"] = reason
    return payload


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    cfg = RATE_LIMITS.get(request.url.path)
    if cfg is not None:
        client_ip = request.client.host if request.client else "unknown"
        wallet_scope = ""
        if request.url.path == "/api/mint/base-sepolia":
            wallet_sub = _try_extract_wallet_from_auth_header(request)
            wallet_scope = wallet_sub or "anon"
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
    _warn_if_claims_uniqueness_index_missing()


@app.get("/api/health")
def health():
    return {"ok": True, "service": "user_web_backend"}


@app.get("/api/server-time")
def server_time() -> Dict[str, str]:
    return {"now_utc_iso": datetime.now(timezone.utc).isoformat()}


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


@app.post("/api/eligibility")
def check_eligibility(payload: EligibilityRequest, request: Request) -> Dict[str, Any]:
    token_wallet = _extract_wallet_from_request(request).lower()
    proxy_wallet = _load_proxy_wallet_for_user_wallet(token_wallet)
    trader_rank = _load_trader_rank_for_user_wallet(token_wallet)
    block_reason = _mint_block_reason(proxy_wallet, trader_rank)
    if block_reason is not None:
        return _blocked_eligibility_payload(
            token_wallet=token_wallet,
            proxy_wallet=proxy_wallet,
            trader_rank=trader_rank,
            reason=block_reason,
        )
    eligibility_wallet = _resolve_eligibility_wallet(token_wallet, proxy_wallet)
    linked_addresses = _addresses_for_claim_uniqueness(token_wallet, proxy_wallet)
    if not Web3.is_address(eligibility_wallet):
        return _blocked_eligibility_payload(
            token_wallet=token_wallet,
            proxy_wallet=proxy_wallet,
            trader_rank=trader_rank,
            reason="Mint blocked: invalid Polymarket proxy wallet format.",
        )
    try:
        result = season_manager.check_user_eligibility(eligibility_wallet)
        if isinstance(result.get("genesis"), dict):
            genesis_season_id = int(result["genesis"].get("season_id") or 0)
            if genesis_season_id > 0:
                result["genesis"] = _apply_claim_uniqueness_guard(
                    eligibility_payload=result["genesis"],
                    season_id=genesis_season_id,
                    addresses=linked_addresses,
                )
        if isinstance(result.get("standard"), dict):
            standard_season_id = int(result["standard"].get("season_id") or 0)
            if standard_season_id > 0:
                result["standard"] = _apply_claim_uniqueness_guard(
                    eligibility_payload=result["standard"],
                    season_id=standard_season_id,
                    addresses=linked_addresses,
                )
        genesis_ok = bool((result.get("genesis") or {}).get("eligible_now"))
        standard_ok = bool((result.get("standard") or {}).get("eligible_now"))
        result["double_mint"] = {
            "can_claim_genesis": genesis_ok,
            "can_claim_standard": standard_ok,
            "can_claim_both_now": genesis_ok and standard_ok,
        }
        result["wallet_address"] = token_wallet
        result["proxy_wallet"] = proxy_wallet
        result["trader_rank"] = trader_rank
        result["eligibility_wallet"] = eligibility_wallet
        if payload.season_id is not None:
            selected = season_manager.check_user_eligibility_for_season(
                wallet_address=eligibility_wallet,
                season_id=payload.season_id,
            )
            selected = _apply_claim_uniqueness_guard(
                eligibility_payload=selected,
                season_id=payload.season_id,
                addresses=linked_addresses,
            )
            result["selected_season_id"] = payload.season_id
            result["is_origin_wallet_active_standard"] = result.get("is_origin_wallet")
            result["is_origin_wallet"] = bool(selected.get("is_origin_wallet"))
            result["selected_season"] = selected
        return result
    except HTTPException:
        raise
    except Exception:
        logger.exception(
            "Eligibility check failed for wallet=%s proxy=%s eligibility_wallet=%s",
            token_wallet,
            proxy_wallet,
            eligibility_wallet,
        )
        raise HTTPException(status_code=400, detail="Eligibility check failed")


@app.post("/api/auth/wallet/challenge")
def wallet_challenge(payload: ChallengeRequest):
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
    return {
        "signed_in": True,
        "wallet_address": row[0],
        "first_seen_at": row[1].isoformat(),
        "last_signed_in_at": row[2].isoformat(),
        "sign_in_count": row[3],
        "proxy_wallet": row[4],
        "trader_rank": row[5],
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": JWT_TTL_SECONDS,
    }


@app.get("/api/polymarket/public-profile")
def polymarket_public_profile(request: Request) -> Dict[str, Any]:
    wallet = _extract_wallet_from_request(request)
    profile = _fetch_polymarket_public_profile(wallet)
    proxy_wallet = _proxy_wallet_from_profile(profile) or PM_NOT_REGISTERED_VALUE
    return {
        "wallet_address": wallet,
        "proxy_wallet": proxy_wallet,
        "profile": profile,
    }


@app.get("/api/me/nfts")
def me_nfts(request: Request) -> Dict[str, Any]:
    connected_wallet = _extract_wallet_from_request(request).lower()
    if not Web3.is_address(connected_wallet):
        raise HTTPException(status_code=400, detail="Invalid connected wallet")
    return _get_user_nfts_cached(connected_wallet)


@app.post("/api/mint/base-sepolia")
def mint_base_sepolia(request: Request) -> UserMintResponse:
    wallet = _extract_wallet_from_request(request).lower()
    proxy_wallet = _load_proxy_wallet_for_user_wallet(wallet)
    trader_rank = _load_trader_rank_for_user_wallet(wallet)
    block_reason = _mint_block_reason(proxy_wallet, trader_rank)
    if block_reason is not None:
        raise HTTPException(status_code=400, detail=block_reason)
    eligibility_wallet = _resolve_eligibility_wallet(wallet, proxy_wallet)
    linked_addresses = _addresses_for_claim_uniqueness(wallet, proxy_wallet)
    if not Web3.is_address(eligibility_wallet):
        raise HTTPException(status_code=400, detail="Mint blocked: invalid Polymarket proxy wallet format.")

    try:
        eligibility = season_manager.check_user_eligibility(eligibility_wallet)
        # Apply linked-wallet uniqueness guard for each stream before minting.
        for stream_key in ("genesis", "standard"):
            stream_payload = eligibility.get(stream_key)
            if not isinstance(stream_payload, dict):
                continue
            stream_season_id = int(stream_payload.get("season_id") or 0)
            if stream_season_id <= 0:
                continue
            eligibility[stream_key] = _apply_claim_uniqueness_guard(
                eligibility_payload=stream_payload,
                season_id=stream_season_id,
                addresses=linked_addresses,
            )

        eligible_streams = _collect_eligible_streams(eligibility)
        if not eligible_streams:
            raise ValueError(_build_eligibility_failure_message(eligibility))

        minted_claims: List[Dict[str, Any]] = []
        failed_claims: List[Dict[str, Any]] = []
        for stream_name, stream in eligible_streams:
            season_id = int(stream.get("season_id") or 0)
            if season_id <= 0:
                failed_claims.append(
                    {
                        "stream": stream_name,
                        "season_id": season_id,
                        "reason": "Invalid season id",
                    }
                )
                continue
            if _has_claim_in_season_any_address(season_id=season_id, addresses=linked_addresses):
                failed_claims.append(
                    {
                        "stream": stream_name,
                        "season_id": season_id,
                        "reason": "Already claimed in this season by connected/proxy wallet linkage",
                    }
                )
                continue
            lock_conn = _try_acquire_mint_lock(season_id=season_id, eligibility_wallet=eligibility_wallet)
            if lock_conn is None:
                failed_claims.append(
                    {
                        "stream": stream_name,
                        "season_id": season_id,
                        "reason": "Mint already in progress for this wallet/season. Retry in a few seconds.",
                    }
                )
                continue

            # Recipient is the connected wallet; eligibility/winner allocation use eligibility wallet.
            try:
                if _has_claim_in_season_any_address(season_id=season_id, addresses=linked_addresses):
                    failed_claims.append(
                        {
                            "stream": stream_name,
                            "season_id": season_id,
                            "reason": "Already claimed in this season by connected/proxy wallet linkage",
                        }
                    )
                    continue
                mint_result = mint_service.run_mint_claim(
                    MintClaimRequest(
                        wallet=eligibility_wallet,
                        recipient_address=wallet,
                        season_id=season_id,
                        phase=str(stream.get("phase") or "breach"),
                        auto_phase=True,
                        db_only=False,
                        blockchain=BLOCKCHAIN_BASE_ZORA,
                    )
                )
            except Exception as exc:
                logger.exception(
                    "Mint stream failed stream=%s season_id=%s wallet=%s eligibility_wallet=%s",
                    stream_name,
                    season_id,
                    wallet,
                    eligibility_wallet,
                )
                text = str(exc)
                failed_claims.append(
                    {
                        "stream": stream_name,
                        "season_id": season_id,
                        "reason": (
                            "Already claimed in this season by connected/proxy wallet linkage"
                            if "duplicate" in text.lower() or "unique" in text.lower()
                            else "Mint processing failed. Please retry shortly."
                        ),
                    }
                )
                continue
            finally:
                _release_mint_lock(lock_conn, season_id=season_id, eligibility_wallet=eligibility_wallet)

            minted_claims.append(
                {
                    "stream": stream_name,
                    "season_id": int(mint_result.get("season_id") or season_id),
                    "phase": str(mint_result.get("phase") or stream.get("phase") or ""),
                    "claim_id": int(mint_result.get("claim_id") or 0),
                    "chain": str(mint_result.get("chain") or "base_zora"),
                    "tx_hash": str((mint_result.get("mint_result") or {}).get("tx_hash") or ""),
                    "asset_address": str((mint_result.get("mint_result") or {}).get("asset_address") or ""),
                }
            )
    except HTTPException:
        raise
    except Exception as exc:
        detail = str(exc)
        if "Mint eligibility failed:" not in detail and "eligible" not in detail.lower():
            detail = _build_eligibility_failure_message(
                season_manager.check_user_eligibility(eligibility_wallet)
            )
        raise HTTPException(status_code=400, detail=detail)

    if not minted_claims:
        reasons = "; ".join(str(item.get("reason") or "Mint failed") for item in failed_claims) or "Mint failed"
        raise HTTPException(status_code=400, detail=reasons)

    status = "ok" if not failed_claims else "partial_success"
    message = (
        "Mint completed on Base Sepolia"
        if not failed_claims
        else f"Mint completed partially on Base Sepolia ({len(minted_claims)} success, {len(failed_claims)} failed)"
    )

    return UserMintResponse(
        status=status,
        message=message,
        wallet_address=wallet,
        proxy_wallet=proxy_wallet,
        minted_count=len(minted_claims),
        minted_claims=minted_claims,
        failed_claims=failed_claims,
    )
