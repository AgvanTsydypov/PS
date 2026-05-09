"""
Admin-only batch simulation: populate ``preview_cards`` from ``participants``.

Replaces the legacy ``winner_wallets_nft_to_claim`` simulator. Reads candidates
from the participants partitions of all seasons, generates the card SVG/PNG
via the shared cardgen pipeline, uploads the PNGs to R2, and INSERTs into
``preview_cards``.

Idempotent on ``(season_id, event_slug, LOWER(owner_proxy_wallet))`` — a
re-run skips combinations already present in ``preview_cards``. The unique
index ``ux_preview_cards_logical_slot`` enforces this at the DB layer too,
so concurrent runs collapse onto the same slot at INSERT time.

Slug continuity with real mints is wired in
``polystars_card_payload.build_polystars_card_from_claim``: when a real
claim later lands on a slot that already has a preview, the mint reuses the
preview's slug and re-renders, so ``/cards/{slug}`` keeps working seamlessly.
"""

from __future__ import annotations

import json
import logging
import random
import secrets
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import psycopg2
import psycopg2.extras

from scripts.cardgen.assets import (
    delete_r2_object_by_key,
    render_card_pngs,
    upload_card_assets_to_r2,
)
from scripts.data_loading_manager import DataLoadingManager
from scripts.polystars_card_payload import (
    _build_card_payload_from_source_row,
    _build_render_payload,
    _generated_card_slug,
)

logger = logging.getLogger(__name__)


# Source row pulled from the participants partition + event_cards + tags +
# seasons. Same column shape that ``_build_card_payload_from_source_row``
# consumes; the only difference vs the claims-rooted SELECT is the FROM
# table, so payload-building logic stays in one place.
_PARTICIPANT_CARD_SOURCE_SELECT = """
SELECT
    p.season_id,
    s.type          AS season_type,
    s.season_number,
    p.proxy_wallet,
    p.event_id,
    p.event_slug,
    e.title         AS event_title,
    p.entry_cwap,
    p.total_volume,
    p.total_pnl,
    p.entry_bracket,
    p.archetype,
    p.archetype_description,
    p.archetype_math,
    p.rarity_bracket,
    p.edge,
    p.yield,
    p.gravity,
    p.rank,
    ec.reccurence,
    ec.manual_image_url,
    ec.card_title,
    ec.card_lore,
    ec.primary_tag,
    ec.secondary_tag,
    tp.hex_color    AS primary_tag_hex_color,
    s.start_date    AS season_start_date,
    s.end_date      AS season_end_date,
    s.total_supply  AS season_size
FROM participants p
JOIN event_cards ec ON ec.event_id = p.event_id
LEFT JOIN events  e ON e.id        = p.event_id
LEFT JOIN seasons s ON s.id        = p.season_id
LEFT JOIN tags    tp ON LOWER(BTRIM(tp.label)) = LOWER(BTRIM(ec.primary_tag))
"""

_ELIGIBLE_PREDICATES = """
WHERE p.proxy_wallet IS NOT NULL
  AND ec.manual_image_url IS NOT NULL
  AND BTRIM(ec.manual_image_url) <> ''
  AND NOT EXISTS (
      SELECT 1 FROM preview_cards pc
      WHERE pc.season_id  = p.season_id
        AND pc.event_slug = p.event_slug
        AND LOWER(pc.owner_proxy_wallet) = LOWER(p.proxy_wallet)
  )
"""


def _all_season_ids(cursor: Any) -> List[int]:
    """Every season that has a row in ``seasons``. We deliberately do not
    filter by ``is_active`` — operators can pre-warm the showcase before a
    season opens, and pre-start Genesis sits with ``is_active=true`` anyway.
    """
    cursor.execute("SELECT id FROM seasons ORDER BY id ASC")
    return [int(r[0] if not isinstance(r, dict) else r["id"]) for r in cursor.fetchall()]


def _normalize_event_ids(event_ids: Optional[Sequence[str]]) -> Optional[List[str]]:
    """Sanitize the optional event-id allowlist. Returns ``None`` for "no
    filter" (empty list / all-blank entries treated identically)."""
    if not event_ids:
        return None
    cleaned = [str(e).strip() for e in event_ids if str(e or "").strip()]
    return cleaned or None


