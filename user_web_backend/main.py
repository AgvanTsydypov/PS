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
from starlette.middleware.trustedhost import TrustedHostMiddleware
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
from scripts.http_fetch_ssrf import urlopen_after_ssrf_check

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


# ── DB connection pool ────────────────────────────────────────────────────────
# Per-request ``psycopg2.connect()`` was the root cause of intermittent 503s on
# /me: every eligibility request opened ~10 fresh TLS connections to Postgres
# (one per SeasonManager method × multiple methods). Under any concurrency this
# saturated ``max_connections``; hung requests then blew past nginx's
# upstream timeout and the user saw nginx's static 503 page.
#
# A ThreadedConnectionPool keeps a small set of connections open and reuses
# them across requests. Pool is initialized lazily on first call so module
# import (and the test suite, which stubs psycopg2) does not require a live
# database. Returned connections are wrapped in ``_PooledConnection`` so
# existing ``conn.close()`` patterns return-to-pool instead of actually
# closing — no call-site changes needed.
_db_pool = None
_db_pool_lock = threading.Lock()


def _ensure_db_pool():
    """Lazy-init a process-wide connection pool. Idempotent and thread-safe."""
    global _db_pool
    if _db_pool is not None:
        return _db_pool
    with _db_pool_lock:
        if _db_pool is None:
            from psycopg2.pool import ThreadedConnectionPool
            minconn = max(1, int(os.getenv("USER_WEB_DB_POOL_MIN", "1") or 1))
            maxconn = max(minconn, int(os.getenv("USER_WEB_DB_POOL_MAX", "10") or 10))
            _db_pool = ThreadedConnectionPool(minconn, maxconn, **_db_params())
    return _db_pool


class _PooledConnection:
    """Thin proxy over a pooled psycopg2 connection.

    Delegates everything to the wrapped connection except ``close()``, which
    returns the connection to the pool instead of really closing it. Allows
    ``try / finally: conn.close()`` call sites to keep working unmodified.
    """

    __slots__ = ("_conn", "_released")

    def __init__(self, conn):
        self._conn = conn
        self._released = False

    def close(self):
        if self._released:
            return
        self._released = True
        try:
            _ensure_db_pool().putconn(self._conn)
        except Exception:
            try:
                self._conn.close()
            except Exception:
                pass

    # ``with conn:`` — psycopg2 connections support transaction context. The
    # underlying ``__enter__`` returns the real connection and commits/rolls
    # back on ``__exit__``. We forward both so callers using ``with conn:``
    # behave exactly like before; close() is still our responsibility.
    def __enter__(self):
        return self._conn.__enter__()

    def __exit__(self, exc_type, exc, tb):
        return self._conn.__exit__(exc_type, exc, tb)

    def __getattr__(self, name):
        return getattr(self._conn, name)


def _get_connection():
    return _PooledConnection(_ensure_db_pool().getconn())


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


class MintMyNftRequest(BaseModel):
    """Body for POST /api/me/mint."""

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


def _trusted_hosts() -> List[str]:
    """Hostnames allowed in the ``Host`` header. Anything else gets 400.

    Defends against virtual-host confusion: without this, the server happily
    answers requests directed at the raw IP, at internal hostnames, or at
    spoofed ``Host`` headers used for cache poisoning / password-reset link
    forgery. ``USER_WEB_TRUSTED_HOSTS`` must be set in production; supports
    leading ``*.`` wildcards (Starlette semantics). The dev fallback covers
    only loopback so a misconfigured prod doesn't silently inherit it.
    """
    raw = os.getenv("USER_WEB_TRUSTED_HOSTS", "").strip()
    if raw:
        return [host.strip() for host in raw.split(",") if host.strip()]
    if os.getenv("NODE_ENV", "development") == "development":
        return ["localhost", "127.0.0.1", "testserver"]
    return []


_trusted_hosts_initial = _trusted_hosts()
if _trusted_hosts_initial:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=_trusted_hosts_initial)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
    allow_headers=[
        "Accept",
        "Accept-Language",
        "Content-Type",
        "Origin",
        "Referer",
        "User-Agent",
        "X-Requested-With",
    ],
)

CHALLENGE_TTL_SECONDS = int(os.getenv("USER_WEB_CHALLENGE_TTL_SECONDS", "300"))
season_manager = SeasonManager(use_local_db=True, connection_factory=_get_connection)
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

