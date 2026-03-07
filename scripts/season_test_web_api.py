"""
FastAPI web API for season_test_gui functionality.

Run:
    uvicorn scripts.season_test_web_api:app --host 0.0.0.0 --port 8001 --reload
"""

from __future__ import annotations

import json
import os
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


class MintClaimRequest(BaseModel):
    wallet: str
    recipient_address: str
    season_id: int
    phase: str = "breach"
    auto_phase: bool = True
    db_only: bool = False
    force_insert: bool = False
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
            lines.extend(
                [
                    "Transition rules (Genesis):",
                    "- Claims are open in Scavenge while remaining_supply > 0.",
                    "- Transition to Transmission happens when remaining_supply reaches 0.",
                    "- No day-based windows are used.",
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
                stream = self._resolve_stream_for_season_id(eligibility, season_id)
                lines.extend(["", "Checklist before insert:", f"- wallet: {wallet_normalized}", f"- blockchain: {blockchain}"])
                lines.append(f"- is_origin_wallet: {bool(eligibility.get('is_origin_wallet'))}")
                if stream:
                    lines.append(f"- stream_phase: {stream.get('phase')} | is_claim_open={bool(stream.get('is_claim_open'))}")
                    lines.append(f"- already_claimed_in_this_season: {bool(stream.get('already_claimed'))}")
                    lines.append(f"- eligible_now: {bool(stream.get('eligible_now'))}")
                    if stream.get("ineligible_reason"):
                        lines.append(f"- ineligible_reason: {stream.get('ineligible_reason')}")
                else:
                    lines.append("- stream_phase: season is not current active genesis/standard stream")
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
                if not req.force_insert:
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
                if not req.force_insert:
                    raise ValueError(warning_reason)
        except Exception as exc:
            if not req.force_insert:
                raise
            warnings.append(f"Eligibility check warning: {exc}")

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
        return service.season_manager.check_user_eligibility(wallet)
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

