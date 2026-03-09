from __future__ import annotations

import os
import secrets
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict

import psycopg2
from dotenv import load_dotenv
from eth_account import Account
from eth_account.messages import encode_defunct
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
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


app = FastAPI(title="PolyStars User Web API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in os.getenv("USER_WEB_CORS_ORIGINS", "*").split(",") if origin.strip()],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

_challenge_store: Dict[str, ChallengeRecord] = {}
_challenge_lock = threading.Lock()
CHALLENGE_TTL_SECONDS = int(os.getenv("USER_WEB_CHALLENGE_TTL_SECONDS", "300"))


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


@app.get("/api/health")
def health():
    return {"ok": True, "service": "user_web_backend"}


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
        raise
    finally:
        conn.close()

    with _challenge_lock:
        _challenge_store.pop(payload.challenge_id, None)

    return {
        "signed_in": True,
        "wallet_address": row[0],
        "first_seen_at": row[1].isoformat(),
        "last_signed_in_at": row[2].isoformat(),
        "sign_in_count": row[3],
    }
