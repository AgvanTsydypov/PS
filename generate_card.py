#!/usr/bin/env python3
"""
generate_card.py — Pixel-perfect SVG card generator for Polystars NFT cards.

Reads card data (Python dict) and generates a standards-compliant SVG
matching the Figma design spec (polystars_card_svg_V3.md).

Usage:
    python generate_card.py            # writes output.svg with sample data
    python generate_card.py data.json  # reads card data from JSON file
"""

import base64
import html
import json
import sys
from pathlib import Path
from typing import Any, Dict, Tuple


# ═══════════════════════════════════════════════════════════════════════════
# LAYOUT CONSTANTS — Figma JSON coordinates, adjusted for SVG text-anchor
# ═══════════════════════════════════════════════════════════════════════════

CANVAS_W, CANVAS_H = 516, 802

# Border glow rect (filled with YIELD color, blurred)
FRAME_X, FRAME_Y, FRAME_W, FRAME_H, FRAME_RX = 3, 3, 510, 796, 30

# Event image
IMG_X, IMG_Y, IMG_W, IMG_H, IMG_RX = 13, 13, 490, 490, 23

# Logo overlay (top-left of image zone)
LOGO_X, LOGO_Y, LOGO_W, LOGO_H = 19, 19, 62, 67
LOGO_HREF = "logo.svg"

# Metadata badge (top-right)
BADGE_X, BADGE_Y, BADGE_W, BADGE_H, BADGE_RX = 314, 22, 180, 62, 10
BADGE_CX = BADGE_X + BADGE_W // 2  # 404

# Title bar (overlaps image/data boundary)
TB_X, TB_Y, TB_W, TB_H, TB_RX = 29, 472, 458, 61, 9
TB_CX = TB_X + TB_W // 2  # 258

# Data zone
DZ_X, DZ_Y, DZ_W, DZ_H, DZ_RX = 13, 503, 490, 286, 22
DZ_CX = DZ_X + DZ_W // 2  # 258

# Metric column centers — computed from Figma label+value bounding boxes
COL_EDGE    = 130   # label x=72 w=116 → center 130; value x=104 w=53 → 130.5
COL_YIELD   = 263   # label x=212 w=103 → 263.5; value x=237 w=53 → 263.5
COL_GRAVITY = 391   # label x=339 w=105 → 391.5; value x=365 w=53 → 391.5

# Y-coordinates for every text anchor (from Figma JSON)
Y_SEASON        = 31
Y_INSTANCE      = 47
Y_OWNERSHIP     = 63
Y_TITLE_TEXT    = 495   # vertically centered in 61px title bar: 472 + (61-16)/2 ≈ 495
Y_SECTOR        = 548
Y_NODE          = 579
Y_UPPER_SEP     = 608   # verified against all 3 reference SVGs
Y_COG_TEL       = 617
Y_BRACKET       = 654
Y_WALLET        = 655
Y_METRIC_LABELS = 686
Y_METRIC_VALUES = 707
Y_LOWER_SEP     = 743   # verified against all 3 reference SVGs
Y_FOOTER        = 754

# Separator endpoints (from reference SVGs)
UPPER_SEP_X1, UPPER_SEP_X2 = 58, 458
LOWER_SEP_X1, LOWER_SEP_X2 = 57, 457

# Orbitron Bold WOFF2 — resolved relative to this script
_FONT_PATH = Path(__file__).resolve().parent / "orbitron-bold.woff2"

# Corner dots flanking the upper separator
DOT_LEFT_X  = 54
DOT_RIGHT_X = 458
DOT_Y       = 606
DOT_SZ      = 4


# ═══════════════════════════════════════════════════════════════════════════
# COLOR SYSTEM (Section 4 of the spec)
# ═══════════════════════════════════════════════════════════════════════════

_GRAD = "url(#master-gradient)"

