"""
Admin-only batch simulation of user POST /api/cards/get.

Lives under scripts/ so admin_backend can import it without the ``user_web_backend`` package
(which may be absent on admin-only deployments). Keep logic aligned with user_web_backend/main.py.
"""

from __future__ import annotations

import base64
import json
import logging
import mimetypes
import os
import random
import secrets
import sys
import threading
import urllib.parse
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from dataclasses import dataclass
from time import monotonic
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional, Tuple

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
from web3 import Web3

try:
    import boto3
    from botocore.config import Config
except Exception:  # pragma: no cover
    boto3 = None
    Config = None

_repo_root = Path(__file__).resolve().parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

load_dotenv(dotenv_path=_repo_root / os.getenv("ENV_FILE", ".env"), override=False)

from scripts.cardgen.generate_card import generate_card_back_svg, generate_card_svg

logger = logging.getLogger(__name__)

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
_ANOMALY_SUBTIER_OPTIONS = frozenset({"P99", "P90", "P70", "P50"})
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

_WINNER_CATALOG_JOIN = """
FROM winner_wallets_nft_to_claim w
JOIN event_cards ec ON ec.event_id = w.event_id
WHERE ec.manual_image_url IS NOT NULL
  AND BTRIM(ec.manual_image_url) <> ''
"""

# Eligible catalog rows: winner snapshot + event_cards (image) + not yet in user_generated_cards.
# Archetype/metrics for planning come from w only — no participants join.
_SHOWCASE_CANDIDATE_BODY = """
FROM winner_wallets_nft_to_claim w
JOIN event_cards ec ON ec.event_id = w.event_id
LEFT JOIN user_generated_cards gc ON gc.winner_row_id = w.id
WHERE ec.manual_image_url IS NOT NULL
  AND BTRIM(ec.manual_image_url) <> ''
  AND gc.id IS NULL
"""

_ELIGIBLE_WINNER_BASE = _SHOWCASE_CANDIDATE_BODY

POLYMARKET_REQUEST_TIMEOUT_SECONDS = float(os.getenv("POLYMARKET_REQUEST_TIMEOUT_SECONDS", "8"))
USER_WEB_CARD_IMAGE_TIMEOUT_SECONDS = float(os.getenv("USER_WEB_CARD_IMAGE_TIMEOUT_SECONDS", "6"))
USER_WEB_CARD_SUPPLY_JOIN_TOTAL_CACHE_TTL = float(os.getenv("USER_WEB_CARD_SUPPLY_JOIN_TOTAL_CACHE_TTL", "60"))
USER_WEB_CARD_IMAGE_DATA_URI_CACHE_TTL = float(os.getenv("USER_WEB_CARD_IMAGE_DATA_URI_CACHE_TTL", "900"))
USER_WEB_CARD_IMAGE_DATA_URI_CACHE_MAX_ITEMS = max(
    1, int(os.getenv("USER_WEB_CARD_IMAGE_DATA_URI_CACHE_MAX_ITEMS", "256"))
)
SIMULATE_SHOWCASE_MAX_CANDIDATES = max(1, int(os.getenv("SIMULATE_SHOWCASE_MAX_CANDIDATES", "8000")))

GENESIS_START_DATE: Optional[str] = os.getenv("GENESIS_START_DATE", "").strip() or None
GENESIS_END_DATE: Optional[str] = os.getenv("GENESIS_END_DATE", "").strip() or None

CARD_BASE_URL = (
    os.getenv("CARD_BASE_URL")
    or os.getenv("NEXT_PUBLIC_APP_URL")
    or "https://polystars.app"
).strip().rstrip("/")

_winner_catalog_join_total_lock = threading.Lock()
_winner_catalog_join_total_cache: Tuple[float, int] = (0.0, 0)
_embedded_image_cache_lock = threading.Lock()
_embedded_image_cache: Dict[str, Tuple[float, str]] = {}
_r2_client: Any = None


