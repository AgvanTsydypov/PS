"""
FastAPI web API for season_test_gui functionality.

Run:
    uvicorn web_backend.main:app --host 0.0.0.0 --port 8001 --reload
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import monotonic
from typing import Any, Dict, List, Optional

import psycopg2.extras
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from solders.pubkey import Pubkey

# Add project root to path (same approach as other scripts)
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from scripts.data_loading_manager import DataLoadingManager
from scripts.daily_scheduler_simple import SimplifiedScheduler
from scripts.season_manager import SeasonManager
from scripts.solana_service import MintedNftResult, SolanaClient
from scripts.zora_service import ZoraClient

logger = logging.getLogger(__name__)

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
        # Wallet list is mostly stable within a season. Cache for 10 days by default.
        self.wallets_cache_ttl_seconds = int(os.getenv("WALLETS_CACHE_TTL_SECONDS", "864000"))
        self._wallets_cache: Dict[tuple[int, str, bool, int], tuple[float, List[str]]] = {}
        self.ensure_claims_schema_for_mint()
        self.ensure_winners_schema_for_assignment()

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
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

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
                        SELECT DISTINCT lower(wallet_address) AS wallet
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
                            SELECT lower(wallet_address) AS wallet
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
                            SELECT lower(wallet_address) AS wallet
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
        event_title = str(row.get("event_title") or "").strip()
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
        if event_title:
            cursor.execute(
                """
                SELECT COALESCE(NULLIF(TRIM(image), ''), NULLIF(TRIM(icon), '')) AS event_image
                FROM events WHERE title = %s LIMIT 1
                """,
                (event_title,),
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
            "total_pnl_window": float(row.get("total_pnl_window") or 0),
            "pnl_rank": int(row.get("pnl_rank") or 0),
            "window_start": row.get("window_start").isoformat() if row.get("window_start") else None,
            "window_end": row.get("window_end").isoformat() if row.get("window_end") else None,
            "snapshot_at": row.get("snapshot_at").isoformat() if row.get("snapshot_at") else None,
            "event_id": row.get("event_id"),
            "market_id": row.get("market_id"),
            "condition_id": row.get("condition_id"),
            "event_slug": row.get("event_slug"),
            "event_title": row.get("event_title"),
            "event_image_url": event_image_url,
        }
        return WinnerClaimAllocation(
            row_id=int(row["id"]),
            winner_wallet_address=str(row["wallet_address"]),
            assignment_type=assignment_type,
            pnl_value=float(row.get("total_pnl_window") or 0),
            rank=int(row.get("pnl_rank") or 0),
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
                        wallet_address,
                        source,
                        total_pnl_window,
                        pnl_rank,
                        window_start,
                        window_end,
                        snapshot_at,
                        event_id,
                        market_id,
                        condition_id,
                        event_slug,
                        event_title,
                        COALESCE(is_minted, FALSE) AS is_minted,
                        minted_to_wallet
                    FROM winner_wallets_nft_to_claim
                    WHERE season_id = %s
                      AND LOWER(wallet_address) = LOWER(%s)
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
                        wallet_address,
                        source,
                        total_pnl_window,
                        pnl_rank,
                        window_start,
                        window_end,
                        snapshot_at,
                        event_id,
                        market_id,
                        condition_id,
                        event_slug,
                        event_title
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

    def run_reset_sql(self) -> None:
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

    def list_winner_wallet_rows(self, season_id: Optional[int], limit: int) -> List[Dict[str, Any]]:
        safe_limit = min(max(limit, 1), 1000)
        conn = self.manager.get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                if season_id is None:
                    cursor.execute(
                        """
                        SELECT
                            id, season_id, wallet_address, source, total_pnl_window, pnl_rank,
                            window_start, window_end, snapshot_at, created_at, event_id, market_id,
                            condition_id, event_slug, event_title, is_minted, minted_at,
                            minted_to_wallet, minted_to_solana_wallet, minted_claim_id,
                            minted_tx_hash, minted_asset_address
                        FROM winner_wallets_nft_to_claim
                        ORDER BY season_id DESC, pnl_rank ASC NULLS LAST, id DESC
                        LIMIT %s
                        """,
                        (safe_limit,),
                    )
                else:
                    cursor.execute(
                        """
                        SELECT
                            id, season_id, wallet_address, source, total_pnl_window, pnl_rank,
                            window_start, window_end, snapshot_at, created_at, event_id, market_id,
                            condition_id, event_slug, event_title, is_minted, minted_at,
                            minted_to_wallet, minted_to_solana_wallet, minted_claim_id,
                            minted_tx_hash, minted_asset_address
                        FROM winner_wallets_nft_to_claim
                        WHERE season_id = %s
                        ORDER BY pnl_rank ASC NULLS LAST, id DESC
                        LIMIT %s
                        """,
                        (season_id, safe_limit),
                    )
                return [self._format_winner_row(dict(row)) for row in cursor.fetchall()]
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
                        season_id, wallet_address, source, total_pnl_window, pnl_rank,
                        window_start, window_end, snapshot_at, event_id, market_id, condition_id,
                        event_slug, event_title, is_minted, minted_at, minted_to_wallet,
                        minted_to_solana_wallet, minted_claim_id, minted_tx_hash, minted_asset_address
                    ) VALUES (
                        %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s,
                        %s, %s, %s, %s
                    )
                    RETURNING
                        id, season_id, wallet_address, source, total_pnl_window, pnl_rank,
                        window_start, window_end, snapshot_at, created_at, event_id, market_id,
                        condition_id, event_slug, event_title, is_minted, minted_at,
                        minted_to_wallet, minted_to_solana_wallet, minted_claim_id,
                        minted_tx_hash, minted_asset_address
                    """,
                    (
                        req.season_id,
                        wallet.lower(),
                        source,
                        req.total_pnl_window,
                        req.pnl_rank,
                        window_start,
                        window_end,
                        snapshot_at,
                        self._normalize_optional_text(req.event_id),
                        self._normalize_optional_text(req.market_id),
                        self._normalize_optional_text(req.condition_id),
                        self._normalize_optional_text(req.event_slug),
                        self._normalize_optional_text(req.event_title),
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
                        wallet_address = %s,
                        source = %s,
                        total_pnl_window = %s,
                        pnl_rank = %s,
                        window_start = %s,
                        window_end = %s,
                        snapshot_at = %s,
                        event_id = %s,
                        market_id = %s,
                        condition_id = %s,
                        event_slug = %s,
                        event_title = %s,
                        is_minted = %s,
                        minted_at = %s,
                        minted_to_wallet = %s,
                        minted_to_solana_wallet = %s,
                        minted_claim_id = %s,
                        minted_tx_hash = %s,
                        minted_asset_address = %s
                    WHERE id = %s
                    RETURNING
                        id, season_id, wallet_address, source, total_pnl_window, pnl_rank,
                        window_start, window_end, snapshot_at, created_at, event_id, market_id,
                        condition_id, event_slug, event_title, is_minted, minted_at,
                        minted_to_wallet, minted_to_solana_wallet, minted_claim_id,
                        minted_tx_hash, minted_asset_address
                    """,
                    (
                        req.season_id,
                        wallet.lower(),
                        source,
                        req.total_pnl_window,
                        req.pnl_rank,
                        window_start,
                        window_end,
                        snapshot_at,
                        self._normalize_optional_text(req.event_id),
                        self._normalize_optional_text(req.market_id),
                        self._normalize_optional_text(req.condition_id),
                        self._normalize_optional_text(req.event_slug),
                        self._normalize_optional_text(req.event_title),
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
    ) -> List[Dict[str, Any]]:
        safe_limit = min(max(limit, 1), 2000)
        status_filter = (status or "").strip().lower()
        if status_filter and status_filter not in {"ok", "error"}:
            raise ValueError("status must be one of: ok, error")
        event_id_filter = (event_id or "").strip()

        conn = self.manager.get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                if event_id_filter and status_filter:
                    cursor.execute(
                        """
                        SELECT
                            ec.event_id,
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
                        FROM event_cards ec
                        LEFT JOIN events e ON e.id = ec.event_id
                        LEFT JOIN tags tp ON LOWER(BTRIM(tp.label)) = LOWER(BTRIM(ec.primary_tag))
                        LEFT JOIN tags ts ON LOWER(BTRIM(ts.label)) = LOWER(BTRIM(ec.secondary_tag))
                        WHERE ec.event_id = %s
                          AND ec.status = %s
                        ORDER BY ec.generated_at DESC, ec.event_id ASC
                        LIMIT %s
                        """,
                        (event_id_filter, status_filter, safe_limit),
                    )
                elif event_id_filter:
                    cursor.execute(
                        """
                        SELECT
                            ec.event_id,
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
                        FROM event_cards ec
                        LEFT JOIN events e ON e.id = ec.event_id
                        LEFT JOIN tags tp ON LOWER(BTRIM(tp.label)) = LOWER(BTRIM(ec.primary_tag))
                        LEFT JOIN tags ts ON LOWER(BTRIM(ts.label)) = LOWER(BTRIM(ec.secondary_tag))
                        WHERE ec.event_id = %s
                        ORDER BY ec.generated_at DESC, ec.event_id ASC
                        LIMIT %s
                        """,
                        (event_id_filter, safe_limit),
                    )
                elif status_filter:
                    cursor.execute(
                        """
                        SELECT
                            ec.event_id,
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
                        FROM event_cards ec
                        LEFT JOIN events e ON e.id = ec.event_id
                        LEFT JOIN tags tp ON LOWER(BTRIM(tp.label)) = LOWER(BTRIM(ec.primary_tag))
                        LEFT JOIN tags ts ON LOWER(BTRIM(ts.label)) = LOWER(BTRIM(ec.secondary_tag))
                        WHERE ec.status = %s
                        ORDER BY ec.generated_at DESC, ec.event_id ASC
                        LIMIT %s
                        """,
                        (status_filter, safe_limit),
                    )
                else:
                    cursor.execute(
                        """
                        SELECT
                            ec.event_id,
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
                        FROM event_cards ec
                        LEFT JOIN events e ON e.id = ec.event_id
                        LEFT JOIN tags tp ON LOWER(BTRIM(tp.label)) = LOWER(BTRIM(ec.primary_tag))
                        LEFT JOIN tags ts ON LOWER(BTRIM(ts.label)) = LOWER(BTRIM(ec.secondary_tag))
                        ORDER BY ec.generated_at DESC, ec.event_id ASC
                        LIMIT %s
                        """,
                        (safe_limit,),
                    )
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


@app.post("/api/reset")
async def run_reset(req: ResetRequest) -> Dict[str, str]:
    if not req.confirm:
        raise HTTPException(status_code=400, detail="Set confirm=true to run reset")
    try:
        service.run_reset_sql()
        await ws_hub.broadcast("reset", {"status": "ok", "message": "Reset SQL executed successfully"})
        return {"status": "ok", "message": "Reset SQL executed successfully"}
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
) -> Dict[str, Any]:
    try:
        rows = service.list_event_cards(limit=limit, status=status, event_id=event_id)
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

