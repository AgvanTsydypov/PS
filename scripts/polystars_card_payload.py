"""
Build canonical PolyStars card payloads for NFT mint metadata.

This mirrors the user-generated card payload shape and also generates
front/back SVG card assets so the minted NFT metadata can reference them.
"""

from __future__ import annotations

import base64
import json
import os
import pathlib
import secrets
import urllib.parse
import urllib.request
import uuid
from datetime import date, datetime, timezone
from typing import Any, Dict, Optional, Tuple

_CARDGEN_DIR = pathlib.Path(__file__).resolve().parent / "cardgen"
import psycopg2.extras

from scripts.cardgen.assets import (
    render_card_svgs,
    rasterize_card_svgs,
    unpin_pinata_urls,
    upload_card_assets_to_pinata,
)
from scripts.data_loading_manager import DataLoadingManager
from scripts.http_fetch_ssrf import urlopen_after_ssrf_check

__all__ = ["unpin_pinata_urls"]  # re-exported for admin_backend import path

CARD_BASE_URL = (
    os.getenv("CARD_BASE_URL")
    or os.getenv("NEXT_PUBLIC_APP_URL")
    or "https://polystars.app"
).strip().rstrip("/")
GENESIS_START_DATE: Optional[str] = os.getenv("GENESIS_START_DATE", "").strip() or None
GENESIS_END_DATE: Optional[str] = os.getenv("GENESIS_END_DATE", "").strip() or None
CARD_IMAGE_TIMEOUT_SECONDS = float(os.getenv("USER_WEB_CARD_IMAGE_TIMEOUT_SECONDS", "12"))

CARD_SEASON_TYPE_OPTIONS = ("standard", "genesis")
CARD_ENTRY_BRACKET_OPTIONS = (
    "[0.00 - 0.20]",
    "[0.20 - 0.40]",
    "[0.40 - 0.60]",
    "[0.60 - 0.80]",
    "[0.80 - 0.97]",
    "[0.97 - 1.00]",
)
CARD_TIER_OPTIONS = ("P99", "P90", "P70", "P50", "BASE")
CARD_ARCHETYPE_OPTIONS = (
    "ICARUS",
    "BURNER",
    "BOT",
    "EXTRACTOR",
    "PASSENGER",
    "ANOMALY",
    "INSIDER",
    "SIGNAL",
    "VECTOR",
    "EQUILIBRIUM",
    "GRAVITON",
    "SUBSTRATE",
    "OPERATOR",
)
LEGACY_ENTRY_BRACKET_MAP: Dict[str, str] = {
    "ANOMALY": "[0.00 - 0.20]",
    "ORACLE": "[0.20 - 0.40]",
    "OUTLIER": "[0.40 - 0.60]",
    "VECTOR": "[0.60 - 0.80]",
    "EXTRACTOR": "[0.97 - 1.00]",
    "PASSENGER": "[0.97 - 1.00]",
}
_PTIER_CSS_COLORS: Dict[str, str] = {
    "P99": "#FFD700",
    "P90": "#FFBF00",
    "P70": "#265DD2",
    "P50": "#38BE50",
    "BASE": "#B6BBC8",
}
def _fmt_date_field(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc)
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d")
    s = str(value).strip()
    return s[:10] if len(s) >= 10 else s or None


def _normalize_choice(raw: Optional[str], options: Tuple[str, ...], fallback: str) -> str:
    value = str(raw or "").strip().upper()
    for option in options:
        if option.upper() == value:
            return option
    return fallback


def _normalize_archetype(raw: Optional[str], inferred: str) -> str:
    cleaned = str(raw or "").strip().upper()
    if cleaned.startswith("THE "):
        cleaned = cleaned[4:].strip()
    return _normalize_choice(cleaned, CARD_ARCHETYPE_OPTIONS, inferred)


def _normalize_entry_bracket(raw: Optional[str]) -> str:
    value = str(raw or "").strip().upper()
    if value in LEGACY_ENTRY_BRACKET_MAP:
        return LEGACY_ENTRY_BRACKET_MAP[value]
    return _normalize_choice(value, CARD_ENTRY_BRACKET_OPTIONS, "[0.80 - 0.97]")


def _to_float(raw: Any) -> Optional[float]:
    try:
        if raw is None or str(raw).strip() == "":
            return None
        return float(raw)
    except (TypeError, ValueError):
        return None