def _count_eligible_per_archetype(
    cursor: Any,
    season_ids: List[int],
    event_ids: Optional[Sequence[str]] = None,
) -> Tuple[int, Dict[str, int]]:
    """Return ``(total, per_archetype_counts)`` over the eligible pool.
    Single GROUP BY query — Postgres prunes participants partitions by
    ``season_id = ANY(...)`` and the NOT EXISTS anti-join is small.

    When ``event_ids`` is provided, the count is restricted to those events
    only — used by the admin Scenarios tab to scope a preview-generation
    batch to a hand-picked subset.
    """
    events = _normalize_event_ids(event_ids)
    event_clause = " AND p.event_id = ANY(%s)" if events else ""
    params: List[Any] = [season_ids]
    if events:
        params.append(events)
    cursor.execute(
        f"""
        SELECT COALESCE(p.archetype, '') AS archetype,
               COUNT(*) AS cnt
        FROM participants p
        JOIN event_cards ec ON ec.event_id = p.event_id
        {_ELIGIBLE_PREDICATES}
          AND p.season_id = ANY(%s){event_clause}
        GROUP BY p.archetype
        """,
        tuple(params),
    )
    counts: Dict[str, int] = {}
    total = 0
    for row in cursor.fetchall():
        archetype = (row["archetype"] if isinstance(row, dict) else row[0]) or ""
        cnt = int(row["cnt"] if isinstance(row, dict) else row[1])
        counts[archetype] = cnt
        total += cnt
    return total, counts


def _count_eligible_events_per_archetype(
    cursor: Any,
    season_ids: List[int],
    event_ids: Optional[Sequence[str]] = None,
) -> Dict[str, Dict[str, int]]:
    """Per-(archetype, event_id) eligible-row counts. Same predicates as
    ``_count_eligible_per_archetype`` but grouped on ``event_id`` as well —
    used by maximum-diversity mode to spread picks across distinct events,
    not just archetypes.
    """
    events = _normalize_event_ids(event_ids)
    event_clause = " AND p.event_id = ANY(%s)" if events else ""
    params: List[Any] = [season_ids]
    if events:
        params.append(events)
    cursor.execute(
        f"""
        SELECT COALESCE(p.archetype, '') AS archetype,
               p.event_id                AS event_id,
               COUNT(*)                  AS cnt
        FROM participants p
        JOIN event_cards ec ON ec.event_id = p.event_id
        {_ELIGIBLE_PREDICATES}
          AND p.season_id = ANY(%s){event_clause}
        GROUP BY p.archetype, p.event_id
        """,
        tuple(params),
    )
    out: Dict[str, Dict[str, int]] = {}
    for row in cursor.fetchall():
        archetype = (row["archetype"] if isinstance(row, dict) else row[0]) or ""
        event_id = row["event_id"] if isinstance(row, dict) else row[1]
        cnt = int(row["cnt"] if isinstance(row, dict) else row[2])
        if event_id is None:
            continue
        out.setdefault(archetype, {})[event_id] = cnt
    return out


def _plan_balanced_archetype_event_pairs(
    n_target: int,
    events_per_archetype: Dict[str, Dict[str, int]],
) -> Dict[Tuple[str, str], int]:
    """Joint balancing of ``n_target`` slots across both archetypes AND
    distinct ``event_id``s. Greedy per-slot: pick the
    ``(archetype, event_id)`` pair that minimises
    ``used_arch[arch] + used_event[event]`` so far; ties broken randomly.

    Why a joint score: the previous planner allocated per-archetype quotas
    first and only spread events *within* each archetype. That gave good
    archetype balance but skewed the event distribution when archetypes
    drew from very different event pools. Summing both counters drives
    every iteration toward the underrepresented dimension regardless of
    which side is currently behind, so the resulting plan is near-uniform
    on archetypes AND on events whenever the eligible pool allows it.

    Caps per ``(archetype, event_id)`` come from the eligible-row counts,
    so we never plan more than the DB has. If the pool is exhausted before
    ``n_target`` is hit, the loop returns whatever it could place and the
    caller surfaces the shortfall via ``stopped_reason='completed_short'``.
    """
    arch_event_caps: Dict[Tuple[str, str], int] = {
        (archetype or "", event_id): cap
        for archetype, events in events_per_archetype.items()
        for event_id, cap in events.items()
        if cap > 0 and event_id
    }
    if not arch_event_caps or n_target <= 0:
        return {}

    plan: Dict[Tuple[str, str], int] = {}
    used_arch: Dict[str, int] = {}
    used_event: Dict[str, int] = {}

    for _ in range(n_target):
        available = [
            pair for pair, cap in arch_event_caps.items()
            if plan.get(pair, 0) < cap
        ]
        if not available:
            break
        min_score = min(
            used_arch.get(a, 0) + used_event.get(e, 0) for (a, e) in available
        )
        best = [
            pair for pair in available
            if used_arch.get(pair[0], 0) + used_event.get(pair[1], 0) == min_score
        ]
        chosen = random.choice(best)
        plan[chosen] = plan.get(chosen, 0) + 1
        used_arch[chosen[0]] = used_arch.get(chosen[0], 0) + 1
        used_event[chosen[1]] = used_event.get(chosen[1], 0) + 1
    return plan