def _db_params() -> Dict[str, object]:
    host = os.getenv("DB_HOST", os.getenv("LOCAL_DB_HOST", "127.0.0.1"))
    port = int(os.getenv("DB_PORT", os.getenv("LOCAL_DB_PORT", "5432")))
    return {
        "host": host,
        "port": port,
        "database": os.getenv("DB_NAME", os.getenv("LOCAL_DB_NAME")),
        "user": os.getenv("DB_USER", os.getenv("LOCAL_DB_USER")),
        "password": os.getenv("DB_PASSWORD", os.getenv("LOCAL_DB_PASSWORD")),
        "sslmode": os.getenv("DB_SSLMODE", "require"),
    }


def _get_connection():
    return psycopg2.connect(**_db_params())


def _fmt_date_field(value: Any) -> Optional[str]:
    if value is None:
        return None
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


def _anomaly_balance_tier(
    archetype: str,
    edge: str,
    yield_tier: str,
    gravity: str,
) -> Optional[str]:
    """Within ANOMALY, bucket by common edge/yield/gravity tier (P99…P50) for even showcase spread."""
    if archetype != "ANOMALY":
        return None
    if edge == yield_tier == gravity and edge in _ANOMALY_SUBTIER_OPTIONS:
        return edge
    return None


def _normalize_proxy_wallet_for_compare(addr: Optional[str]) -> Optional[str]:
    raw = str(addr or "").strip()
    if not raw.startswith("0x") or len(raw) != 42:
        return None
    body = raw[2:]
    if not all(c in "0123456789abcdefABCDEF" for c in body):
        return None
    return raw.lower()


def _resolve_card_claim_type(winner_proxy_wallet: Optional[str], session_signin_proxy_wallet: Optional[str]) -> str:
    w = _normalize_proxy_wallet_for_compare(winner_proxy_wallet)
    c = _normalize_proxy_wallet_for_compare(session_signin_proxy_wallet)
    if w and c and w == c:
        return "origin"
    return "looter"


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


def _border_css_color(yield_tier: str) -> str:
    return _PTIER_CSS_COLORS.get(str(yield_tier or "BASE").upper(), "#B6BBC8")


def _build_card_payload_from_source_row(
    row: Dict[str, Any],
    *,
    session_signin_proxy_wallet: Optional[str] = None,
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
    recurrence_out: Optional[str]
    if rec_raw is None:
        recurrence_out = None
    else:
        recurrence_out = str(rec_raw).strip() or None

    return {
        "season_type": _normalize_choice(row.get("season_type"), CARD_SEASON_TYPE_OPTIONS, "standard").lower(),
        "season_number": int(row.get("season_number") or 1),
        "recurrence": recurrence_out,
        "claim_type": _resolve_card_claim_type(
            str(row.get("proxy_wallet") or "").strip() or None,
            str(session_signin_proxy_wallet or "").strip() or None,
        ),
        "image_url": str(row.get("manual_image_url") or "").strip(),
        "card_title": str(row.get("card_title") or "").strip(),
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
            GENESIS_START_DATE
            if _normalize_choice(row.get("season_type"), CARD_SEASON_TYPE_OPTIONS, "standard").lower() == "genesis"
            and GENESIS_START_DATE
            else _fmt_date_field(row.get("season_start_date"))
        ),
        "season_end_date": (
            GENESIS_END_DATE
            if _normalize_choice(row.get("season_type"), CARD_SEASON_TYPE_OPTIONS, "standard").lower() == "genesis"
            and GENESIS_END_DATE
            else _fmt_date_field(row.get("season_end_date"))
        ),
        "season_size": row.get("season_size"),
    }


def _generated_card_slug(season_type: Optional[str], season_number: Any) -> str:
    normalized_type = "".join(
        ch.lower() if ch.isalnum() else "-"
        for ch in str(season_type or "season").strip()
    ).strip("-") or "season"
    try:
        normalized_number = str(int(season_number))
    except Exception:
        normalized_number = "0"
    random_chunk = secrets.token_hex(16)
    uuid_chunk = uuid.uuid4().hex
    return f"card-{normalized_type}-s{normalized_number}-{random_chunk}-{uuid_chunk}"


