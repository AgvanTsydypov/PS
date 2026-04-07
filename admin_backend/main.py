"""
FastAPI web API for season_test_gui functionality.

Run:
    uvicorn admin_backend.main:app --host 0.0.0.0 --port 8001 --reload
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import monotonic
from typing import Any, Dict, List, Optional

import psycopg2.extras
from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from solders.pubkey import Pubkey

try:
    import boto3
    from botocore.config import Config
except Exception:  # pragma: no cover - fallback for environments without R2 deps
    boto3 = None
    Config = None

# Add project root to path (same approach as other scripts)
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from scripts.data_loading_manager import DataLoadingManager, GENESIS_START_DATE, GENESIS_END_DATE
from scripts.daily_scheduler_simple import SimplifiedScheduler
from scripts.cardgen.generate_card import generate_card_back_svg, generate_card_svg
from scripts.simulate_user_generated_cards_batch import run_admin_simulated_card_generations
from scripts.season_manager import SeasonManager
from scripts.solana_service import MintedNftResult, SolanaClient
from scripts.zora_service import ZoraClient

logger = logging.getLogger(__name__)


def _user_web_wallet_actions_env_override() -> bool:
    truthy = {"1", "true", "yes"}
    return (
        str(os.getenv("USER_WEB_WALLET_ACTIONS_DISABLED", "")).strip().lower() in truthy
        or str(os.getenv("USER_WEB_DISABLE_ME_API", "")).strip().lower() in truthy
    )


MASTER_COLLECTION_ENV_KEY = "MASTER_COLLECTION_ADDRESS"
BLOCKCHAIN_SOLANA = "solana"
BLOCKCHAIN_BASE_ZORA = "base_zora"
DEFAULT_SOLANA_RECIPIENT = "H1wsggroxpW3LwCCv8dVeiJW73oYPkcDGgSqhiT5Zbz3"
DEFAULT_BASE_RECIPIENT = "0xdC65DFF7EED4c1C05511395Ccf19CF507066aCe1"


@dataclass(frozen=True)
class WinnerClaimAllocation:
    row_id: int
    winner_wallet_address: str
    assignment_type: str
    pnl_value: float
    rank: int
    snapshot: Dict[str, Any]


class EligibilityRequest(BaseModel):
    wallet: str
    season_id: Optional[int] = None


class MintClaimRequest(BaseModel):
    wallet: str
    recipient_address: str
    season_id: int
    phase: str = "breach"
    auto_phase: bool = True
    db_only: bool = False
    blockchain: str = BLOCKCHAIN_SOLANA


class QuickPhaseRequest(BaseModel):
    season_id: int
    days_since_start: int = Field(ge=0)


class ManualDateShiftRequest(BaseModel):
    season_id: int
    shift_days: int


class RemainingSupplyRequest(BaseModel):
    season_id: int
    remaining_supply: int


class AdvancedScenarioRequest(BaseModel):
    season_id: int
    season_number: int
    total_supply: int
    remaining_supply: int
    start_date_iso: str
    end_date_iso: str
    is_active: bool
    is_completed: bool


class SimulateGeneratedCardsBatchRequest(BaseModel):
    """Mirrors repeated user POST /api/cards/get for load testing (same DB + R2 as user_web)."""

    max_count: int = Field(default=50, ge=1, le=200)
    origin_match_fraction: float = Field(default=0.1, ge=0.0, le=1.0)


class UserWebWalletActionsUpdate(BaseModel):
    disabled: bool


class ResetRequest(BaseModel):
    confirm: bool


class WinnerWalletsUpsertRequest(BaseModel):
    season_id: int
    wallet_address: str
    source: str = "manual_admin"
    total_pnl_window: Optional[float] = None
    pnl_rank: Optional[int] = None
    window_start_iso: str
    window_end_iso: str
    snapshot_at_iso: Optional[str] = None
    event_id: Optional[str] = None
    market_id: Optional[str] = None
    condition_id: Optional[str] = None
    event_slug: Optional[str] = None
    event_title: Optional[str] = None
    is_minted: bool = False
    minted_at_iso: Optional[str] = None
    minted_to_wallet: Optional[str] = None
    minted_to_solana_wallet: Optional[str] = None
    minted_claim_id: Optional[int] = None
    minted_tx_hash: Optional[str] = None
    minted_asset_address: Optional[str] = None


class EventCardUpdateRequest(BaseModel):
    card_title: Optional[str] = None
    card_lore: Optional[str] = None
    primary_tag: Optional[str] = None
    secondary_tag: Optional[str] = None
    agent_name: Optional[str] = None
    model_name: Optional[str] = None
    prompt_version: Optional[str] = None
    status: str = "ok"
    error_text: Optional[str] = None


class EventCardPromptPartsOverride(BaseModel):
    event_title: str
    event_description: str
    series: Any = None
    tags: List[str]
    recurring_rule: str
    system_instruction: str
    user_prompt: str


class EventCardRegenerateRequest(BaseModel):
    prompt_parts: Optional[EventCardPromptPartsOverride] = None


class CardBuilderPreviewRequest(BaseModel):
    payload: Dict[str, Any]


class WsHub:
    def __init__(self) -> None:
        self._connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections.append(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self._connections:
            self._connections.remove(websocket)

    async def broadcast(self, event: str, payload: Dict[str, Any]) -> None:
        message = {"event": event, "payload": payload, "ts": datetime.now(timezone.utc).isoformat()}
        stale: List[WebSocket] = []
        for ws in self._connections:
            try:
                await ws.send_json(message)
            except Exception:
                stale.append(ws)
        for ws in stale:
            self.disconnect(ws)


class SeasonWorkbenchService:
    EVM_WALLET_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")

    def __init__(self) -> None:
        self.manager = DataLoadingManager(use_local_db=True)
        self.season_manager = SeasonManager(use_local_db=True)
        self.scheduler = SimplifiedScheduler(use_local_db=True, dry_run=False)
        self._r2_client: Any = None
        # Wallet list is mostly stable within a season. Cache for 10 days by default.
        self.wallets_cache_ttl_seconds = int(os.getenv("WALLETS_CACHE_TTL_SECONDS", "864000"))
        # Keep admin snapshot filters aligned with scheduler standard-window logic.
        self.origin_snapshot_offset_days = int(
            os.getenv("POLYSTARS_ORIGIN_SNAPSHOT_OFFSET_DAYS_STANDARD", "0")
        )
        self.origin_lookback_days_standard = int(os.getenv("POLYSTARS_ORIGIN_LOOKBACK_DAYS_STANDARD", "10"))
        self._wallets_cache: Dict[tuple[int, str, bool, int], tuple[float, List[str]]] = {}
        self.ensure_claims_schema_for_mint()
        self.ensure_winners_schema_for_assignment()
        self.ensure_user_web_controls_schema()

    def clear_wallets_cache(self) -> None:
        self._wallets_cache.clear()

    @staticmethod
    def parse_iso_datetime_utc(value: str) -> datetime:
        raw = value.strip()
        if not raw:
            raise ValueError("Datetime value is empty")
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    @staticmethod
    def fmt_dt(value: Optional[datetime]) -> str:
        if value is None:
            return "n/a"
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    @staticmethod
    def fmt_remaining(delta_seconds: float) -> str:
        if delta_seconds <= 0:
            return "0s"
        total = int(delta_seconds)
        days, rem = divmod(total, 86400)
        hours, rem = divmod(rem, 3600)
        minutes, seconds = divmod(rem, 60)
        parts: List[str] = []
        if days:
            parts.append(f"{days}d")
        if hours:
            parts.append(f"{hours}h")
        if minutes or parts:
            parts.append(f"{minutes}m")
        parts.append(f"{seconds}s")
        return " ".join(parts)

    @staticmethod
    def _resolve_stream_for_season_id(eligibility: Dict[str, object], season_id: int) -> Optional[Dict[str, object]]:
        genesis = eligibility.get("genesis")
        standard = eligibility.get("standard")
        if isinstance(genesis, dict) and genesis.get("season_id") == season_id:
            return genesis
        if isinstance(standard, dict) and standard.get("season_id") == season_id:
            return standard
        return None

    def ensure_claims_schema_for_mint(self) -> None:
        conn = self.manager.get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute("ALTER TABLE claims ADD COLUMN IF NOT EXISTS recipient_solana_wallet TEXT")
                cursor.execute("ALTER TABLE claims ADD COLUMN IF NOT EXISTS asset_address TEXT")
                cursor.execute("ALTER TABLE claims ADD COLUMN IF NOT EXISTS mint_chain TEXT")
                cursor.execute("ALTER TABLE claims ALTER COLUMN tx_hash TYPE TEXT")
                cursor.execute("ALTER TABLE claims ALTER COLUMN metadata_uri TYPE TEXT")
                cursor.execute("ALTER TABLE claims ALTER COLUMN asset_address TYPE TEXT")
                cursor.execute("ALTER TABLE claims DROP CONSTRAINT IF EXISTS claims_wallet_check")
                cursor.execute(
                    """
                    ALTER TABLE claims
                    ADD CONSTRAINT claims_wallet_check
                    CHECK (
                        user_wallet ~* '^0x[a-f0-9]{40}$'
                        OR user_wallet ~ '^[1-9A-HJ-NP-Za-km-z]{32,44}$'
                    )
                    """
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def ensure_winners_schema_for_assignment(self) -> None:
        conn = self.manager.get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    ALTER TABLE winner_wallets_nft_to_claim
                    ADD COLUMN IF NOT EXISTS is_minted BOOLEAN NOT NULL DEFAULT FALSE
                    """
                )
                cursor.execute(
                    """
                    ALTER TABLE winner_wallets_nft_to_claim
                    ADD COLUMN IF NOT EXISTS minted_at TIMESTAMPTZ
                    """
                )
                cursor.execute(
                    """
                    ALTER TABLE winner_wallets_nft_to_claim
                    ADD COLUMN IF NOT EXISTS minted_to_wallet TEXT
                    """
                )
                cursor.execute(
                    """
                    ALTER TABLE winner_wallets_nft_to_claim
                    ADD COLUMN IF NOT EXISTS minted_to_solana_wallet TEXT
                    """
                )
                cursor.execute(
                    """
                    ALTER TABLE winner_wallets_nft_to_claim
                    ADD COLUMN IF NOT EXISTS minted_claim_id BIGINT
                    """
                )
                cursor.execute(
                    """
                    ALTER TABLE winner_wallets_nft_to_claim
                    ADD COLUMN IF NOT EXISTS minted_tx_hash TEXT
                    """
                )
                cursor.execute(
                    """
                    ALTER TABLE winner_wallets_nft_to_claim
                    ADD COLUMN IF NOT EXISTS minted_asset_address TEXT
                    """
                )
                cursor.execute(
                    """
                    ALTER TABLE winner_wallets_nft_to_claim
                    ADD COLUMN IF NOT EXISTS archetype TEXT
                    """
                )
                cursor.execute(
                    """
                    ALTER TABLE winner_wallets_nft_to_claim
                    ADD COLUMN IF NOT EXISTS archetype_description TEXT
                    """
                )
                cursor.execute(
                    """
                    ALTER TABLE winner_wallets_nft_to_claim
                    ADD COLUMN IF NOT EXISTS archetype_math TEXT
                    """
                )
                cursor.execute(
                    """
                    ALTER TABLE winner_wallets_nft_to_claim
                    ADD COLUMN IF NOT EXISTS rarity_bracket TEXT
                    """
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def ensure_user_web_controls_schema(self) -> None:
        conn = self.manager.get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS polystars_user_web_controls (
                        singleton_id SMALLINT PRIMARY KEY CHECK (singleton_id = 1),
                        wallet_actions_disabled BOOLEAN NOT NULL DEFAULT FALSE,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
                cursor.execute(
                    """
                    INSERT INTO polystars_user_web_controls (singleton_id, wallet_actions_disabled)
                    VALUES (1, FALSE)
                    ON CONFLICT (singleton_id) DO NOTHING
                    """
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def get_user_web_wallet_actions_disabled_db(self) -> bool:
        self.ensure_user_web_controls_schema()
        conn = self.manager.get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT wallet_actions_disabled FROM polystars_user_web_controls WHERE singleton_id = 1"
                )
                row = cursor.fetchone()
                return bool(row and row[0])
        finally:
            conn.close()

    def set_user_web_wallet_actions_disabled(self, disabled: bool) -> None:
        self.ensure_user_web_controls_schema()
        conn = self.manager.get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE polystars_user_web_controls
                    SET wallet_actions_disabled = %s, updated_at = NOW()
                    WHERE singleton_id = 1
                    """,
                    (disabled,),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _r2_required_env(self) -> Dict[str, str]:
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

    def _get_r2_client(self) -> Any:
        if boto3 is None or Config is None:
            raise ValueError("R2 upload dependencies are missing. Install boto3 and botocore.")
        if self._r2_client is not None:
            return self._r2_client
        cfg = self._r2_required_env()
        self._r2_client = boto3.client(
            "s3",
            endpoint_url=cfg["endpoint"],
            aws_access_key_id=cfg["access_key"],
            aws_secret_access_key=cfg["secret_key"],
            region_name="auto",
            config=Config(signature_version="s3v4"),
        )
        return self._r2_client

    @staticmethod
    def _detect_content_type(filename: str, declared_content_type: Optional[str]) -> str:
        content_type = (declared_content_type or "").strip().lower()
        if content_type in {"image/jpeg", "image/png", "image/webp", "image/gif"}:
            return content_type
        lower_name = filename.lower()
        if lower_name.endswith(".jpg") or lower_name.endswith(".jpeg"):
            return "image/jpeg"
        if lower_name.endswith(".png"):
            return "image/png"
        if lower_name.endswith(".webp"):
            return "image/webp"
        if lower_name.endswith(".gif"):
            return "image/gif"
        raise ValueError("Unsupported image type. Allowed: jpeg, png, webp, gif")

    @staticmethod
    def _content_type_extension(content_type: str) -> str:
        if content_type == "image/jpeg":
            return "jpg"
        if content_type == "image/png":
            return "png"
        if content_type == "image/webp":
            return "webp"
        if content_type == "image/gif":
            return "gif"
        raise ValueError("Unsupported image type")

    def _build_r2_object_key(self, event_id: str, ext: str) -> str:
        prefix = str(os.getenv("R2_PREFIX", "dev")).strip().strip("/")
        safe_event_id = re.sub(r"[^a-zA-Z0-9_.-]", "_", event_id.strip())
        unique = uuid.uuid4().hex
        if prefix:
            return f"{prefix}/event-images/{safe_event_id}/{unique}.{ext}"
        return f"event-images/{safe_event_id}/{unique}.{ext}"

    @staticmethod
    def _env_flag(name: str, default: str = "0") -> bool:
        return str(os.getenv(name, default)).strip().lower() in {"1", "true", "yes", "on"}

    def _resolve_gemini_watermark_tool_bin(self) -> str:
        configured = str(os.getenv("GEMINI_WATERMARK_TOOL_BIN", "")).strip()
        if configured:
            return configured

        discovered = shutil.which("GeminiWatermarkTool")
        if discovered:
            return discovered

        candidates = [
            Path(__file__).resolve().parent / "GeminiWatermarkTool",
            Path(project_root) / "GeminiWatermarkTool",
            Path.cwd() / "GeminiWatermarkTool",
        ]
        for candidate in candidates:
            if candidate.exists() and candidate.is_file():
                return str(candidate)

        raise FileNotFoundError(
            "GeminiWatermarkTool binary not found. Set GEMINI_WATERMARK_TOOL_BIN "
            "or place GeminiWatermarkTool in admin_backend/"
        )

    def _maybe_remove_gemini_watermark(self, file_bytes: bytes, ext: str) -> bytes:
        if not self._env_flag("ADMIN_MANUAL_IMAGE_REMOVE_GEMINI_WATERMARK", "1"):
            return file_bytes

        tool_bin = self._resolve_gemini_watermark_tool_bin()
        timeout_seconds = int(os.getenv("GEMINI_WATERMARK_TOOL_TIMEOUT_SECONDS", "45"))
        strict_mode = self._env_flag("ADMIN_MANUAL_IMAGE_WATERMARK_STRICT", "0")
        force_mode = self._env_flag("ADMIN_MANUAL_IMAGE_WATERMARK_FORCE", "1")
        denoise_mode = str(os.getenv("GEMINI_WATERMARK_TOOL_DENOISE", "off")).strip().lower()
        threshold_raw = str(os.getenv("GEMINI_WATERMARK_TOOL_THRESHOLD", "0.25")).strip()
        denoise_strength = str(os.getenv("GEMINI_WATERMARK_TOOL_STRENGTH", "")).strip()
        denoise_sigma = str(os.getenv("GEMINI_WATERMARK_TOOL_SIGMA", "")).strip()
        denoise_radius = str(os.getenv("GEMINI_WATERMARK_TOOL_RADIUS", "")).strip()

        input_ext = ext if ext in {"jpg", "jpeg", "png", "webp", "gif", "bmp"} else "jpg"
        try:
            with tempfile.TemporaryDirectory(prefix="manual-image-watermark-") as tmpdir:
                input_path = Path(tmpdir) / f"input.{input_ext}"
                output_path = Path(tmpdir) / f"output.{input_ext}"
                input_path.write_bytes(file_bytes)

                def append_numeric_flag(cmd: List[str], flag: str, raw: str) -> None:
                    if not raw:
                        return
                    # Accept integer/float-looking values only, skip invalid env values.
                    if re.fullmatch(r"\d+(\.\d+)?", raw):
                        cmd.extend([flag, raw])
                    else:
                        logger.warning("Ignoring invalid %s value: %s", flag, raw)

                def build_cmd(chosen_denoise: str) -> List[str]:
                    cmd: List[str] = [tool_bin, "-i", str(input_path), "-o", str(output_path)]
                    if force_mode:
                        cmd.append("--force")
                    if chosen_denoise and chosen_denoise != "off":
                        cmd.extend(["--denoise", chosen_denoise])
                        append_numeric_flag(cmd, "--strength", denoise_strength)
                        if chosen_denoise == "ai":
                            append_numeric_flag(cmd, "--sigma", denoise_sigma)
                        elif chosen_denoise in {"ns", "telea", "soft"}:
                            append_numeric_flag(cmd, "--radius", denoise_radius)
                    if threshold_raw and not force_mode:
                        cmd.extend(["--threshold", threshold_raw])
                    return cmd

                def run_cmd(cmd: List[str]) -> subprocess.CompletedProcess[str]:
                    return subprocess.run(
                        cmd,
                        capture_output=True,
                        text=True,
                        timeout=max(1, timeout_seconds),
                        check=False,
                    )

                result = run_cmd(build_cmd(denoise_mode))
                if result.returncode != 0:
                    stderr = (result.stderr or "").strip()
                    stdout = (result.stdout or "").strip()
                    details = stderr or stdout or f"exit code {result.returncode}"
                    needs_vulkan_fallback = (
                        denoise_mode == "ai"
                        and ("libvulkan" in details.lower() or "vulkan" in details.lower())
                    )
                    if needs_vulkan_fallback:
                        logger.warning("GeminiWatermarkTool AI denoise unavailable; retrying without denoise")
                        result = run_cmd(build_cmd("off"))
                        if result.returncode != 0:
                            stderr = (result.stderr or "").strip()
                            stdout = (result.stdout or "").strip()
                            details = stderr or stdout or f"exit code {result.returncode}"
                            raise RuntimeError(f"GeminiWatermarkTool failed after fallback: {details}")
                    else:
                        raise RuntimeError(f"GeminiWatermarkTool failed: {details}")

                if not output_path.exists():
                    raise RuntimeError("GeminiWatermarkTool did not produce output file")
                cleaned = output_path.read_bytes()
                if not cleaned:
                    raise RuntimeError("GeminiWatermarkTool produced empty output")
                return cleaned
        except Exception as exc:
            if strict_mode:
                raise RuntimeError(f"Manual image watermark removal failed: {exc}") from exc
            logger.warning("Manual image watermark removal failed; keeping original upload", exc_info=True)
            return file_bytes

    @staticmethod
    def _extract_r2_key_from_public_url(public_base_url: str, url: Optional[str]) -> Optional[str]:
        if not url:
            return None
        base = public_base_url.rstrip("/")
        value = str(url).strip()
        if not value.startswith(base + "/"):
            return None
        return value[len(base) + 1 :]

    def _delete_r2_object_if_managed(self, old_url: Optional[str]) -> None:
        if not old_url:
            return
        try:
            cfg = self._r2_required_env()
            key = self._extract_r2_key_from_public_url(cfg["public_base_url"], old_url)
            if not key:
                return
            self._get_r2_client().delete_object(Bucket=cfg["bucket"], Key=key)
        except Exception:
            # Old image cleanup should not block replacing with a new one.
            logger.warning("Could not delete old manual image from R2", exc_info=True)

    def upload_event_card_manual_image(self, event_id: str, file: UploadFile) -> Dict[str, Any]:
        target_event_id = event_id.strip()
        if not target_event_id:
            raise ValueError("event_id is required")
        if file is None:
            raise ValueError("file is required")

        filename = file.filename or "upload"
        content_type = self._detect_content_type(filename=filename, declared_content_type=file.content_type)
        max_bytes = int(os.getenv("ADMIN_MANUAL_IMAGE_MAX_BYTES", str(100 * 1024 * 1024)))
        file_bytes = file.file.read(max_bytes + 1)
        if not file_bytes:
            raise ValueError("Uploaded file is empty")
        if len(file_bytes) > max_bytes:
            raise ValueError(f"File is too large. Max size is {max_bytes} bytes")

        cfg = self._r2_required_env()
        ext = self._content_type_extension(content_type)
        # Gemini watermark strip disabled (needs admin_backend/GeminiWatermarkTool or GEMINI_WATERMARK_TOOL_BIN).
        # file_bytes = self._maybe_remove_gemini_watermark(file_bytes=file_bytes, ext=ext)
        key = self._build_r2_object_key(target_event_id, ext)
        self._get_r2_client().put_object(
            Bucket=cfg["bucket"],
            Key=key,
            Body=file_bytes,
            ContentType=content_type,
            CacheControl="public, max-age=31536000, immutable",
        )
        new_public_url = f"{cfg['public_base_url']}/{key}"

        old_manual_image_urls: List[str] = []
        conn = self.manager.get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                cursor.execute(
                    """
                    SELECT
                        ec.event_id,
                        COALESCE(ec.series_id, e.series_id) AS series_id
                    FROM event_cards ec
                    LEFT JOIN events e ON e.id = ec.event_id
                    WHERE ec.event_id = %s
                    LIMIT 1
                    """,
                    (target_event_id,),
                )
                target = cursor.fetchone()
                if not target:
                    raise ValueError(f"Event card for event_id={target_event_id} not found")
                target_series_id = self._normalize_optional_text(target.get("series_id"))

                if target_series_id:
                    cursor.execute(
                        """
                        SELECT DISTINCT manual_image_url
                        FROM event_cards
                        WHERE series_id = %s
                          AND manual_image_url IS NOT NULL
                        """,
                        (target_series_id,),
                    )
                    old_manual_image_urls = [
                        str(row.get("manual_image_url")).strip()
                        for row in cursor.fetchall()
                        if row.get("manual_image_url")
                    ]
                    cursor.execute(
                        """
                        UPDATE event_cards
                        SET manual_image_url = %s, updated_at = NOW()
                        WHERE series_id = %s
                        """,
                        (new_public_url, target_series_id),
                    )
                else:
                    cursor.execute(
                        "SELECT manual_image_url FROM event_cards WHERE event_id = %s LIMIT 1",
                        (target_event_id,),
                    )
                    existing = cursor.fetchone()
                    if not existing:
                        raise ValueError(f"Event card for event_id={target_event_id} not found")
                    old_manual = self._normalize_optional_text(existing.get("manual_image_url"))
                    if old_manual:
                        old_manual_image_urls = [old_manual]
                    cursor.execute(
                        """
                        UPDATE event_cards
                        SET manual_image_url = %s, updated_at = NOW()
                        WHERE event_id = %s
                        """,
                        (new_public_url, target_event_id),
                    )
                cursor.execute(
                    """
                    SELECT
                        ec.event_id,
                        ec.series_id,
                        ec.reccurence,
                        e.ticker AS event_ticker,
                        e.slug AS event_slug,
                        e.title AS event_title,
                        e.description AS event_description,
                        COALESCE(NULLIF(BTRIM(e.image), ''), NULLIF(BTRIM(e.icon), '')) AS event_image_url,
                        ec.manual_image_url,
                        ec.card_title,
                        ec.card_lore,
                        ec.primary_tag,
                        ec.secondary_tag,
                        tp.hex_color AS primary_tag_hex_color,
                        ts.hex_color AS secondary_tag_hex_color,
                        ec.agent_name,
                        ec.model_name,
                        ec.prompt_version,
                        ec.status,
                        ec.error_text,
                        ec.generated_at,
                        ec.updated_at
                    FROM event_cards ec
                    LEFT JOIN events e ON e.id = ec.event_id
                    LEFT JOIN tags tp ON LOWER(BTRIM(tp.label)) = LOWER(BTRIM(ec.primary_tag))
                    LEFT JOIN tags ts ON LOWER(BTRIM(ts.label)) = LOWER(BTRIM(ec.secondary_tag))
                    WHERE ec.event_id = %s
                    LIMIT 1
                    """,
                    (target_event_id,),
                )
                row = cursor.fetchone()
                if not row:
                    raise RuntimeError(f"Event card for event_id={target_event_id} not found after update")
            conn.commit()
        except Exception:
            conn.rollback()
            try:
                self._get_r2_client().delete_object(Bucket=cfg["bucket"], Key=key)
            except Exception:
                logger.warning("Could not rollback uploaded R2 image after DB failure", exc_info=True)
            raise
        finally:
            conn.close()

        for old_url in {url for url in old_manual_image_urls if url and url != new_public_url}:
            self._delete_r2_object_if_managed(old_url)
        return self._format_event_card_row(dict(row))

    def delete_event_card_manual_image(self, event_id: str) -> Dict[str, Any]:
        target_event_id = event_id.strip()
        if not target_event_id:
            raise ValueError("event_id is required")

        old_manual_image_urls: List[str] = []
        conn = self.manager.get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                cursor.execute(
                    """
                    SELECT
                        ec.event_id,
                        COALESCE(ec.series_id, e.series_id) AS series_id
                    FROM event_cards ec
                    LEFT JOIN events e ON e.id = ec.event_id
                    WHERE ec.event_id = %s
                    LIMIT 1
                    """,
                    (target_event_id,),
                )
                target = cursor.fetchone()
                if not target:
                    raise ValueError(f"Event card for event_id={target_event_id} not found")
                target_series_id = self._normalize_optional_text(target.get("series_id"))

                if target_series_id:
                    cursor.execute(
                        """
                        SELECT DISTINCT manual_image_url
                        FROM event_cards
                        WHERE series_id = %s
                          AND manual_image_url IS NOT NULL
                        """,
                        (target_series_id,),
                    )
                    old_manual_image_urls = [
                        str(row.get("manual_image_url")).strip()
                        for row in cursor.fetchall()
                        if row.get("manual_image_url")
                    ]
                    cursor.execute(
                        """
                        UPDATE event_cards
                        SET manual_image_url = NULL, updated_at = NOW()
                        WHERE series_id = %s
                        """,
                        (target_series_id,),
                    )
                else:
                    cursor.execute(
                        "SELECT manual_image_url FROM event_cards WHERE event_id = %s LIMIT 1",
                        (target_event_id,),
                    )
                    existing = cursor.fetchone()
                    if not existing:
                        raise ValueError(f"Event card for event_id={target_event_id} not found")
                    old_manual = self._normalize_optional_text(existing.get("manual_image_url"))
                    if old_manual:
                        old_manual_image_urls = [old_manual]
                    cursor.execute(
                        """
                        UPDATE event_cards
                        SET manual_image_url = NULL, updated_at = NOW()
                        WHERE event_id = %s
                        """,
                        (target_event_id,),
                    )
                cursor.execute(
                    """
                    SELECT
                        ec.event_id,
                        ec.series_id,
                        ec.reccurence,
                        e.ticker AS event_ticker,
                        e.slug AS event_slug,
                        e.title AS event_title,
                        e.description AS event_description,
                        COALESCE(NULLIF(BTRIM(e.image), ''), NULLIF(BTRIM(e.icon), '')) AS event_image_url,
                        ec.manual_image_url,
                        ec.card_title,
                        ec.card_lore,
                        ec.primary_tag,
                        ec.secondary_tag,
                        tp.hex_color AS primary_tag_hex_color,
                        ts.hex_color AS secondary_tag_hex_color,
                        ec.agent_name,
                        ec.model_name,
                        ec.prompt_version,
                        ec.status,
                        ec.error_text,
                        ec.generated_at,
                        ec.updated_at
                    FROM event_cards ec
                    LEFT JOIN events e ON e.id = ec.event_id
                    LEFT JOIN tags tp ON LOWER(BTRIM(tp.label)) = LOWER(BTRIM(ec.primary_tag))
                    LEFT JOIN tags ts ON LOWER(BTRIM(ts.label)) = LOWER(BTRIM(ec.secondary_tag))
                    WHERE ec.event_id = %s
                    LIMIT 1
                    """,
                    (target_event_id,),
                )
                row = cursor.fetchone()
                if not row:
                    raise RuntimeError(f"Event card for event_id={target_event_id} not found after update")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

        for old_url in {url for url in old_manual_image_urls if url}:
            self._delete_r2_object_if_managed(old_url)
        return self._format_event_card_row(dict(row))

    def _sync_event_tag_primary_flags(self, cursor: Any, event_id: str, primary_tag: Optional[str]) -> None:
        normalized_primary = self._normalize_optional_text(primary_tag)
        if not normalized_primary:
            return
        cursor.execute(
            """
            UPDATE tags t
            SET is_primary = TRUE
            FROM event_tags et
            WHERE et.event_id = %s
              AND et.tag_id = t.id
              AND LOWER(BTRIM(t.label)) = LOWER(BTRIM(%s))
              AND COALESCE(t.is_primary, FALSE) = FALSE
            """,
            (event_id, normalized_primary),
        )

    def get_seasons(self) -> List[Dict[str, Any]]:
        conn = self.manager.get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                cursor.execute(
                    """
                    SELECT id, type, season_number, start_date, end_date, total_supply, remaining_supply, is_active, is_completed
                    FROM seasons
                    ORDER BY is_active DESC, start_date DESC, id DESC
                    """
                )
                rows = cursor.fetchall()
                return [dict(row) for row in rows]
        finally:
            conn.close()

    def get_overview(self) -> Dict[str, Any]:
        conn = self.manager.get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                cursor.execute(
                    """
                    SELECT id, type, season_number, start_date, end_date, total_supply, remaining_supply, is_active, is_completed
                    FROM seasons
                    ORDER BY type, season_number
                    """
                )
                seasons = [dict(row) for row in cursor.fetchall()]
                cursor.execute(
                    """
                    SELECT created_at, event_name, season_id, details
                    FROM season_events_log
                    ORDER BY created_at DESC
                    LIMIT 60
                    """
                )
                logs = [dict(row) for row in cursor.fetchall()]
                return {"seasons": seasons, "logs": logs}
        finally:
            conn.close()

    def get_wallets(
        self,
        season_id: int,
        wallet_filter: str = "all",
        include_position_wallets: bool = True,
        limit: int = 35,
    ) -> List[str]:
        if wallet_filter not in {"all", "origin", "non_origin"}:
            wallet_filter = "all"
        if limit <= 0:
            limit = 35

        cache_key = (season_id, wallet_filter, include_position_wallets, limit)
        cached = self._wallets_cache.get(cache_key)
        now = monotonic()
        if cached and cached[0] > now:
            return cached[1]

        conn = self.manager.get_connection()
        try:
            with conn.cursor() as cursor:
                if wallet_filter == "origin":
                    cursor.execute(
                        """
                        SELECT DISTINCT lower(proxy_wallet) AS wallet
                        FROM winner_wallets_nft_to_claim
                        WHERE season_id = %s
                        ORDER BY wallet
                        LIMIT %s
                        """,
                        (season_id, limit),
                    )
                    wallets = [str(row[0]) for row in cursor.fetchall()]
                    self._wallets_cache[cache_key] = (now + self.wallets_cache_ttl_seconds, wallets)
                    return wallets

                if include_position_wallets:
                    cursor.execute(
                        """
                        WITH origin_wallets AS (
                            SELECT lower(proxy_wallet) AS wallet
                            FROM winner_wallets_nft_to_claim
                            WHERE season_id = %s
                        ),
                        bounds AS (
                            SELECT
                                COALESCE(ssw.window_start, s.start_date - INTERVAL '30 days') AS lower_bound,
                                COALESCE(ssw.window_end, s.start_date) AS upper_bound
                            FROM seasons
                            s
                            LEFT JOIN (
                                SELECT MIN(window_start) AS window_start, MAX(window_end) AS window_end
                                FROM winner_wallets_nft_to_claim
                                WHERE season_id = %s
                            ) ssw ON TRUE
                            WHERE s.id = %s
                            LIMIT 1
                        ),
                        bounds_epoch AS (
                            SELECT
                                lower_bound,
                                upper_bound,
                                FLOOR(EXTRACT(EPOCH FROM lower_bound))::BIGINT AS lower_epoch_s,
                                FLOOR(EXTRACT(EPOCH FROM upper_bound))::BIGINT AS upper_epoch_s,
                                FLOOR(EXTRACT(EPOCH FROM lower_bound) * 1000)::BIGINT AS lower_epoch_ms,
                                FLOOR(EXTRACT(EPOCH FROM upper_bound) * 1000)::BIGINT AS upper_epoch_ms
                            FROM bounds
                        ),
                        position_wallets AS (
                            SELECT DISTINCT wallet
                            FROM (
                                SELECT lower(ucp.proxy_wallet) AS wallet
                                FROM user_closed_positions ucp
                                CROSS JOIN bounds_epoch b
                                WHERE ucp.proxy_wallet IS NOT NULL
                                  AND lower(ucp.proxy_wallet) ~* '^0x[a-f0-9]{40}$'
                                  AND ucp.timestamp_human IS NOT NULL
                                  AND ucp.timestamp_human >= b.lower_bound
                                  AND ucp.timestamp_human < b.upper_bound
                                UNION
                                SELECT lower(ucp.proxy_wallet) AS wallet
                                FROM user_closed_positions ucp
                                CROSS JOIN bounds_epoch b
                                WHERE ucp.proxy_wallet IS NOT NULL
                                  AND lower(ucp.proxy_wallet) ~* '^0x[a-f0-9]{40}$'
                                  AND ucp.timestamp_human IS NULL
                                  AND (
                                      (ucp.timestamp_unix > 1000000000000 AND ucp.timestamp_unix >= b.lower_epoch_ms AND ucp.timestamp_unix < b.upper_epoch_ms)
                                      OR
                                      (ucp.timestamp_unix <= 1000000000000 AND ucp.timestamp_unix >= b.lower_epoch_s AND ucp.timestamp_unix < b.upper_epoch_s)
                                  )
                            ) w
                        ),
                        claimed_wallets AS (
                            SELECT lower(user_wallet) AS wallet
                            FROM claims
                            WHERE season_id = %s
                        ),
                        candidates AS (
                            SELECT wallet FROM origin_wallets
                            UNION
                            SELECT wallet FROM position_wallets
                            UNION
                            SELECT wallet FROM claimed_wallets
                        ),
                        normalized AS (
                            SELECT DISTINCT wallet FROM candidates WHERE wallet ~* '^0x[a-f0-9]{40}$'
                        ),
                        classified AS (
                            SELECT n.wallet, (o.wallet IS NOT NULL) AS is_origin
                            FROM normalized n
                            LEFT JOIN origin_wallets o ON o.wallet = n.wallet
                        )
                        SELECT wallet
                        FROM classified
                        WHERE (
                            %s = 'all'
                            OR (%s = 'origin' AND is_origin = TRUE)
                            OR (%s = 'non_origin' AND is_origin = FALSE)
                        )
                        ORDER BY wallet
                        LIMIT %s
                        """,
                        (
                            season_id,
                            season_id,
                            season_id,
                            season_id,
                            wallet_filter,
                            wallet_filter,
                            wallet_filter,
                            limit,
                        ),
                    )
                else:
                    # Fast path for UI dropdowns: use winners + claims only.
                    cursor.execute(
                        """
                        WITH origin_wallets AS (
                            SELECT lower(proxy_wallet) AS wallet
                            FROM winner_wallets_nft_to_claim
                            WHERE season_id = %s
                        ),
                        claimed_wallets AS (
                            SELECT lower(user_wallet) AS wallet
                            FROM claims
                            WHERE season_id = %s
                        ),
                        candidates AS (
                            SELECT wallet FROM origin_wallets
                            UNION
                            SELECT wallet FROM claimed_wallets
                        ),
                        normalized AS (
                            SELECT DISTINCT wallet FROM candidates WHERE wallet ~* '^0x[a-f0-9]{40}$'
                        ),
                        classified AS (
                            SELECT n.wallet, (o.wallet IS NOT NULL) AS is_origin
                            FROM normalized n
                            LEFT JOIN origin_wallets o ON o.wallet = n.wallet
                        )
                        SELECT wallet
                        FROM classified
                        WHERE (
                            %s = 'all'
                            OR (%s = 'origin' AND is_origin = TRUE)
                            OR (%s = 'non_origin' AND is_origin = FALSE)
                        )
                        ORDER BY wallet
                        LIMIT %s
                        """,
                        (
                            season_id,
                            season_id,
                            wallet_filter,
                            wallet_filter,
                            wallet_filter,
                            limit,
                        ),
                    )
                wallets = [str(row[0]) for row in cursor.fetchall()]
                self._wallets_cache[cache_key] = (now + self.wallets_cache_ttl_seconds, wallets)
                return wallets
        finally:
            conn.close()

    def get_claim_phase_enum_values(self) -> List[str]:
        conn = self.manager.get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT e.enumlabel
                    FROM pg_type t
                    JOIN pg_enum e ON t.oid = e.enumtypid
                    WHERE t.typname = 'phase_type'
                    ORDER BY e.enumsortorder
                    """
                )
                return [str(row[0]) for row in cursor.fetchall()]
        finally:
            conn.close()

    def derive_claim_phase_type(self, season_id: int) -> tuple[Optional[str], Optional[str]]:
        try:
            phase_info = self.season_manager.get_current_phase(season_id)
        except Exception as exc:
            return None, f"Could not detect season phase: {exc}"
        current_phase = str(phase_info.get("phase") or "")
        is_claim_open = bool(phase_info.get("is_claim_open"))
        if not is_claim_open:
            return None, f"Claims are closed in phase: {current_phase or 'unknown'}"
        if current_phase in {"breach", "vault", "scavenge"}:
            return current_phase, None
        return None, f"Unsupported claim phase for insert: {current_phase or 'unknown'}"

    def get_claim_season_info(
        self,
        season_id: int,
        wallet: str,
        auto_phase: bool,
        manual_phase: str,
        blockchain: str,
    ) -> Dict[str, Any]:
        conn = self.manager.get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                cursor.execute(
                    """
                    SELECT id, type, season_number, start_date, end_date, total_supply, remaining_supply, is_active, is_completed
                    FROM seasons
                    WHERE id = %s
                    """,
                    (season_id,),
                )
                season = cursor.fetchone()
        finally:
            conn.close()
        if not season:
            raise ValueError(f"Season {season_id} not found")

        phase = "unknown"
        phase_reason = ""
        try:
            phase_info = self.season_manager.get_current_phase(season_id)
            phase = str(phase_info.get("phase", "unknown"))
            phase_reason = str(phase_info.get("reason", ""))
        except Exception as exc:
            phase_reason = f"Phase detection failed: {exc}"

        total_supply = int(season["total_supply"])
        remaining_supply = int(season["remaining_supply"])
        claimed_supply = max(total_supply - remaining_supply, 0)
        start_date = season["start_date"]
        if start_date.tzinfo is None:
            start_date = start_date.replace(tzinfo=timezone.utc)
        end_date = season["end_date"]
        if end_date.tzinfo is None:
            end_date = end_date.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)

        lines: List[str] = [
            f"Season: id={season['id']} | {season['type']}#{season['season_number']} | active={season['is_active']} completed={season['is_completed']}",
            f"Supply: claimed={claimed_supply} / total={total_supply} | remaining={remaining_supply}",
            f"Current phase: {phase} | reason: {phase_reason}",
            f"Window: start={self.fmt_dt(start_date)} | end={self.fmt_dt(end_date)}",
            "",
        ]

        if season["type"] == "genesis":
            breach_end = start_date + timedelta(days=3)
            vault_end = start_date + timedelta(days=6)
            lines.extend(
                [
                    "Transition rules (Genesis):",
                    "- Breach: day 1-3, open for all, cap 20% of total supply.",
                    "- Vault: day 4-6, Origins only.",
                    "- Scavenge: day 7+, open for all until remaining_supply reaches 0.",
                    "- Transmission phase is not used for Genesis.",
                    "",
                    "Timing checkpoints:",
                    f"- breach_end: {self.fmt_dt(breach_end)}",
                    f"- vault_end: {self.fmt_dt(vault_end)}",
                ]
            )
        else:
            breach_end = start_date + timedelta(days=3)
            vault_end = start_date + timedelta(days=6)
            scavenge_end = start_date + timedelta(days=9)
            transmission_end = start_date + timedelta(days=10)
            breach_cap = int(total_supply * self.season_manager.BREACH_CAP_PERCENT)
            lines.extend(
                [
                    "Transition rules (Standard):",
                    f"- Breach: day 1-3, open for all, cap {breach_cap}/{total_supply} (20%).",
                    "- Vault: day 4-6 or earlier if Breach cap reached, Origins only.",
                    "- Scavenge: day 7-9, open for all.",
                    "- Transmission: day 10, claims closed.",
                    "",
                    "Timing checkpoints:",
                    f"- breach_end: {self.fmt_dt(breach_end)}",
                    f"- vault_end: {self.fmt_dt(vault_end)}",
                    f"- scavenge_end: {self.fmt_dt(scavenge_end)}",
                    f"- cycle_boundary(day10): {self.fmt_dt(transmission_end)}",
                    "",
                    "Phase timeline (UTC):",
                    f"- Breach:      {self.fmt_dt(start_date)}  ->  {self.fmt_dt(breach_end)}",
                    f"- Vault:       {self.fmt_dt(breach_end)}  ->  {self.fmt_dt(vault_end)}",
                    f"- Scavenge:    {self.fmt_dt(vault_end)}  ->  {self.fmt_dt(scavenge_end)}",
                    f"- Transmission:{self.fmt_dt(scavenge_end)}  ->  {self.fmt_dt(transmission_end)}",
                ]
            )

        resolved_phase = manual_phase
        if auto_phase:
            detected_phase, _ = self.derive_claim_phase_type(season_id)
            resolved_phase = detected_phase or manual_phase

        lines.append(
            f"\nInsert mode: {'Auto phase ON' if auto_phase else 'Manual phase'} -> claim phase_type will be '{resolved_phase}'."
        )

        wallet_normalized = wallet.strip().lower()
        if wallet_normalized:
            try:
                eligibility = self.season_manager.check_user_eligibility(wallet_normalized)
                selected_season_eligibility = self.season_manager.check_user_eligibility_for_season(
                    wallet_address=wallet_normalized,
                    season_id=season_id,
                )
                lines.extend(["", "Checklist before insert:", f"- wallet: {wallet_normalized}", f"- blockchain: {blockchain}"])
                lines.append(
                    f"- is_origin_wallet_selected_season: {bool(selected_season_eligibility.get('is_origin_wallet'))}"
                )
                lines.append(
                    f"- is_origin_wallet_active_standard: {bool(eligibility.get('is_origin_wallet'))}"
                )
                lines.append(
                    "- stream_phase: "
                    f"{selected_season_eligibility.get('phase')} | "
                    f"is_claim_open={bool(selected_season_eligibility.get('is_claim_open'))}"
                )
                lines.append(
                    "- already_claimed_in_this_season: "
                    f"{bool(selected_season_eligibility.get('already_claimed'))}"
                )
                lines.append(
                    f"- eligible_now: {bool(selected_season_eligibility.get('eligible_now'))}"
                )
                if selected_season_eligibility.get("ineligible_reason"):
                    lines.append(
                        f"- ineligible_reason: {selected_season_eligibility.get('ineligible_reason')}"
                    )
            except Exception as exc:
                lines.extend(["", "Checklist before insert:", f"- eligibility_check_error: {exc}"])
        else:
            lines.extend(["", "Checklist before insert:", "- wallet: not selected"])

        return {"phase": phase, "phase_reason": phase_reason, "resolved_phase": resolved_phase, "lines": lines}

    def get_season_claims(self, season_id: int) -> Dict[str, Any]:
        conn = self.manager.get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                cursor.execute(
                    """
                    SELECT id, user_wallet, recipient_solana_wallet, phase_type, status, tx_hash, asset_address, timestamp, created_at
                    FROM claims
                    WHERE season_id = %s
                    ORDER BY created_at DESC, id DESC
                    """,
                    (season_id,),
                )
                rows = [dict(row) for row in cursor.fetchall()]
                cursor.execute(
                    """
                    SELECT
                        COUNT(*) AS total_claims,
                        COUNT(*) FILTER (WHERE status = 'COMPLETED') AS completed_claims,
                        COUNT(*) FILTER (WHERE status = 'PENDING') AS pending_claims,
                        COUNT(*) FILTER (WHERE status = 'PROCESSING') AS processing_claims,
                        COUNT(*) FILTER (WHERE status = 'FAILED') AS failed_claims,
                        COUNT(*) FILTER (WHERE phase_type::text = 'breach') AS breach_claims,
                        COUNT(*) FILTER (WHERE phase_type::text = 'vault') AS vault_claims,
                        COUNT(*) FILTER (WHERE phase_type::text = 'scavenge') AS scavenge_claims,
                        COUNT(*) FILTER (WHERE phase_type::text = 'public') AS legacy_public_claims
                    FROM claims
                    WHERE season_id = %s
                    """,
                    (season_id,),
                )
                stats = dict(cursor.fetchone() or {})
            return {"rows": rows, "stats": stats}
        finally:
            conn.close()

    @staticmethod
    def _extract_image(record: Optional[Dict[str, Any]]) -> Optional[str]:
        if not record:
            return None
        image = str(record.get("event_image") or "").strip()
        return image or None

    def _resolve_event_image_url(self, cursor: Any, row: Dict[str, Any]) -> Optional[str]:
        event_id = str(row.get("event_id") or "").strip()
        event_slug = str(row.get("event_slug") or "").strip()
        if event_id:
            cursor.execute(
                """
                SELECT COALESCE(NULLIF(TRIM(image), ''), NULLIF(TRIM(icon), '')) AS event_image
                FROM events WHERE id = %s LIMIT 1
                """,
                (event_id,),
            )
            image = self._extract_image(cursor.fetchone())
            if image:
                return image
        if event_slug:
            cursor.execute(
                """
                SELECT COALESCE(NULLIF(TRIM(image), ''), NULLIF(TRIM(icon), '')) AS event_image
                FROM events WHERE slug = %s LIMIT 1
                """,
                (event_slug,),
            )
            image = self._extract_image(cursor.fetchone())
            if image:
                return image
        return None

    def _build_winner_allocation(
        self,
        row: Dict[str, Any],
        assignment_type: str,
        event_image_url: Optional[str] = None,
    ) -> WinnerClaimAllocation:
        snapshot: Dict[str, Any] = {
            "winner_row_id": int(row["id"]),
            "winner_wallet_address": str(row["wallet_address"]),
            "source": row.get("source"),
            "total_pnl_window": float(row.get("total_pnl") or 0),
            "pnl_rank": int(row.get("rank") or 0),
            "window_start": row.get("window_start").isoformat() if row.get("window_start") else None,
            "window_end": row.get("window_end").isoformat() if row.get("window_end") else None,
            "snapshot_at": row.get("snapshot_at").isoformat() if row.get("snapshot_at") else None,
            "event_id": row.get("event_id"),
            "event_slug": row.get("event_slug"),
            "entry_cwap": row.get("entry_cwap"),
            "total_volume": row.get("total_volume"),
            "roi_percentage": row.get("roi_percentage"),
            "entry_bracket": row.get("entry_bracket"),
            "edge": row.get("edge"),
            "yield": row.get("yield"),
            "gravity": row.get("gravity"),
            "event_image_url": event_image_url,
        }
        return WinnerClaimAllocation(
            row_id=int(row["id"]),
            winner_wallet_address=str(row["wallet_address"]),
            assignment_type=assignment_type,
            pnl_value=float(row.get("total_pnl") or 0),
            rank=int(row.get("rank") or 0),
            snapshot=snapshot,
        )

    def allocate_winner_claim_row(self, wallet: str, season_id: int) -> WinnerClaimAllocation:
        conn = self.manager.get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                cursor.execute(
                    """
                    SELECT
                        id,
                        proxy_wallet AS wallet_address,
                        source,
                        window_start,
                        window_end,
                        snapshot_at,
                        event_id,
                        event_slug,
                        entry_cwap,
                        total_volume,
                        total_pnl,
                        roi_percentage,
                        entry_bracket,
                        edge,
                        yield,
                        gravity,
                        rank,
                        COALESCE(is_minted, FALSE) AS is_minted,
                        minted_to_wallet
                    FROM winner_wallets_nft_to_claim
                    WHERE season_id = %s
                      AND LOWER(proxy_wallet) = LOWER(%s)
                    LIMIT 1
                    """,
                    (season_id, wallet),
                )
                row = cursor.fetchone()
                if row:
                    if bool(row["is_minted"]):
                        assignee = row.get("minted_to_wallet")
                        suffix = f" (assigned to {assignee})" if assignee else ""
                        raise ValueError(f"This winner row was already minted and cannot be reused{suffix}.")
                    event_image_url = self._resolve_event_image_url(cursor, row)
                    return self._build_winner_allocation(row=row, assignment_type="winner_self", event_image_url=event_image_url)

                cursor.execute(
                    """
                    SELECT
                        id,
                        proxy_wallet AS wallet_address,
                        source,
                        window_start,
                        window_end,
                        snapshot_at,
                        event_id,
                        event_slug,
                        entry_cwap,
                        total_volume,
                        total_pnl,
                        roi_percentage,
                        entry_bracket,
                        edge,
                        yield,
                        gravity,
                        rank
                    FROM winner_wallets_nft_to_claim
                    WHERE season_id = %s
                      AND COALESCE(is_minted, FALSE) = FALSE
                    ORDER BY RANDOM()
                    LIMIT 1
                    """,
                    (season_id,),
                )
                fallback_row = cursor.fetchone()
                if not fallback_row:
                    raise ValueError("No unminted winner rows left in this season.")
                event_image_url = self._resolve_event_image_url(cursor, fallback_row)
                return self._build_winner_allocation(
                    row=fallback_row,
                    assignment_type="random_fallback",
                    event_image_url=event_image_url,
                )
        finally:
            conn.close()

    def reserve_claim_id(self) -> int:
        conn = self.manager.get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT nextval(pg_get_serial_sequence('claims', 'id'))")
                row = cursor.fetchone()
                if not row:
                    raise RuntimeError("Could not reserve claim id")
                return int(row[0])
        finally:
            conn.close()

    def get_season_name(self, season_id: int) -> str:
        conn = self.manager.get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                cursor.execute("SELECT type, season_number FROM seasons WHERE id = %s", (season_id,))
                row = cursor.fetchone()
                if not row:
                    return f"season-{season_id}"
                return f"{row['type']}#{row['season_number']}"
        finally:
            conn.close()

    def mark_winner_row_as_minted(
        self,
        allocation: WinnerClaimAllocation,
        claim_id: int,
        claimer_wallet: str,
        recipient_solana_wallet: str,
        mint_result: MintedNftResult,
    ) -> None:
        conn = self.manager.get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE winner_wallets_nft_to_claim
                    SET
                        is_minted = TRUE,
                        minted_at = NOW(),
                        minted_to_wallet = %s,
                        minted_to_solana_wallet = %s,
                        minted_claim_id = %s,
                        minted_tx_hash = %s,
                        minted_asset_address = %s
                    WHERE id = %s
                      AND COALESCE(is_minted, FALSE) = FALSE
                    """,
                    (
                        claimer_wallet,
                        recipient_solana_wallet,
                        claim_id,
                        mint_result.tx_hash,
                        mint_result.asset_address,
                        allocation.row_id,
                    ),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError("Winner row is already marked as minted.")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def insert_completed_claim(
        self,
        claim_id: int,
        wallet: str,
        recipient_wallet: str,
        season_id: int,
        phase: str,
        mint_result: MintedNftResult,
        mint_chain: str,
    ) -> None:
        payload = (
            claim_id,
            wallet,
            recipient_wallet,
            season_id,
            phase,
            mint_result.tx_hash,
            None,
            mint_result.metadata_uri,
            mint_result.asset_address,
            "COMPLETED",
            mint_chain,
        )
        insert_sql = """
            INSERT INTO claims (
                id,
                user_wallet,
                recipient_solana_wallet,
                season_id,
                phase_type,
                timestamp,
                tx_hash,
                token_id,
                metadata_uri,
                asset_address,
                status,
                mint_chain,
                created_at,
                updated_at
            )
            VALUES (%s, %s, %s, %s, %s, NOW(), %s, %s, %s, %s, %s, %s, NOW(), NOW())
        """
        for attempt in range(2):
            conn = self.manager.get_connection()
            try:
                with conn.cursor() as cursor:
                    cursor.execute(insert_sql, payload)
                conn.commit()
                return
            except Exception as exc:
                conn.rollback()
                text = str(exc).lower()
                if attempt == 0 and "value too long for type character varying" in text:
                    self.ensure_claims_schema_for_mint()
                    continue
                raise
            finally:
                conn.close()

    def insert_pending_claim_db_only(
        self,
        claim_id: int,
        wallet: str,
        recipient_wallet: str,
        season_id: int,
        phase: str,
        mint_chain: str,
    ) -> None:
        conn = self.manager.get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO claims (
                        id,
                        user_wallet,
                        recipient_solana_wallet,
                        season_id,
                        phase_type,
                        timestamp,
                        status,
                        metadata_uri,
                        mint_chain,
                        created_at,
                        updated_at
                    )
                    VALUES (%s, %s, %s, %s, %s, NOW(), %s, %s, %s, NOW(), NOW())
                    """,
                    (
                        claim_id,
                        wallet,
                        recipient_wallet,
                        season_id,
                        phase,
                        "PENDING",
                        "https://arweave.net/placeholder",
                        mint_chain,
                    ),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def get_master_collection_address(self) -> str:
        value = os.environ.get(MASTER_COLLECTION_ENV_KEY, "").strip()
        if value:
            return value
        env_path = Path(project_root) / ".env"
        if not env_path.exists():
            return ""
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, raw_value = line.split("=", 1)
            if key.strip() != MASTER_COLLECTION_ENV_KEY:
                continue
            resolved = raw_value.strip().strip('"').strip("'")
            if resolved:
                return resolved
        return ""

    def run_mint_claim(self, req: MintClaimRequest) -> Dict[str, Any]:
        wallet = req.wallet.strip().lower()
        recipient_raw = req.recipient_address.strip()
        if not wallet:
            raise ValueError("Wallet is required")
        if not recipient_raw:
            raise ValueError("Recipient address is required")

        if req.blockchain == BLOCKCHAIN_SOLANA:
            try:
                recipient_address = str(Pubkey.from_string(recipient_raw))
            except Exception:
                raise ValueError("Invalid Solana recipient address")
        elif req.blockchain == BLOCKCHAIN_BASE_ZORA:
            recipient_address = recipient_raw.lower()
            if not (recipient_address.startswith("0x") and len(recipient_address) == 42):
                raise ValueError("Invalid Base recipient address")
        else:
            raise ValueError(f"Unsupported blockchain: {req.blockchain}")

        phase = req.phase
        warnings: List[str] = []
        if req.auto_phase:
            detected_phase, phase_error = self.derive_claim_phase_type(req.season_id)
            if detected_phase:
                phase = detected_phase
            elif phase_error:
                warnings.append(phase_error)
                raise ValueError(phase_error)

        try:
            eligibility = self.season_manager.check_user_eligibility(wallet)
            stream = self._resolve_stream_for_season_id(eligibility, req.season_id)
            warning_reason: Optional[str] = None
            if stream:
                stream_is_origin = bool(stream.get("is_origin_wallet", eligibility.get("is_origin_wallet")))
                if not bool(stream.get("eligible_now", False)):
                    warning_reason = str(stream.get("ineligible_reason") or "Wallet not eligible for this season now")
                elif phase == "vault" and not stream_is_origin:
                    warning_reason = "Wallet is non-origin but phase='vault'"
            elif phase == "vault" and not bool(eligibility.get("is_origin_wallet")):
                warning_reason = "Wallet is non-origin but phase='vault'"
            if warning_reason:
                warnings.append(warning_reason)
                raise ValueError(warning_reason)
        except Exception as exc:
            raise

        supported_phases = set(self.get_claim_phase_enum_values())
        if phase not in supported_phases:
            supported = ", ".join(sorted(supported_phases)) if supported_phases else "(none)"
            raise ValueError(f"DB enum phase_type does not support '{phase}'. Supported: {supported}")

        allocation = self.allocate_winner_claim_row(wallet=wallet, season_id=req.season_id)
        season_name = self.get_season_name(req.season_id)
        claim_id = self.reserve_claim_id()
        if req.db_only:
            self.insert_pending_claim_db_only(
                claim_id=claim_id,
                wallet=wallet,
                recipient_wallet=recipient_address,
                season_id=req.season_id,
                phase=phase,
                mint_chain=req.blockchain,
            )
            self.clear_wallets_cache()
            return {
                "status": "db_only_inserted",
                "claim_id": claim_id,
                "wallet": wallet,
                "recipient_address": recipient_address,
                "season_id": req.season_id,
                "phase": phase,
                "chain": req.blockchain,
                "allocation": allocation.__dict__,
                "warnings": warnings,
            }

        winner_context = {
            "assignment_type": allocation.assignment_type,
            "winner_wallet_address": allocation.winner_wallet_address,
            "claimer_wallet_address": wallet,
            "season_id": req.season_id,
            "snapshot": allocation.snapshot,
            "blockchain": req.blockchain,
        }

        if req.blockchain == BLOCKCHAIN_SOLANA:
            mint_client = SolanaClient(keypair_path=Path(project_root) / "my-keypair.json")
            mint_result = mint_client.mint_user_nft(
                user_wallet_address=recipient_address,
                pnl_value=allocation.pnl_value,
                rank=allocation.rank,
                season_name=season_name,
                claim_id=claim_id,
                winner_context=winner_context,
            )
        else:
            zora_client = ZoraClient(project_root=project_root)
            mint_result = zora_client.mint_user_nft(
                user_wallet_address=recipient_address,
                pnl_value=allocation.pnl_value,
                rank=allocation.rank,
                season_name=season_name,
                claim_id=claim_id,
                winner_context=winner_context,
            )

        self.insert_completed_claim(
            claim_id=claim_id,
            wallet=wallet,
            recipient_wallet=recipient_address,
            season_id=req.season_id,
            phase=phase,
            mint_result=mint_result,
            mint_chain=req.blockchain,
        )
        self.mark_winner_row_as_minted(
            allocation=allocation,
            claim_id=claim_id,
            claimer_wallet=wallet,
            recipient_solana_wallet=recipient_address,
            mint_result=mint_result,
        )
        self.clear_wallets_cache()

        return {
            "status": "mint_completed",
            "claim_id": claim_id,
            "wallet": wallet,
            "recipient_address": recipient_address,
            "season_id": req.season_id,
            "phase": phase,
            "chain": req.blockchain,
            "allocation": allocation.__dict__,
            "mint_result": mint_result.__dict__,
            "warnings": warnings,
            "collection_address": self.get_master_collection_address(),
        }

    def run_season_lifecycle_update(self) -> str:
        self.scheduler.run_standard_season_update()
        self.clear_wallets_cache()
        return "run_standard_season_update finished"

    def load_scenario_season_params(self, season_id: int) -> Dict[str, Any]:
        conn = self.manager.get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                cursor.execute(
                    """
                    SELECT id, type, season_number, start_date, end_date, total_supply, remaining_supply, is_active, is_completed
                    FROM seasons
                    WHERE id = %s
                    """,
                    (season_id,),
                )
                row = cursor.fetchone()
                if not row:
                    raise ValueError(f"Season {season_id} not found")
                return dict(row)
        finally:
            conn.close()

    def update_season_dates(self, season_id: int, start_date: datetime, end_date: datetime) -> None:
        conn = self.manager.get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE seasons
                    SET start_date = %s, end_date = %s, updated_at = NOW()
                    WHERE id = %s
                    """,
                    (start_date, end_date, season_id),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def get_season_meta(self, season_id: int) -> Dict[str, Any]:
        conn = self.manager.get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                cursor.execute("SELECT id, type, start_date, end_date FROM seasons WHERE id = %s", (season_id,))
                row = cursor.fetchone()
                if not row:
                    raise ValueError(f"Season {season_id} not found")
                return dict(row)
        finally:
            conn.close()

    def apply_advanced_scenario(self, req: AdvancedScenarioRequest) -> None:
        if req.season_number <= 0:
            raise ValueError("season_number must be > 0")
        if req.total_supply <= 0:
            raise ValueError("total_supply must be > 0")
        if req.remaining_supply < 0 or req.remaining_supply > req.total_supply:
            raise ValueError("remaining_supply must be between 0 and total_supply")
        start_date = self.parse_iso_datetime_utc(req.start_date_iso)
        end_date = self.parse_iso_datetime_utc(req.end_date_iso)
        if end_date <= start_date:
            raise ValueError("end_date must be later than start_date")

        conn = self.manager.get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE seasons
                    SET
                        season_number = %s,
                        start_date = %s,
                        end_date = %s,
                        total_supply = %s,
                        remaining_supply = %s,
                        is_active = %s,
                        is_completed = %s,
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    (
                        req.season_number,
                        start_date,
                        end_date,
                        req.total_supply,
                        req.remaining_supply,
                        req.is_active,
                        req.is_completed,
                        req.season_id,
                    ),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def apply_remaining_supply(self, season_id: int, remaining_supply: int) -> None:
        conn = self.manager.get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE seasons
                    SET
                        remaining_supply = %s,
                        is_active = CASE WHEN %s > 0 THEN is_active ELSE FALSE END,
                        is_completed = CASE WHEN %s > 0 THEN is_completed ELSE TRUE END,
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    (remaining_supply, remaining_supply, remaining_supply, season_id),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _r2_cards_images_key_prefix() -> str:
        """Same layout as user_web_backend card uploads: {R2_PREFIX}/cards-images/... or cards-images/..."""
        prefix = str(os.getenv("R2_PREFIX", "dev")).strip().strip("/")
        if prefix:
            return f"{prefix}/cards-images/"
        return "cards-images/"

    def _purge_r2_cards_images(self) -> tuple[int, str]:
        """Delete every object whose key starts with the cards-images prefix. Returns (deleted_count, prefix)."""
        cfg = self._r2_required_env()
        key_prefix = self._r2_cards_images_key_prefix()
        client = self._get_r2_client()
        bucket = cfg["bucket"]
        deleted = 0
        token: Optional[str] = None
        while True:
            list_kw: Dict[str, Any] = {
                "Bucket": bucket,
                "Prefix": key_prefix,
                "MaxKeys": 1000,
            }
            if token:
                list_kw["ContinuationToken"] = token
            resp = client.list_objects_v2(**list_kw)
            contents = resp.get("Contents") or []
            if contents:
                del_resp = client.delete_objects(
                    Bucket=bucket,
                    Delete={"Objects": [{"Key": obj["Key"]} for obj in contents], "Quiet": True},
                )
                errors = del_resp.get("Errors") or []
                if errors:
                    first = errors[0]
                    raise RuntimeError(
                        f"R2 delete_objects failed key={first.get('Key')!r} "
                        f"code={first.get('Code')} message={first.get('Message')}"
                    )
                deleted += len(contents)
            if not resp.get("IsTruncated"):
                break
            token = resp.get("NextContinuationToken")
        return deleted, key_prefix

    def run_reset_sql(self) -> str:
        sql_path = Path(__file__).resolve().parents[1] / "sql" / "queries" / "clear_seasons_logic.sql"
        if not sql_path.exists():
            raise FileNotFoundError(f"Reset SQL not found: {sql_path}")
        sql = sql_path.read_text(encoding="utf-8")
        conn = self.manager.get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(sql)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        self.clear_wallets_cache()

        parts = ["Reset SQL executed successfully."]
        try:
            removed, prefix = self._purge_r2_cards_images()
            parts.append(f"Removed {removed} R2 object(s) under {prefix!r}.")
        except ValueError as exc:
            parts.append(f"R2 cards-images cleanup skipped: {exc}")
        except Exception as exc:
            logger.exception("R2 cards-images cleanup failed after reset")
            parts.append(f"R2 cards-images cleanup failed: {exc}")
        return " ".join(parts)

    @staticmethod
    def _normalize_optional_text(value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @staticmethod
    def _format_winner_row(row: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(row)
        for key in ("window_start", "window_end", "snapshot_at", "created_at", "minted_at"):
            value = payload.get(key)
            if isinstance(value, datetime):
                payload[key] = value.astimezone(timezone.utc).isoformat()
        return payload

    @staticmethod
    def _format_event_card_row(row: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(row)
        for key in ("generated_at", "updated_at"):
            value = payload.get(key)
            if isinstance(value, datetime):
                payload[key] = value.astimezone(timezone.utc).isoformat()
        return payload

    @staticmethod
    def _format_card_builder_candidate(row: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(row)
        for key in ("window_start", "window_end", "snapshot_at", "event_card_generated_at", "event_card_updated_at"):
            value = payload.get(key)
            if isinstance(value, datetime):
                payload[key] = value.astimezone(timezone.utc).isoformat()
        return payload

    def list_winner_wallet_rows(self, season_id: Optional[int], limit: int) -> List[Dict[str, Any]]:
        safe_limit = min(max(limit, 1), 1000)
        conn = self.manager.get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                if season_id is None:
                    cursor.execute(
                        """
                        SELECT
                            id, season_id, proxy_wallet AS wallet_address, source,
                            window_start, window_end, snapshot_at, created_at,
                            event_id, event_slug, entry_cwap, total_volume, total_pnl,
                            roi_percentage, entry_bracket, edge, yield, gravity, rank,
                            archetype, archetype_description, archetype_math, rarity_bracket,
                            is_minted, minted_at,
                            minted_to_wallet, minted_to_solana_wallet, minted_claim_id,
                            minted_tx_hash, minted_asset_address
                        FROM winner_wallets_nft_to_claim
                        ORDER BY season_id DESC, rank ASC NULLS LAST, id DESC
                        LIMIT %s
                        """,
                        (safe_limit,),
                    )
                else:
                    cursor.execute(
                        """
                        SELECT
                            id, season_id, proxy_wallet AS wallet_address, source,
                            window_start, window_end, snapshot_at, created_at,
                            event_id, event_slug, entry_cwap, total_volume, total_pnl,
                            roi_percentage, entry_bracket, edge, yield, gravity, rank,
                            archetype, archetype_description, archetype_math, rarity_bracket,
                            is_minted, minted_at,
                            minted_to_wallet, minted_to_solana_wallet, minted_claim_id,
                            minted_tx_hash, minted_asset_address
                        FROM winner_wallets_nft_to_claim
                        WHERE season_id = %s
                        ORDER BY rank ASC NULLS LAST, id DESC
                        LIMIT %s
                        """,
                        (season_id, safe_limit),
                    )
                return [self._format_winner_row(dict(row)) for row in cursor.fetchall()]
        finally:
            conn.close()

    def list_card_builder_candidates(
        self,
        *,
        season_id: Optional[int],
        limit: int,
        offset: int,
        search_query: Optional[str],
    ) -> Dict[str, Any]:
        safe_limit = min(max(limit, 1), 1000)
        safe_offset = max(offset, 0)
        normalized_query = (search_query or "").strip().lower()

        conn = self.manager.get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                where_parts: List[str] = [
                    "w.event_id IS NOT NULL",
                    "ec.manual_image_url IS NOT NULL",
                    "BTRIM(ec.manual_image_url) <> ''",
                ]
                params: List[Any] = []

                if season_id is not None:
                    where_parts.append("w.season_id = %s")
                    params.append(season_id)

                if normalized_query:
                    where_parts.append(
                        """
                        (
                            LOWER(w.proxy_wallet) LIKE %s
                            OR LOWER(COALESCE(w.event_id, '')) LIKE %s
                            OR LOWER(COALESCE(w.event_slug, '')) LIKE %s
                            OR LOWER(COALESCE(e.title, '')) LIKE %s
                            OR LOWER(COALESCE(ec.primary_tag, '')) LIKE %s
                            OR LOWER(COALESCE(ec.card_title, '')) LIKE %s
                        )
                        """
                    )
                    q = f"%{normalized_query}%"
                    params.extend([q, q, q, q, q, q])

                where_sql = f"WHERE {' AND '.join(where_parts)}"
                from_sql = f"""
                    FROM winner_wallets_nft_to_claim w
                    JOIN event_cards ec ON ec.event_id = w.event_id
                    LEFT JOIN events e ON e.id = w.event_id
                    LEFT JOIN seasons s ON s.id = w.season_id
                    LEFT JOIN tags tp ON LOWER(BTRIM(tp.label)) = LOWER(BTRIM(ec.primary_tag))
                    {where_sql}
                """
                cursor.execute(
                    f"""
                    SELECT COUNT(*) AS total
                    {from_sql}
                    """,
                    tuple(params),
                )
                total_row = cursor.fetchone() or {}
                total = int(total_row.get("total") or 0) if isinstance(total_row, dict) else int(total_row[0] or 0)
                cursor.execute(
                    f"""
                    SELECT
                        w.id AS winner_row_id,
                        w.season_id,
                        s.type AS season_type,
                        s.season_number,
                        w.proxy_wallet,
                        w.event_id,
                        w.event_slug,
                        e.title AS event_title,
                        ec.series_id,
                        ec.reccurence,
                        ec.primary_tag,
                        tp.hex_color AS primary_tag_hex_color
                    {from_sql}
                    ORDER BY w.season_id DESC, w.rank ASC NULLS LAST, w.id DESC
                    LIMIT %s
                    OFFSET %s
                    """,
                    (*params, safe_limit, safe_offset),
                )
                rows = [dict(row) for row in cursor.fetchall()]
                return {
                    "rows": [self._format_card_builder_candidate(row) for row in rows],
                    "total": total,
                }
        finally:
            conn.close()

    def get_card_builder_candidate(self, winner_row_id: int) -> Dict[str, Any]:
        if winner_row_id <= 0:
            raise ValueError("winner_row_id must be positive")
        conn = self.manager.get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
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
                        w.entry_bracket,
                        COALESCE(w.archetype, p.archetype) AS archetype,
                        COALESCE(w.archetype_description, p.archetype_description) AS archetype_description,
                        COALESCE(w.archetype_math, p.archetype_math) AS archetype_math,
                        COALESCE(w.rarity_bracket, p.rarity_bracket) AS rarity_bracket,
                        w.edge,
                        w.yield,
                        w.gravity,
                        w.rank,
                        w.window_start,
                        w.window_end,
                        w.snapshot_at,
                        ec.series_id,
                        ec.reccurence,
                        ec.manual_image_url,
                        ec.card_title,
                        ec.card_lore,
                        ec.primary_tag,
                        ec.secondary_tag,
                        tp.hex_color AS primary_tag_hex_color,
                        ec.generated_at AS event_card_generated_at,
                        ec.updated_at AS event_card_updated_at
                    FROM winner_wallets_nft_to_claim w
                    JOIN event_cards ec ON ec.event_id = w.event_id
                    LEFT JOIN LATERAL (
                        SELECT
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
                    LIMIT 1
                    """,
                    (winner_row_id,),
                )
                row = cursor.fetchone()
                if not row:
                    raise ValueError(f"Card builder candidate with row_id={winner_row_id} not found")
                return self._format_card_builder_candidate(dict(row))
        finally:
            conn.close()

    def create_winner_wallet_row(self, req: WinnerWalletsUpsertRequest) -> Dict[str, Any]:
        wallet = req.wallet_address.strip()
        if not self.EVM_WALLET_RE.fullmatch(wallet):
            raise ValueError("wallet_address must be a valid EVM address (0x + 40 hex chars)")
        source = req.source.strip() or "manual_admin"
        window_start = self.parse_iso_datetime_utc(req.window_start_iso)
        window_end = self.parse_iso_datetime_utc(req.window_end_iso)
        if window_end <= window_start:
            raise ValueError("window_end must be later than window_start")
        snapshot_at = (
            self.parse_iso_datetime_utc(req.snapshot_at_iso)
            if req.snapshot_at_iso and req.snapshot_at_iso.strip()
            else datetime.now(timezone.utc)
        )
        minted_at = (
            self.parse_iso_datetime_utc(req.minted_at_iso)
            if req.minted_at_iso and req.minted_at_iso.strip()
            else None
        )

        conn = self.manager.get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                cursor.execute(
                    """
                    INSERT INTO winner_wallets_nft_to_claim (
                        season_id, proxy_wallet, source,
                        window_start, window_end, snapshot_at,
                        event_id, event_slug,
                        is_minted, minted_at, minted_to_wallet,
                        minted_to_solana_wallet, minted_claim_id, minted_tx_hash, minted_asset_address
                    ) VALUES (
                        %s, %s, %s,
                        %s, %s, %s, %s, %s,
                        %s, %s, %s,
                        %s, %s, %s, %s
                    )
                    RETURNING
                        id, season_id, proxy_wallet AS wallet_address, source,
                        window_start, window_end, snapshot_at, created_at,
                        event_id, event_slug, entry_cwap, total_volume, total_pnl,
                        roi_percentage, entry_bracket, edge, yield, gravity, rank,
                        archetype, archetype_description, archetype_math, rarity_bracket,
                        is_minted, minted_at,
                        minted_to_wallet, minted_to_solana_wallet, minted_claim_id,
                        minted_tx_hash, minted_asset_address
                    """,
                    (
                        req.season_id,
                        wallet.lower(),
                        source,
                        window_start,
                        window_end,
                        snapshot_at,
                        self._normalize_optional_text(req.event_id),
                        self._normalize_optional_text(req.event_slug),
                        req.is_minted,
                        minted_at,
                        self._normalize_optional_text(req.minted_to_wallet),
                        self._normalize_optional_text(req.minted_to_solana_wallet),
                        req.minted_claim_id,
                        self._normalize_optional_text(req.minted_tx_hash),
                        self._normalize_optional_text(req.minted_asset_address),
                    ),
                )
                row = cursor.fetchone()
                if not row:
                    raise RuntimeError("Failed to create winner wallet row")
            conn.commit()
            self.clear_wallets_cache()
            return self._format_winner_row(dict(row))
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def update_winner_wallet_row(self, row_id: int, req: WinnerWalletsUpsertRequest) -> Dict[str, Any]:
        wallet = req.wallet_address.strip()
        if not self.EVM_WALLET_RE.fullmatch(wallet):
            raise ValueError("wallet_address must be a valid EVM address (0x + 40 hex chars)")
        source = req.source.strip() or "manual_admin"
        window_start = self.parse_iso_datetime_utc(req.window_start_iso)
        window_end = self.parse_iso_datetime_utc(req.window_end_iso)
        if window_end <= window_start:
            raise ValueError("window_end must be later than window_start")
        snapshot_at = (
            self.parse_iso_datetime_utc(req.snapshot_at_iso)
            if req.snapshot_at_iso and req.snapshot_at_iso.strip()
            else datetime.now(timezone.utc)
        )
        minted_at = (
            self.parse_iso_datetime_utc(req.minted_at_iso)
            if req.minted_at_iso and req.minted_at_iso.strip()
            else None
        )

        conn = self.manager.get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                cursor.execute(
                    """
                    UPDATE winner_wallets_nft_to_claim
                    SET
                        season_id = %s,
                        proxy_wallet = %s,
                        source = %s,
                        window_start = %s,
                        window_end = %s,
                        snapshot_at = %s,
                        event_id = %s,
                        event_slug = %s,
                        is_minted = %s,
                        minted_at = %s,
                        minted_to_wallet = %s,
                        minted_to_solana_wallet = %s,
                        minted_claim_id = %s,
                        minted_tx_hash = %s,
                        minted_asset_address = %s
                    WHERE id = %s
                    RETURNING
                        id, season_id, proxy_wallet AS wallet_address, source,
                        window_start, window_end, snapshot_at, created_at,
                        event_id, event_slug, entry_cwap, total_volume, total_pnl,
                        roi_percentage, entry_bracket, edge, yield, gravity, rank,
                        archetype, archetype_description, archetype_math, rarity_bracket,
                        is_minted, minted_at,
                        minted_to_wallet, minted_to_solana_wallet, minted_claim_id,
                        minted_tx_hash, minted_asset_address
                    """,
                    (
                        req.season_id,
                        wallet.lower(),
                        source,
                        window_start,
                        window_end,
                        snapshot_at,
                        self._normalize_optional_text(req.event_id),
                        self._normalize_optional_text(req.event_slug),
                        req.is_minted,
                        minted_at,
                        self._normalize_optional_text(req.minted_to_wallet),
                        self._normalize_optional_text(req.minted_to_solana_wallet),
                        req.minted_claim_id,
                        self._normalize_optional_text(req.minted_tx_hash),
                        self._normalize_optional_text(req.minted_asset_address),
                        row_id,
                    ),
                )
                row = cursor.fetchone()
                if not row:
                    raise ValueError(f"Winner wallet row {row_id} not found")
            conn.commit()
            self.clear_wallets_cache()
            return self._format_winner_row(dict(row))
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def delete_winner_wallet_row(self, row_id: int) -> None:
        conn = self.manager.get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute("DELETE FROM winner_wallets_nft_to_claim WHERE id = %s", (row_id,))
                if cursor.rowcount != 1:
                    raise ValueError(f"Winner wallet row {row_id} not found")
            conn.commit()
            self.clear_wallets_cache()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def list_event_cards(
        self,
        *,
        limit: int,
        status: Optional[str],
        event_id: Optional[str],
        snapshot_scope: Optional[str],
        future_standard_filtered: bool = False,
    ) -> List[Dict[str, Any]]:
        safe_limit = min(max(limit, 1), 2000)
        status_filter = (status or "").strip().lower()
        if status_filter and status_filter not in {"ok", "error"}:
            raise ValueError("status must be one of: ok, error")
        event_id_filter = (event_id or "").strip()
        snapshot_scope_filter = (snapshot_scope or "all").strip().lower()
        standard_snapshot_season_id: Optional[int] = None
        if snapshot_scope_filter.startswith("standard_season:"):
            raw_season_id = snapshot_scope_filter.split(":", 1)[1].strip()
            if not raw_season_id or not raw_season_id.isdigit():
                raise ValueError("snapshot_scope standard_season must include numeric season_id")
            standard_snapshot_season_id = int(raw_season_id)
            snapshot_scope_filter = "standard_season"
        if snapshot_scope_filter not in {"all", "genesis", "standard", "standard_season", "next_window"}:
            raise ValueError(
                "snapshot_scope must be one of: all, genesis, standard, standard_season:<season_id>, next_window"
            )

        conn = self.manager.get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                where_parts: List[str] = []
                params: List[Any] = []
                if future_standard_filtered:
                    if snapshot_scope_filter == "next_window":
                        filtered_event_ids = self.scheduler._get_standard_filtered_event_ids(cursor)
                    elif snapshot_scope_filter == "standard_season" and standard_snapshot_season_id is not None:
                        filtered_event_ids = self.scheduler._get_standard_filtered_event_ids(
                            cursor,
                            season_id=standard_snapshot_season_id,
                        )
                    else:
                        return []
                    if not filtered_event_ids:
                        return []
                    where_parts.append("ec.event_id = ANY(%s)")
                    params.append(filtered_event_ids)
                    snapshot_scope_filter = "all"
                if event_id_filter:
                    where_parts.append("ec.event_id = %s")
                    params.append(event_id_filter)
                if status_filter:
                    where_parts.append("ec.status = %s")
                    params.append(status_filter)
                if snapshot_scope_filter == "genesis":
                    where_parts.append(
                        """
                        COALESCE(e.end_date::date, e.creation_date::date, e.start_date::date)
                        BETWEEN %s AND %s
                        """
                    )
                    params.extend([GENESIS_START_DATE, GENESIS_END_DATE])
                elif snapshot_scope_filter == "standard":
                    where_parts.append(
                        """
                        (
                            EXISTS (
                                SELECT 1
                                FROM seasons s
                                LEFT JOIN event_resolution_queue erq ON erq.event_id = ec.event_id
                                WHERE s.type = 'standard'
                                  AND erq.status = 'processed'
                                  AND erq.resolution_ready_at IS NOT NULL
                                  AND erq.resolution_ready_at >= (
                                      s.start_date
                                      - make_interval(days => %s)
                                      - make_interval(days => %s)
                                  )
                                  AND erq.resolution_ready_at < (
                                      s.start_date
                                      - make_interval(days => %s)
                                  )
                            )
                            OR (
                                EXISTS (
                                    SELECT 1
                                    FROM event_resolution_queue erq
                                    WHERE erq.event_id = ec.event_id
                                      AND erq.status = 'processed'
                                      AND erq.resolution_ready_at IS NOT NULL
                                      AND erq.resolution_ready_at < (
                                          SELECT
                                              s1.start_date
                                              - make_interval(days => %s)
                                              - make_interval(days => %s)
                                          FROM seasons s1
                                          WHERE s1.type = 'standard'
                                          ORDER BY s1.start_date ASC, s1.id ASC
                                          LIMIT 1
                                      )
                                )
                                AND NOT (
                                    COALESCE(e.end_date::date, e.creation_date::date, e.start_date::date)
                                    BETWEEN %s AND %s
                                )
                            )
                        )
                        """
                    )
                    params.extend(
                        [
                            self.origin_snapshot_offset_days,
                            self.origin_lookback_days_standard,
                            self.origin_snapshot_offset_days,
                            self.origin_snapshot_offset_days,
                            self.origin_lookback_days_standard,
                            GENESIS_START_DATE,
                            GENESIS_END_DATE,
                        ]
                    )
                elif snapshot_scope_filter == "standard_season":
                    where_parts.append(
                        """
                        (
                            EXISTS (
                                SELECT 1
                                FROM seasons s
                                LEFT JOIN event_resolution_queue erq ON erq.event_id = ec.event_id
                                WHERE s.type = 'standard'
                                  AND s.id = %s
                                  AND erq.status = 'processed'
                                  AND erq.resolution_ready_at IS NOT NULL
                                  AND erq.resolution_ready_at >= (
                                      s.start_date
                                      - make_interval(days => %s)
                                      - make_interval(days => %s)
                                  )
                                  AND erq.resolution_ready_at < (
                                      s.start_date
                                      - make_interval(days => %s)
                                  )
                            )
                            OR (
                                EXISTS (
                                    SELECT 1
                                    FROM event_resolution_queue erq
                                    WHERE erq.event_id = ec.event_id
                                      AND erq.status = 'processed'
                                      AND erq.resolution_ready_at IS NOT NULL
                                      AND erq.resolution_ready_at < (
                                          SELECT
                                              s1.start_date
                                              - make_interval(days => %s)
                                              - make_interval(days => %s)
                                          FROM seasons s1
                                          WHERE s1.type = 'standard'
                                          ORDER BY s1.start_date ASC, s1.id ASC
                                          LIMIT 1
                                      )
                                )
                                AND %s = (
                                    SELECT s0.id
                                    FROM seasons s0
                                    WHERE s0.type = 'standard'
                                    ORDER BY s0.start_date ASC, s0.id ASC
                                    LIMIT 1
                                )
                                AND NOT (
                                    COALESCE(e.end_date::date, e.creation_date::date, e.start_date::date)
                                    BETWEEN %s AND %s
                                )
                            )
                        )
                        """
                    )
                    params.extend(
                        [
                            standard_snapshot_season_id,
                            self.origin_snapshot_offset_days,
                            self.origin_lookback_days_standard,
                            self.origin_snapshot_offset_days,
                            self.origin_snapshot_offset_days,
                            self.origin_lookback_days_standard,
                            standard_snapshot_season_id,
                            GENESIS_START_DATE,
                            GENESIS_END_DATE,
                        ]
                    )
                elif snapshot_scope_filter == "next_window":
                    where_parts.append(
                        """
                        EXISTS (
                            SELECT 1
                            FROM (
                                SELECT
                                    (
                                        s.end_date
                                        - make_interval(days => %s)
                                        - make_interval(days => %s)
                                    ) AS window_start,
                                    (
                                        s.end_date
                                        - make_interval(days => %s)
                                    ) AS window_end
                                FROM seasons s
                                WHERE s.type = 'standard'
                                ORDER BY s.start_date DESC, s.id DESC
                                LIMIT 1
                            ) nw
                            LEFT JOIN event_resolution_queue erq ON erq.event_id = ec.event_id
                            WHERE erq.status = 'processed'
                              AND erq.resolution_ready_at IS NOT NULL
                              AND erq.resolution_ready_at >= nw.window_start
                              AND erq.resolution_ready_at < nw.window_end
                        )
                        """
                    )
                    params.extend(
                        [
                            self.origin_snapshot_offset_days,
                            self.origin_lookback_days_standard,
                            self.origin_snapshot_offset_days,
                        ]
                    )

                where_sql = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
                query = f"""
                    SELECT
                        ec.event_id,
                        ec.series_id,
                        ec.reccurence,
                        e.ticker AS event_ticker,
                        e.slug AS event_slug,
                        e.title AS event_title,
                        e.description AS event_description,
                        COALESCE(NULLIF(BTRIM(e.image), ''), NULLIF(BTRIM(e.icon), '')) AS event_image_url,
                        ec.manual_image_url,
                        ec.card_title,
                        ec.card_lore,
                        ec.primary_tag,
                        ec.secondary_tag,
                        tp.hex_color AS primary_tag_hex_color,
                        ts.hex_color AS secondary_tag_hex_color,
                        ec.agent_name,
                        ec.model_name,
                        ec.prompt_version,
                        ec.status,
                        ec.error_text,
                        ec.generated_at,
                        ec.updated_at
                    FROM event_cards ec
                    LEFT JOIN events e ON e.id = ec.event_id
                    LEFT JOIN tags tp ON LOWER(BTRIM(tp.label)) = LOWER(BTRIM(ec.primary_tag))
                    LEFT JOIN tags ts ON LOWER(BTRIM(ts.label)) = LOWER(BTRIM(ec.secondary_tag))
                    {where_sql}
                    ORDER BY ec.generated_at DESC, ec.event_id ASC
                    LIMIT %s
                """
                params.append(safe_limit)
                cursor.execute(query, tuple(params))
                rows = [dict(row) for row in cursor.fetchall()]
                return [self._format_event_card_row(row) for row in rows]
        finally:
            conn.close()

    def update_event_card(self, event_id: str, req: EventCardUpdateRequest) -> Dict[str, Any]:
        target_event_id = event_id.strip()
        if not target_event_id:
            raise ValueError("event_id is required")
        status = req.status.strip().lower()
        if status not in {"ok", "error"}:
            raise ValueError("status must be one of: ok, error")

        card_title = self._normalize_optional_text(req.card_title)
        card_lore = self._normalize_optional_text(req.card_lore)
        primary_tag = self._normalize_optional_text(req.primary_tag)
        secondary_tag = self._normalize_optional_text(req.secondary_tag)
        agent_name = self._normalize_optional_text(req.agent_name)
        model_name = self._normalize_optional_text(req.model_name)
        prompt_version = self._normalize_optional_text(req.prompt_version)
        error_text = self._normalize_optional_text(req.error_text)

        conn = self.manager.get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                cursor.execute(
                    """
                    UPDATE event_cards
                    SET
                        card_title = %s,
                        card_lore = %s,
                        primary_tag = %s,
                        secondary_tag = %s,
                        agent_name = COALESCE(%s, agent_name),
                        model_name = COALESCE(%s, model_name),
                        prompt_version = COALESCE(%s, prompt_version),
                        status = %s,
                        error_text = %s,
                        updated_at = NOW()
                    WHERE event_id = %s
                    RETURNING
                        event_id,
                        series_id,
                        reccurence,
                        card_title,
                        card_lore,
                        primary_tag,
                        secondary_tag,
                        agent_name,
                        model_name,
                        prompt_version,
                        status,
                        error_text,
                        generated_at,
                        updated_at
                    """,
                    (
                        card_title,
                        card_lore,
                        primary_tag,
                        secondary_tag,
                        agent_name,
                        model_name,
                        prompt_version,
                        status,
                        error_text,
                        target_event_id,
                    ),
                )
                row = cursor.fetchone()
                if not row:
                    raise ValueError(f"Event card for event_id={target_event_id} not found")
                persisted = dict(row)
                primary_for_sync = persisted.get("primary_tag") if persisted.get("status") == "ok" else None
                self._sync_event_tag_primary_flags(
                    cursor=cursor,
                    event_id=target_event_id,
                    primary_tag=primary_for_sync,
                )
            conn.commit()
            return self._format_event_card_row(persisted)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def regenerate_event_card(
        self,
        event_id: str,
        prompt_parts_override: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        target_event_id = event_id.strip()
        if not target_event_id:
            raise ValueError("event_id is required")

        generator = self.scheduler._get_event_card_generator()
        conn = self.manager.get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                rows = self.scheduler._fetch_event_card_payloads(cursor, [target_event_id])
            payload_row = rows[0] if rows else None
            if not payload_row:
                raise ValueError(f"Event payload not found for event_id={target_event_id}")

            payload = {
                "title": payload_row.get("title"),
                "description": payload_row.get("description"),
                "series": {
                    "title": payload_row.get("series_title"),
                    "recurrence": payload_row.get("series_recurrence"),
                },
                "tags": payload_row.get("tags") or [],
            }
            try:
                if prompt_parts_override:
                    card, prompt_ctx = generator.generate_with_prompt_parts(
                        payload=payload,
                        prompt_parts=prompt_parts_override,
                    )
                else:
                    prompt_ctx = generator.build_prompt_context(payload)
                    card = generator.generate(payload)
            except Exception as exc:
                raise ValueError(f"Failed to regenerate event card for event_id={target_event_id}: {exc}")

            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                cursor.execute(
                    """
                    SELECT
                        e.id AS event_id,
                        ec.series_id,
                        ec.reccurence,
                        e.ticker AS event_ticker,
                        e.slug AS event_slug,
                        e.title AS event_title,
                        e.description AS event_description,
                        ec.card_title,
                        ec.card_lore,
                        ec.primary_tag,
                        ec.secondary_tag,
                        tp.hex_color AS primary_tag_hex_color,
                        ts.hex_color AS secondary_tag_hex_color,
                        ec.agent_name,
                        ec.model_name,
                        ec.prompt_version,
                        ec.status,
                        ec.error_text,
                        ec.generated_at,
                        ec.updated_at
                    FROM events e
                    LEFT JOIN event_cards ec ON ec.event_id = e.id
                    LEFT JOIN tags tp ON LOWER(BTRIM(tp.label)) = LOWER(BTRIM(ec.primary_tag))
                    LEFT JOIN tags ts ON LOWER(BTRIM(ts.label)) = LOWER(BTRIM(ec.secondary_tag))
                    WHERE e.id = %s
                    LIMIT 1
                    """,
                    (target_event_id,),
                )
                row = cursor.fetchone()
                if not row:
                    raise RuntimeError(f"Event {target_event_id} not found")

            preview = dict(row)
            generated = card.model_dump()
            preview["card_title"] = generated.get("card_title")
            preview["card_lore"] = generated.get("card_lore")
            preview["primary_tag"] = generated.get("primary_tag")
            preview["secondary_tag"] = generated.get("secondary_tag")
            preview["agent_name"] = str(preview.get("agent_name") or self.scheduler.event_cards_agent_name)
            preview["model_name"] = str(generator.model)
            preview["prompt_version"] = str(generator.prompt_version)
            preview["status"] = "ok"
            preview["error_text"] = None
            # Preview-only regeneration: DB is not modified here.
            return {
                "row": self._format_event_card_row(preview),
                "prompt_text": str(prompt_ctx["full_prompt"]),
                "system_instruction": str(prompt_ctx["system_instruction"]),
                "user_prompt": str(prompt_ctx["prompt"]),
                "prompt_parts": dict(prompt_ctx["prompt_parts"]),
            }
        finally:
            conn.close()

    def get_event_card_prompt_preview(self, event_id: str) -> Dict[str, Any]:
        target_event_id = event_id.strip()
        if not target_event_id:
            raise ValueError("event_id is required")

        generator = self.scheduler._get_event_card_generator()
        conn = self.manager.get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                rows = self.scheduler._fetch_event_card_payloads(cursor, [target_event_id])
            payload_row = rows[0] if rows else None
            if not payload_row:
                raise ValueError(f"Event payload not found for event_id={target_event_id}")

            payload = {
                "title": payload_row.get("title"),
                "description": payload_row.get("description"),
                "series": {
                    "title": payload_row.get("series_title"),
                    "recurrence": payload_row.get("series_recurrence"),
                },
                "tags": payload_row.get("tags") or [],
            }
            prompt_ctx = generator.build_prompt_context(payload)
            return {
                "event_id": target_event_id,
                "agent_name": self.scheduler.event_cards_agent_name,
                "model_name": str(generator.model),
                "prompt_version": str(generator.prompt_version),
                "prompt_text": str(prompt_ctx["full_prompt"]),
                "system_instruction": str(prompt_ctx["system_instruction"]),
                "user_prompt": str(prompt_ctx["prompt"]),
                "prompt_parts": dict(prompt_ctx["prompt_parts"]),
            }
        finally:
            conn.close()


service = SeasonWorkbenchService()
ws_hub = WsHub()

app = FastAPI(title="PolyStars Season Test Web API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.get("/api/server-time")
def server_time() -> Dict[str, str]:
    return {"now_utc_iso": datetime.now(timezone.utc).isoformat()}


@app.get("/api/config")
def get_config() -> Dict[str, Any]:
    return {
        "default_solana_recipient": DEFAULT_SOLANA_RECIPIENT,
        "default_base_recipient": DEFAULT_BASE_RECIPIENT,
        "blockchains": [BLOCKCHAIN_SOLANA, BLOCKCHAIN_BASE_ZORA],
    }


@app.get("/api/user-web/wallet-actions")
def get_user_web_wallet_actions() -> Dict[str, Any]:
    env_on = _user_web_wallet_actions_env_override()
    db_disabled = service.get_user_web_wallet_actions_disabled_db()
    return {
        "wallet_actions_disabled": env_on or db_disabled,
        "database_wallet_actions_disabled": db_disabled,
        "env_override_active": env_on,
    }


@app.put("/api/user-web/wallet-actions")
def put_user_web_wallet_actions(body: UserWebWalletActionsUpdate) -> Dict[str, Any]:
    service.set_user_web_wallet_actions_disabled(body.disabled)
    env_on = _user_web_wallet_actions_env_override()
    db_disabled = service.get_user_web_wallet_actions_disabled_db()
    return {
        "wallet_actions_disabled": env_on or db_disabled,
        "database_wallet_actions_disabled": db_disabled,
        "env_override_active": env_on,
    }


@app.get("/api/seasons")
def get_seasons() -> List[Dict[str, Any]]:
    return service.get_seasons()


@app.get("/api/overview")
def get_overview() -> Dict[str, Any]:
    return service.get_overview()


@app.get("/api/wallets")
def get_wallets(
    season_id: int,
    wallet_filter: str = "all",
    include_position_wallets: bool = True,
    limit: int = 35,
) -> Dict[str, Any]:
    wallets = service.get_wallets(
        season_id=season_id,
        wallet_filter=wallet_filter,
        include_position_wallets=include_position_wallets,
        limit=limit,
    )
    return {"wallets": wallets}


@app.post("/api/eligibility")
def check_eligibility(req: EligibilityRequest) -> Dict[str, Any]:
    wallet = req.wallet.strip().lower()
    if not wallet:
        raise HTTPException(status_code=400, detail="Wallet is required")
    try:
        payload = service.season_manager.check_user_eligibility(wallet)
        if req.season_id is not None:
            selected = service.season_manager.check_user_eligibility_for_season(
                wallet_address=wallet,
                season_id=req.season_id,
            )
            payload["selected_season_id"] = req.season_id
            payload["is_origin_wallet_active_standard"] = payload.get("is_origin_wallet")
            payload["is_origin_wallet"] = bool(selected.get("is_origin_wallet"))
            payload["selected_season"] = selected
        return payload
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/claims/season-info")
def claim_season_info(
    season_id: int,
    wallet: str = "",
    auto_phase: bool = True,
    manual_phase: str = "breach",
    blockchain: str = BLOCKCHAIN_SOLANA,
) -> Dict[str, Any]:
    try:
        return service.get_claim_season_info(
            season_id=season_id,
            wallet=wallet,
            auto_phase=auto_phase,
            manual_phase=manual_phase,
            blockchain=blockchain,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/claims/by-season/{season_id}")
def season_claims(season_id: int) -> Dict[str, Any]:
    try:
        return service.get_season_claims(season_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/claims/mint")
async def mint_claim(req: MintClaimRequest) -> Dict[str, Any]:
    try:
        result = service.run_mint_claim(req)
        await ws_hub.broadcast("mint_finished", {"status": "ok", "claim_id": result.get("claim_id")})
        return result
    except Exception as exc:
        logger.exception(
            "Mint failed for wallet=%s season_id=%s chain=%s",
            req.wallet,
            req.season_id,
            req.blockchain,
        )
        await ws_hub.broadcast("mint_finished", {"status": "error", "error": str(exc)})
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/actions/season-update")
async def run_season_update() -> Dict[str, str]:
    try:
        message = service.run_season_lifecycle_update()
        await ws_hub.broadcast("season_update", {"status": "ok", "message": message})
        return {"status": "ok", "message": message}
    except Exception as exc:
        await ws_hub.broadcast("season_update", {"status": "error", "message": str(exc)})
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/scenarios/season/{season_id}")
def load_scenario_params(season_id: int) -> Dict[str, Any]:
    try:
        row = service.load_scenario_season_params(season_id)
        row["start_date_iso"] = row["start_date"].astimezone(timezone.utc).isoformat()
        row["end_date_iso"] = row["end_date"].astimezone(timezone.utc).isoformat()
        return row
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/scenarios/quick-phase")
def set_quick_phase(req: QuickPhaseRequest) -> Dict[str, str]:
    try:
        season = service.get_season_meta(req.season_id)
        if season["type"] != "standard":
            raise ValueError("Quick phase setup is only for standard season")
        now = datetime.now(timezone.utc)
        new_start = now - timedelta(days=req.days_since_start)
        new_end = new_start + timedelta(days=10)
        service.update_season_dates(req.season_id, new_start, new_end)
        return {"status": "ok", "message": f"Updated season {req.season_id} dates for quick phase"}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/scenarios/manual-date-shift")
def manual_date_shift(req: ManualDateShiftRequest) -> Dict[str, str]:
    try:
        season = service.get_season_meta(req.season_id)
        original_start = season["start_date"]
        original_end = season["end_date"]
        duration = original_end - original_start
        start_date = datetime.now(timezone.utc) + timedelta(days=req.shift_days)
        end_date = start_date + duration
        service.update_season_dates(req.season_id, start_date, end_date)
        return {"status": "ok", "message": f"Shifted season {req.season_id} by {req.shift_days} days"}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/scenarios/remaining-supply")
def update_remaining_supply(req: RemainingSupplyRequest) -> Dict[str, str]:
    try:
        service.apply_remaining_supply(req.season_id, req.remaining_supply)
        return {"status": "ok", "message": f"Updated season {req.season_id} remaining_supply={req.remaining_supply}"}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/scenarios/apply-advanced")
def apply_advanced(req: AdvancedScenarioRequest) -> Dict[str, str]:
    try:
        service.apply_advanced_scenario(req)
        return {"status": "ok", "message": f"Updated season {req.season_id} advanced params"}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/scenarios/simulate-generated-cards-batch")
def simulate_generated_cards_batch(req: SimulateGeneratedCardsBatchRequest) -> Dict[str, Any]:
    try:
        return run_admin_simulated_card_generations(
            max_count=req.max_count,
            origin_match_fraction=req.origin_match_fraction,
        )
    except Exception as exc:
        logger.exception("simulate-generated-cards-batch failed")
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/reset")
async def run_reset(req: ResetRequest) -> Dict[str, str]:
    if not req.confirm:
        raise HTTPException(status_code=400, detail="Set confirm=true to run reset")
    try:
        message = service.run_reset_sql()
        await ws_hub.broadcast("reset", {"status": "ok", "message": message})
        return {"status": "ok", "message": message}
    except Exception as exc:
        await ws_hub.broadcast("reset", {"status": "error", "message": str(exc)})
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/master-collection")
def master_collection() -> Dict[str, str]:
    address = service.get_master_collection_address()
    return {"address": address}


@app.get("/api/winners")
def get_winners(season_id: Optional[int] = None, limit: int = 300) -> Dict[str, Any]:
    try:
        rows = service.list_winner_wallet_rows(season_id=season_id, limit=limit)
        return {"rows": rows}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/winners")
def create_winner(req: WinnerWalletsUpsertRequest) -> Dict[str, Any]:
    try:
        row = service.create_winner_wallet_row(req)
        return {"row": row}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.put("/api/winners/{row_id}")
def update_winner(row_id: int, req: WinnerWalletsUpsertRequest) -> Dict[str, Any]:
    try:
        row = service.update_winner_wallet_row(row_id=row_id, req=req)
        return {"row": row}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.delete("/api/winners/{row_id}")
def delete_winner(row_id: int) -> Dict[str, Any]:
    try:
        service.delete_winner_wallet_row(row_id=row_id)
        return {"status": "ok", "message": f"Winner wallet row {row_id} deleted"}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/event-cards")
def get_event_cards(
    limit: int = 500,
    status: Optional[str] = None,
    event_id: Optional[str] = None,
    snapshot_scope: Optional[str] = "all",
    future_standard_filtered: bool = False,
) -> Dict[str, Any]:
    try:
        rows = service.list_event_cards(
            limit=limit,
            status=status,
            event_id=event_id,
            snapshot_scope=snapshot_scope,
            future_standard_filtered=future_standard_filtered,
        )
        return {"rows": rows}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.put("/api/event-cards/{event_id}")
def update_event_card(event_id: str, req: EventCardUpdateRequest) -> Dict[str, Any]:
    try:
        row = service.update_event_card(event_id=event_id, req=req)
        return {"row": row}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/event-cards/{event_id}/manual-image")
def upload_event_card_manual_image(event_id: str, file: UploadFile = File(...)) -> Dict[str, Any]:
    try:
        row = service.upload_event_card_manual_image(event_id=event_id, file=file)
        return {"row": row}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.delete("/api/event-cards/{event_id}/manual-image")
def delete_event_card_manual_image(event_id: str) -> Dict[str, Any]:
    try:
        row = service.delete_event_card_manual_image(event_id=event_id)
        return {"row": row}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/event-cards/{event_id}/regenerate")
def regenerate_event_card(event_id: str, req: Optional[EventCardRegenerateRequest] = None) -> Dict[str, Any]:
    try:
        prompt_parts_override = req.prompt_parts.model_dump() if req and req.prompt_parts else None
        payload = service.regenerate_event_card(
            event_id=event_id,
            prompt_parts_override=prompt_parts_override,
        )
        return {"status": "ok", **payload}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/event-cards/{event_id}/prompt")
def event_card_prompt(event_id: str) -> Dict[str, Any]:
    try:
        return service.get_event_card_prompt_preview(event_id=event_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/card-builder/candidates")
def get_card_builder_candidates(
    season_id: Optional[int] = None,
    limit: int = 200,
    offset: int = 0,
    q: Optional[str] = None,
) -> Dict[str, Any]:
    try:
        payload = service.list_card_builder_candidates(
            season_id=season_id,
            limit=limit,
            offset=offset,
            search_query=q,
        )
        return payload
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/card-builder/candidates/{winner_row_id}")
def get_card_builder_candidate(winner_row_id: int) -> Dict[str, Any]:
    try:
        row = service.get_card_builder_candidate(winner_row_id=winner_row_id)
        return {"row": row}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/card-builder/preview")
def card_builder_preview(req: CardBuilderPreviewRequest) -> Dict[str, Any]:
    try:
        payload = dict(req.payload or {})
        image_url = str(payload.get("image_url") or "").strip()
        if not image_url:
            raise ValueError("image_url is required and must come from manual_image_url")
        payload["image_url"] = image_url
        svg = generate_card_svg(payload)
        back_svg = generate_card_back_svg(payload)
        arch = str(payload.get("archetype", "") or "").strip() or None
        return {
            "svg": svg,
            "back_svg": back_svg,
            "archetype": arch,
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.websocket("/ws/events")
async def ws_events(websocket: WebSocket) -> None:
    await ws_hub.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_hub.disconnect(websocket)
    except Exception:
        ws_hub.disconnect(websocket)

