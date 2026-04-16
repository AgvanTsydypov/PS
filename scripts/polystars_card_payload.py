"""
Build canonical PolyStars card payloads for NFT mint metadata.

This mirrors the user-generated card payload shape and also generates
front/back SVG card assets so the minted NFT metadata can reference them.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from typing import Any, Dict, Optional, Tuple

import io

import httpx
import psycopg2.extras
from reportlab.graphics import renderPM
from svglib.svglib import svg2rlg

from scripts.cardgen.generate_card import generate_card_back_svg, generate_card_svg
from scripts.data_loading_manager import DataLoadingManager

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
    "ANOMALY",
    "ICARUS",
    "BOT",
    "BURNER",
    "SIGNAL",
    "VECTOR",
    "EQUILIBRIUM",
    "AMASSER",
    "EXTRACTOR",
    "PASSENGER",
    "SUBSTRATE",
    "OPERATOR",
)
LEGACY_ENTRY_BRACKET_MAP: Dict[str, str] = {
    "ANOMALY": "[0.00 - 0.20]",
    "ORACLE": "[0.20 - 0.40]",
    "OUTLIER": "[0.40 - 0.60]",
    "VECTOR": "[0.60 - 0.80]",
    "HARVESTER": "[0.80 - 0.97]",
    "EXTRACTOR": "[0.97 - 1.00]",
    "PASSENGER": "[0.97 - 1.00]",
}
LEGACY_ARCHETYPE_MAP: Dict[str, str] = {
    "HARVESTER": "EXTRACTOR",
    "MARTYR": "ICARUS",
}
_PTIER_CSS_COLORS: Dict[str, str] = {
    "P999": "#FFD700",
    "P99": "#FFD700",
    "P95": "#FFBF00",
    "P90": "#FFBF00",
    "P80": "#265DD2",
    "P70": "#265DD2",
    "P50": "#38BE50",
    "BASE": "#B6BBC8",
}
PINATA_JWT_ENV_KEY = "PINATA_JWT"
PINATA_FILE_API_URL = "https://api.pinata.cloud/pinning/pinFileToIPFS"

_CARD_SOURCE_SQL = """
SELECT
    w.id AS winner_row_id,
    w.season_id,
    s.type AS season_type,
    s.season_number,
    w.proxy_wallet,
    w.event_id,
    w.event_slug,
    e.title AS event_title,
    COALESCE(NULLIF(BTRIM(e.image), ''), NULLIF(BTRIM(e.icon), '')) AS event_image_url,
    COALESCE(w.entry_cwap, p.entry_cwap) AS entry_cwap,
    COALESCE(w.total_volume, p.total_volume) AS total_volume,
    COALESCE(w.total_pnl, p.total_pnl) AS total_pnl,
    w.entry_bracket,
    COALESCE(w.archetype, p.archetype) AS archetype,
    COALESCE(w.archetype_description, p.archetype_description) AS archetype_description,
    COALESCE(w.archetype_math, p.archetype_math) AS archetype_math,
    COALESCE(w.rarity_bracket, p.rarity_bracket) AS rarity_bracket,
    w.edge,
    w.yield,
    w.gravity,
    w.rank,
    ec.reccurence,
    ec.manual_image_url,
    ec.card_title,
    ec.card_lore,
    ec.primary_tag,
    ec.secondary_tag,
    tp.hex_color AS primary_tag_hex_color,
    s.start_date AS season_start_date,
    s.end_date AS season_end_date,
    s.total_supply AS season_size