ENTRY_BRACKET_COLORS: Dict[str, str] = {
    "ANOMALY":   _GRAD,
    "ORACLE":    "#FFBF00",
    "OUTLIER":   "#0051FF",
    "VECTOR":    "#00FF2F",
    "HARVESTER": "#FFFFFF",
}

PTIER_COLORS: Dict[str, str] = {
    "P999": _GRAD,
    "P99":  _GRAD,
    "P95":  "#FFBF00",
    "P90":  "#FFBF00",
    "P80":  "#0051FF",
    "P70":  "#0051FF",
    "P50":  "#00FF2F",
    "BASE": "#FFFFFF",
}

# Entry bracket → equivalent P-Tier position (for UNIFORM detection)
_BRACKET_EQUIV: Dict[str, str] = {
    "ANOMALY":   "P99",
    "ORACLE":    "P90",
    "OUTLIER":   "P70",
    "VECTOR":    "P50",
    "HARVESTER": "BASE",
}


def get_bracket_color(name: str) -> str:
    return ENTRY_BRACKET_COLORS.get(name.upper(), "#FFFFFF")


def get_ptier_color(tier: str) -> str:
    return PTIER_COLORS.get(tier.upper(), "#FFFFFF")


# ═══════════════════════════════════════════════════════════════════════════
# PATTERN DETECTION (Section 10.4)
# ═══════════════════════════════════════════════════════════════════════════

def detect_pattern(data: Dict[str, Any]) -> str:
    """Return the data-zone background pattern name. Priority order per spec."""
    eb   = data.get("entry_bracket", "").upper()
    edge = data.get("edge", "").upper()
    yld  = data.get("yield", "").upper()
    grav = data.get("gravity", "").upper()

    bp = _BRACKET_EQUIV.get(eb, "BASE")

    # Priority 1 — UNIFORM: all 4 axes at same equivalent tier, tier ≠ Base
    if bp != "BASE" and edge == bp and yld == bp and grav == bp:
        return "UNIFORM"

    # Priority 2 — SIGNAL
    high = ("P99", "P90")
    if eb in ("ANOMALY", "ORACLE") and edge in high and yld in high:
        return "SIGNAL"

    # Priority 3 — CONTRARIAN
    if eb == "OUTLIER" and edge in high and yld in high:
        return "CONTRARIAN"

    # Priority 4 — EQUILIBRIUM
    top3 = ("P99", "P90", "P70")
    if edge in top3 and yld in top3 and grav in top3:
        return "EQUILIBRIUM"

    # Priority 5 — LIQUIDATOR
    low = ("BASE", "P50")
    if eb in ("VECTOR", "HARVESTER") and grav in high and edge in low and yld in low:
        return "LIQUIDATOR"

    return "DEFAULT"


# ═══════════════════════════════════════════════════════════════════════════
# DATA ZONE STYLE RESOLUTION (Section 10.2)
# ═══════════════════════════════════════════════════════════════════════════

_DZ_STYLES: Dict[str, Tuple[str, str, bool]] = {
    #               (fill,                       stroke,    is_signal)
    "DEFAULT":     ("#1C1B1B",                   "#FFFFFF", False),
    "LIQUIDATOR":  ("#3B2647",                   "#FFFFFF", False),
    "EQUILIBRIUM": ("#474332",                   "#FFFFFF", False),
    "SIGNAL":      ("#CDD2DE",                   "#000000", True),
    "CONTRARIAN":  ("#0A2A2A",                   "#FFFFFF", False),
    "UNIFORM":     ("url(#uniform-gradient)",    "#000000", False),
}


def dz_style(pattern: str) -> Tuple[str, str, bool]:
    """Return (fill, stroke, is_signal) for the data zone background."""
    return _DZ_STYLES.get(pattern, _DZ_STYLES["DEFAULT"])


# ═══════════════════════════════════════════════════════════════════════════
# METADATA HELPERS (Section 5)
# ═══════════════════════════════════════════════════════════════════════════

