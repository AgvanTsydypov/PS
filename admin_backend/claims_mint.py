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

import psycopg2
import psycopg2.errors
import psycopg2.extras
from pydantic import BaseModel
from web3 import Web3

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from scripts.evm_service import EVM_CONTRACT_ADDRESS_ENV_KEY
from scripts.cardgen.generate_card import compute_structural_signature

logger = logging.getLogger(__name__)

BLOCKCHAIN_ETHEREUM = "ethereum"


@dataclass(frozen=True)
class ParticipantAllocation:
    """Allocation drawn directly from the per-season participants partition.

    Carries every column needed to write a full claims-row snapshot at queue time.
    """
    proxy_wallet: str
    event_id: Optional[str]
    event_slug: Optional[str]
    claim_type: str  # 'origin' | 'looter'
    snapshot: Dict[str, Any]


# Looter random pick can lose to a concurrent INSERT on the unique index over
# (season_id, LOWER(proxy_wallet)). Retry with a fresh random pick before
# bubbling up. The cap-violation path also retries since a different random
# pick may belong to an event/tag that has not exhausted its cap yet.
LOOTER_ALLOC_MAX_ATTEMPTS = 6


# Best → worst ranking used to pick the most flattering archetype an origin
# wallet has across its events in the season. Tiebreaker is RANDOM().
_ARCHETYPE_PRIORITY_CASE_SQL = """
        CASE p.archetype
            WHEN 'INSIDER'     THEN 1
            WHEN 'ANOMALY'     THEN 2
            WHEN 'ICARUS'      THEN 3
            WHEN 'EXTRACTOR'   THEN 4
            WHEN 'SIGNAL'      THEN 5
            WHEN 'VECTOR'      THEN 6
            WHEN 'GRAVITON'    THEN 7
            WHEN 'EQUILIBRIUM' THEN 8
            WHEN 'BURNER'      THEN 9
            WHEN 'BOT'         THEN 10
            WHEN 'PASSENGER'   THEN 11
            WHEN 'OPERATOR'    THEN 12
            WHEN 'SUBSTRATE'   THEN 13
            ELSE 99
        END
"""


class MintClaimRequest(BaseModel):
    wallet: str
    recipient_address: str
    season_id: int
    phase: str = "breach"
    auto_phase: bool = True
    db_only: bool = False
    # What gets written on the card:
    #   "auto"   — origin if the wallet is in the season's participants
    #              partition, otherwise a random looter row (legacy behaviour);
    #   "origin" — force an Origin card from this wallet's own best-archetype
    #              row; fails if the wallet is not an Origin in the season;
    #   "looter" — force a looter card drawn from a random unclaimed
    #              participant row, even if the wallet is an Origin.
    claim_type: str = "auto"


_PARTICIPANT_ALLOCATION_COLUMNS = """
    p.proxy_wallet,
    p.event_id,
    p.event_slug,
    p.entry_cwap,
    p.total_volume,
    p.total_pnl,
    p.roi_percentage,
    p.entry_bracket,
    p.edge,
    p.yield,
    p.gravity,
    p.archetype,
    p.archetype_description,
    p.archetype_math,
    p.rarity_bracket,
    p.rank
"""