FROM winner_wallets_nft_to_claim w
JOIN event_cards ec ON ec.event_id = w.event_id
LEFT JOIN LATERAL (
    SELECT
        p.entry_cwap,
        p.total_volume,
        p.total_pnl,
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
"""


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
    cleaned = LEGACY_ARCHETYPE_MAP.get(cleaned, cleaned)
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
        return "AMASSER"
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
    with urllib.request.urlopen(req, timeout=timeout_seconds) as response:
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


def _pinata_jwt() -> str:
    jwt = str(os.getenv(PINATA_JWT_ENV_KEY, "")).strip()
    if not jwt:
        raise ValueError(f"{PINATA_JWT_ENV_KEY} is required")
    return jwt


def _svg_to_png_bytes(svg_body: str) -> bytes:
    """Convert SVG string to PNG bytes using svglib + reportlab (no native deps)."""
    drawing = svg2rlg(io.BytesIO(svg_body.encode("utf-8")))
    if drawing is None:
        raise RuntimeError("svglib failed to parse SVG for PNG conversion")
    buf = io.BytesIO()
    renderPM.drawToFile(drawing, buf, fmt="PNG")
    return buf.getvalue()


def _pin_svg_to_pinata(filename: str, svg_body: str) -> str:
    """Convert SVG to PNG and upload to Pinata.

    Phantom and most wallets/marketplaces do not render SVG images for NFTs.
    PNG is the universally supported format.
    """
    png_filename = filename.replace(".svg", ".png")
    png_bytes = _svg_to_png_bytes(svg_body)

    jwt = _pinata_jwt()
    headers = {"Authorization": f"Bearer {jwt}"}
    files = {
        "file": (png_filename, png_bytes, "image/png"),
    }
    data = {
        "pinataMetadata": json.dumps({"name": png_filename}),
    }
    response = httpx.post(
        PINATA_FILE_API_URL,
        headers=headers,
        data=data,
        files=files,
        timeout=40.0,
    )
    response.raise_for_status()
    body = response.json()
    ipfs_hash = body.get("IpfsHash")
    if not ipfs_hash:
        raise RuntimeError(f"Pinata file upload missing IpfsHash: {body}")
    return f"https://gateway.pinata.cloud/ipfs/{ipfs_hash}"


def _upload_generated_card_assets_to_pinata(slug: str, front_svg: str, back_svg: str) -> Tuple[str, str]:
    safe_slug = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in str(slug or "").strip()) or "card"
    front_name = f"{safe_slug}-front.svg"
    back_name = f"{safe_slug}-back.svg"

    with ThreadPoolExecutor(max_workers=2) as pool:
        front_f = pool.submit(_pin_svg_to_pinata, front_name, front_svg)
        back_f = pool.submit(_pin_svg_to_pinata, back_name, back_svg)
        return front_f.result(), back_f.result()


def _load_card_source_row(manager: DataLoadingManager, winner_row_id: int) -> Optional[Dict[str, Any]]:
    conn = manager.get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
            cursor.execute(_CARD_SOURCE_SQL, (winner_row_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
    finally:
        conn.close()


def _build_card_payload_from_source_row(
    row: Dict[str, Any],
    *,
    claim_id: int,
    claim_type: str,
    snapshot_event_image_url: str = "",
    collection_mint_number: Optional[int] = None,
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

    manual = str(row.get("manual_image_url") or "").strip()
    event_image = str(row.get("event_image_url") or "").strip()
    image_url = manual or str(snapshot_event_image_url or "").strip() or event_image
    if not image_url:
        raise ValueError("Card payload requires an image source URL")

    season_type = _normalize_choice(row.get("season_type"), CARD_SEASON_TYPE_OPTIONS, "standard").lower()
    slug = _generated_card_slug(season_type, row.get("season_number"))

    payload = {
        "season_type": season_type,
        "season_number": int(row.get("season_number") or 1),
        "recurrence": recurrence_out,
        "claim_type": str(claim_type or "looter").strip().lower() if str(claim_type or "").strip() else "looter",
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
    front_svg = generate_card_svg(render_payload)
    back_svg = generate_card_back_svg(render_payload)
    slug = str(payload.get("qr_payload") or "").rstrip("/").rsplit("/", 1)[-1] or _generated_card_slug(
        payload.get("season_type"),
        payload.get("season_number"),
    )
    front_image_url, back_image_url = _upload_generated_card_assets_to_pinata(slug, front_svg, back_svg)
    output = dict(payload)
    output["front_image_url"] = front_image_url
    output["back_image_url"] = back_image_url
    return output


def _attach_fixed_card_images(
    payload: Dict[str, Any],
    *,
    fixed_front_image_url: str,
    fixed_back_image_url: str,
) -> Dict[str, Any]:
    output = dict(payload)
    output["front_image_url"] = fixed_front_image_url
    output["back_image_url"] = fixed_back_image_url
    return output


def build_polystars_card_for_mint(
    manager: DataLoadingManager,
    *,
    winner_row_id: int,
    claim_id: int,
    claim_type: str,
    snapshot_event_image_url: str = "",
    fixed_front_image_url: str = "",
    fixed_back_image_url: str = "",
    collection_mint_number: Optional[int] = None,
) -> Dict[str, Any]:
    row = _load_card_source_row(manager, winner_row_id)
    if not row:
        raise ValueError(f"winner_wallets_nft_to_claim id={winner_row_id} not found")
    payload = _build_card_payload_from_source_row(
        row,
        claim_id=claim_id,
        claim_type=claim_type,
        snapshot_event_image_url=snapshot_event_image_url,
        collection_mint_number=collection_mint_number,
    )
    if fixed_front_image_url and fixed_back_image_url:
        return _attach_fixed_card_images(
            payload,
            fixed_front_image_url=fixed_front_image_url,
            fixed_back_image_url=fixed_back_image_url,
        )
    return _attach_generated_card_images(payload)