def _season(data: Dict[str, Any]) -> Tuple[str, str]:
    if data.get("season_type", "").lower() == "genesis":
        return "GENESIS", "#00FFFF"
    n = data.get("season_number", 1)
    return f"STANDARD #{n}", "#B1ABAB"


def _instance(data: Dict[str, Any]) -> Tuple[str, str]:
    rec = data.get("recurrence")
    if rec and str(rec).lower() not in ("null", "none", ""):
        return "FRACTAL", "#2A8FEE"
    return "SINGULAR", "#E8A72F"


def _ownership(data: Dict[str, Any]) -> Tuple[str, str]:
    if data.get("claim_type", "").lower() == "origin":
        return "ORIGIN SECURED", "#06EE6E"
    return "HOSTILE TAKEOVER", "#FF007F"


def _wallet_display(addr: str) -> str:
    return (addr[:9] + "...") if len(addr) > 9 else addr


# ═══════════════════════════════════════════════════════════════════════════
# SVG ASSEMBLY
# ═══════════════════════════════════════════════════════════════════════════

def _esc(text: str) -> str:
    """XML-escape user-supplied content."""
    return html.escape(str(text), quote=True)


def _sig_attrs(is_signal: bool) -> str:
    """Extra stroke for text legibility on SIGNAL's light background."""
    return ' stroke="black" stroke-width="0.5" paint-order="stroke"' if is_signal else ""


_SCRIPT_DIR = Path(__file__).resolve().parent

_MIME_MAP = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".png": "image/png",  ".gif": "image/gif",
    ".svg": "image/svg+xml", ".webp": "image/webp",
    ".woff2": "font/woff2",
}


def _load_font_b64() -> str:
    """Return Orbitron Bold WOFF2 as a base64 string for inline embedding."""
    if _FONT_PATH.exists():
        return base64.b64encode(_FONT_PATH.read_bytes()).decode("ascii")
    return ""


