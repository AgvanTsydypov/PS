from __future__ import annotations

import base64
import json
import logging
import mimetypes
import os
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
try:
    import boto3
    from botocore.config import Config
except Exception:  # pragma: no cover - keep import resilient in minimal envs
    boto3 = None
    Config = None
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
from admin_backend.main import SeasonWorkbenchService
from scripts.cardgen.generate_card import generate_card_back_svg, generate_card_svg

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
CLAIMS_UNIQUENESS_INDEX_NAME = "ux_claims_active_season_user_wallet_lower"
_r2_client: Any = None

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


def _r2_required_env() -> Dict[str, str]:
    endpoint = str(os.getenv("R2_ENDPOINT", "")).strip()
    bucket = str(os.getenv("R2_BUCKET", "")).strip()
    access_key = str(os.getenv("R2_ACCESS_KEY_ID", "")).strip()
    secret_key = str(os.getenv("R2_SECRET_ACCESS_KEY", "")).strip()
    public_base_url = str(os.getenv("R2_PUBLIC_BASE_URL", "")).strip().rstrip("/")
    if not endpoint:
        raise ValueError("R2_ENDPOINT is required")
    if not bucket:
        raise ValueError("R2_BUCKET is required")
    if not access_key:
        raise ValueError("R2_ACCESS_KEY_ID is required")
    if not secret_key:
        raise ValueError("R2_SECRET_ACCESS_KEY is required")
    if not public_base_url:
        raise ValueError("R2_PUBLIC_BASE_URL is required")
    return {
        "endpoint": endpoint,
        "bucket": bucket,
        "access_key": access_key,
        "secret_key": secret_key,
        "public_base_url": public_base_url,
    }


def _get_r2_client() -> Any:
    global _r2_client
    if boto3 is None or Config is None:
        raise ValueError("R2 upload dependencies are missing. Install boto3 and botocore.")
    if _r2_client is not None:
        return _r2_client
    cfg = _r2_required_env()
    _r2_client = boto3.client(
        "s3",
        endpoint_url=cfg["endpoint"],
        aws_access_key_id=cfg["access_key"],
        aws_secret_access_key=cfg["secret_key"],
        region_name="auto",
        config=Config(signature_version="s3v4"),
    )
    return _r2_client


def _generated_card_r2_key(slug: str, side: str) -> str:
    prefix = str(os.getenv("R2_PREFIX", "dev")).strip().strip("/")
    safe_slug = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in str(slug or "").strip())
    safe_side = "front" if side == "front" else "back"
    if prefix:
        return f"{prefix}/cards-images/{safe_slug}/{safe_side}.svg"
    return f"cards-images/{safe_slug}/{safe_side}.svg"


def _extract_r2_key_from_public_url(public_base_url: str, url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    base = public_base_url.rstrip("/")
    value = str(url).strip()
    if not value.startswith(base + "/"):
        return None
    return value[len(base) + 1 :]


def _delete_r2_object_by_key(key: Optional[str]) -> None:
    if not key:
        return
    try:
        cfg = _r2_required_env()
        _get_r2_client().delete_object(Bucket=cfg["bucket"], Key=key)
    except Exception:
        logger.warning("Could not delete generated card asset from R2 key=%s", key, exc_info=True)


def _upload_generated_card_assets_to_r2(slug: str, front_svg: str, back_svg: str) -> Tuple[str, str, str, str]:
    cfg = _r2_required_env()
    front_key = _generated_card_r2_key(slug, "front")
    back_key = _generated_card_r2_key(slug, "back")
    client = _get_r2_client()
    common_kwargs = {
        "Bucket": cfg["bucket"],
        "ContentType": "image/svg+xml",
        "CacheControl": "public, max-age=31536000, immutable",
    }

    def _put(key: str, body: str) -> None:
        client.put_object(Key=key, Body=body.encode("utf-8"), **common_kwargs)

    with ThreadPoolExecutor(max_workers=2) as pool:
        f_front = pool.submit(_put, front_key, front_svg)
        f_back = pool.submit(_put, back_key, back_svg)
        f_front.result()
        f_back.result()

    return (
        f"{cfg['public_base_url']}/{front_key}",
        f"{cfg['public_base_url']}/{back_key}",
        front_key,
        back_key,
    )


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
    cfg = _r2_required_env()
    current_front = str(row.get("front_image_path") or "").strip()
    current_back = str(row.get("back_image_path") or "").strip()
    front_key = _extract_r2_key_from_public_url(cfg["public_base_url"], current_front)
    back_key = _extract_r2_key_from_public_url(cfg["public_base_url"], current_back)
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
    front_svg = generate_card_svg(render_payload)
    back_svg = generate_card_back_svg(render_payload)
    front_url, back_url, _, _ = _upload_generated_card_assets_to_r2(slug, front_svg, back_svg)
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


def _load_wallet_session_row(user_wallet_address: str) -> Optional[Dict[str, Any]]:
    conn = _get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
            cursor.execute(
                """
                SELECT sign_in_count, proxy_wallet, trader_rank
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
        }
    return {
        "signed_in": True,
        "wallet_address": wallet,
        "sign_in_count": int(db_row.get("sign_in_count") or 0),
        "proxy_wallet": str(db_row.get("proxy_wallet") or "").strip() or None,
        "trader_rank": str(db_row.get("trader_rank") or "").strip() or None,
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
    _require_wallet_actions_enabled()
    connected_wallet = _extract_wallet_from_request(request).lower()
    if not Web3.is_address(connected_wallet):
        raise HTTPException(status_code=400, detail="Invalid connected wallet")

    conn = _get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
            cursor.execute(
                """
                SELECT
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
                FROM user_generated_cards
                WHERE LOWER(owner_wallet) = LOWER(%s)
                ORDER BY created_at DESC, id DESC
                """,
                (connected_wallet,),
            )
            rows = [dict(row) for row in cursor.fetchall()]
            total_available, remaining_available = _generated_cards_supply_counts(cursor)
    except Exception:
        logger.exception("Failed to load generated cards for wallet=%s", connected_wallet)
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    finally:
        conn.close()

    return {
        "wallet_address": connected_wallet,
        "items": [_format_generated_card_row(row, request=request, include_payload=True) for row in rows],
        "total": len(rows),
        "total_available": total_available,
        "remaining_available": remaining_available,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
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
                    wwin.proxy_wallet AS winner_proxy_wallet
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

            # ── Step 3: Render SVGs now that mint number is known ─────────────────
            render_payload = _build_render_payload(payload)
            phase_t = _log_card_get_phase("fetch_manual_image_http", phase_t)
            front_svg = generate_card_svg(render_payload)
            back_svg = generate_card_back_svg(render_payload)
            phase_t = _log_card_get_phase("generate_svg_local", phase_t)

            # ── Step 4: Upload to R2 ──────────────────────────────────────────────
            front_image_path, back_image_path, uploaded_front_key, uploaded_back_key = _upload_generated_card_assets_to_r2(
                slug,
                front_svg,
                back_svg,
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
        _delete_r2_object_by_key(uploaded_front_key)
        _delete_r2_object_by_key(uploaded_back_key)
        raise
    except Exception:
        conn.rollback()
        _delete_r2_object_by_key(uploaded_front_key)
        _delete_r2_object_by_key(uploaded_back_key)
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