def _count_winner_catalog_join(cursor: Any) -> int:
    cursor.execute(
        f"""
        SELECT COUNT(*) AS total_count
        {_WINNER_CATALOG_JOIN}
        """
    )
    row = cursor.fetchone()
    raw = (row.get("total_count") if isinstance(row, dict) else row[0]) if row else 0
    return int(raw or 0)


def _winner_catalog_join_total_cached(cursor: Any) -> int:
    global _winner_catalog_join_total_cache
    now = monotonic()
    with _winner_catalog_join_total_lock:
        ts, cached_total = _winner_catalog_join_total_cache
        if ts > 0 and (now - ts) < USER_WEB_CARD_SUPPLY_JOIN_TOTAL_CACHE_TTL:
            return cached_total
    total = _count_winner_catalog_join(cursor)
    with _winner_catalog_join_total_lock:
        _winner_catalog_join_total_cache = (monotonic(), total)
    return total


def _generated_cards_supply_counts(
    cursor: Any,
    *,
    use_cached_join_total: bool = True,
) -> Tuple[int, int]:
    if use_cached_join_total:
        total_available = _winner_catalog_join_total_cached(cursor)
    else:
        total_available = _count_winner_catalog_join(cursor)
    cursor.execute("SELECT COUNT(*) AS claimed_count FROM user_generated_cards")
    claimed_row = cursor.fetchone()
    claimed_count = int(
        (claimed_row.get("claimed_count") if isinstance(claimed_row, dict) else claimed_row[0]) if claimed_row else 0
    )
    remaining_available = max(total_available - claimed_count, 0)
    return total_available, remaining_available


def _pick_eligible_winner_row_id(cursor: Any) -> Optional[int]:
    cursor.execute(
        f"""
        SELECT w.season_id AS season_id, COUNT(*)::bigint AS eligible_count
        {_ELIGIBLE_WINNER_BASE}
        GROUP BY w.season_id
        ORDER BY w.season_id
        """
    )
    season_rows = cursor.fetchall()
    if not season_rows:
        return None

    parsed: List[Tuple[Optional[int], int]] = []
    total = 0
    for r in season_rows:
        sid_raw = r.get("season_id") if isinstance(r, dict) else r[0]
        c_raw = r.get("eligible_count") if isinstance(r, dict) else r[1]
        c = int(c_raw or 0)
        if c <= 0:
            continue
        sid: Optional[int] = int(sid_raw) if sid_raw is not None else None
        parsed.append((sid, c))
        total += c
    if total <= 0 or not parsed:
        return None

    pick = secrets.randbelow(total)
    cumulative = 0
    chosen_season: Optional[int]
    for sid, c in parsed:
        if pick < cumulative + c:
            chosen_season = sid
            break
        cumulative += c
    else:
        return None

    season_filter = "AND w.season_id IS NULL" if chosen_season is None else "AND w.season_id = %s"
    season_params: Tuple[Any, ...] = () if chosen_season is None else (chosen_season,)

    for _ in range(8):
        cursor.execute(
            f"""
            SELECT w.id
            {_ELIGIBLE_WINNER_BASE}
              {season_filter}
            ORDER BY RANDOM()
            LIMIT 1
            FOR UPDATE OF w SKIP LOCKED
            """,
            season_params,
        )
        row = cursor.fetchone()
        if row:
            rid = row.get("id") if isinstance(row, dict) else row[0]
            return int(rid)
    return None


def _count_eligible_showcase_candidates(cursor: Any) -> int:
    cursor.execute(
        f"""
        SELECT COUNT(*)::bigint AS c
        {_ELIGIBLE_WINNER_BASE}
        """
    )
    row = cursor.fetchone()
    raw = (row.get("c") if isinstance(row, dict) else row[0]) if row else 0
    return int(raw or 0)