def _infer_archetype_from_metrics(
    entry_bracket: str,
    edge_raw: Optional[str],
    yield_raw: Optional[str],
    gravity_raw: Optional[str],
    entry_cwap_raw: Any = None,
    total_volume_raw: Any = None,
    total_pnl_raw: Any = None,
) -> str:
    edge = _normalize_choice(edge_raw, CARD_TIER_OPTIONS, "BASE")
    yld = _normalize_choice(yield_raw, CARD_TIER_OPTIONS, "BASE")
    grav = _normalize_choice(gravity_raw, CARD_TIER_OPTIONS, "BASE")
    entry_cwap = _to_float(entry_cwap_raw)
    total_volume = _to_float(total_volume_raw)
    total_pnl = _to_float(total_pnl_raw)

    if total_pnl is not None:
        if total_pnl < 0 and entry_cwap is not None:
            return "ICARUS" if entry_cwap < 0.60 else "BURNER"
        if total_pnl == 0:
            return "BOT"
    if entry_bracket == "[0.97 - 1.00]":
        if total_volume is not None and total_volume >= 5000:
            return "EXTRACTOR"
        if total_volume is not None and total_volume >= 50:
            return "PASSENGER"
        return "SUBSTRATE"

    if (
        entry_bracket != "[0.80 - 0.97]"
        and (
            (entry_bracket == "[0.00 - 0.20]" and edge == "P99" and yld == "P99" and grav == "P99")
            or (entry_bracket == "[0.20 - 0.40]" and edge == "P90" and yld == "P90" and grav == "P90")
            or (entry_bracket == "[0.40 - 0.60]" and edge == "P70" and yld == "P70" and grav == "P70")
            or (entry_bracket == "[0.60 - 0.80]" and edge == "P50" and yld == "P50" and grav == "P50")
        )
    ):
        return "ANOMALY"
    if entry_cwap is not None and entry_cwap <= 0.10 and yld == "P99":
        return "INSIDER"
    if (
        (entry_bracket == "[0.00 - 0.20]" or entry_bracket == "[0.20 - 0.40]")
        and (edge == "P99" or edge == "P90")
        and (yld == "P99" or yld == "P90")
    ):
        return "SIGNAL"
    if entry_bracket == "[0.40 - 0.60]" and (edge == "P99" or edge == "P90") and (yld == "P99" or yld == "P90"):
        return "VECTOR"
    if (
        (edge == "P99" or edge == "P90" or edge == "P70")
        and (yld == "P99" or yld == "P90" or yld == "P70")
        and (grav == "P99" or grav == "P90" or grav == "P70")
    ):
        return "EQUILIBRIUM"
    if grav == "P99" or grav == "P90":
        return "GRAVITON"
    if (
        (entry_bracket == "[0.60 - 0.80]" or entry_bracket == "[0.80 - 0.97]")
        and (edge == "BASE" or edge == "P50")
        and (yld == "BASE" or yld == "P50")
        and (grav == "BASE" or grav == "P50" or grav == "P70")
    ):
        return "SUBSTRATE"
    return "OPERATOR"


def _border_css_color(yield_tier: str) -> str:
    return _PTIER_CSS_COLORS.get(str(yield_tier or "BASE").upper(), "#B6BBC8")


def _generated_card_slug(season_type: Optional[str], season_number: Any) -> str:
    normalized_type = "".join(
        ch.lower() if ch.isalnum() else "-"
        for ch in str(season_type or "season").strip()
    ).strip("-") or "season"
    try:
        normalized_number = str(int(season_number))
    except Exception:
        normalized_number = "0"
    return f"card-{normalized_type}-s{normalized_number}-{secrets.token_hex(16)}-{uuid.uuid4().hex}"


def _remote_image_to_data_uri(image_url: str, *, timeout_seconds: float) -> str:
    normalized = str(image_url or "").strip()
    if not normalized or normalized.startswith("data:"):
        return normalized
    if not normalized.startswith(("http://", "https://")):
        return normalized

    req = urllib.request.Request(
        normalized,
        method="GET",
        headers={
            "Accept": "image/*,*/*;q=0.8",
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
        },
    )
    with urlopen_after_ssrf_check(req, timeout=float(timeout_seconds)) as response:
        raw = response.read()
        if not raw:
            raise ValueError("Downloaded image is empty")
        content_type = str(response.headers.get_content_type() or "").strip().lower() or "image/jpeg"
    encoded = base64.b64encode(raw).decode("ascii")
    return f"data:{content_type};base64,{encoded}"