# Home-page ticker feed: random sample of minted STARs *plus* unminted
# preview cards from the admin showcase simulator.
#
# Two sources, same ticker column shape (``slug``, ``card_title``,
# ``front_image_path``, ``back_image_path``, ``created_at``) so the
# downstream cache and frontend code don't need to know which side a row
# came from:
#
#   1. ``claims`` (minted): denormalized at mint time by
#      ``denormalize_card_onto_claim``. Status filter excludes
#      QUEUED/PENDING/PROCESSING (no rendered card yet) and FAILED
#      (no on-chain artefact).
#   2. ``preview_cards`` (showcase buffer): populated by the admin
#      ``/api/scenarios/simulate-generated-cards-batch`` endpoint, drained
#      slot-by-slot when a real user mints onto the same slot — at that
#      point the preview is deleted by slug in the same transaction that
#      writes ``claims.card_slug``. So a single slug is in claims XOR
#      preview_cards, never both, and the UNION won't duplicate cards.
#
# Both branches require non-empty image paths because the simulator and
# the mint worker both INSERT placeholder rows first and UPDATE the URLs
# after R2/Pinata upload — without that filter we'd surface half-rendered
# in-flight rows whose images aren't yet reachable.
_CARDS_TICKER_SAMPLE_SQL = """
SELECT slug, card_title, front_image_path, back_image_path, created_at
FROM (
    SELECT c.card_slug                          AS slug,
           c.card_title                         AS card_title,
           c.front_image_url                    AS front_image_path,
           c.back_image_url                     AS back_image_path,
           COALESCE(c.timestamp, c.created_at)  AS created_at
    FROM claims c
    WHERE c.status = 'COMPLETED'
      AND c.card_slug IS NOT NULL
      AND BTRIM(c.card_slug) <> ''
      AND c.front_image_url IS NOT NULL
      AND c.back_image_url  IS NOT NULL
    UNION ALL
    SELECT pc.slug                              AS slug,
           pc.card_title                        AS card_title,
           pc.front_image_path                  AS front_image_path,
           pc.back_image_path                   AS back_image_path,
           pc.created_at                        AS created_at
    FROM preview_cards pc
    WHERE pc.slug IS NOT NULL
      AND BTRIM(pc.slug) <> ''
      AND pc.front_image_path IS NOT NULL
      AND BTRIM(pc.front_image_path) <> ''
      AND pc.back_image_path  IS NOT NULL
      AND BTRIM(pc.back_image_path)  <> ''
) ticker
ORDER BY RANDOM()
LIMIT %s
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
    "ICARUS",
    "BURNER",
    "BOT",
    "EXTRACTOR",
    "PASSENGER",
    "ANOMALY",
    "INSIDER",
    "SIGNAL",
    "VECTOR",
    "EQUILIBRIUM",
    "GRAVITON",
    "SUBSTRATE",
    "OPERATOR",
)
LEGACY_ENTRY_BRACKET_MAP: Dict[str, str] = {
    "ANOMALY": "[0.00 - 0.20]",
    "ORACLE": "[0.20 - 0.40]",
    "OUTLIER": "[0.40 - 0.60]",
    "VECTOR": "[0.60 - 0.80]",
    "EXTRACTOR": "[0.97 - 1.00]",
    "PASSENGER": "[0.97 - 1.00]",
}


def _cleanup_expired_siwe_challenges_loop() -> None:
    """Best-effort periodic purge so abandoned SIWE rows do not accumulate."""
    while True:
        threading.Event().wait(60)
        conn = _get_connection()
        try:
            with conn.cursor() as cursor:
                _purge_stale_siwe_challenges(cursor)
            conn.commit()
        except Exception:
            conn.rollback()
            logger.debug("SIWE challenge purge tick failed", exc_info=True)
        finally:
            conn.close()


threading.Thread(target=_cleanup_expired_siwe_challenges_loop, daemon=True).start()


def _normalize_evm_address(value: str) -> str:
    if not Web3.is_address(value):
        raise HTTPException(status_code=400, detail="Invalid wallet address")
    return Web3.to_checksum_address(value)


def _siwe_uri() -> str:
    """Canonical URI bound into the SIWE message (EIP-4361 ``URI`` field)."""
    explicit = os.getenv("USER_WEB_SIWE_URI", "").strip().rstrip("/")
    if explicit:
        return explicit
    return CARD_BASE_URL


def _siwe_domain() -> str:
    """RFC 3986 authority bound into the SIWE message (EIP-4361 ``domain`` field).

    Wallets compare this against ``window.location.host`` and warn the user when
    they don't match, which is the actual phishing protection. Defaults to the
    host of ``USER_WEB_SIWE_URI`` so the two stay in sync; override with
    ``USER_WEB_SIWE_DOMAIN`` only if the public host differs from the URI host
    (e.g. when proxied behind a different external hostname).
    """
    explicit = os.getenv("USER_WEB_SIWE_DOMAIN", "").strip()
    if explicit:
        return explicit
    parsed = urllib.parse.urlparse(_siwe_uri())
    host = parsed.netloc or parsed.path
    return host.strip("/")


def _siwe_chain_id() -> int:
    raw = os.getenv("USER_WEB_SIWE_CHAIN_ID", "").strip() or os.getenv("EVM_CHAIN_ID", "").strip()
    if not raw:
        return 1
    try:
        return int(raw)
    except ValueError:
        return 1


def _build_challenge_message(wallet_address: str, nonce: str, expires_at: datetime) -> str:
    """Build a canonical EIP-4361 ``Sign-In with Ethereum`` message.

    Spec: https://eips.ethereum.org/EIPS/eip-4361. The address must be EIP-55
    checksummed (callers normalize via ``_normalize_evm_address``). The
    ``Domain``, ``URI``, ``Chain ID`` and ``Expiration Time`` lines are what
    let wallets like MetaMask / Rabby render the enhanced sign-in UI and warn
    the user if the message domain doesn't match the calling page's origin —
    that's the cross-site signature-reuse defence we previously lacked.
    """
    issued_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    expiration_time = expires_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return (
        f"{_siwe_domain()} wants you to sign in with your Ethereum account:\n"
        f"{wallet_address}\n"
        "\n"
        "Sign in to PolyStars\n"
        "\n"
        f"URI: {_siwe_uri()}\n"
        "Version: 1\n"
        f"Chain ID: {_siwe_chain_id()}\n"
        f"Nonce: {nonce}\n"
        f"Issued At: {issued_at}\n"
        f"Expiration Time: {expiration_time}"
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


def _purge_stale_siwe_challenges(cursor: Any) -> None:
    cursor.execute("DELETE FROM wallet_siwe_challenges WHERE expires_at < NOW()")


def _insert_siwe_challenge_record(
    challenge_id: str,
    wallet_address: str,
    message: str,
    expires_at: datetime,
) -> None:
    conn = _get_connection()
    try:
        with conn.cursor() as cursor:
            _purge_stale_siwe_challenges(cursor)
            cursor.execute(
                """
                INSERT INTO wallet_siwe_challenges (id, wallet_address, message, expires_at)
                VALUES (%s, %s, %s, %s)
                """,
                (challenge_id, wallet_address.lower(), message, expires_at),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _peek_siwe_challenge(challenge_id: str) -> Tuple[Optional[ChallengeRecord], str]:
    """Return ``(record, status)`` where status is ``ok``, ``missing``, or ``expired``."""
    conn = _get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT wallet_address, message, expires_at
                FROM wallet_siwe_challenges
                WHERE id = %s
                """,
                (challenge_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None, "missing"
            wallet, msg, exp = row[0], row[1], row[2]
            if getattr(exp, "tzinfo", None) is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if exp < datetime.now(timezone.utc):
                return None, "expired"
            return (
                ChallengeRecord(
                    wallet_address=str(wallet),
                    message=str(msg),
                    expires_at=exp,
                ),
                "ok",
            )
    finally:
        conn.close()


def _delete_siwe_challenge_by_id(challenge_id: str) -> None:
    conn = _get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("DELETE FROM wallet_siwe_challenges WHERE id = %s", (challenge_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        logger.exception("Failed to delete SIWE challenge id=%s", challenge_id)
    finally:
        conn.close()


def _update_generated_card_asset_urls(slug: str, front_url: str, back_url: str) -> None:
    conn = _get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE preview_cards
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
    return _normalize_choice(cleaned, CARD_ARCHETYPE_OPTIONS, inferred)


def _normalize_archetype_for_stats(raw: Optional[str]) -> str:
    """Same mapping as mint payloads, but unknown / empty labels bucket to UNKNOWN (not a card fallback)."""
    cleaned = str(raw or "").strip().upper()
    if not cleaned:
        return "UNKNOWN"
    if cleaned.startswith("THE "):
        cleaned = cleaned[4:].strip()
    return _normalize_choice(cleaned, CARD_ARCHETYPE_OPTIONS, "UNKNOWN")


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
    if entry_cwap is not None and entry_cwap <= 0.10 and yld == "P99":
        return "INSIDER"
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
        return "GRAVITON"
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

    - *Winner side*: `claims.proxy_wallet` (trader identity frozen on the allocation row).
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
    "P99":  "#FFD700",
    "P90":  "#FFBF00",
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
    with urlopen_after_ssrf_check(req, timeout=effective_timeout) as response:
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


def _generated_cards_supply_counts(
    cursor: Any,
    *,
    use_cached_join_total: bool = True,  # noqa: ARG001 — kept for call-site parity
) -> Tuple[int, int]:
    """Return (total_pool, remaining_pool) using the participants partitions
    of all seasons. Total = participants whose event has a ready
    ``manual_image_url``. Remaining = same minus proxy_wallets that already
    have an active claim in their season."""
    cursor.execute(
        """
        SELECT
            COUNT(*) AS total_count,
            COUNT(*) FILTER (
                WHERE NOT EXISTS (
                    SELECT 1 FROM claims c
                    WHERE c.season_id = p.season_id
                      AND c.proxy_wallet IS NOT NULL
                      AND LOWER(c.proxy_wallet) = LOWER(p.proxy_wallet)
                      AND c.status IN ('QUEUED','PENDING','PROCESSING','COMPLETED')
                )
            ) AS remaining_count
        FROM participants p
        JOIN event_cards ec ON ec.event_id = p.event_id
        WHERE ec.manual_image_url IS NOT NULL
          AND BTRIM(ec.manual_image_url) <> ''
        """
    )
    row = cursor.fetchone() or {}
    if isinstance(row, dict):
        total = int(row.get("total_count") or 0)
        remaining = int(row.get("remaining_count") or 0)
    else:
        total = int(row[0] or 0)
        remaining = int(row[1] or 0)
    return total, remaining


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


def _metadata_embedded_card(metadata: Any) -> Optional[Dict[str, Any]]:
    """Return ``card_display_data`` (new) or legacy ``polystars_card`` from metadata JSON."""
    if not isinstance(metadata, dict):
        return None
    block = metadata.get("card_display_data")
    if isinstance(block, dict):
        return block
    legacy = metadata.get("polystars_card")
    return legacy if isinstance(legacy, dict) else None


def _extract_card_slug_from_polystars_card(polystars_card: Any) -> Optional[str]:
    """Extract the in-app card slug from ``qr_payload`` on an embedded card dict.

    Mint metadata uses ``card_display_data`` (or legacy ``polystars_card``); see
    ``scripts/polystars_card_payload.py`` for ``qr_payload`` shape.
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

    Mirrors the Metaplex NFT metadata layout: the primary front image is in
    ``image``, and ``properties.files`` lists ``[front, back]`` so the back
    image is the first ``files[].uri`` that differs from ``image``.
    The in-app card slug is recovered from ``card_display_data`` (or legacy
    ``polystars_card``) ``qr_payload`` so minted STARs deep-link to ``/cards/{slug}``.

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
    card_slug = _extract_card_slug_from_polystars_card(_metadata_embedded_card(metadata))
    return {
        "name": name,
        "front_image_url": front,
        "back_image_url": back,
        "card_slug": card_slug,
    }


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
    user does not have a leaderboard rank yet. Used both for UI surfaces and
    as a hard mint gate in ``/api/me/mint`` — wallets without a real rank
    (registered but never traded) are not allowed to mint.
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


def _trust_x_forwarded_for_rate_limit() -> bool:
    """When true, use the first ``X-Forwarded-For`` hop as the client IP for rate limits.

    Enable only when this app is deployed behind a trusted reverse proxy that sets
    ``X-Forwarded-For`` correctly; otherwise clients can spoof arbitrary IPs.
    """
    return os.getenv("USER_WEB_TRUST_X_FORWARDED_FOR", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _rate_limit_client_ip(request: Request) -> str:
    if _trust_x_forwarded_for_rate_limit():
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            parts = [p.strip() for p in forwarded.split(",") if p.strip()]
            if parts:
                return parts[0][:200]
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    cfg = RATE_LIMITS.get(request.url.path)
    if cfg is not None:
        client_ip = _rate_limit_client_ip(request)
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


def _enforce_production_security_invariants() -> None:
    """Hard-fail startup when production-critical secrets/CORS are misconfigured.

    A silent ``[]`` allowlist or a 4-byte JWT secret is the kind of misconfig
    that boots successfully and only fails open in production. We refuse to
    start under those conditions in non-development modes.
    """
    if os.getenv("NODE_ENV", "development") == "development":
        return

    origins = _allowed_origins()
    if not origins:
        raise RuntimeError(
            "USER_WEB_CORS_ORIGINS must be set to a comma-separated list of "
            "https origins in production (e.g. 'https://polystars.app'). "
            "Empty allow-list with credentialed CORS is a misconfiguration."
        )
    bad = [o for o in origins if o in ("*", "null") or "*" in o]
    if bad:
        raise RuntimeError(
            f"USER_WEB_CORS_ORIGINS contains wildcard/null entries which are "
            f"incompatible with allow_credentials=True: {bad!r}"
        )

    secret = os.getenv("USER_WEB_JWT_SECRET", "").strip()
    if len(secret) < 32:
        raise RuntimeError(
            "USER_WEB_JWT_SECRET must be at least 32 characters in production "
            "(generate one with `python -c \"import secrets; print(secrets.token_urlsafe(48))\"`). "
            "Short HS256 secrets are brute-forceable offline."
        )

    if not _trusted_hosts():
        raise RuntimeError(
            "USER_WEB_TRUSTED_HOSTS must be set in production (e.g. "
            "'polystars.app,www.polystars.app'). Without an allowlist the "
            "server answers requests directed at the raw IP or arbitrary "
            "Host headers, enabling cache-poisoning and link-forgery attacks."
        )

    if not os.getenv("USER_WEB_SIWE_DOMAIN", "").strip() and not os.getenv("USER_WEB_SIWE_URI", "").strip():
        # Defaults derive from CARD_BASE_URL; only refuse to boot if that's also unset.
        if not os.getenv("CARD_BASE_URL", "").strip() and not os.getenv("NEXT_PUBLIC_APP_URL", "").strip():
            raise RuntimeError(
                "SIWE binding requires USER_WEB_SIWE_URI (or CARD_BASE_URL / "
                "NEXT_PUBLIC_APP_URL) so the signed message embeds the public "
                "domain. Set it to your user-facing https origin."
            )


@app.on_event("startup")
def startup_checks() -> None:
    _enforce_production_security_invariants()
    _configure_user_web_timing_logging()
    _warn_if_claims_uniqueness_index_missing()
    _warm_rasterizer_pool_in_background()


def _warm_rasterizer_pool_in_background() -> None:
    """Boot the Playwright worker pool off the startup thread.

    ``/api/cards/get`` lazily rasterizes showcase SVGs to PNG on first access,
    and the first ``svg_to_png`` call otherwise pays the full Chromium cold
    start. Pre-warming here makes the first real request land on an
    already-ready pool while keeping uvicorn's startup non-blocking —
    if Playwright is missing or browsers crash, the warm thread logs and
    exits without breaking health checks.
    """
    import threading

    def _warm() -> None:
        try:
            from scripts.cardgen.rasterize import warmup

            warmup()
        except Exception:
            logger.warning("rasterizer pool warmup failed; continuing lazy", exc_info=True)

    threading.Thread(target=_warm, name="rasterizer-warmup", daemon=True).start()


@app.on_event("shutdown")
def _shutdown_rasterizer_pool() -> None:
    """Close Chromium workers cleanly on uvicorn reload/stop so we don't
    leak ``chrome-headless-shell`` zombie processes."""
    try:
        from scripts.cardgen.rasterize import shutdown

        shutdown()
    except Exception:
        logger.warning("rasterizer shutdown failed", exc_info=True)


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


# ── "Community achievements" board — frozen ──────────────────────────────────
# Both endpoints below power the homepage "Community achievements" widget,
# which is currently UNDER CONSTRUCTION. They are disabled at the route layer
# so no DB load is incurred even if a stale frontend client tries to call
# them. Restore the previous bodies (see git history) when re-enabling.

_COMMUNITY_ACHIEVEMENTS_DISABLED_DETAIL = "Community achievements feature is under construction."


@app.get("/api/seasons/catalog")
def seasons_catalog() -> Dict[str, Any]:
    raise HTTPException(status_code=503, detail=_COMMUNITY_ACHIEVEMENTS_DISABLED_DETAIL)


@app.get("/api/seasons/{season_id}/opened-archetypes")
def season_opened_archetype_counts(season_id: int) -> Dict[str, Any]:
    raise HTTPException(status_code=503, detail=_COMMUNITY_ACHIEVEMENTS_DISABLED_DETAIL)


def _mask_hex_address_for_public_ticker(addr: str) -> str:
    """Reduces wallet-address scraping value while keeping a short visual hint."""
    raw = (addr or "").strip()
    if not raw:
        return ""
    if raw.lower().startswith("0x") and len(raw) >= 12:
        return f"{raw[:6]}…{raw[-4:]}"
    if len(raw) >= 10:
        return f"{raw[:4]}…{raw[-4:]}"
    return "…"


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
                    FROM participants
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

    wallets = [
        _mask_hex_address_for_public_ticker(str(row[0]))
        for row in rows
        if row and row[0]
    ]
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
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=CHALLENGE_TTL_SECONDS)
    message = _build_challenge_message(wallet_address, nonce, expires_at)

    _insert_siwe_challenge_record(
        challenge_id,
        wallet_address,
        message,
        expires_at,
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
    challenge, peek_status = _peek_siwe_challenge(payload.challenge_id)
    if peek_status == "missing":
        raise HTTPException(status_code=400, detail="Unknown challenge")
    if peek_status == "expired":
        _delete_siwe_challenge_by_id(payload.challenge_id)
        raise HTTPException(status_code=400, detail="Challenge expired")
    assert challenge is not None
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
            cursor.execute(
                "DELETE FROM wallet_siwe_challenges WHERE id = %s",
                (payload.challenge_id,),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        logger.exception("Failed to persist wallet sign-in")
        raise HTTPException(
            status_code=503,
            detail="Sign-in could not be completed. Please try again.",
        ) from None
    finally:
        conn.close()

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
        samesite="strict",
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
        samesite="strict",
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
            cursor.execute(_CARDS_TICKER_SAMPLE_SQL, (safe_limit,))
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


# Preview card detail — reads the live preview buffer. Same shape as the
# minted-detail endpoint so the frontend can share a single card component.
#
# ``collection_mint_number`` is deliberately NOT selected here: previews are
# not part of the minted collection, so exposing a "Collection mint #N" on
# the preview response would be a meaningless contract. The number still
# exists on ``preview_cards`` (it's burned into the card-back SVG at
# preview-creation time), just not surfaced through the public API.
_PREVIEW_CARD_DETAIL_SQL = """
SELECT
    gc.id,
    gc.slug,
    gc.owner_wallet,
    gc.owner_proxy_wallet,
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
    gc.owner_proxy_wallet AS winner_proxy_wallet,
    NULL::text AS minted_asset_address
FROM preview_cards gc
LEFT JOIN events e ON e.id = gc.event_id
WHERE gc.slug = %s
LIMIT 1
"""

# Minted card detail — reads the denormalized card fields that
# ``promote_preview_to_claim`` wrote onto ``claims``. Aliases ``card_slug``,
# ``front_image_url`` and ``back_image_url`` to the preview-shaped column
# names so a single formatter (``_format_generated_card_row``) can render
# both preview and minted responses.
_MINTED_CARD_DETAIL_SQL = """
SELECT
    c.id,
    c.collection_mint_number,
    c.card_slug AS slug,
    COALESCE(NULLIF(c.recipient_address, ''), c.user_wallet) AS owner_wallet,
    uws.proxy_wallet AS owner_proxy_wallet,
    c.season_id,
    c.event_id AS event_id,
    c.event_slug AS event_slug,
    c.card_title,
    c.primary_tag,
    c.secondary_tag,
    c.pattern,
    c.front_image_url AS front_image_path,
    c.back_image_url  AS back_image_path,
    c.card_payload_json,
    c.timestamp AS created_at,
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
    c.proxy_wallet AS winner_proxy_wallet,
    c.asset_address AS minted_asset_address,
    c.metadata_uri AS minted_metadata_uri
FROM claims c
LEFT JOIN events e ON e.id = c.event_id
LEFT JOIN user_wallet_signins uws ON LOWER(uws.wallet_address) = LOWER(COALESCE(NULLIF(c.recipient_address, ''), c.user_wallet))
WHERE c.card_slug = %s
LIMIT 1
"""


def _build_card_detail_response(row_dict: Dict[str, Any], request: Request) -> Dict[str, Any]:
    """Shared formatter for the unified ``/api/cards/{slug}`` endpoint.

    Both the minted-claim SQL and the preview-buffer SQL select the same
    aliased column shape (slug, card_title, front_image_path,
    back_image_path, card_payload_json, event_*, winner_proxy_wallet,
    minted_asset_address) so one formatter can serve both preview and
    minted rows. The caller stamps ``card["is_preview"]`` to tell the
    frontend whether to render the minted-only chips (mint number,
    explorer links).
    """
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

    # Etherscan/L2-explorer link for minted rows. Computed from the
    # ``"<contract>/<tokenId>"`` asset_address string so we don't need an
    # RPC round-trip on the read path. ``EVM_CHAIN_ID`` env decides which
    # explorer host (``etherscan_nft_url`` knows mainnet, Sepolia, Base,
    # Base Sepolia; falls back to mainnet for unknowns). Preview rows have
    # no asset_address yet → no link.
    explorer_url: Optional[str] = None
    if minted_asset_address:
        try:
            from scripts.evm_service import parse_asset_address, etherscan_nft_url
            contract_part, token_id = parse_asset_address(minted_asset_address)
            if contract_part and token_id is not None:
                chain_id_raw = os.environ.get("EVM_CHAIN_ID", "").strip()
                chain_id = int(chain_id_raw) if chain_id_raw else 1
                explorer_url = etherscan_nft_url(contract_part, token_id, chain_id)
        except Exception:
            explorer_url = None
    card["explorer_asset_url"] = explorer_url

    raw_metadata_uri = str(row_dict.get("minted_metadata_uri") or "").strip() or None
    card["metadata_uri"] = _normalize_ipfs_gateway_url(raw_metadata_uri) if raw_metadata_uri else None

    return {"card": card}


def _fetch_card_detail_row(sql: str, slug: str, log_label: str) -> Optional[Dict[str, Any]]:
    conn = _get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
            cursor.execute(sql, (slug,))
            row = cursor.fetchone()
    except Exception:
        logger.exception("Failed to load %s slug=%s", log_label, slug)
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    finally:
        conn.close()
    return dict(row) if row else None


@app.get("/api/cards/{slug}")
def card_by_slug(slug: str, request: Request) -> Dict[str, Any]:
    """Unified card detail — serves both minted STARs and preview cards.

    Lookup order:
      1. ``claims`` (minted): denormalized card fields written by
         ``denormalize_card_onto_claim`` after a successful on-chain mint.
         Carries ``collection_mint_number`` and on-chain explorer links.
      2. ``preview_cards`` (preview): the live showcase buffer, populated
         before any winner has been allocated. Same JSON shape, but
         ``collection_mint_number`` / ``asset_address`` are absent.

    A slug naturally migrates from preview → minted: the cron worker writes
    the slug onto the claims row and deletes the matching preview row in
    the same transaction, so this URL keeps working through the transition
    with zero redirect — only the underlying data changes (and ``is_preview``
    flips to ``false``). That gives the showcase ticker permanent links
    that survive a card being minted.
    """
    normalized_slug = str(slug or "").strip()
    if not normalized_slug:
        raise HTTPException(status_code=400, detail="Card slug is required")

    row = _fetch_card_detail_row(_MINTED_CARD_DETAIL_SQL, normalized_slug, "minted card")
    if row:
        response = _build_card_detail_response(row, request)
        response["card"]["is_preview"] = False
        return response

    row = _fetch_card_detail_row(_PREVIEW_CARD_DETAIL_SQL, normalized_slug, "preview card")
    if row:
        response = _build_card_detail_response(row, request)
        response["card"]["is_preview"] = True
        return response

    raise HTTPException(status_code=404, detail="Card not found")


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


@app.get("/api/me/eligibility")
def me_eligibility(request: Request) -> Dict[str, Any]:
    """Mint eligibility for the signed-in wallet across the current Genesis and Standard streams."""
    _require_wallet_actions_enabled()
    wallet = _extract_wallet_from_request(request).lower()
    if not Web3.is_address(wallet):
        raise HTTPException(status_code=400, detail="Invalid connected wallet")
    try:
        return season_manager.check_user_eligibility(wallet)
    except Exception:
        logger.exception("Failed to compute eligibility for wallet=%s", wallet)
        raise HTTPException(
            status_code=503,
            detail="Eligibility could not be determined. Please try again later.",
        ) from None


@app.post("/api/me/mint")
def me_mint(payload: MintMyNftRequest, request: Request) -> Dict[str, Any]:
    """Mints the next eligible NFT for the signed-in wallet and given season."""
    _require_wallet_actions_enabled()
    wallet = _extract_wallet_from_request(request).lower()
    if not Web3.is_address(wallet):
        raise HTTPException(status_code=400, detail="Invalid connected wallet")

    # Strict server-side gate, two checks:
    #   1. Wallet must be registered on Polymarket (has a real proxy wallet,
    #      not the sentinel ``PM_NOT_REGISTERED_VALUE``).
    #   2. Wallet must have a real Polymarket leaderboard rank. Sentinels
    #      ("Not registered in PM" / "No trades yet") fail this check —
    #      registered-but-never-traded wallets cannot mint.
    # The frontend mirrors both checks, but we MUST also enforce them here
    # so the API cannot be bypassed.
    #
    # Both checks do a *live* lookup against Polymarket's public APIs so the
    # gate does not depend on whatever was cached in ``user_wallet_signins``
    # at sign-in time (which can be stale or empty if the API was temporarily
    # unavailable then). If a live call fails, we fall back to the cached
    # snapshot so a transient Polymarket outage does not lock legitimate
    # users out of minting.
    cached_proxy_wallet, cached_trader_rank = _load_wallet_signin_snapshot(wallet)
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

    # Trader-rank gate. Mirrors the proxy-wallet gate above: live lookup with
    # cached fallback. ``_fetch_polymarket_trader_rank`` returns ``(rank, api_available)``;
    # we treat any non-exception result as a successful lookup so a wallet
    # that genuinely has no rank yet ("No trades yet") is correctly rejected
    # instead of falling back to a possibly-stale cache.
    live_trader_rank: Optional[str] = None
    trader_rank_lookup_succeeded = False
    try:
        live_trader_rank, _live_api_available = _fetch_polymarket_trader_rank(
            effective_proxy_wallet or ""
        )
        trader_rank_lookup_succeeded = bool(_live_api_available)
    except Exception as exc:
        logger.warning(
            "Live Polymarket trader-rank lookup failed for wallet=%s proxy=%s: %s; "
            "falling back to cached trader_rank=%r",
            wallet,
            effective_proxy_wallet,
            str(exc),
            cached_trader_rank,
        )

    effective_trader_rank = (
        live_trader_rank if trader_rank_lookup_succeeded else cached_trader_rank
    )
    has_rank = _has_valid_polymarket_rank(effective_trader_rank)
    logger.info(
        "Polymarket trader-rank mint gate: wallet=%s live_ok=%s live_rank=%r "
        "cached_rank=%r effective_rank=%r has_rank=%s",
        wallet,
        trader_rank_lookup_succeeded,
        live_trader_rank,
        cached_trader_rank,
        effective_trader_rank,
        has_rank,
    )
    if not has_rank:
        raise HTTPException(
            status_code=403,
            detail="WALLET HAS NO POLYMARKET TRADING HISTORY.",
        )

    try:
        season_id = int(payload.season_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid season_id")
    if season_id <= 0:
        raise HTTPException(status_code=400, detail="Invalid season_id")

    mint_request = MintClaimRequest(
        wallet=wallet,
        recipient_address=wallet,
        season_id=season_id,
        phase="breach",
        auto_phase=True,
        db_only=False,
    )
    try:
        return mint_service.run_queue_mint_request(mint_request)
    except HTTPException:
        raise
    except ValueError as exc:
        # ``run_queue_mint_request`` raises ValueError for predictable user-
        # facing rejections: "Wallet is non-origin but phase='vault'", "user
        # wallet already has an active claim in this season", "season pool
        # exhausted", phase detection / enum errors, invalid recipient, etc.
        # Surface the message verbatim so the dashboard can show the real
        # reason instead of a generic "try again later".
        logger.info(
            "Mint rejected for wallet=%s season_id=%s: %s",
            wallet,
            season_id,
            exc,
        )
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except Exception:
        logger.exception(
            "Mint failed for wallet=%s season_id=%s",
            wallet,
            season_id,
        )
        raise HTTPException(
            status_code=503,
            detail="Mint could not be completed. Please try again later.",
        ) from None


# ──────────────────────────────────────────────────────────────────────────────
# /api/me/cards — owned-on-chain PolyStars NFTs for the signed-in wallet
# ──────────────────────────────────────────────────────────────────────────────
# Source of truth is on-chain ownership on the configured EVM contract
# (Sepolia in test, Ethereum mainnet in prod). For each COMPLETED claim that
# names this wallet (recipient_address with fallback to user_wallet for
# legacy rows), we call ``ownerOf(tokenId)`` and only return claims whose
# token is still held by the wallet. Transferred-away NFTs disappear; burnt
# tokens disappear (ownerOf reverts → filtered out).
#
# Replaces a previous Solana/Metaplex DAS-based implementation removed in the
# Solana-cleanup commit; the response shape matches the frontend's
# ``MyMintedNftsResponse`` so the dashboard component is unchanged.

# In-memory cache so dashboard refreshes don't hammer the RPC. TTL is
# intentionally short (default 30s) — on-chain transfers are rare but when
# they happen the user expects the panel to update quickly.
_ME_CARDS_CACHE_TTL_SECONDS = max(0, int(os.environ.get("USER_WEB_ME_CARDS_TTL_SECONDS", "30") or 30))
_ME_CARDS_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_ME_CARDS_CACHE_LOCK = threading.Lock()


def _me_cards_cache_get(wallet_lower: str) -> Optional[Dict[str, Any]]:
    if _ME_CARDS_CACHE_TTL_SECONDS <= 0:
        return None
    now = monotonic()
    with _ME_CARDS_CACHE_LOCK:
        entry = _ME_CARDS_CACHE.get(wallet_lower)
        if entry and entry[0] > now:
            return entry[1]
    return None


def _me_cards_cache_set(wallet_lower: str, payload: Dict[str, Any]) -> None:
    if _ME_CARDS_CACHE_TTL_SECONDS <= 0:
        return
    with _ME_CARDS_CACHE_LOCK:
        _ME_CARDS_CACHE[wallet_lower] = (
            monotonic() + _ME_CARDS_CACHE_TTL_SECONDS,
            payload,
        )


# Loads denormalized card metadata for any tokenId currently owned on-chain
# by the signed-in wallet. The wallet itself is NOT in the WHERE clause —
# that's by design: a STAR transferred from another wallet still belongs to
# whoever holds it on-chain right now, but the claims row was written
# against the original recipient. We match purely by tokenId on the
# configured contract and let on-chain ownerOf() be the authority.
#
# ``%(contract)s`` is the lowercased contract address (asset_address is
# stored as ``"<contract>/<tokenId>"`` and we LOWER both sides for a
# case-insensitive match). ``%(token_ids)s`` is a tuple of integer tokenIds
# rendered to text for the ``LIKE`` join.
_ME_CARDS_CLAIMS_SQL = """
SELECT
    c.id                       AS claim_id,
    c.asset_address            AS asset_address,
    c.tx_hash                  AS tx_hash,
    c.metadata_uri             AS metadata_uri,
    c.season_id                AS season_id,
    s.type                     AS season_type,
    s.season_number            AS season_number,
    c.phase_type               AS phase,
    c.collection_mint_number   AS collection_mint_number,
    c.card_title               AS name,
    c.front_image_url          AS front_image_url,
    c.back_image_url           AS back_image_url,
    c.card_slug                AS card_slug,
    c.timestamp                AS minted_at
FROM claims c
LEFT JOIN seasons s ON s.id = c.season_id
WHERE c.status = 'COMPLETED'
  AND c.asset_address IS NOT NULL
  AND LOWER(c.asset_address) = ANY(%(asset_keys)s)
"""


def _serialize_minted_at(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


@app.get("/api/me/cards")
def me_cards(request: Request) -> Dict[str, Any]:
    """Return PolyStars NFTs currently owned on-chain by the signed-in wallet.

    The on-chain collection is the source of truth: we enumerate every
    tokenId the wallet currently owns on the configured contract (via
    Transfer-log scan + ``ownerOf`` confirmation), then enrich each tokenId
    with denormalized metadata from the ``claims`` table when available.
    NFTs received from another wallet (sale, transfer) appear automatically;
    NFTs the wallet has transferred away disappear automatically. Cached
    briefly per wallet to spare the RPC.
    """
    _require_wallet_actions_enabled()
    wallet = _extract_wallet_from_request(request).lower()
    if not Web3.is_address(wallet):
        raise HTTPException(status_code=400, detail="Invalid connected wallet")

    cached = _me_cards_cache_get(wallet)
    if cached is not None:
        return {**cached, "cached": True}

    # Lazy import: avoids pulling web3/evm_service into modules that don't
    # need it and keeps test conftest stubs simpler.
    try:
        from scripts.evm_service import (
            EvmReader,
            etherscan_nft_url,
            etherscan_tx_url,
        )
    except Exception:
        logger.exception("Failed to import evm_service for /api/me/cards")
        raise HTTPException(status_code=503, detail="On-chain verification unavailable") from None

    try:
        reader = EvmReader()
    except Exception as exc:
        logger.exception("EvmReader init failed for /api/me/cards: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="On-chain verification unavailable — EVM contract not configured.",
        ) from None

    contract_address = reader.contract_address
    contract_address_lower = contract_address.lower()
    chain_id = reader.chain_id

    try:
        owned_nfts = reader.tokens_owned_by(wallet)
    except Exception as exc:
        logger.exception("tokens_owned_by failed for wallet=%s", wallet)
        raise HTTPException(
            status_code=503,
            detail=f"On-chain verification unavailable: {exc}",
        ) from None

    # Build the asset_address keys we'll look up in claims. Stored format is
    # "<contract>/<tokenId>"; matching is done lowercased to absorb any
    # casing mismatch between checksum-cased mint-time writes and lowercase
    # wallet input.
    asset_keys = [f"{contract_address_lower}/{nft.token_id}" for nft in owned_nfts]
    rows_by_asset: Dict[str, Dict[str, Any]] = {}
    if asset_keys:
        conn = _get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                cursor.execute(_ME_CARDS_CLAIMS_SQL, {"asset_keys": asset_keys})
                for row in cursor.fetchall():
                    asset = str(row.get("asset_address") or "").strip().lower()
                    if asset and asset not in rows_by_asset:
                        rows_by_asset[asset] = dict(row)
        except Exception:
            logger.exception("Failed to load claim metadata for wallet=%s", wallet)
            raise HTTPException(status_code=503, detail="Service temporarily unavailable") from None
        finally:
            conn.close()

    items: List[Dict[str, Any]] = []
    for nft in owned_nfts:
        token_id = nft.token_id
        asset_key = f"{contract_address_lower}/{token_id}"
        row = rows_by_asset.get(asset_key)
        tx_hash = (str(row.get("tx_hash") or "").strip() or None) if row else None
        # Display asset_address uses checksum-cased contract for consistency
        # with what we write at mint time.
        asset_address_display = f"{contract_address}/{token_id}"
        # Claims is the richer source (carries our own card_slug, season,
        # phase, polished labels). Fall back to Alchemy-indexed metadata
        # only when we don't have a local row — that covers NFTs received
        # via secondary transfer or minted from another instance.
        claim_front = (str(row["front_image_url"]).strip() or None) if row and row.get("front_image_url") else None
        claim_back = (str(row["back_image_url"]).strip() or None) if row and row.get("back_image_url") else None
        claim_name = (str(row["name"]).strip() or None) if row and row.get("name") else None
        claim_metadata_uri = (str(row["metadata_uri"]).strip() or None) if row and row.get("metadata_uri") else None
        items.append({
            "claim_id": int(row["claim_id"]) if row and row.get("claim_id") is not None else None,
            "asset_address": asset_address_display,
            "tx_hash": tx_hash,
            "metadata_uri": claim_metadata_uri or nft.metadata_uri,
            "season_id": row.get("season_id") if row else None,
            "season_type": (str(row["season_type"]).strip() or None) if row and row.get("season_type") else None,
            "season_number": row.get("season_number") if row else None,
            "phase": (str(row["phase"]).strip() or None) if row and row.get("phase") else None,
            "collection_mint_number": row.get("collection_mint_number") if row else None,
            "name": claim_name or nft.name,
            "front_image_url": claim_front or nft.image_url,
            # Back falls back to ``properties.files[]`` from the on-chain
            # metadata (our minter writes [front, back] there). Null only
            # when neither claims row nor on-chain metadata carry it — the
            # UI shows "No back preview" in that case.
            "back_image_url": claim_back or nft.back_image_url,
            "card_slug": (str(row["card_slug"]).strip() or None) if row and row.get("card_slug") else None,
            "explorer_asset_url": etherscan_nft_url(contract_address, token_id, chain_id),
            "explorer_tx_url": etherscan_tx_url(tx_hash, chain_id) if tx_hash else None,
            "minted_at": _serialize_minted_at(row.get("minted_at")) if row else None,
        })

    payload = {
        "wallet_address": wallet,
        "contract_address": contract_address,
        "chain_id": chain_id,
        "items": items,
        "total": len(items),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "source": "onchain",
    }
    _me_cards_cache_set(wallet, payload)
    return payload