def _fetch_eligible_showcase_candidates(cursor: Any, *, max_rows: int) -> List[Dict[str, Any]]:
    total = _count_eligible_showcase_candidates(cursor)
    if total <= 0:
        return []
    select_cols = """
        SELECT
            w.id,
            w.entry_cwap,
            w.total_volume,
            w.total_pnl,
            w.entry_bracket,
            w.edge,
            w.yield,
            w.gravity,
            w.archetype AS archetype_coalesced,
            ec.manual_image_url AS manual_image_url
        """
    if total <= max_rows:
        cursor.execute(select_cols + _SHOWCASE_CANDIDATE_BODY + " ORDER BY w.id")
    else:
        cursor.execute(
            select_cols
            + f"""
        FROM (
            SELECT w.id AS id
            {_ELIGIBLE_WINNER_BASE}
            ORDER BY RANDOM()
            LIMIT %s
        ) si
        JOIN winner_wallets_nft_to_claim w ON w.id = si.id
        JOIN event_cards ec ON ec.event_id = w.event_id
        """,
            (max_rows,),
        )
    rows = cursor.fetchall()
    return [dict(r) for r in rows]


@dataclass(frozen=True)
class _ShowcasePick:
    winner_row_id: int
    image_key: str
    archetype: str
    metrics_quad: Tuple[str, str, str, str]
    anomaly_tier: Optional[str]


def _showcase_pick_from_db_row(row: Dict[str, Any]) -> Optional[_ShowcasePick]:
    try:
        wid = int(row["id"])
    except Exception:
        return None
    url_raw = str(row.get("manual_image_url") or "").strip()
    if not url_raw:
        return None
    image_key = url_raw.lower()
    eb = _normalize_entry_bracket(row.get("entry_bracket"))
    edge = _normalize_choice(row.get("edge"), CARD_TIER_OPTIONS, "BASE")
    yld = _normalize_choice(row.get("yield"), CARD_TIER_OPTIONS, "BASE")
    grav = _normalize_choice(row.get("gravity"), CARD_TIER_OPTIONS, "BASE")
    inferred = _infer_archetype_from_metrics(
        eb,
        edge,
        yld,
        grav,
        row.get("entry_cwap"),
        row.get("total_volume"),
        row.get("total_pnl"),
    )
    arch = _normalize_archetype(row.get("archetype_coalesced"), inferred)
    atier = _anomaly_balance_tier(arch, edge, yld, grav)
    return _ShowcasePick(
        winner_row_id=wid,
        image_key=image_key,
        archetype=arch,
        metrics_quad=(eb, edge, yld, grav),
        anomaly_tier=atier,
    )


def _select_diverse_winner_row_plan(candidates: List[Dict[str, Any]], k: int) -> List[int]:
    """Greedy: new image URL, balance archetypes, balance ANOMALY P99/P90/P70/P50 buckets, new metric quad."""
    picks: List[_ShowcasePick] = []
    seen_ids: set[int] = set()
    for raw in candidates:
        p = _showcase_pick_from_db_row(raw)
        if p is None or p.winner_row_id in seen_ids:
            continue
        seen_ids.add(p.winner_row_id)
        picks.append(p)
    if not picks or k <= 0:
        return []
    k = min(k, len(picks))
    remaining = list(picks)
    selected_images: set[str] = set()
    arch_counts: Dict[str, int] = defaultdict(int)
    anomaly_tier_counts: Dict[str, int] = defaultdict(int)
    used_quads: set[Tuple[str, str, str, str]] = set()
    plan: List[int] = []
    for _ in range(k):
        best_i = 0
        best_key: Tuple[int, int, int, int, int] = (-1, -10**9, -10**9, -1, -1)
        for i, c in enumerate(remaining):
            nu = 1 if c.image_key not in selected_images else 0
            ac = -arch_counts[c.archetype]
            if c.anomaly_tier:
                asub = -anomaly_tier_counts[c.anomaly_tier]
            else:
                asub = 0
            nq = 1 if c.metrics_quad not in used_quads else 0
            tie = secrets.randbelow(1_000_000)
            key = (nu, ac, asub, nq, tie)
            if key > best_key:
                best_key = key
                best_i = i
        c = remaining.pop(best_i)
        plan.append(c.winner_row_id)
        selected_images.add(c.image_key)
        arch_counts[c.archetype] += 1
        if c.anomaly_tier:
            anomaly_tier_counts[c.anomaly_tier] += 1
        used_quads.add(c.metrics_quad)
    return plan