def _fetch_candidates(
    cursor: Any,
    season_ids: List[int],
    archetype: Optional[str],
    limit: int,
    event_ids: Optional[Sequence[str]] = None,
) -> List[Dict[str, Any]]:
    """Random sample of eligible participant rows. Dedup is enforced by the
    ``NOT EXISTS preview_cards`` predicate, but the unique index also catches
    races at INSERT time — so a row sampled here may still lose to a
    concurrent run; the per-row INSERT handles that gracefully.
    """
    events = _normalize_event_ids(event_ids)
    event_clause = " AND p.event_id = ANY(%s)" if events else ""
    if archetype is None:
        params: List[Any] = [season_ids]
        if events:
            params.append(events)
        params.append(limit)
        cursor.execute(
            f"""
            {_PARTICIPANT_CARD_SOURCE_SELECT}
            {_ELIGIBLE_PREDICATES}
              AND p.season_id = ANY(%s){event_clause}
            ORDER BY RANDOM()
            LIMIT %s
            """,
            tuple(params),
        )
    else:
        params = [season_ids, archetype]
        if events:
            params.append(events)
        params.append(limit)
        cursor.execute(
            f"""
            {_PARTICIPANT_CARD_SOURCE_SELECT}
            {_ELIGIBLE_PREDICATES}
              AND p.season_id = ANY(%s)
              AND p.archetype = %s{event_clause}
            ORDER BY RANDOM()
            LIMIT %s
            """,
            tuple(params),
        )
    return [dict(r) for r in cursor.fetchall()]


def _fetch_candidates_for_event(
    cursor: Any,
    season_ids: List[int],
    archetype: str,
    event_id: str,
    limit: int,
) -> List[Dict[str, Any]]:
    """Random sample of eligible participants for one ``(archetype, event_id)``
    pair. Used by maximum-diversity mode where the planner has already pinned
    which event each slot draws from."""
    cursor.execute(
        f"""
        {_PARTICIPANT_CARD_SOURCE_SELECT}
        {_ELIGIBLE_PREDICATES}
          AND p.season_id = ANY(%s)
          AND p.archetype = %s
          AND p.event_id  = %s
        ORDER BY RANDOM()
        LIMIT %s
        """,
        (season_ids, archetype, event_id, limit),
    )
    return [dict(r) for r in cursor.fetchall()]


def _synthetic_eoa() -> str:
    """Random hex EOA — placeholder until a real claimer wallet replaces it
    on mint. Format-wise indistinguishable from a real wallet so the
    ``preview_card_owner_wallet_format_check`` CHECK passes; semantically a
    burner the simulator doesn't track."""
    return "0x" + secrets.token_hex(20)


def _insert_preview_row(
    cursor: Any,
    *,
    slug_hint: str,
    source_row: Dict[str, Any],
    owner_wallet: str,
    owner_proxy_wallet: str,
) -> Optional[Dict[str, Any]]:
    """INSERT a placeholder preview row. Trigger
    ``preview_cards_assign_season_mint_number`` fills
    ``collection_mint_number``. Returns the freshly inserted row, or ``None``
    on a unique-key collision (concurrent simulator picked the same slot).
    """
    try:
        cursor.execute(
            """
            INSERT INTO preview_cards (
                slug, owner_wallet, owner_proxy_wallet, season_id,
                event_id, event_slug, card_title,
                primary_tag, secondary_tag,
                front_image_path, back_image_path, card_payload_json
            ) VALUES (
                %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s,
                '', '', '{}'::jsonb
            )
            RETURNING id, slug, collection_mint_number
            """,
            (
                slug_hint,
                owner_wallet,
                owner_proxy_wallet,
                int(source_row.get("season_id") or 0),
                source_row.get("event_id"),
                source_row.get("event_slug"),
                str(source_row.get("card_title") or "").strip(),
                str(source_row.get("primary_tag") or "").strip(),
                str(source_row.get("secondary_tag") or "").strip(),
            ),
        )
    except psycopg2.errors.UniqueViolation:
        return None
    row = cursor.fetchone()
    if not row:
        return None
    return dict(row) if not isinstance(row, dict) else row


