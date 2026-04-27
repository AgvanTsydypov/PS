"""
Mixin and models for the NFT claims mint pipeline.

SeasonWorkbenchService inherits ClaimsMintMixin to keep mint logic isolated.
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from datetime import timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import psycopg2.extras
from pydantic import BaseModel
from solders.pubkey import Pubkey

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from scripts.polystars_card_payload import (
    build_polystars_card_for_mint,
    promote_preview_to_claim,
    unpin_pinata_urls,
)
from scripts.solana_service import MintedNftResult, SolanaClient

logger = logging.getLogger(__name__)

MASTER_COLLECTION_ENV_KEY = "MASTER_COLLECTION_ADDRESS"
BLOCKCHAIN_SOLANA = "solana"
FIXED_CLAIM_FRONT_IMAGE_URL = "https://gateway.pinata.cloud/ipfs/bafkreieucptbdshpv6pegj74maofwd3frc4666vh7wzwksg5pxtkbc3td4"
FIXED_CLAIM_BACK_IMAGE_URL = "https://gateway.pinata.cloud/ipfs/bafkreierblyo7tqhbq2qlcyxtorxx76oufadsd2cyvy4ojeigstajpiyx4"


@dataclass(frozen=True)
class WinnerClaimAllocation:
    row_id: int
    winner_wallet_address: str
    assignment_type: str
    pnl_value: float
    rank: int
    snapshot: Dict[str, Any]


class MintClaimRequest(BaseModel):
    wallet: str
    recipient_address: str
    season_id: int
    phase: str = "breach"
    auto_phase: bool = True
    db_only: bool = False
    use_fixed_claim_images: bool = True


class ClaimsMintMixin:
    """
    All methods required by the NFT mint pipeline.
    Assumes self.manager, self.season_manager, self.clear_wallets_cache(),
    and self.fmt_dt() are provided by the host class (SeasonWorkbenchService).
    """

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
                cursor.execute("ALTER TABLE claims ADD COLUMN IF NOT EXISTS collection_mint_number BIGINT")
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
                # Backfill collection_mint_number for any pre-existing rows, numbering
                # chronologically per season so legacy data keeps deterministic ordering.
                cursor.execute(
                    """
                    WITH numbered AS (
                        SELECT
                            id,
                            ROW_NUMBER() OVER (
                                PARTITION BY season_id
                                ORDER BY COALESCE(timestamp, created_at) ASC, id ASC
                            ) AS rn
                        FROM claims
                        WHERE collection_mint_number IS NULL
                    )
                    UPDATE claims c
                    SET collection_mint_number = n.rn
                    FROM numbered n
                    WHERE c.id = n.id
                    """
                )
                cursor.execute(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS ux_claims_season_collection_mint
                        ON claims(season_id, collection_mint_number)
                    """
                )
                # Trigger assigns a per-season sequential collection_mint_number on INSERT.
                cursor.execute(
                    """
                    CREATE OR REPLACE FUNCTION claims_assign_season_mint_number()
                    RETURNS TRIGGER AS $$
                    BEGIN
                        IF NEW.collection_mint_number IS NOT NULL THEN
                            RETURN NEW;
                        END IF;
                        PERFORM pg_advisory_xact_lock(9283742, NEW.season_id);
                        SELECT COALESCE(MAX(collection_mint_number), 0) + 1
                        INTO NEW.collection_mint_number
                        FROM claims
                        WHERE season_id = NEW.season_id;
                        RETURN NEW;
                    END;
                    $$ LANGUAGE plpgsql
                    """
                )
                cursor.execute("DROP TRIGGER IF EXISTS tr_claims_assign_season_mint ON claims")
                cursor.execute(
                    """
                    CREATE TRIGGER tr_claims_assign_season_mint
                        BEFORE INSERT ON claims
                        FOR EACH ROW
                        EXECUTE PROCEDURE claims_assign_season_mint_number()
                    """
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
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
                lines.extend(["", "Checklist before insert:", f"- wallet: {wallet_normalized}", f"- blockchain: {BLOCKCHAIN_SOLANA}"])
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
                        w.id,
                        w.proxy_wallet AS wallet_address,
                        w.source,
                        w.window_start,
                        w.window_end,
                        w.snapshot_at,
                        w.event_id,
                        w.event_slug,
                        w.entry_cwap,
                        w.total_volume,
                        w.total_pnl,
                        w.roi_percentage,
                        w.entry_bracket,
                        w.edge,
                        w.yield,
                        w.gravity,
                        w.rank,
                        COALESCE(w.is_minted, FALSE) AS is_minted,
                        w.minted_to_wallet
                    FROM winner_wallets_nft_to_claim w
                    JOIN event_cards ec ON ec.event_id = w.event_id
                    WHERE w.season_id = %s
                      AND LOWER(w.proxy_wallet) = LOWER(%s)
                      AND ec.manual_image_url IS NOT NULL
                      AND BTRIM(ec.manual_image_url) <> ''
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
                        w.id,
                        w.proxy_wallet AS wallet_address,
                        w.source,
                        w.window_start,
                        w.window_end,
                        w.snapshot_at,
                        w.event_id,
                        w.event_slug,
                        w.entry_cwap,
                        w.total_volume,
                        w.total_pnl,
                        w.roi_percentage,
                        w.entry_bracket,
                        w.edge,
                        w.yield,
                        w.gravity,
                        w.rank
                    FROM winner_wallets_nft_to_claim w
                    JOIN event_cards ec ON ec.event_id = w.event_id
                    WHERE w.season_id = %s
                      AND COALESCE(w.is_minted, FALSE) = FALSE
                      AND ec.manual_image_url IS NOT NULL
                      AND BTRIM(ec.manual_image_url) <> ''
                    ORDER BY RANDOM()
                    LIMIT 1
                    """,
                    (season_id,),
                )
                fallback_row = cursor.fetchone()
                if not fallback_row:
                    raise ValueError("No unminted winner rows with manual_image_url left in this season.")
                event_image_url = self._resolve_event_image_url(cursor, fallback_row)
                return self._build_winner_allocation(
                    row=fallback_row,
                    assignment_type="random_fallback",
                    event_image_url=event_image_url,
                )
        finally:
            conn.close()

    def reserve_pending_claim(
        self,
        *,
        wallet: str,
        recipient_wallet: str,
        season_id: int,
        phase: str,
        mint_chain: str,
        placeholder_metadata_uri: str = "https://gateway.pinata.cloud/ipfs/QmPendingMetadataPlaceholder",
    ) -> Dict[str, int]:
        """
        Insert a PENDING claim row up-front so the BEFORE-INSERT trigger assigns a
        per-season collection_mint_number. Returns both the freshly issued claim id
        and its per-season mint number, which are later used to name the NFT and
        finalize the row once the mint transaction succeeds.
        """
        insert_sql = """
            INSERT INTO claims (
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
            VALUES (%s, %s, %s, %s, NOW(), %s, %s, %s, NOW(), NOW())
            RETURNING id, collection_mint_number
        """
        for attempt in range(2):
            conn = self.manager.get_connection()
            try:
                with conn.cursor() as cursor:
                    cursor.execute(
                        insert_sql,
                        (
                            wallet,
                            recipient_wallet,
                            season_id,
                            phase,
                            "PENDING",
                            placeholder_metadata_uri,
                            mint_chain,
                        ),
                    )
                    row = cursor.fetchone()
                    if not row:
                        raise RuntimeError("Failed to reserve pending claim row")
                    claim_id = int(row[0])
                    collection_mint_number = int(row[1]) if row[1] is not None else None
                conn.commit()
                if collection_mint_number is None:
                    raise RuntimeError("collection_mint_number was not assigned by trigger")
                return {
                    "claim_id": claim_id,
                    "collection_mint_number": collection_mint_number,
                }
            except Exception as exc:
                conn.rollback()
                text = str(exc).lower()
                if attempt == 0 and (
                    "value too long for type character varying" in text
                    or "column \"collection_mint_number\" of relation \"claims\"" in text
                    or "function claims_assign_season_mint_number" in text
                ):
                    self.ensure_claims_schema_for_mint()
                    continue
                raise
            finally:
                conn.close()
        raise RuntimeError("Failed to reserve pending claim row after schema retry")

    def release_reserved_claim(self, claim_id: int) -> bool:
        """
        Free a PENDING claim row reserved by `reserve_pending_claim` when the
        subsequent mint pipeline fails. Only deletes rows that are still PENDING
        and have no on-chain artefacts (tx_hash / asset_address), so a successful
        finalize can never be undone by a stray rollback. Returns True if a row
        was actually released.
        """
        conn = self.manager.get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    DELETE FROM claims
                    WHERE id = %s
                      AND status = 'PENDING'
                      AND tx_hash IS NULL
                      AND asset_address IS NULL
                    """,
                    (claim_id,),
                )
                deleted = cursor.rowcount == 1
            conn.commit()
            return deleted
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def finalize_completed_claim(
        self,
        *,
        claim_id: int,
        mint_result: MintedNftResult,
        mint_chain: str,
    ) -> None:
        """
        Mark a previously reserved PENDING claim row as COMPLETED and attach the
        on-chain mint artefacts. Used after `reserve_pending_claim` + successful mint.
        """
        conn = self.manager.get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE claims
                    SET
                        tx_hash = %s,
                        metadata_uri = %s,
                        asset_address = %s,
                        status = 'COMPLETED',
                        mint_chain = %s,
                        timestamp = NOW(),
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    (
                        mint_result.tx_hash,
                        mint_result.metadata_uri,
                        mint_result.asset_address,
                        mint_chain,
                        claim_id,
                    ),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError(
                        f"Failed to finalize claim {claim_id}: row not found or updated twice"
                    )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
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

        try:
            recipient_address = str(Pubkey.from_string(recipient_raw))
        except Exception:
            raise ValueError("Invalid Solana recipient address")

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
        except Exception:
            raise

        supported_phases = set(self.get_claim_phase_enum_values())
        if phase not in supported_phases:
            supported = ", ".join(sorted(supported_phases)) if supported_phases else "(none)"
            raise ValueError(f"DB enum phase_type does not support '{phase}'. Supported: {supported}")

        allocation = self.allocate_winner_claim_row(wallet=wallet, season_id=req.season_id)
        season_name = self.get_season_name(req.season_id)

        # Reserve a PENDING claim row up-front so the DB trigger assigns a per-season
        # collection_mint_number we can use for both the NFT name and the card payload.
        reservation = self.reserve_pending_claim(
            wallet=wallet,
            recipient_wallet=recipient_address,
            season_id=req.season_id,
            phase=phase,
            mint_chain=BLOCKCHAIN_SOLANA,
        )
        claim_id = reservation["claim_id"]
        collection_mint_number = reservation["collection_mint_number"]

        if req.db_only:
            self.clear_wallets_cache()
            return {
                "status": "db_only_inserted",
                "claim_id": claim_id,
                "collection_mint_number": collection_mint_number,
                "wallet": wallet,
                "recipient_address": recipient_address,
                "season_id": req.season_id,
                "phase": phase,
                "chain": BLOCKCHAIN_SOLANA,
                "allocation": allocation.__dict__,
                "warnings": warnings,
            }

        winner_context = {
            "assignment_type": allocation.assignment_type,
            "winner_wallet_address": allocation.winner_wallet_address,
            "claimer_wallet_address": wallet,
            "season_id": req.season_id,
            "snapshot": allocation.snapshot,
            "blockchain": BLOCKCHAIN_SOLANA,
        }

        card_claim_type = "origin" if allocation.assignment_type == "winner_self" else "looter"

        # Everything between reservation and finalize can fail (Pinata upload,
        # Solana RPC, transaction confirmation). On any failure we:
        #   1. Release the PENDING claim row so collection_mint_number stays gapless.
        #   2. Unpin any card images already uploaded to Pinata so they don't
        #      accumulate as orphaned pins. Metadata JSON unpin is handled inside
        #      mint_user_nft itself (it knows the URI at the point of failure).
        polystars_card: dict[str, Any] | None = None
        try:
            polystars_card = build_polystars_card_for_mint(
                self.manager,
                winner_row_id=allocation.row_id,
                claim_id=claim_id,
                collection_mint_number=collection_mint_number,
                claim_type=card_claim_type,
                fixed_front_image_url=FIXED_CLAIM_FRONT_IMAGE_URL if req.use_fixed_claim_images else "",
                fixed_back_image_url=FIXED_CLAIM_BACK_IMAGE_URL if req.use_fixed_claim_images else "",
            )

            mint_client = SolanaClient(keypair_path=Path(project_root) / "my-keypair.json")
            mint_result = mint_client.mint_user_nft(
                user_wallet_address=recipient_address,
                season_name=season_name,
                claim_id=claim_id,
                collection_mint_number=collection_mint_number,
                winner_context=winner_context,
                polystars_card=polystars_card,
            )

            self.finalize_completed_claim(
                claim_id=claim_id,
                mint_result=mint_result,
                mint_chain=BLOCKCHAIN_SOLANA,
            )
        except Exception:
            # release_reserved_claim is a no-op once finalize_completed_claim
            # has set tx_hash / asset_address, so a partial success is never
            # silently erased.
            try:
                self.release_reserved_claim(claim_id)
            except Exception:
                pass
            # Unpin card images that were already uploaded to Pinata.
            # Metadata JSON unpin is handled inside mint_user_nft.
            if polystars_card:
                try:
                    unpin_pinata_urls([
                        str(polystars_card.get("front_image_url") or ""),
                        str(polystars_card.get("back_image_url") or ""),
                    ])
                except Exception:
                    pass
            raise
        self.mark_winner_row_as_minted(
            allocation=allocation,
            claim_id=claim_id,
            claimer_wallet=wallet,
            recipient_solana_wallet=recipient_address,
            mint_result=mint_result,
        )
        # Promote the preview row into a minted ``claims`` row. This path is
        # the SAME for both the admin-workbench mint button and the public
        # ``POST /api/me/mint`` user mint — both routes end up in this
        # ``run_mint_claim`` method on ``SeasonWorkbenchService``. The helper
        # atomically (a) denormalizes card fields onto ``claims`` so the
        # public permalink ``/api/cards/{slug}`` can be served straight from
        # ``claims``, and (b) DELETEs the matching row from
        # ``preview_cards`` so the preview vanishes from the home
        # showcase ticker and from ``/api/preview/{slug}``. Run AFTER
        # ``mark_winner_row_as_minted`` so ``minted_asset_address`` is
        # available for the explorer/MagicEden links on first page load.
        # Persistence failures must not roll back a successful mint — the
        # on-chain NFT is already final and authoritative.
        try:
            promote_preview_to_claim(
                self.manager,
                claim_id=claim_id,
                winner_row_id=allocation.row_id,
                owner_wallet=wallet,
                polystars_card=polystars_card,
            )
        except Exception:
            logger.exception(
                "Failed to promote preview into claims for claim_id=%s; "
                "mint succeeded so skipping rollback",
                claim_id,
            )
        self.clear_wallets_cache()

        return {
            "status": "mint_completed",
            "claim_id": claim_id,
            "collection_mint_number": collection_mint_number,
            "wallet": wallet,
            "recipient_address": recipient_address,
            "season_id": req.season_id,
            "phase": phase,
            "chain": BLOCKCHAIN_SOLANA,
            "allocation": allocation.__dict__,
            "mint_result": mint_result.__dict__,
            "polystars_card": polystars_card,
            "warnings": warnings,
            "collection_address": self.get_master_collection_address(),
        }