def _build_render_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    render_payload = dict(payload or {})
    image_url = str(render_payload.get("image_url") or "").strip()
    if image_url.startswith(("http://", "https://")):
        render_payload["image_url"] = _remote_image_to_data_uri(
            image_url,
            timeout_seconds=CARD_IMAGE_TIMEOUT_SECONDS,
        )
    return render_payload


def _build_card_payload_from_source_row(
    row: Dict[str, Any],
    *,
    claim_id: int,
    claim_type: str,
    collection_mint_number: Optional[int] = None,
    preview_slug: Optional[str] = None,
) -> Dict[str, Any]:
    normalized_entry_bracket = _normalize_entry_bracket(row.get("entry_bracket"))
    normalized_edge = _normalize_choice(row.get("edge"), CARD_TIER_OPTIONS, "BASE")
    normalized_yield = _normalize_choice(row.get("yield"), CARD_TIER_OPTIONS, "BASE")
    normalized_gravity = _normalize_choice(row.get("gravity"), CARD_TIER_OPTIONS, "BASE")
    inferred_archetype = _infer_archetype_from_metrics(
        normalized_entry_bracket,
        normalized_edge,
        normalized_yield,
        normalized_gravity,
        row.get("entry_cwap"),
        row.get("total_volume"),
        row.get("total_pnl"),
    )
    normalized_archetype = _normalize_archetype(row.get("archetype"), inferred_archetype)

    rec_raw = row.get("reccurence")
    recurrence_out = None if rec_raw is None else (str(rec_raw).strip() or None)

    image_url = str(row.get("manual_image_url") or "").strip()
    if not image_url:
        raise ValueError("Card payload requires manual_image_url in the DB row")

    season_type = _normalize_choice(row.get("season_type"), CARD_SEASON_TYPE_OPTIONS, "standard").lower()
    # Reuse the preview slug if one was minted from a pre-generated preview row.
    # Generating a fresh slug at mint time used to create a visible mismatch:
    # showcase links (``/preview/{preview_slug}``) and the on-chain QR code
    # (``/cards/{mint_slug}``) pointed at two different random strings for the
    # same card. Now the slug is an attribute of the winner slot and lives
    # through the preview → mint transition, so the QR code baked on the
    # physical card and the ticker that first surfaced it agree.
    normalized_preview_slug = str(preview_slug or "").strip()
    slug = normalized_preview_slug or _generated_card_slug(season_type, row.get("season_number"))

    payload = {
        "season_type": season_type,
        "season_number": int(row.get("season_number") or 1),
        "recurrence": recurrence_out,
        "claim_type": str(claim_type or "looter").strip().lower() if str(claim_type or "").strip() else "looter",
        "event_id": str(row.get("event_id") or "").strip(),
        "signature": str(row.get("signature") or "").strip(),
        "image_url": image_url,
        "card_title": str(row.get("card_title") or row.get("event_title") or "").strip(),
        "card_lore": str(row.get("card_lore") or "").strip(),
        "primary_tag": str(row.get("primary_tag") or "UNKNOWN").strip() or "UNKNOWN",
        "primary_tag_color": str(row.get("primary_tag_hex_color") or "#FFFFFF").strip() or "#FFFFFF",
        "secondary_tag": str(row.get("secondary_tag") or "NONE").strip() or "NONE",
        "entry_bracket": normalized_entry_bracket,
        "archetype": normalized_archetype,
        "archetype_description": str(row.get("archetype_description") or "").strip(),
        "archetype_math": str(row.get("archetype_math") or "").strip(),
        "rarity_bracket": str(row.get("rarity_bracket") or "").strip(),
        "proxy_wallet": str(row.get("proxy_wallet") or "").strip(),
        "edge": normalized_edge,
        "yield": normalized_yield,
        "gravity": normalized_gravity,
        "border_color": _border_css_color(normalized_yield),
        "leaderboard_rank": int(row.get("rank") or 0),
        "season_start_date": (
            GENESIS_START_DATE if season_type == "genesis" and GENESIS_START_DATE else _fmt_date_field(row.get("season_start_date"))
        ),
        "season_end_date": (
            GENESIS_END_DATE if season_type == "genesis" and GENESIS_END_DATE else _fmt_date_field(row.get("season_end_date"))
        ),
        "season_size": row.get("season_size"),
        "collection_mint_number": int(
            collection_mint_number if collection_mint_number is not None else claim_id
        ),
        "qr_payload": f"{CARD_BASE_URL}/cards/{slug}",
    }
    return payload