def _participant_row_to_allocation(row: Dict[str, Any], claim_type: str) -> ParticipantAllocation:
    snapshot: Dict[str, Any] = {
        "proxy_wallet":          str(row["proxy_wallet"]),
        "event_id":              row.get("event_id"),
        "event_slug":            row.get("event_slug"),
        "entry_cwap":            row.get("entry_cwap"),
        "total_volume":          row.get("total_volume"),
        "total_pnl":             row.get("total_pnl"),
        "roi_percentage":        row.get("roi_percentage"),
        "entry_bracket":         row.get("entry_bracket"),
        "edge":                  row.get("edge"),
        "yield":                 row.get("yield"),
        "gravity":               row.get("gravity"),
        "archetype":             row.get("archetype"),
        "archetype_description": row.get("archetype_description"),
        "archetype_math":        row.get("archetype_math"),
        "rarity_bracket":        row.get("rarity_bracket"),
        "rank":                  row.get("rank"),
    }
    return ParticipantAllocation(
        proxy_wallet=str(row["proxy_wallet"]),
        event_id=row.get("event_id"),
        event_slug=row.get("event_slug"),
        claim_type=claim_type,
        snapshot=snapshot,
    )


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
                lines.extend(["", "Checklist before insert:", f"- wallet: {wallet_normalized}", f"- blockchain: {BLOCKCHAIN_ETHEREUM}"])
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
                # ``is_renumbered`` flags the rare-but-permanent state where
                # the recovery pass had to renumber a stuck PROCESSING row
                # because its original cmn collided with a sibling COMPLETED.
                # The DB row now carries the new cmn, but the on-chain NFT
                # and the IPFS-pinned card image still reference the old
                # number — the admin UI surfaces this divergence with a
                # badge so operators don't think the row is fully consistent.
                cursor.execute(
                    """
                    SELECT id, user_wallet, phase_type, status, tx_hash, asset_address,
                           timestamp, created_at, updated_at, collection_mint_number,
                           error_message,
                           (error_message ILIKE '%%[auto-renumbered by recovery%%')
                               AS is_renumbered,
                           -- ── RBF-related fields for the Health column ────
                           -- ``tx_attempts_count`` lets the UI show "RBF #2/5"
                           -- without sending the full audit array (which can
                           -- get heavy when a claim has been bumped repeatedly).
                           jsonb_array_length(COALESCE(tx_attempts, '[]'::jsonb))
                               AS tx_attempts_count,
                           -- ``is_stuck`` surfaces the operator-attention badge
                           -- written by _replace_stuck_transactions / wallet
                           -- guards. UI colors the row red when true.
                           (error_message LIKE '[stuck:%%') AS is_stuck
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

    def _allocate_for_origin(
        self,
        cursor: Any,
        wallet: str,
        season_id: int,
    ) -> Optional[ParticipantAllocation]:
        """Pick the most flattering archetype row this wallet has in the
        season's participants partition. Returns None if the wallet is not in
        the partition (i.e., not an origin for this season)."""
        cursor.execute(
            f"""
            SELECT {_PARTICIPANT_ALLOCATION_COLUMNS}
            FROM participants p
            WHERE p.season_id = %s
              AND LOWER(p.proxy_wallet) = LOWER(%s)
            ORDER BY {_ARCHETYPE_PRIORITY_CASE_SQL}, random()
            LIMIT 1
            """,
            (season_id, wallet),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return _participant_row_to_allocation(dict(row), claim_type="origin")

    def _allocate_for_looter(
        self,
        cursor: Any,
        season_id: int,
    ) -> ParticipantAllocation:
        """Pick a uniformly random unclaimed participant row from the season's
        partition. Excludes proxy_wallets that already have an active claim
        (QUEUED/PENDING/PROCESSING/COMPLETED) for this season."""
        cursor.execute(
            f"""
            SELECT {_PARTICIPANT_ALLOCATION_COLUMNS}
            FROM participants p
            WHERE p.season_id = %s
              AND NOT EXISTS (
                  SELECT 1
                  FROM claims c
                  WHERE c.season_id = %s
                    AND c.proxy_wallet IS NOT NULL
                    AND LOWER(c.proxy_wallet) = LOWER(p.proxy_wallet)
                    AND c.status IN ('QUEUED','PENDING','PROCESSING','COMPLETED')
              )
            ORDER BY random()
            LIMIT 1
            """,
            (season_id, season_id),
        )
        row = cursor.fetchone()
        if not row:
            raise ValueError(
                "No unclaimed participant rows remaining for this season "
                "(season pool exhausted)."
            )
        return _participant_row_to_allocation(dict(row), claim_type="looter")

    def _resolve_event_card_meta(
        self,
        cursor: Any,
        event_id: Optional[str],
        event_slug: Optional[str],
    ) -> Dict[str, Optional[str]]:
        """Look up event_cards.{primary_tag, reccurence} for the trigger's per-tag
        cap check and the structural signature's INST segment.
        """
        for column, value in (("event_id", event_id), ("event_slug", event_slug)):
            if not value:
                continue
            cursor.execute(
                f"SELECT primary_tag, reccurence FROM event_cards WHERE {column} = %s LIMIT 1",
                (value,),
            )
            row = cursor.fetchone()
            if not row:
                continue
            tag_raw = row.get("primary_tag") if isinstance(row, dict) else row[0]
            rec_raw = row.get("reccurence")  if isinstance(row, dict) else row[1]
            tag = str(tag_raw or "").strip() or None
            rec = str(rec_raw or "").strip() or None
            if tag or rec:
                return {"primary_tag": tag, "recurrence": rec}
        return {"primary_tag": None, "recurrence": None}

    def _resolve_season_meta(
        self,
        cursor: Any,
        season_id: int,
    ) -> Dict[str, Any]:
        """Fetch (type, season_number) for the structural signature's S[N] segment."""
        cursor.execute(
            "SELECT type AS season_type, season_number FROM seasons WHERE id = %s LIMIT 1",
            (season_id,),
        )
        row = cursor.fetchone()
        if not row:
            return {"season_type": None, "season_number": None}
        if isinstance(row, dict):
            return {
                "season_type": str(row.get("season_type") or "").strip() or None,
                "season_number": row.get("season_number"),
            }
        return {
            "season_type": str(row[0] or "").strip() or None,
            "season_number": row[1],
        }

    def _insert_queued_claim(
        self,
        cursor: Any,
        *,
        user_wallet: str,
        recipient_address: str,
        season_id: int,
        phase: str,
        allocation: ParticipantAllocation,
        primary_tag: Optional[str],
        recurrence: Optional[str],
        season_type: Optional[str],
        season_number: Any,
    ) -> Dict[str, Any]:
        """Insert a QUEUED claim row carrying the full participant snapshot.

        Raises psycopg2.errors.UniqueViolation on collisions
        (active claim already exists for this user_wallet OR proxy_wallet),
        and check_violation when the cap trigger refuses the row.

        The structural signature is computed here from the same snapshot that
        is being persisted, so the value written to ``claims.signature`` is the
        exact string that ``generate_card_back_svg`` will later render on the
        physical card. Any future change to the encoding logic will only affect
        new mints — existing rows keep the signature they were minted with.
        """
        snap = allocation.snapshot
        signature = compute_structural_signature({
            "archetype":     snap.get("archetype"),
            "entry_bracket": snap.get("entry_bracket"),
            "edge":          snap.get("edge"),
            "yield":         snap.get("yield"),
            "gravity":       snap.get("gravity"),
            "claim_type":    allocation.claim_type,
            "event_id":      allocation.event_id,
            "recurrence":    recurrence,
            "season_type":   season_type,
            "season_number": season_number,
        })
        cursor.execute(
            """
            INSERT INTO claims (
                user_wallet, recipient_address, season_id, phase_type, status,
                proxy_wallet, event_id, event_slug, primary_tag,
                snapshot_at,
                entry_cwap, total_volume, total_pnl, roi_percentage,
                entry_bracket, edge, yield, gravity,
                archetype, archetype_description, archetype_math, rarity_bracket,
                participant_rank, claim_type,
                signature,
                mint_chain,
                created_at, updated_at
            )
            VALUES (
                %s, %s, %s, %s, 'QUEUED',
                %s, %s, %s, %s,
                NOW(),
                %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s,
                %s,
                %s,
                NOW(), NOW()
            )
            RETURNING id, collection_mint_number
            """,
            (
                user_wallet, recipient_address, season_id, phase,
                allocation.proxy_wallet,
                allocation.event_id,
                allocation.event_slug,
                primary_tag,
                snap.get("entry_cwap"), snap.get("total_volume"),
                snap.get("total_pnl"),  snap.get("roi_percentage"),
                snap.get("entry_bracket"), snap.get("edge"),
                snap.get("yield"),         snap.get("gravity"),
                snap.get("archetype"),     snap.get("archetype_description"),
                snap.get("archetype_math"), snap.get("rarity_bracket"),
                snap.get("rank"), allocation.claim_type,
                signature,
                BLOCKCHAIN_ETHEREUM,
            ),
        )
        row = cursor.fetchone()
        if not row:
            raise RuntimeError("INSERT INTO claims returned no row")
        if isinstance(row, dict):
            return {
                "claim_id": int(row["id"]),
                "collection_mint_number": int(row["collection_mint_number"])
                    if row.get("collection_mint_number") is not None else None,
            }
        return {
            "claim_id": int(row[0]),
            "collection_mint_number": int(row[1]) if row[1] is not None else None,
        }

    def run_queue_mint_request(self, req: MintClaimRequest) -> Dict[str, Any]:
        """Allocate a participant slot and INSERT a QUEUED claim for it.

        Origins get their best-archetype row when available. If an Origin's
        proxy_wallet is already locked by an active claim (a looter beat
        them to their own slot), the Origin transparently falls back to
        the looter pool — *unless* the season is in the ``vault`` phase,
        which is Origins-only by design and has no looter mode.

        Looters get a uniformly random unclaimed row, with retry on
        collision. The on-chain mint is performed later by the daily cron
        worker (see process_mint_queue).
        """
        wallet = req.wallet.strip().lower()
        recipient_raw = req.recipient_address.strip()
        if not wallet:
            raise ValueError("Wallet is required")
        if not recipient_raw:
            raise ValueError("Recipient address is required")
        try:
            recipient_address = Web3.to_checksum_address(recipient_raw)
        except Exception:
            raise ValueError("Invalid EVM recipient address (expected 0x…40 hex)")

        phase = req.phase
        if req.auto_phase:
            detected_phase, phase_error = self.derive_claim_phase_type(req.season_id)
            if not detected_phase:
                raise ValueError(phase_error or "Phase detection failed")
            phase = detected_phase

        supported_phases = set(self.get_claim_phase_enum_values())
        if phase not in supported_phases:
            supported = ", ".join(sorted(supported_phases)) if supported_phases else "(none)"
            raise ValueError(f"DB enum phase_type does not support '{phase}'. Supported: {supported}")

        claim_type_pref = (req.claim_type or "auto").strip().lower()
        if claim_type_pref not in {"auto", "origin", "looter"}:
            raise ValueError(
                f"Invalid claim_type '{req.claim_type}' (expected 'auto', 'origin' or 'looter')"
            )
        if claim_type_pref == "looter" and phase == "vault":
            raise ValueError("Vault phase is Origins-only; a looter card cannot be minted in vault.")
        # Operator forced an Origin card: never fall back to the looter pool.
        require_origin = claim_type_pref == "origin"

        last_error: Optional[Exception] = None
        # When True, skip _allocate_for_origin and route through the looter
        # pool. Starts True if the operator explicitly asked for a looter card;
        # also gets flipped on if this caller's own Origin slot turns out to be
        # already taken (auto mode only). Survives across iterations so we
        # don't keep re-fetching the same blocked row on every attempt.
        force_looter_path = claim_type_pref == "looter"
        for attempt in range(LOOTER_ALLOC_MAX_ATTEMPTS):
            conn = self.manager.get_connection()
            try:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                    origin_alloc = (
                        None
                        if force_looter_path
                        else self._allocate_for_origin(cursor, wallet, req.season_id)
                    )
                    is_origin = origin_alloc is not None
                    if require_origin and not is_origin:
                        raise ValueError(
                            f"Wallet {wallet} is not an Origin in season {req.season_id} "
                            f"(no row in its participants partition); cannot mint an Origin card. "
                            f"Use claim_type='looter' to mint a looter card instead."
                        )
                    if phase == "vault" and not is_origin:
                        # Vault is Origins-only. If the caller is non-Origin
                        # to begin with, OR their Origin slot was just shown
                        # to be locked, vault has nothing else to give them.
                        raise ValueError(
                            "Wallet is non-origin but phase='vault'"
                            if not force_looter_path
                            else "This origin wallet has already been claimed in this season "
                                 "(vault phase does not allow looter fallback)."
                        )
                    allocation: ParticipantAllocation = (
                        origin_alloc
                        if origin_alloc is not None
                        else self._allocate_for_looter(cursor, req.season_id)
                    )
                    event_meta = self._resolve_event_card_meta(
                        cursor,
                        allocation.event_id,
                        allocation.event_slug,
                    )
                    season_meta = self._resolve_season_meta(cursor, req.season_id)
                    primary_tag = event_meta["primary_tag"]
                    try:
                        inserted = self._insert_queued_claim(
                            cursor,
                            user_wallet=wallet,
                            recipient_address=recipient_address,
                            season_id=req.season_id,
                            phase=phase,
                            allocation=allocation,
                            primary_tag=primary_tag,
                            recurrence=event_meta["recurrence"],
                            season_type=season_meta["season_type"],
                            season_number=season_meta["season_number"],
                        )
                    except psycopg2.errors.UniqueViolation as exc:
                        conn.rollback()
                        constraint = (
                            getattr(getattr(exc, "diag", None), "constraint_name", None)
                            or ""
                        )
                        if "user_wallet" in constraint:
                            raise ValueError(
                                "This user wallet already has an active claim in this season."
                            ) from exc
                        if is_origin:
                            # Origin's own proxy_wallet is locked by an active
                            # claim. If the operator forced an Origin card we
                            # don't silently downgrade to a looter — fail loud.
                            if require_origin:
                                raise ValueError(
                                    f"Origin wallet {wallet} already has an active claim "
                                    f"in season {req.season_id}."
                                ) from exc
                            # Outside vault we transparently retry as a looter.
                            # Inside vault we already raised above.
                            force_looter_path = True
                            last_error = exc
                            continue
                        # Looter race on proxy_wallet: another caller grabbed
                        # the same random row. Retry with a fresh random pick.
                        last_error = exc
                        continue
                    except Exception as exc:
                        conn.rollback()
                        # Cap-violation. Two possible reasons (see
                        # claims_check_caps trigger in
                        # sql/schemas/create_seasons_system.sql):
                        #   1. Total supply exhausted — the whole season is
                        #      out of slots. Retrying with another random
                        #      pick CANNOT help (every event in the season
                        #      is over the cap by definition). Fail fast
                        #      so we don't waste the remaining 5 retries
                        #      on a guaranteed loss and inflate latency
                        #      for the user under a season-end stampede.
                        #   2. Per-event cap reached — only this specific
                        #      event is full; another random pick can land
                        #      on a still-available event. Retry as before.
                        # Origins always fail on cap (their slot is
                        # structurally tied to a specific event/wallet).
                        pgcode = getattr(exc, "pgcode", "") or ""
                        if pgcode == "23514" and not is_origin:
                            err_text = str(exc).lower()
                            if "total supply" in err_text and "exhausted" in err_text:
                                raise ValueError(
                                    f"Season {req.season_id} is out of supply "
                                    f"(total_supply cap reached). No mint slots remain."
                                ) from exc
                            last_error = exc
                            continue
                        raise

                conn.commit()
                self.clear_wallets_cache()
                return {
                    "status": "queued",
                    "claim_id": inserted["claim_id"],
                    "collection_mint_number": inserted["collection_mint_number"],
                    "wallet": wallet,
                    "recipient_address": recipient_address,
                    "season_id": req.season_id,
                    "phase": phase,
                    "chain": BLOCKCHAIN_ETHEREUM,
                    "claim_type": allocation.claim_type,
                    "allocation": allocation.snapshot,
                    "attempt": attempt + 1,
                    "origin_fallback_to_looter": force_looter_path and allocation.claim_type == "looter",
                }
            finally:
                conn.close()

        raise RuntimeError(
            f"Failed to allocate a looter slot after {LOOTER_ALLOC_MAX_ATTEMPTS} attempts; "
            f"last error: {last_error}"
        )

    def get_master_collection_address(self) -> str:
        value = os.environ.get(EVM_CONTRACT_ADDRESS_ENV_KEY, "").strip()
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
            if key.strip() != EVM_CONTRACT_ADDRESS_ENV_KEY:
                continue
            resolved = raw_value.strip().strip('"').strip("'")
            if resolved:
                return resolved
        return ""

