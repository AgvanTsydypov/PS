"""
Season manager for NFT minting phases.

State machine:
- Breach (days 1-3): open for all, capped at 20% of total supply.
- Vault (days 4-6): open only for Origins from season start snapshot.
- Scavenge:
  - standard: days 7-9 (hard stop at day 9 end)
  - genesis: day 7+ until remaining_supply reaches 0
- Transmission (standard only, day 10): claims closed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
import os

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class PhaseResult:
    """Normalized response for current season phase."""

    phase: str
    is_claim_open: bool
    requires_origin: bool
    reason: str
    season_type: str
    supply_remaining: int
    supply_total: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "phase": self.phase,
            "is_claim_open": self.is_claim_open,
            "requires_origin": self.requires_origin,
            "reason": self.reason,
            "season_type": self.season_type,
            "supply_remaining": self.supply_remaining,
            "supply_total": self.supply_total,
        }


class SeasonManager:
    """Determines active phase for a season and checks Origins access."""

    BREACH_CAP_PERCENT = 0.20

    def __init__(self, use_local_db: bool = True) -> None:
        self.use_local_db = use_local_db
        self.connection_params = self._get_db_params()

    def _get_db_params(self) -> Dict[str, Any]:
        """Load DB connection params in the same style as existing scripts."""
        if self.use_local_db:
            ssl_mode = os.getenv("DB_SSLMODE", "require")
            return {
                "host": os.getenv("LOCAL_DB_HOST", os.getenv("DB_HOST")),
                "port": int(os.getenv("LOCAL_DB_PORT", os.getenv("DB_PORT", 5432))),
                "database": os.getenv("LOCAL_DB_NAME", os.getenv("DB_NAME")),
                "user": os.getenv("LOCAL_DB_USER", os.getenv("DB_USER")),
                "password": os.getenv("LOCAL_DB_PASSWORD", os.getenv("DB_PASSWORD")),
                "sslmode": ssl_mode,
            }
        raise NotImplementedError("Supabase connection not implemented")

    def _fetch_season(self, season_id: int) -> Dict[str, Any]:
        """Load a single season row."""
        conn = psycopg2.connect(**self.connection_params)
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                cursor.execute(
                    """
                    SELECT id, type, start_date, end_date, total_supply, remaining_supply, is_active
                    FROM seasons
                    WHERE id = %s
                    """,
                    (season_id,),
                )
                season = cursor.fetchone()
                if not season:
                    raise ValueError(f"Season not found: {season_id}")
                return dict(season)
        finally:
            conn.close()

    def _get_current_season_by_type(self, season_type: str) -> Optional[Dict[str, Any]]:
        """Get current active season by type."""
        conn = psycopg2.connect(**self.connection_params)
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                cursor.execute(
                    """
                    SELECT id, type, start_date, end_date, total_supply, remaining_supply, is_active
                    FROM seasons
                    WHERE type = %s
                      AND is_active = TRUE
                    ORDER BY start_date DESC, id DESC
                    LIMIT 1
                    """,
                    (season_type,),
                )
                row = cursor.fetchone()
                return dict(row) if row else None
        finally:
            conn.close()

    def _has_claimed_in_season(self, wallet_address: str, season_id: int) -> bool:
        """
        Check if user already has a claim in this season.

        We treat pending/processing/completed as already claimed or in progress
        to prevent duplicate mint attempts.
        """
        conn = psycopg2.connect(**self.connection_params)
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM claims
                        WHERE lower(user_wallet) = lower(%s)
                          AND season_id = %s
                          AND status IN ('PENDING', 'PROCESSING', 'COMPLETED')
                    )
                    """,
                    (wallet_address, season_id),
                )
                return bool(cursor.fetchone()[0])
        finally:
            conn.close()

    def is_origin_wallet_for_season(self, user_wallet: str, season_id: int) -> bool:
        """Check whether wallet is in Origins snapshot for a specific season."""
        conn = psycopg2.connect(**self.connection_params)
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM winner_wallets_nft_to_claim
                        WHERE season_id = %s
                          AND lower(wallet_address) = lower(%s)
                    )
                    """,
                    (season_id, user_wallet),
                )
                return bool(cursor.fetchone()[0])
        finally:
            conn.close()

    def _get_origin_snapshot_mint_status(
        self, user_wallet: str, season_id: int
    ) -> Optional[Dict[str, Any]]:
        """
        Return mint status for user's origin snapshot row in this season.

        Useful to detect cases where an Origin allocation was already consumed
        in open/public phases by another claimant.
        """
        conn = psycopg2.connect(**self.connection_params)
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                cursor.execute(
                    """
                    SELECT
                        id,
                        wallet_address,
                        COALESCE(is_minted, FALSE) AS is_minted,
                        minted_to_wallet,
                        minted_to_solana_wallet,
                        minted_claim_id,
                        minted_tx_hash,
                        minted_asset_address,
                        minted_at
                    FROM winner_wallets_nft_to_claim
                    WHERE season_id = %s
                      AND lower(wallet_address) = lower(%s)
                    LIMIT 1
                    """,
                    (season_id, user_wallet),
                )
                row = cursor.fetchone()
                return dict(row) if row else None
        finally:
            conn.close()

    def is_origin_wallet(self, user_wallet: str) -> bool:
        """
        Compatibility helper:
        checks Origins membership for the currently active standard season.
        """
        conn = psycopg2.connect(**self.connection_params)
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    WITH active_standard AS (
                        SELECT id
                        FROM seasons
                        WHERE type = 'standard'
                          AND is_active = TRUE
                        ORDER BY start_date DESC, id DESC
                        LIMIT 1
                    )
                    SELECT EXISTS (
                        SELECT 1
                        FROM winner_wallets_nft_to_claim sow
                        JOIN active_standard a ON a.id = sow.season_id
                        WHERE lower(sow.wallet_address) = lower(%s)
                    )
                    """,
                    (user_wallet,),
                )
                return bool(cursor.fetchone()[0])
        finally:
            conn.close()

    def get_current_phase(self, season_id: int) -> Dict[str, Any]:
        """
        Compute current phase based on season timing and remaining supply.

        Returns dict with:
        - phase: breach | vault | scavenge | transmission
        - is_claim_open: whether claim flow should be open
        - requires_origin: true only for vault phase
        - reason: short reason for current decision
        """
        season = self._fetch_season(season_id)

        season_type = season["type"]
        total_supply = int(season["total_supply"])
        remaining_supply = int(season["remaining_supply"])
        used_supply = total_supply - remaining_supply

        # Hard stop for all season types if supply is exhausted.
        if remaining_supply <= 0:
            return PhaseResult(
                phase="transmission",
                is_claim_open=False,
                requires_origin=False,
                reason="Season supply exhausted",
                season_type=season_type,
                supply_remaining=remaining_supply,
                supply_total=total_supply,
            ).to_dict()

        now = datetime.now(timezone.utc)
        start_date = season["start_date"]

        # Safety: if start_date comes without timezone, treat it as UTC.
        if start_date.tzinfo is None:
            start_date = start_date.replace(tzinfo=timezone.utc)

        breach_end = start_date + timedelta(days=3)
        vault_end = start_date + timedelta(days=6)
        scavenge_end = start_date + timedelta(days=9)
        transmission_end = start_date + timedelta(days=10)

        breach_cap = int(total_supply * self.BREACH_CAP_PERCENT)
        breach_cap_reached = used_supply >= breach_cap

        # Before season starts, claims are not open yet.
        if now < start_date:
            return PhaseResult(
                phase="transmission",
                is_claim_open=False,
                requires_origin=False,
                reason="Season has not started yet",
                season_type=season_type,
                supply_remaining=remaining_supply,
                supply_total=total_supply,
            ).to_dict()

        # Genesis uses the same phase windows as standard for days 1-6,
        # then stays in scavenge until supply is exhausted.
        if season_type == "genesis":
            if now < breach_end and not breach_cap_reached:
                return PhaseResult(
                    phase="breach",
                    is_claim_open=True,
                    requires_origin=False,
                    reason="Genesis breach active: within days 1-3 and cap not reached",
                    season_type=season_type,
                    supply_remaining=remaining_supply,
                    supply_total=total_supply,
                ).to_dict()

            if now < vault_end:
                if breach_cap_reached and now < breach_end:
                    reason = "Genesis breach cap reached early, moved to Vault"
                else:
                    reason = "Genesis vault active: days 4-6"
                return PhaseResult(
                    phase="vault",
                    is_claim_open=True,
                    requires_origin=True,
                    reason=reason,
                    season_type=season_type,
                    supply_remaining=remaining_supply,
                    supply_total=total_supply,
                ).to_dict()

            return PhaseResult(
                phase="scavenge",
                is_claim_open=True,
                requires_origin=False,
                reason="Genesis scavenge active: day 7+ until supply exhausted",
                season_type=season_type,
                supply_remaining=remaining_supply,
                supply_total=total_supply,
            ).to_dict()

        # Transmission: day 10 (last 24h of 10-day cycle).
        if now >= scavenge_end and now < transmission_end:
            return PhaseResult(
                phase="transmission",
                is_claim_open=False,
                requires_origin=False,
                reason="Transmission window: claims closed",
                season_type=season_type,
                supply_remaining=remaining_supply,
                supply_total=total_supply,
            ).to_dict()

        # After the cycle window ended, keep claims closed.
        if now >= transmission_end:
            return PhaseResult(
                phase="transmission",
                is_claim_open=False,
                requires_origin=False,
                reason="Season cycle ended (hard stop reached)",
                season_type=season_type,
                supply_remaining=remaining_supply,
                supply_total=total_supply,
            ).to_dict()

        # Breach: active while both conditions hold.
        if now < breach_end and not breach_cap_reached:
            return PhaseResult(
                phase="breach",
                is_claim_open=True,
                requires_origin=False,
                reason="Breach active: within days 1-3 and cap not reached",
                season_type=season_type,
                supply_remaining=remaining_supply,
                supply_total=total_supply,
            ).to_dict()

        # Vault: active after breach ended and before day 6.
        if now < vault_end:
            if breach_cap_reached and now < breach_end:
                reason = "Breach cap reached early, moved to Vault"
            else:
                reason = "Vault active: days 4-6"
            return PhaseResult(
                phase="vault",
                is_claim_open=True,
                requires_origin=True,
                reason=reason,
                season_type=season_type,
                supply_remaining=remaining_supply,
                supply_total=total_supply,
            ).to_dict()

        # Scavenge: day 7 through end of day 9.
        return PhaseResult(
            phase="scavenge",
            is_claim_open=True,
            requires_origin=False,
            reason="Scavenge active: day 7+ until hard stop at day 9 end",
            season_type=season_type,
            supply_remaining=remaining_supply,
            supply_total=total_supply,
        ).to_dict()

    def check_user_eligibility(self, wallet_address: str) -> Dict[str, Any]:
        """
        Check Double Mint eligibility for current Genesis and Standard streams.

        Returns eligibility status for both streams simultaneously:
        - already claimed in current Genesis
        - already claimed in current Standard
        - phase access checks (including Origins-only Vault)
        """
        normalized_wallet = wallet_address.lower()
        is_origin = self.is_origin_wallet(normalized_wallet)

        genesis_season = self._get_current_season_by_type("genesis")
        standard_season = self._get_current_season_by_type("standard")

        def build_stream_status(stream_season: Optional[Dict[str, Any]]) -> Dict[str, Any]:
            if not stream_season:
                return {
                    "season_id": None,
                    "season_type": None,
                    "phase": None,
                    "phase_reason": "No active season for this stream",
                    "already_claimed": False,
                    "eligible_now": False,
                    "ineligible_reason": "No active season",
                }

            season_id = int(stream_season["id"])
            phase_info = self.get_current_phase(season_id)
            already_claimed = self._has_claimed_in_season(normalized_wallet, season_id)
            is_origin_for_stream = self.is_origin_wallet_for_season(normalized_wallet, season_id)
            origin_snapshot_status = (
                self._get_origin_snapshot_mint_status(normalized_wallet, season_id)
                if is_origin_for_stream
                else None
            )
            origin_snapshot_is_minted = bool(
                origin_snapshot_status and origin_snapshot_status.get("is_minted")
            )

            if already_claimed:
                eligible_now = False
                ineligible_reason = "User already claimed (or has active claim) in current season"
            elif not phase_info["is_claim_open"]:
                eligible_now = False
                ineligible_reason = f"Claims closed in current phase: {phase_info['phase']}"
            elif phase_info["requires_origin"] and not is_origin_for_stream:
                eligible_now = False
                ineligible_reason = "Current phase requires Origin wallet (Vault)"
            elif is_origin_for_stream and origin_snapshot_is_minted:
                minted_to = origin_snapshot_status.get("minted_to_wallet")
                if minted_to and minted_to.lower() != normalized_wallet:
                    ineligible_reason = (
                        "Origin allocation already minted "
                        f"by another wallet: {minted_to}"
                    )
                else:
                    ineligible_reason = "Origin allocation already minted"
                eligible_now = False
            else:
                eligible_now = True
                ineligible_reason = None

            return {
                "season_id": season_id,
                "season_type": phase_info["season_type"],
                "phase": phase_info["phase"],
                "phase_reason": phase_info["reason"],
                "already_claimed": already_claimed,
                "eligible_now": eligible_now,
                "ineligible_reason": ineligible_reason,
                "requires_origin": phase_info["requires_origin"],
                "is_claim_open": phase_info["is_claim_open"],
                "is_origin_wallet": is_origin_for_stream,
                "origin_snapshot_is_minted": origin_snapshot_is_minted,
                "origin_snapshot_minted_to_wallet": (
                    origin_snapshot_status.get("minted_to_wallet")
                    if origin_snapshot_status
                    else None
                ),
            }

        genesis_status = build_stream_status(genesis_season)
        standard_status = build_stream_status(standard_season)

        can_claim_genesis = genesis_status["eligible_now"]
        can_claim_standard = standard_status["eligible_now"]

        return {
            "wallet_address": normalized_wallet,
            "is_origin_wallet": is_origin,
            "genesis": genesis_status,
            "standard": standard_status,
            "double_mint": {
                "can_claim_genesis": can_claim_genesis,
                "can_claim_standard": can_claim_standard,
                "can_claim_both_now": can_claim_genesis and can_claim_standard,
            },
        }