def _to_data_uri(href: str) -> str:
    """Convert a local file path to a base64 data URI for inline embedding.
    Returns the original href unchanged if the file doesn't exist or is already a URL."""
    if href.startswith(("http://", "https://", "data:")):
        return href
    p = _SCRIPT_DIR / href
    if not p.exists():
        return href
    mime = _MIME_MAP.get(p.suffix.lower(), "application/octet-stream")
    b64 = base64.b64encode(p.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def generate_card_svg(data: Dict[str, Any]) -> str:
    """Build a complete front-of-card SVG string from a card data dict."""

    # ── 1. Resolve pattern & data zone style ──────────────────────────
    pattern = detect_pattern(data)
    dz_fill, dz_stroke, is_signal = dz_style(pattern)

    # ── 2. Resolve tier colors ────────────────────────────────────────
    eb   = data.get("entry_bracket", "HARVESTER").upper()
    edge = data.get("edge", "BASE").upper()
    yld  = data.get("yield", "BASE").upper()
    grav = data.get("gravity", "BASE").upper()

    bracket_color = get_bracket_color(eb)
    edge_color    = get_ptier_color(edge)
    yield_color   = get_ptier_color(yld)
    gravity_color = get_ptier_color(grav)

    # ── 3. Propagation (Section 9) ────────────────────────────────────
    wallet_color = edge_color           # EDGE → wallet
    border_color = yield_color          # YIELD → outer glow
    dotted_color = gravity_color        # GRAVITY → lower dotted line

    # ── 4. Metadata ───────────────────────────────────────────────────
    season_val,    season_clr    = _season(data)
    instance_val,  instance_clr  = _instance(data)
    ownership_val, ownership_clr = _ownership(data)

    # ── 5. Text content ──────────────────────────────────────────────
    title       = _esc(data.get("card_title", "UNTITLED EVENT").upper())
    sector      = _esc(data.get("primary_tag", "UNKNOWN").upper())
    sector_clr  = data.get("primary_tag_color", "#FFFFFF")
    node        = _esc(data.get("secondary_tag", "NONE").upper())
    wallet_disp = _esc(_wallet_display(data.get("proxy_wallet", "0x00000000")))
    rank        = data.get("leaderboard_rank", 0)
    rank_str    = _esc(f"#{rank}")
    image_url   = _to_data_uri(data.get("image_url", ""))
    logo_href   = _to_data_uri(LOGO_HREF)

    sig = _sig_attrs(is_signal)

    # ── 6. Build SVG string ──────────────────────────────────────────
    font_b64 = _load_font_b64()
    parts: list[str] = []

    # Build the font CSS — embedded @font-face if woff2 available, else @import fallback
    if font_b64:
        font_css = (
            f"@font-face {{\n"
            f"      font-family: 'Orbitron';\n"
            f"      font-style: normal;\n"
            f"      font-weight: 700;\n"
            f"      src: url('data:font/woff2;base64,{font_b64}') format('woff2');\n"
            f"    }}"
        )
    else:
        font_css = "@import url('https://fonts.googleapis.com/css2?family=Orbitron:wght@700&amp;display=swap');"

    # ---- SVG root & defs ----
    parts.append(f'''<svg width="{CANVAS_W}" height="{CANVAS_H}"
     viewBox="0 0 {CANVAS_W} {CANVAS_H}"
     fill="none"
     xmlns="http://www.w3.org/2000/svg"
     xmlns:xlink="http://www.w3.org/1999/xlink">

<defs>
  <style>
    {font_css}
    text {{
      font-family: 'Orbitron', sans-serif;
      font-weight: 700;
      letter-spacing: 0.1em;
    }}
  </style>

  <!-- P99 / Anomaly master gradient (Section 3) -->
  <linearGradient id="master-gradient" x1="0.5" y1="0" x2="0.45" y2="1">
    <stop offset="0%"  stop-color="#FFFFFF"/>
    <stop offset="20%" stop-color="#51FF48"/>
    <stop offset="40%" stop-color="#0051FF"/>
    <stop offset="60%" stop-color="#8A2BE2"/>
    <stop offset="80%" stop-color="#FFBF00"/>
  </linearGradient>

  <!-- UNIFORM data zone background (Section 10.2) -->
  <linearGradient id="uniform-gradient" x1="0.5" y1="0" x2="0.5" y2="1">
    <stop offset="15%" stop-color="#28AEAE"/>
    <stop offset="30%" stop-color="#4A99BB"/>
    <stop offset="45%" stop-color="#8C92D1"/>
    <stop offset="60%" stop-color="#C38CCE"/>
    <stop offset="75%" stop-color="#BB7382"/>
    <stop offset="90%" stop-color="#C5CC84"/>
  </linearGradient>

  <!-- COGNITIVE TELEMETRY radial gradient (Section 7.3) — horizontally stretched ellipse -->
  <radialGradient id="cognitive-gradient" cx="0" cy="0" r="1"
                  gradientUnits="userSpaceOnUse"
                  gradientTransform="translate({DZ_CX} {Y_COG_TEL + 11.5}) scale(140 11.5)">
    <stop offset="0.144231" stop-color="#EFE2E2"/>
    <stop offset="1"        stop-color="#686262"/>
  </radialGradient>

  <!-- Border glow blur (2px stdDev = 4px Figma blur) -->
  <filter id="glow" x="-1" y="-1"
          width="{CANVAS_W + 2}" height="{CANVAS_H + 2}"
          filterUnits="userSpaceOnUse" color-interpolation-filters="sRGB">
    <feGaussianBlur stdDeviation="2"/>
  </filter>

  <!-- Drop shadow: 0 4px 4px rgba(0,0,0,0.25) -->
  <filter id="shadow" x="-20%" y="-20%" width="140%" height="160%"
          color-interpolation-filters="sRGB">
    <feFlood flood-opacity="0" result="bg"/>
    <feColorMatrix in="SourceAlpha" type="matrix"
        values="0 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 127 0"
        result="alpha"/>
    <feOffset dy="4" result="off"/>
    <feGaussianBlur in="off" stdDeviation="2" result="blur"/>
    <feComposite in="blur" in2="alpha" operator="out" result="s"/>
    <feColorMatrix in="s" type="matrix"
        values="0 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 0.25 0"/>
    <feMerge>
      <feMergeNode/>
      <feMergeNode in="SourceGraphic"/>
    </feMerge>
  </filter>

  <!-- Image zone clip (rounded corners) -->
  <clipPath id="img-clip">
    <rect x="{IMG_X}" y="{IMG_Y}"
          width="{IMG_W}" height="{IMG_H}" rx="{IMG_RX}"/>
  </clipPath>
</defs>''')

    # ---- Layer 1: Background canvas ----
    parts.append(f'''
<!-- ══ BACKGROUND ══ -->
<rect width="{CANVAS_W}" height="{CANVAS_H}" rx="33" fill="#0B0C10"/>''')

    # ---- Layer 2: Border glow (YIELD color, blurred) ----
    parts.append(f'''
<!-- ══ BORDER GLOW (driven by YIELD = {yld}) ══ -->
<g filter="url(#glow)">
  <rect x="{FRAME_X}" y="{FRAME_Y}"
        width="{FRAME_W}" height="{FRAME_H}"
        rx="{FRAME_RX}" fill="{border_color}"/>
</g>''')

    # ---- Layer 3: Event image ----
    parts.append(f'''
<!-- ══ EVENT IMAGE ══ -->
<image x="{IMG_X}" y="{IMG_Y}" width="{IMG_W}" height="{IMG_H}"
       href="{image_url}" clip-path="url(#img-clip)"
       preserveAspectRatio="xMidYMid slice"/>''')

    # ---- Layer 4: Logo overlay ----
    parts.append(f'''
<!-- ══ LOGO ══ -->
<image x="{LOGO_X}" y="{LOGO_Y}" width="{LOGO_W}" height="{LOGO_H}"
       href="{logo_href}"/>''')

    # ---- Layer 5: Metadata badge ----
    parts.append(f'''
<!-- ══ METADATA BADGE ══ -->
<rect opacity="0.7" x="{BADGE_X + 0.5}" y="{BADGE_Y + 0.5}"
      width="{BADGE_W - 1}" height="{BADGE_H - 1}"
      rx="{BADGE_RX - 0.5}" fill="black" stroke="#666666"/>

<text x="{BADGE_CX}" y="{Y_SEASON}"
      text-anchor="middle" dominant-baseline="hanging"
      font-size="10">
  <tspan fill="white">SEASON: </tspan>
  <tspan fill="{season_clr}">{_esc(season_val)}</tspan>
</text>

<text x="{BADGE_CX}" y="{Y_INSTANCE}"
      text-anchor="middle" dominant-baseline="hanging"
      font-size="10">
  <tspan fill="white">INSTANCE: </tspan>
  <tspan fill="{instance_clr}">{_esc(instance_val)}</tspan>
</text>

<text x="{BADGE_CX}" y="{Y_OWNERSHIP}"
      text-anchor="middle" dominant-baseline="hanging"
      font-size="10">
  <tspan fill="white">[ </tspan>
  <tspan fill="{ownership_clr}">{_esc(ownership_val)}</tspan>
  <tspan fill="white"> ]</tspan>
</text>''')

    # ---- Layer 6: Data zone background ----
    parts.append(f'''
<!-- ══ DATA ZONE (pattern: {pattern}) ══ -->
<rect x="{DZ_X + 0.5}" y="{DZ_Y + 0.5}"
      width="{DZ_W - 1}" height="{DZ_H - 1}"
      rx="{DZ_RX - 0.5}" fill="{dz_fill}" stroke="{dz_stroke}"/>''')

    # ---- Layer 7: Title bar ----
    parts.append(f'''
<!-- ══ TITLE BAR ══ -->
<rect x="{TB_X}" y="{TB_Y}" width="{TB_W}" height="{TB_H}"
      rx="{TB_RX}" fill="#171717" fill-opacity="0.95"
      stroke="#333333" stroke-width="2"/>

<text x="{TB_CX}" y="{Y_TITLE_TEXT}"
      text-anchor="middle" dominant-baseline="hanging"
      font-size="16" fill="white">
  {title}
</text>''')

    # ---- Layer 8: SECTOR ----
    parts.append(f'''
<!-- ══ SECTOR ══ -->
<g filter="url(#shadow)">
  <text x="{DZ_CX}" y="{Y_SECTOR}"
        text-anchor="middle" dominant-baseline="hanging"
        font-size="18"{sig}>
    <tspan fill="white">SECTOR: </tspan>
    <tspan fill="{sector_clr}">{sector}</tspan>
  </text>
</g>''')

    # ---- Layer 9: NODE ----
    parts.append(f'''
<!-- ══ NODE ══ -->
<g filter="url(#shadow)">
  <text x="{DZ_CX}" y="{Y_NODE}"
        text-anchor="middle" dominant-baseline="hanging"
        font-size="16" fill="#888888" letter-spacing="0"{sig}>
    NODE: {node}
  </text>
</g>''')

    # ---- Layer 10: Upper separator + corner dots ----
    parts.append(f'''
<!-- ══ UPPER SEPARATOR ══ -->
<line x1="{UPPER_SEP_X1}" y1="{Y_UPPER_SEP}"
      x2="{UPPER_SEP_X2}" y2="{Y_UPPER_SEP}"
      stroke="#333333" stroke-width="2"/>
<rect x="{DOT_LEFT_X}" y="{DOT_Y}"
      width="{DOT_SZ}" height="{DOT_SZ}" fill="#333333"/>
<rect x="{DOT_RIGHT_X}" y="{DOT_Y}"
      width="{DOT_SZ}" height="{DOT_SZ}" fill="#333333"/>''')

    # ---- Layer 11: COGNITIVE TELEMETRY ----
    parts.append(f'''
<!-- ══ COGNITIVE TELEMETRY ══ -->
<text x="{DZ_CX}" y="{Y_COG_TEL}"
      text-anchor="middle" dominant-baseline="hanging"
      font-size="20"
      fill="url(#cognitive-gradient)"{sig}>
  COGNITIVE TELEMETRY
</text>''')

    # ---- Layer 12: Entry bracket + wallet ----
    parts.append(f'''
<!-- ══ ENTRY BRACKET + WALLET ══ -->
<text x="{DZ_CX}" y="{Y_BRACKET}"
      text-anchor="middle" dominant-baseline="hanging"
      font-size="15"{sig}>
  <tspan fill="white">[ </tspan>
  <tspan fill="{bracket_color}">{eb}</tspan>
  <tspan fill="white"> ] :// </tspan>
  <tspan fill="{wallet_color}" font-size="14">{wallet_disp}</tspan>
</text>''')

    # ---- Layer 13: Metric labels (EDGE / YIELD / GRAVITY) ----
    parts.append(f'''
<!-- ══ METRIC LABELS ══ -->
<g filter="url(#shadow)">
  <text x="{COL_EDGE}" y="{Y_METRIC_LABELS}"
        text-anchor="middle" dominant-baseline="hanging"
        font-size="12" fill="white"{sig}>
    EDGE:
  </text>
  <text x="{COL_YIELD}" y="{Y_METRIC_LABELS}"
        text-anchor="middle" dominant-baseline="hanging"
        font-size="12" fill="white"{sig}>
    YIELD:
  </text>
  <text x="{COL_GRAVITY}" y="{Y_METRIC_LABELS}"
        text-anchor="middle" dominant-baseline="hanging"
        font-size="12" fill="white"{sig}>
    GRAVITY:
  </text>
</g>''')

    # ---- Layer 14: Metric values ----
    parts.append(f'''
<!-- ══ METRIC VALUES ══ -->
<text x="{COL_EDGE}" y="{Y_METRIC_VALUES}"
      text-anchor="middle" dominant-baseline="hanging"
      font-size="20" fill="{edge_color}"{sig}>
  {edge}
</text>
<text x="{COL_YIELD}" y="{Y_METRIC_VALUES}"
      text-anchor="middle" dominant-baseline="hanging"
      font-size="20" fill="{yield_color}"{sig}>
  {yld}
</text>
<text x="{COL_GRAVITY}" y="{Y_METRIC_VALUES}"
      text-anchor="middle" dominant-baseline="hanging"
      font-size="20" fill="{gravity_color}"{sig}>
  {grav}
</text>''')

    # ---- Layer 15: Lower dotted separator (GRAVITY-colored, no filter) ----
    parts.append(f'''
<!-- ══ LOWER SEPARATOR (driven by GRAVITY = {grav}) ══ -->
<line x1="{LOWER_SEP_X1}" y1="{Y_LOWER_SEP}"
      x2="{LOWER_SEP_X2}" y2="{Y_LOWER_SEP}"
      stroke="{dotted_color}" stroke-width="2"
      stroke-dasharray="2 4"/>''')

    # ---- Layer 16: Footer (POLYMARKET GLOBAL RANK) ----
    parts.append(f'''
<!-- ══ FOOTER ══ -->
<g filter="url(#shadow)">
  <text x="{UPPER_SEP_X1}" y="{Y_FOOTER}"
        dominant-baseline="hanging"
        font-size="16"{sig}>
    <tspan fill="#2E5CFF">POLYMARKET</tspan>
    <tspan fill="white"> GLOBAL RANK:</tspan>
  </text>
  <text x="{UPPER_SEP_X2}" y="{Y_FOOTER}"
        text-anchor="end" dominant-baseline="hanging"
        font-size="16" fill="white"{sig}>
    {rank_str}
  </text>
</g>''')

    # ---- Layer 17: Structural outer border (always #333333) ----
    parts.append(f'''
<!-- ══ STRUCTURAL BORDER ══ -->
<rect x="1.5" y="1.5" width="513" height="799" rx="31.5"
      stroke="#333333" stroke-width="3" fill="none"/>

</svg>''')

    return "\n".join(parts)


# ═══════════════════════════════════════════════════════════════════════════
# SAMPLE DATA (mirrors the data contract in Section 11)
# ═══════════════════════════════════════════════════════════════════════════

SAMPLE_DATA: Dict[str, Any] = {
    "season_type":       "standard",
    "season_number":     3,
    "recurrence":        None,
    "claim_type":        "looter",
    "image_url":         "sample.jpg",
    "card_title":        "ZELENSKYY SUIT WATCH JUN 2025",
    "primary_tag":       "CELEBRITIES",
    "primary_tag_color": "#51E147",
    "secondary_tag":     "NONE",
    "entry_bracket":     "ORACLE",
    "proxy_wallet":      "0xBb8E703abc123def456",
    "edge":              "P99",
    "yield":             "P90",
    "gravity":           "P50",
    "leaderboard_rank":  63564,
}


# ═══════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    if len(sys.argv) > 1:
        src = sys.argv[1]
        with open(src, encoding="utf-8") as f:
            card_data = json.load(f)
        print(f"Loaded card data from {src}")
    else:
        card_data = SAMPLE_DATA
        print("Using built-in sample data")

    svg = generate_card_svg(card_data)

    out_path = "output.svg"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(svg)

    detected = detect_pattern(card_data)
    print(f"Pattern detected: {detected}")
    print(f"SVG written to:   {out_path}")
