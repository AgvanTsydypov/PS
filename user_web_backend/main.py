from __future__ import annotations

import logging
import os
import secrets
import sys
import threading
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import monotonic
from typing import Any, Dict, List, Optional

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
}
_rate_limit_lock = threading.Lock()
_rate_limit_store: Dict[str, deque[float]] = defaultdict(deque)


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


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    cfg = RATE_LIMITS.get(request.url.path)
    if cfg is not None:
        client_ip = request.client.host if request.client else "unknown"
        bucket_key = f"{request.url.path}:{client_ip}"
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


@app.post("/api/eligibility")
def check_eligibility(payload: EligibilityRequest, request: Request) -> Dict[str, Any]:
    wallet = payload.wallet.strip().lower()
    if not wallet:
        raise HTTPException(status_code=400, detail="Wallet is required")
    token_wallet = _extract_wallet_from_request(request)
    if wallet != token_wallet:
        raise HTTPException(status_code=403, detail="Forbidden")

    try:
        result = season_manager.check_user_eligibility(wallet)
        if payload.season_id is not None:
            selected = season_manager.check_user_eligibility_for_season(
                wallet_address=wallet,
                season_id=payload.season_id,
            )
            result["selected_season_id"] = payload.season_id
            result["is_origin_wallet_active_standard"] = result.get("is_origin_wallet")
            result["is_origin_wallet"] = bool(selected.get("is_origin_wallet"))
            result["selected_season"] = selected
        return result
    except HTTPException:
        raise
    except Exception:
        logger.exception("Eligibility check failed for wallet=%s", wallet)
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

    conn = _get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO user_wallet_signins (wallet_address, first_seen_at, last_signed_in_at, sign_in_count)
                VALUES (%s, NOW(), NOW(), 1)
                ON CONFLICT (wallet_address)
                DO UPDATE SET
                    last_signed_in_at = NOW(),
                    sign_in_count = user_wallet_signins.sign_in_count + 1
                RETURNING wallet_address, first_seen_at, last_signed_in_at, sign_in_count
                """,
                (wallet_address,),
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
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": JWT_TTL_SECONDS,
    }