def _attach_generated_card_images(payload: Dict[str, Any]) -> Dict[str, Any]:
    render_payload = _build_render_payload(payload)
    # Canonical pipeline: SVG -> PNG -> Pinata. The raster step produces a plain
    # PNG that every marketplace and wallet renders identically (no dependency
    # on remote @font-face resolution). The shared rasterizer reuses one
    # headless browser across calls, so sequential mints are cheap.
    front_svg, back_svg = render_card_svgs(render_payload)
    (_CARDGEN_DIR / "output.svg").write_text(front_svg, encoding="utf-8")
    (_CARDGEN_DIR / "output_back.svg").write_text(back_svg, encoding="utf-8")

    front_png, back_png = rasterize_card_svgs(front_svg, back_svg)
    (_CARDGEN_DIR / "output.png").write_bytes(front_png)
    (_CARDGEN_DIR / "output_back.png").write_bytes(back_png)

    slug = str(payload.get("qr_payload") or "").rstrip("/").rsplit("/", 1)[-1] or _generated_card_slug(
        payload.get("season_type"),
        payload.get("season_number"),
    )
    front_image_url, back_image_url = upload_card_assets_to_pinata(slug, front_png, back_png)
    output = dict(payload)
    output["front_image_url"] = front_image_url
    output["back_image_url"] = back_image_url
    output["front_image_mime"] = "image/png"
    output["back_image_mime"] = "image/png"
    return output


_CARD_SOURCE_FROM_CLAIM_SQL = """
SELECT
    c.id AS claim_id,
    c.season_id,
    s.type AS season_type,
    s.season_number,
    c.proxy_wallet,
    c.event_id,
    c.event_slug,
    e.title AS event_title,
    COALESCE(NULLIF(BTRIM(e.image), ''), NULLIF(BTRIM(e.icon), '')) AS event_image_url,
    c.entry_cwap,
    c.total_volume,
    c.total_pnl,
    c.entry_bracket,
    c.archetype,
    c.archetype_description,
    c.archetype_math,
    c.rarity_bracket,
    c.edge,
    c.yield,
    c.gravity,
    c.participant_rank AS rank,
    c.claim_type,
    c.signature,
    ec.reccurence,
    ec.manual_image_url,
    ec.card_title,
    ec.card_lore,
    ec.primary_tag,
    ec.secondary_tag,
    tp.hex_color AS primary_tag_hex_color,
    s.start_date AS season_start_date,
    s.end_date AS season_end_date,
    s.total_supply AS season_size,
    c.collection_mint_number
FROM claims c
LEFT JOIN event_cards ec ON ec.event_id = c.event_id
LEFT JOIN events e ON e.id = c.event_id
LEFT JOIN seasons s ON s.id = c.season_id
LEFT JOIN tags tp ON LOWER(BTRIM(tp.label)) = LOWER(BTRIM(ec.primary_tag))
WHERE c.id = %s
LIMIT 1
"""


