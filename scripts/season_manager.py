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
from typing import Any, Callable, Dict, Optional
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
    phase_ends_at: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "phase": self.phase,
            "is_claim_open": self.is_claim_open,
            "requires_origin": self.requires_origin,
            "reason": self.reason,
            "season_type": self.season_type,
            "supply_remaining": self.supply_remaining,
            "supply_total": self.supply_total,
            "phase_ends_at": (
                self.phase_ends_at.isoformat() if self.phase_ends_at else None
            ),
        }


class SeasonManager:
    """Determines active phase for a season and checks Origins access."""

    BREACH_CAP_PERCENT = 0.20

    def __init__(
        self,
        use_local_db: bool = True,
        connection_factory: Optional[Callable[[], Any]] = None,
    ) -> None:
        """``connection_factory`` lets callers (e.g. the user-web backend)
        inject a pooled-connection provider so each method doesn't pay the
        cost of a fresh TLS handshake to Postgres. Default keeps the legacy
        per-call ``psycopg2.connect`` behaviour for batch scripts that don't
        need pooling.
        """
        self.use_local_db = use_local_db
        self.connection_params = self._get_db_params()
        self._connection_factory: Callable[[], Any] = (
            connection_factory
            if connection_factory is not None
            else (lambda: psycopg2.connect(**self.connection_params))
        )

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
        conn = self._connection_factory()
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
        conn = self._connection_factory()
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

        Treats QUEUED/PENDING/PROCESSING/COMPLETED as already-claimed to prevent
        duplicate mint attempts. QUEUED was added with the queue-worker model —
        before this fix, a user with a queued-but-not-yet-minted claim would see
        the dashboard mint button as enabled and hit a unique-violation on retry.
        """
        return self._get_active_claim_for_season(wallet_address, season_id) is not None

    def _get_active_claim_for_season(
        self, wallet_address: str, season_id: int
    ) -> Optional[Dict[str, Any]]:
        """Return the user's most recent non-failed claim row for this season,
        or ``None`` if there isn't one. "Active" means status is one of
        ``QUEUED / PENDING / PROCESSING / COMPLETED`` — anything that prevents
        the user from re-queueing another mint in the same season.

        Used by eligibility checks so the dashboard can render an informative
        "Mint in progress (claim #N, status=QUEUED)" pill instead of a generic
        "already claimed" rejection — the latter was misleading because it
        didn't say *which* state the claim is in.
        """
        conn = self._connection_factory()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, status, phase_type, created_at, updated_at,
                           collection_mint_number, tx_hash, asset_address,
                           card_slug
                    FROM   claims
                    WHERE  lower(user_wallet) = lower(%s)
                      AND  season_id = %s
                      AND  status IN ('QUEUED', 'PENDING', 'PROCESSING', 'COMPLETED')
                    ORDER  BY created_at DESC, id DESC
                    LIMIT  1
                    """,
                    (wallet_address, season_id),
                )
                row = cursor.fetchone()
                if not row:
                    return None
                created_at = row[3]
                updated_at = row[4]
                return {
                    "claim_id": int(row[0]),
                    "status": str(row[1] or "").upper(),
                    "phase_type": (str(row[2]).strip() or None) if row[2] else None,
                    "queued_at": created_at.isoformat() if created_at else None,
                    "updated_at": updated_at.isoformat() if updated_at else None,
                    "collection_mint_number": int(row[5]) if row[5] is not None else None,
                    "tx_hash": (str(row[6]).strip() or None) if row[6] else None,
                    "asset_address": (str(row[7]).strip() or None) if row[7] else None,
                    "card_slug": (str(row[8]).strip() or None) if row[8] else None,
                }
        finally:
            conn.close()

    def is_origin_wallet_for_season(self, user_wallet: str, season_id: int) -> bool:
        """Check whether wallet is in the participants partition for this season.

        An "origin" is any wallet that has at least one row in the season's
        ``participants`` partition. Looters (non-origins) have no rows there.
        """
        conn = self._connection_factory()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM participants
                        WHERE season_id = %s
                          AND lower(proxy_wallet) = lower(%s)
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
        Return mint status for the wallet's origin slot in this season.

        Under the queue model, "Origin allocation already minted" means
        there exists an active claim (QUEUED/PENDING/PROCESSING/COMPLETED)
        on this season for the same proxy_wallet, regardless of who the
        claimer (user_wallet) is. Returns None when no such claim exists.
        """
        conn = self._connection_factory()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                cursor.execute(
                    """
                    SELECT
                        id,
                        proxy_wallet      AS wallet_address,
                        TRUE              AS is_minted,
                        user_wallet       AS minted_to_wallet,
                        id                AS minted_claim_id,
                        tx_hash           AS minted_tx_hash,
                        asset_address     AS minted_asset_address,
                        COALESCE(timestamp, created_at) AS minted_at
                    FROM claims
                    WHERE season_id = %s
                      AND proxy_wallet IS NOT NULL
                      AND lower(proxy_wallet) = lower(%s)
                      AND status IN ('QUEUED','PENDING','PROCESSING','COMPLETED')
                    ORDER BY created_at DESC
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
        Reads from the participants partition for that season.
        """
        conn = self._connection_factory()
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
                        FROM participants p
                        JOIN active_standard a ON a.id = p.season_id
                        WHERE lower(p.proxy_wallet) = lower(%s)
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
                phase_ends_at=None,
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
                phase_ends_at=start_date,
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
                    phase_ends_at=breach_end,
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
                    phase_ends_at=vault_end,
                ).to_dict()

            return PhaseResult(
                phase="scavenge",
                is_claim_open=True,
                requires_origin=False,
                reason="Genesis scavenge active: day 7+ until supply exhausted",
                season_type=season_type,
                supply_remaining=remaining_supply,
                supply_total=total_supply,
                phase_ends_at=None,
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
                phase_ends_at=transmission_end,
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
                phase_ends_at=None,
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
                phase_ends_at=breach_end,
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
                phase_ends_at=vault_end,
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
            phase_ends_at=scavenge_end,
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
            return self.check_user_eligibility_for_season(
                wallet_address=normalized_wallet,
                season_id=int(stream_season["id"]),
            )

        genesis_status = build_stream_status(genesis_season)
        standard_status = build_stream_status(standard_season)

        return {
            "wallet_address": normalized_wallet,
            "is_origin_wallet": is_origin,
            "genesis": genesis_status,
            "standard": standard_status,
        }

    def check_user_eligibility_for_season(
        self, wallet_address: str, season_id: int
    ) -> Dict[str, Any]:
        """Check eligibility for a specific season id."""
        normalized_wallet = wallet_address.lower()
        phase_info = self.get_current_phase(season_id)
        active_claim = self._get_active_claim_for_season(normalized_wallet, season_id)
        already_claimed = active_claim is not None
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
            # Invariant: ``already_claimed`` is True iff ``active_claim`` is a
            # dict produced by ``_get_active_claim_for_season``, which always
            # stamps ``claim_id`` from ``claims.id`` (BIGSERIAL PRIMARY KEY,
            # NOT NULL by schema). Dereference directly so a future refactor
            # that drops the key fails loud here instead of silently rendering
            # a degraded "Mint queued" pill without the claim number.
            assert active_claim is not None
            eligible_now = False
            # Status-aware message so the dashboard can show what the user
            # is actually waiting for (queue → worker pickup → on-chain mint
            # → completed) instead of a generic "already claimed".
            status = active_claim.get("status") or ""
            claim_id = active_claim["claim_id"]
            mint_number = active_claim.get("collection_mint_number")
            if status == "COMPLETED":
                if mint_number is not None:
                    ineligible_reason = f"Already minted in current season (mint #{mint_number})"
                else:
                    ineligible_reason = "Already minted in current season"
            elif status == "PROCESSING":
                ineligible_reason = f"Mint in progress (claim #{claim_id}) — please wait for the next worker tick"
            elif status in ("QUEUED", "PENDING"):
                ineligible_reason = f"Mint queued (claim #{claim_id})"
            else:
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
            # The full active-claim row, exposed so the dashboard can render
            # an informative pending-mint pill ("Mint in progress — claim #N,
            # status=QUEUED") instead of a generic disabled button. ``None``
            # when the wallet has no QUEUED/PROCESSING/COMPLETED claim for
            # this season; in that case ``ineligible_reason`` (if any) comes
            # from phase rules, not from a duplicate-claim block.
            "pending_claim": active_claim,
        }