def _generate_slug_hint(season_type: Optional[str], season_number: Any) -> str:
    """Delegate to the canonical mint-time generator so preview slugs share
    one shape with mint-time slugs. Slug must be unique per INSERT —
    collision recovery is on the caller (regenerate + retry once).
    """
    return _generated_card_slug(season_type, season_number)


def _build_payload(
    source_row: Dict[str, Any],
    *,
    preview_id: int,
    slug: str,
    is_origin: bool,
) -> Dict[str, Any]:
    payload = _build_card_payload_from_source_row(
        source_row,
        claim_id=preview_id,
        claim_type="origin" if is_origin else "looter",
        collection_mint_number=None,
        preview_slug=slug,
    )
    # Previews are not part of the minted collection. The card-back slot
    # carries a literal "—" instead of an int so the page never shows a
    # fake mint number for an unminted card.
    payload["collection_mint_number"] = "-"
    return payload


def run_admin_simulated_card_generations(
    manager: DataLoadingManager,
    *,
    request_id: str,
    max_count: int,
    origin_match_fraction: float,
    maximum_diversity: bool,
    event_ids: Optional[Sequence[str]] = None,
    progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    """Generate up to ``max_count`` preview cards. Returns the response
    contract the admin frontend (``admin_frontend/app/page.tsx``) consumes
    chunk-by-chunk — every key in the legacy shape is preserved so the
    frontend doesn't need updating.

    ``event_ids`` (optional) restricts the eligible pool to participants
    whose ``event_id`` is in the list — used by the admin Scenarios tab to
    target preview generation at a hand-picked subset of events.
    """
    events_filter = _normalize_event_ids(event_ids)
    out: Dict[str, Any] = {
        "request_id": request_id,
        "requested": int(max_count),
        "planned": 0,
        "generated": 0,
        "origin_claim_cards": 0,
        "origin_slots_skipped_no_winner_proxy": 0,
        "remaining_supply_before": 0,
        "remaining_supply_after": 0,
        "showcase_eligible_total": 0,
        "showcase_candidate_pool_size": None,
        "showcase_pool_cap": None,
        "errors": [],
        "stopped_reason": None,
        "maximum_diversity": bool(maximum_diversity),
        "event_ids_filter": list(events_filter) if events_filter else [],
    }
    errors: List[str] = out["errors"]  # alias

    def emit(stage: str, **extra: Any) -> None:
        if progress_callback is None:
            return
        try:
            progress_callback({"stage": stage, **out, **extra})
        except Exception:
            logger.warning("simulate progress callback failed", exc_info=True)

    n = max(0, int(max_count))
    if n == 0:
        out["stopped_reason"] = "zero_planned"
        emit("stopped")
        return out

    plan_conn = manager.get_connection()
    try:
        with plan_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            season_ids = _all_season_ids(cur)
            if not season_ids:
                out["stopped_reason"] = "no_seasons"
                emit("stopped")
                return out

            total, per_archetype = _count_eligible_per_archetype(
                cur, season_ids, events_filter
            )
            out["showcase_eligible_total"] = total
            if total == 0:
                out["stopped_reason"] = "no_eligible_candidates"
                emit("stopped")
                return out

            n = min(n, total)
            diversity_plan: Optional[Dict[Tuple[str, str], int]] = None
            if maximum_diversity:
                events_per_archetype = _count_eligible_events_per_archetype(
                    cur, season_ids, events_filter
                )
                diversity_plan = _plan_balanced_archetype_event_pairs(
                    n, events_per_archetype
                )
                out["planned"] = sum(diversity_plan.values())
                slots = {}
            else:
                slots = {None: n}  # type: ignore[dict-item]
                out["planned"] = sum(slots.values())

            # Track current ``seasons.remaining_supply`` snapshot just so the
            # response contract has values; previews don't decrement supply.
            cur.execute(
                "SELECT COALESCE(SUM(remaining_supply), 0) AS rem FROM seasons WHERE id = ANY(%s)",
                (season_ids,),
            )
            row = cur.fetchone()
            rem_now = int((row.get("rem") if isinstance(row, dict) else row[0]) or 0)
            out["remaining_supply_before"] = rem_now
            out["remaining_supply_after"] = rem_now
    finally:
        plan_conn.close()

    emit("planned")

    # Pull all candidate rows up front so per-row work is a tight loop with
    # no extra round-trips for the SELECT side. In maximum-diversity mode
    # each (archetype, event_id) pair is sampled independently so the
    # planner's per-event spread is preserved; otherwise we sample by
    # archetype only (or the whole pool when archetype is None).
    candidates: List[Dict[str, Any]] = []
    fetch_conn = manager.get_connection()
    try:
        with fetch_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if diversity_plan is not None:
                for (archetype, event_id), pair_count in diversity_plan.items():
                    if pair_count <= 0:
                        continue
                    rows = _fetch_candidates_for_event(
                        cur, season_ids, archetype, event_id, pair_count
                    )
                    candidates.extend(rows)
            else:
                for archetype, slot_count in slots.items():
                    if slot_count <= 0:
                        continue
                    rows = _fetch_candidates(
                        cur, season_ids, archetype, slot_count, events_filter
                    )
                    candidates.extend(rows)
    finally:
        fetch_conn.close()

    if not candidates:
        out["stopped_reason"] = "no_eligible_candidates"
        emit("stopped")
        return out

    # Decide origin/looter assignment once, deterministically per request.
    n_run = len(candidates)
    n_origin = int(round(n_run * float(origin_match_fraction)))
    n_origin = max(0, min(n_run, n_origin))
    origin_indices = (
        set(random.sample(range(n_run), k=n_origin)) if n_origin > 0 else set()
    )

    # One connection for the whole render loop. Each iteration is its own
    # transaction (commit per row) so a single failure doesn't roll back
    # the previously generated preview cards.
    conn = manager.get_connection()
    try:
        for i, source_row in enumerate(candidates):
            is_origin = i in origin_indices
            uploaded_keys: Tuple[Optional[str], Optional[str]] = (None, None)
            try:
                slug = _generate_slug_hint(
                    source_row.get("season_type"),
                    source_row.get("season_number"),
                )
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    inserted = _insert_preview_row(
                        cur,
                        slug_hint=slug,
                        source_row=source_row,
                        owner_wallet=_synthetic_eoa(),
                        owner_proxy_wallet=str(source_row.get("proxy_wallet") or ""),
                    )
                if inserted is None:
                    # UniqueViolation: another simulator filled this slot
                    # concurrently. Skip — total count just decreases.
                    conn.rollback()
                    continue
                preview_id = int(inserted["id"])

                payload = _build_payload(
                    source_row,
                    preview_id=preview_id,
                    slug=slug,
                    is_origin=is_origin,
                )

                emit("rendering", iteration=i + 1, slug=slug)
                front_png, back_png = render_card_pngs(_build_render_payload(payload))
                front_url, back_url, front_key, back_key = upload_card_assets_to_r2(
                    slug, front_png, back_png
                )
                uploaded_keys = (front_key, back_key)

                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE preview_cards
                        SET front_image_path  = %s,
                            back_image_path   = %s,
                            card_payload_json = %s::jsonb
                        WHERE id = %s
                        """,
                        (front_url, back_url, json.dumps(payload), preview_id),
                    )
                conn.commit()

                out["generated"] += 1
                if is_origin:
                    out["origin_claim_cards"] += 1
                emit("progress", iteration=i + 1, slug=slug)
            except Exception as exc:
                try:
                    conn.rollback()
                except Exception:
                    pass
                # Cleanup any orphan R2 objects from this iteration so a
                # retry doesn't waste storage. delete_r2_object_by_key is
                # best-effort (never raises) — safe to call on (None, None).
                front_key, back_key = uploaded_keys
                delete_r2_object_by_key(front_key)
                delete_r2_object_by_key(back_key)
                errors.append(f"iter={i + 1}: {exc}")
                emit("error", iteration=i + 1, error=str(exc))
                logger.exception(
                    "Admin preview generation failed iter=%s slug_hint=%s",
                    i + 1,
                    locals().get("slug"),
                )
    finally:
        conn.close()

    if not out["stopped_reason"] and out["generated"] < out["planned"]:
        out["stopped_reason"] = "completed_short"
    emit("completed" if not out["stopped_reason"] else "stopped")
    return out