def _load_card_source_row_from_claim(
    manager: DataLoadingManager, claim_id: int
) -> Optional[Dict[str, Any]]:
    conn = manager.get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
            cursor.execute(_CARD_SOURCE_FROM_CLAIM_SQL, (claim_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
    finally:
        conn.close()


def _lookup_preview_slug_for_slot(
    manager: DataLoadingManager,
    *,
    season_id: Optional[int],
    event_slug: Optional[str],
    proxy_wallet: Optional[str],
) -> Optional[str]:
    """If the simulator pre-rendered a preview for this exact slot, return
    its slug so the mint reuses it. Slot identity is
    ``(season_id, event_slug, LOWER(proxy_wallet))`` — same dedup key as the
    UNIQUE index ``ux_preview_cards_logical_slot``.

    Returning the existing slug means ``denormalize_card_onto_claim`` later
    DELETEs the preview by that slug, swapping preview → minted under a
    permalink that survives the transition.
    """
    if season_id is None or not event_slug or not proxy_wallet:
        return None
    conn = manager.get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT slug
                FROM preview_cards
                WHERE season_id  = %s
                  AND event_slug = %s
                  AND LOWER(owner_proxy_wallet) = LOWER(%s)
                LIMIT 1
                """,
                (int(season_id), str(event_slug), str(proxy_wallet)),
            )
            row = cursor.fetchone()
    finally:
        conn.close()
    if not row:
        return None
    raw = row[0] if not isinstance(row, dict) else row.get("slug")
    return str(raw).strip() if raw else None


def build_polystars_card_from_claim(
    manager: DataLoadingManager,
    *,
    claim_id: int,
) -> Dict[str, Any]:
    """Build the mint-ready card payload reading the snapshot directly from
    the claims row. Used by the daily cron worker to materialize card images
    for QUEUED claims, after the snapshot has been frozen at queue-insert time.

    Slug continuity: if a simulator-generated preview exists for the same
    slot (``season_id, event_slug, proxy_wallet``), the mint inherits its
    slug, so ``/cards/{slug}`` keeps working through the preview→minted
    transition. ``denormalize_card_onto_claim`` then deletes that preview
    row by slug after the on-chain mint succeeds.
    """
    row = _load_card_source_row_from_claim(manager, claim_id)
    if not row:
        raise ValueError(f"claims id={claim_id} not found")
    claim_type = str(row.get("claim_type") or "looter").strip().lower() or "looter"
    collection_mint_number = row.get("collection_mint_number")
    preview_slug = _lookup_preview_slug_for_slot(
        manager,
        season_id=row.get("season_id"),
        event_slug=row.get("event_slug"),
        proxy_wallet=row.get("proxy_wallet"),
    )
    payload = _build_card_payload_from_source_row(
        row,
        claim_id=claim_id,
        claim_type=claim_type,
        collection_mint_number=int(collection_mint_number) if collection_mint_number is not None else None,
        preview_slug=preview_slug,
    )
    return _attach_generated_card_images(payload)


def _slug_from_qr_payload(qr_payload: Any) -> Optional[str]:
    qr = str(qr_payload or "").strip()
    if not qr:
        return None
    try:
        path = urllib.parse.urlparse(qr).path or qr
    except Exception:
        path = qr
    tail = path.rstrip("/").rsplit("/", 1)[-1].strip()
    return tail or None


def denormalize_card_onto_claim(
    manager: DataLoadingManager,
    *,
    claim_id: int,
    polystars_card: Dict[str, Any],
) -> None:
    """Persist the rendered card payload onto the minted ``claims`` row.

    Under the queue model, the cron worker calls this after a successful
    on-chain mint so ``/api/cards/{slug}`` (which reads ``claims``) can
    resolve the freshly minted STAR without joining anywhere else.

    Also opportunistically drops any matching ``preview_cards`` row by slug
    so the showcase ticker no longer surfaces a card that is already minted.
    Both writes share a single transaction.
    """
    slug = _slug_from_qr_payload(polystars_card.get("qr_payload"))
    if not slug:
        return
    front_image_url = str(polystars_card.get("front_image_url") or "").strip()
    back_image_url = str(polystars_card.get("back_image_url") or "").strip()
    if not front_image_url or not back_image_url:
        return

    card_title = str(polystars_card.get("card_title") or "").strip() or None
    primary_tag = str(polystars_card.get("primary_tag") or "").strip() or None
    secondary_tag = str(polystars_card.get("secondary_tag") or "").strip() or None
    pattern = str(polystars_card.get("pattern") or "").strip() or None
    card_payload_json = json.dumps(polystars_card)

    conn = manager.get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE claims
                SET card_slug         = %s,
                    card_title        = %s,
                    front_image_url   = %s,
                    back_image_url    = %s,
                    primary_tag       = %s,
                    secondary_tag    = %s,
                    pattern           = %s,
                    card_payload_json = %s::jsonb,
                    updated_at        = NOW()
                WHERE id = %s
                """,
                (
                    slug,
                    card_title,
                    front_image_url,
                    back_image_url,
                    primary_tag,
                    secondary_tag,
                    pattern,
                    card_payload_json,
                    claim_id,
                ),
            )
            cursor.execute(
                "DELETE FROM preview_cards WHERE slug = %s",
                (slug,),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