def _try_lock_planned_winner_row_id(cursor: Any, winner_row_id: int) -> Optional[int]:
    cursor.execute(
        f"""
        SELECT w.id
        {_ELIGIBLE_WINNER_BASE}
          AND w.id = %s
        FOR UPDATE OF w SKIP LOCKED
        """,
        (winner_row_id,),
    )
    row = cursor.fetchone()
    if not row:
        return None
    rid = row.get("id") if isinstance(row, dict) else row[0]
    return int(rid)


def _load_card_source_row(cursor: Any, winner_row_id: int) -> Optional[Dict[str, Any]]:
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
            w.archetype AS archetype,
            w.archetype_description AS archetype_description,
            w.archetype_math AS archetype_math,
            w.rarity_bracket AS rarity_bracket,
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
            s.start_date  AS season_start_date,
            s.end_date    AS season_end_date,
            s.total_supply AS season_size
        FROM winner_wallets_nft_to_claim w
        JOIN event_cards ec ON ec.event_id = w.event_id
        LEFT JOIN events e ON e.id = w.event_id
        LEFT JOIN seasons s ON s.id = w.season_id
        LEFT JOIN tags tp ON LOWER(BTRIM(tp.label)) = LOWER(BTRIM(ec.primary_tag))
        WHERE w.id = %s
          AND ec.manual_image_url IS NOT NULL
          AND BTRIM(ec.manual_image_url) <> ''
        LIMIT 1
        """,
        (winner_row_id,),
    )
    row = cursor.fetchone()
    return dict(row) if row else None


def _remote_image_to_data_uri(image_url: str, *, timeout_seconds: Optional[float] = None) -> str:
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
    effective_timeout = POLYMARKET_REQUEST_TIMEOUT_SECONDS if timeout_seconds is None else float(timeout_seconds)
    with urllib.request.urlopen(req, timeout=effective_timeout) as response:
        raw = response.read()
        if not raw:
            raise ValueError("Downloaded image is empty")
        content_type = str(response.headers.get_content_type() or "").strip().lower()

    guessed_type, _ = mimetypes.guess_type(normalized)
    mime_type = content_type if content_type and content_type != "application/octet-stream" else (guessed_type or "image/jpeg")
    encoded = base64.b64encode(raw).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _build_render_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    render_payload = dict(payload or {})
    image_url = str(render_payload.get("image_url") or "").strip()
    if image_url.startswith(("http://", "https://")):
        try:
            render_payload["image_url"] = _remote_image_to_data_uri_cached(
                image_url,
                timeout_seconds=USER_WEB_CARD_IMAGE_TIMEOUT_SECONDS,
            )
        except Exception:
            logger.exception("Failed to embed generated card image url=%s", image_url)
    return render_payload


def _remote_image_to_data_uri_cached(image_url: str, *, timeout_seconds: Optional[float] = None) -> str:
    normalized = str(image_url or "").strip()
    if not normalized.startswith(("http://", "https://")):
        return normalized

    now = monotonic()
    with _embedded_image_cache_lock:
        cached = _embedded_image_cache.get(normalized)
        if cached and (now - cached[0]) < USER_WEB_CARD_IMAGE_DATA_URI_CACHE_TTL:
            return cached[1]

    data_uri = _remote_image_to_data_uri(normalized, timeout_seconds=timeout_seconds)
    with _embedded_image_cache_lock:
        _embedded_image_cache.pop(normalized, None)
        _embedded_image_cache[normalized] = (monotonic(), data_uri)
        while len(_embedded_image_cache) > USER_WEB_CARD_IMAGE_DATA_URI_CACHE_MAX_ITEMS:
            _embedded_image_cache.pop(next(iter(_embedded_image_cache)))
    return data_uri


def _r2_required_env() -> Dict[str, str]:
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


def _get_r2_client() -> Any:
    global _r2_client
    if boto3 is None or Config is None:
        raise ValueError("R2 upload dependencies are missing. Install boto3 and botocore.")
    if _r2_client is not None:
        return _r2_client
    cfg = _r2_required_env()
    _r2_client = boto3.client(
        "s3",
        endpoint_url=cfg["endpoint"],
        aws_access_key_id=cfg["access_key"],
        aws_secret_access_key=cfg["secret_key"],
        region_name="auto",
        config=Config(signature_version="s3v4"),
    )
    return _r2_client


def _generated_card_r2_key(slug: str, side: str) -> str:
    prefix = str(os.getenv("R2_PREFIX", "dev")).strip().strip("/")
    safe_slug = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in str(slug or "").strip())
    safe_side = "front" if side == "front" else "back"
    if prefix:
        return f"{prefix}/cards-images/{safe_slug}/{safe_side}.svg"
    return f"cards-images/{safe_slug}/{safe_side}.svg"


def _delete_r2_object_by_key(key: Optional[str]) -> None:
    if not key:
        return
    try:
        cfg = _r2_required_env()
        _get_r2_client().delete_object(Bucket=cfg["bucket"], Key=key)
    except Exception:
        logger.warning("Could not delete generated card asset from R2 key=%s", key, exc_info=True)


def _upload_generated_card_assets_to_r2(slug: str, front_svg: str, back_svg: str) -> Tuple[str, str, str, str]:
    cfg = _r2_required_env()
    front_key = _generated_card_r2_key(slug, "front")
    back_key = _generated_card_r2_key(slug, "back")
    client = _get_r2_client()
    common_kwargs: Dict[str, Any] = {
        "Bucket": cfg["bucket"],
        "ContentType": "image/svg+xml",
        "CacheControl": "public, max-age=31536000, immutable",
    }

    def _put(key: str, body: str) -> None:
        client.put_object(Key=key, Body=body.encode("utf-8"), **common_kwargs)

    with ThreadPoolExecutor(max_workers=2) as pool:
        f_front = pool.submit(_put, front_key, front_svg)
        f_back = pool.submit(_put, back_key, back_svg)
        f_front.result()
        f_back.result()

    return (
        f"{cfg['public_base_url']}/{front_key}",
        f"{cfg['public_base_url']}/{back_key}",
        front_key,
        back_key,
    )


def run_admin_simulated_card_generations(
    *,
    max_count: int = 50,
    origin_match_fraction: float = 0.1,
    maximum_diversity: bool = True,
    progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    LOOTER_PROXY = "0x000000000000000000000000000000000000dEaD"
    LOOTER_PROXY_ALT = "0x000000000000000000000000000000000000bEef"

    probe = _get_connection()
    try:
        with probe.cursor() as c:
            _, remaining = _generated_cards_supply_counts(c, use_cached_join_total=False)
    finally:
        probe.close()

    remaining_i = int(remaining)
    n = min(max(0, int(max_count)), remaining_i)
    errors: List[str] = []
    out: Dict[str, Any] = {
        "requested": int(max_count),
        "remaining_supply_before": remaining_i,
        "planned": 0,
        "generated": 0,
        "origin_claim_cards": 0,
        "origin_slots_skipped_no_winner_proxy": 0,
        "errors": errors,
        "maximum_diversity": bool(maximum_diversity),
        "showcase_eligible_total": None,
        "showcase_candidate_pool_size": None,
        "showcase_pool_cap": None,
    }

    def emit(stage: str, **extra: Any) -> None:
        if progress_callback is None:
            return
        payload = {
            "stage": stage,
            "requested": int(out["requested"]),
            "remaining_supply_before": int(out["remaining_supply_before"]),
            "planned": int(out["planned"]),
            "generated": int(out["generated"]),
            "origin_claim_cards": int(out["origin_claim_cards"]),
            "origin_slots_skipped_no_winner_proxy": int(out["origin_slots_skipped_no_winner_proxy"]),
            "errors_count": len(errors),
            "maximum_diversity": bool(maximum_diversity),
        }
        payload.update(extra)
        try:
            progress_callback(payload)
        except Exception:
            logger.warning("simulate progress callback failed", exc_info=True)

    emit("started", requested_run_count=n)

    if n <= 0:
        out["stopped_reason"] = "no_remaining_supply" if remaining_i <= 0 else "zero_planned"
        emit("stopped", stopped_reason=out["stopped_reason"])
        return out

    planned_ids: List[int] = []
    if maximum_diversity:
        plan_conn = _get_connection()
        try:
            with plan_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as plan_cur:
                eligible_total = _count_eligible_showcase_candidates(plan_cur)
                out["showcase_eligible_total"] = eligible_total
                candidates_raw = _fetch_eligible_showcase_candidates(
                    plan_cur, max_rows=SIMULATE_SHOWCASE_MAX_CANDIDATES
                )
                out["showcase_candidate_pool_size"] = len(candidates_raw)
                out["showcase_pool_cap"] = SIMULATE_SHOWCASE_MAX_CANDIDATES
                planned_ids = _select_diverse_winner_row_plan(candidates_raw, n)
        finally:
            plan_conn.close()

        n_run = len(planned_ids)
        out["planned"] = n_run
        if n_run <= 0:
            out["stopped_reason"] = "no_eligible_showcase_candidates"
            emit(
                "stopped",
                stopped_reason=out["stopped_reason"],
                showcase_eligible_total=out["showcase_eligible_total"],
                showcase_candidate_pool_size=out["showcase_candidate_pool_size"],
                showcase_pool_cap=out["showcase_pool_cap"],
            )
            return out
    else:
        n_run = n
        out["planned"] = n_run

    emit(
        "planned",
        requested_run_count=n,
        showcase_eligible_total=out["showcase_eligible_total"],
        showcase_candidate_pool_size=out["showcase_candidate_pool_size"],
        showcase_pool_cap=out["showcase_pool_cap"],
    )

    n_origin_target = min(n_run, max(0, int(round(n_run * float(origin_match_fraction)))))
    origin_indices = set(random.sample(range(n_run), k=n_origin_target)) if n_origin_target > 0 else set()

    skipped_no_winner_proxy = 0
    conn = _get_connection()
    try:
        for i in range(n_run):
            want_origin = i in origin_indices
            uploaded_front_key: Optional[str] = None
            uploaded_back_key: Optional[str] = None
            try:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                    if maximum_diversity:
                        planned_winner_row_id = planned_ids[i]
                        winner_row_id = _try_lock_planned_winner_row_id(cursor, planned_winner_row_id)
                        if winner_row_id is None:
                            logger.info(
                                "Showcase plan row id=%s not lockable; falling back to random eligible",
                                planned_winner_row_id,
                            )
                            winner_row_id = _pick_eligible_winner_row_id(cursor)
                    else:
                        winner_row_id = _pick_eligible_winner_row_id(cursor)
                    if winner_row_id is None:
                        out["stopped_reason"] = "no_eligible_winner_mid_run"
                        emit("stopped", stopped_reason=out["stopped_reason"], iteration=i + 1)
                        break

                    source_row = _load_card_source_row(cursor, winner_row_id=winner_row_id)
                    if not source_row:
                        errors.append(f"winner_row_id={winner_row_id}: missing source row")
                        conn.rollback()
                        emit("error", iteration=i + 1, error=errors[-1], winner_row_id=winner_row_id)
                        continue

                    winner_proxy_raw = str(source_row.get("proxy_wallet") or "").strip()
                    w_norm = _normalize_proxy_wallet_for_compare(winner_proxy_raw)

                    if want_origin and w_norm:
                        signin_proxy = winner_proxy_raw
                        owner_proxy_for_db = signin_proxy
                    else:
                        if want_origin and not w_norm:
                            skipped_no_winner_proxy += 1
                        signin_proxy = LOOTER_PROXY
                        if w_norm and signin_proxy.lower() == w_norm:
                            signin_proxy = LOOTER_PROXY_ALT
                        owner_proxy_for_db = signin_proxy

                    fake_eoa = ("0x" + secrets.token_hex(20)).lower()
                    if not Web3.is_address(fake_eoa):
                        raise RuntimeError("internal: invalid synthetic owner wallet")

                    payload = _build_card_payload_from_source_row(
                        source_row,
                        session_signin_proxy_wallet=signin_proxy,
                    )
                    is_origin_claim = str(payload.get("claim_type") or "") == "origin"

                    slug = _generated_card_slug(payload.get("season_type"), payload.get("season_number"))
                    payload["qr_payload"] = f"{CARD_BASE_URL}/cards/{slug}"
                    image_url = str(payload.get("image_url") or "").strip()
                    if not image_url:
                        raise ValueError("Selected row is missing manual image URL")

                    cursor.execute(
                        """
                        INSERT INTO user_generated_cards (
                            slug,
                            owner_wallet,
                            owner_proxy_wallet,
                            winner_row_id,
                            season_id,
                            event_id,
                            event_slug,
                            card_title,
                            primary_tag,
                            secondary_tag,
                            pattern,
                            front_image_path,
                            back_image_path,
                            card_payload_json
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb
                        )
                        RETURNING
                            id,
                            collection_mint_number,
                            slug,
                            owner_wallet,
                            owner_proxy_wallet,
                            winner_row_id,
                            season_id,
                            event_id,
                            event_slug,
                            card_title,
                            primary_tag,
                            secondary_tag,
                            pattern,
                            front_image_path,
                            back_image_path,
                            card_payload_json,
                            created_at
                        """,
                        (
                            slug,
                            fake_eoa,
                            owner_proxy_for_db,
                            winner_row_id,
                            int(source_row.get("season_id") or 0),
                            source_row.get("event_id"),
                            source_row.get("event_slug"),
                            str(payload.get("card_title") or ""),
                            str(payload.get("primary_tag") or ""),
                            str(payload.get("secondary_tag") or ""),
                            None,
                            "",
                            "",
                            json.dumps(payload),
                        ),
                    )
                    created_row = cursor.fetchone()
                    if not created_row:
                        raise RuntimeError("INSERT returned no row")

                    payload["collection_mint_number"] = created_row["collection_mint_number"]
                    render_payload = _build_render_payload(payload)
                    front_svg = generate_card_svg(render_payload)
                    back_svg = generate_card_back_svg(render_payload)
                    front_image_path, back_image_path, uploaded_front_key, uploaded_back_key = (
                        _upload_generated_card_assets_to_r2(slug, front_svg, back_svg)
                    )

                    cursor.execute(
                        """
                        UPDATE user_generated_cards
                        SET front_image_path = %s,
                            back_image_path  = %s,
                            card_payload_json = %s::jsonb
                        WHERE slug = %s
                        """,
                        (front_image_path, back_image_path, json.dumps(payload), slug),
                    )
                conn.commit()
                out["generated"] = int(out["generated"]) + 1
                if is_origin_claim:
                    out["origin_claim_cards"] = int(out["origin_claim_cards"]) + 1
                out["origin_slots_skipped_no_winner_proxy"] = skipped_no_winner_proxy
                emit(
                    "progress",
                    iteration=i + 1,
                    current_chunk_size=n_run,
                    winner_row_id=winner_row_id,
                    slug=slug,
                )
            except Exception as exc:
                conn.rollback()
                _delete_r2_object_by_key(uploaded_front_key)
                _delete_r2_object_by_key(uploaded_back_key)
                errors.append(str(exc))
                out["origin_slots_skipped_no_winner_proxy"] = skipped_no_winner_proxy
                emit("error", iteration=i + 1, error=errors[-1])
                logger.exception("Admin simulate card generation failed on iteration i=%s", i)
    finally:
        conn.close()

    out["origin_slots_skipped_no_winner_proxy"] = skipped_no_winner_proxy

    probe2 = _get_connection()
    try:
        with probe2.cursor() as c2:
            _, rem_after = _generated_cards_supply_counts(c2, use_cached_join_total=False)
    finally:
        probe2.close()
    out["remaining_supply_after"] = int(rem_after)
    if "stopped_reason" not in out and out["generated"] < n_run and not errors:
        out["stopped_reason"] = "completed_short"
    emit(
        "completed" if "stopped_reason" not in out else "stopped",
        remaining_supply_after=out["remaining_supply_after"],
        stopped_reason=out.get("stopped_reason"),
        showcase_eligible_total=out["showcase_eligible_total"],
        showcase_candidate_pool_size=out["showcase_candidate_pool_size"],
        showcase_pool_cap=out["showcase_pool_cap"],
    )
    return out
