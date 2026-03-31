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
import math
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
# GRADIENT ANGLES  — degrees from vertical  (0 = pure top→bottom)
# Positive values tilt clockwise (gold shifts to top-left corner).
# Each group is independent so you can tune them separately.
# ═══════════════════════════════════════════════════════════════════════════

GRAD_ANGLE_ANOMALY = 1.5  # [ BRACKET ] label
GRAD_ANGLE_WALLET  = 1  # wallet address
GRAD_ANGLE_P99     = 2.5  # EDGE / YIELD / GRAVITY metric values
GRAD_ANGLE_BORDER  = 12   # card border glow rect


def _grad_pts(cx: float, cy: float, hh: float, angle_deg: float = 0.0):
    """Return (x1, y1, x2, y2) for a gradient centred at (cx, cy).

    The gradient runs from top to bottom (gold→white) spanning ±hh pixels,
    tilted *angle_deg* degrees from vertical (in screen-pixel space).
    0° = pure vertical, positive = clockwise tilt.
    """
    hw = hh * math.tan(math.radians(angle_deg))
    return round(cx - hw, 2), round(cy - hh, 2), round(cx + hw, 2), round(cy + hh, 2)


def _bracket_line_pos(eb_name: str, wallet_disp: str) -> Dict[str, float]:
    """Compute the absolute x position of every segment in the bracket line.

    The line is:  "[ " + BRACKET + " ] :// " + wallet
    Each segment gets an explicit tspan x= so the browser places it exactly,
    independent of text-anchor or line composition.

    Orbitron Bold advance-width model (fraction of em + letter-spacing 0.1 em):
      uppercase / alpha   0.65 em + 0.10 em = 0.75 em/char
      digits              0.60 em + 0.10 em = 0.70 em/char
      punctuation / space 0.45 em + 0.10 em = 0.55 em/char  ([ ] : / . space)
    """
    def _adv(ch: str, fs: float) -> float:
        ls = fs * 0.10
        if ch in " []:/." :
            return fs * 0.45 + ls
        if ch.isdigit():
            return fs * 0.60 + ls
        return fs * 0.65 + ls

    def _width(s: str, fs: float) -> float:
        return sum(_adv(c, fs) for c in s)

    FS_B = 15.0   # font-size for bracket + surrounding punctuation
    FS_W = 14.0   # font-size for wallet address

    w_prefix  = _width("[ ",        FS_B)
    w_bracket = _width(eb_name,     FS_B)
    w_mid     = _width(" ] :// ",   FS_B)
    w_wallet  = _width(wallet_disp, FS_W)

    total      = w_prefix + w_bracket + w_mid + w_wallet
    line_start = DZ_CX - total / 2

    bracket_x  = line_start  + w_prefix
    bracket_cx = bracket_x   + w_bracket / 2
    mid_x      = bracket_x   + w_bracket
    wallet_x   = mid_x       + w_mid
    wallet_cx  = wallet_x    + w_wallet  / 2

    return {
        "line_start": round(line_start, 1),
        "bracket_x":  round(bracket_x,  1),
        "bracket_cx": round(bracket_cx, 1),
        "mid_x":      round(mid_x,      1),
        "wallet_x":   round(wallet_x,   1),
        "wallet_cx":  round(wallet_cx,  1),
        "bracket_hw": round(w_bracket / 2, 1),
        "wallet_hw":  round(w_wallet  / 2, 1),
    }


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
# FIGMA GRADIENT → SVG CONVERSION
# ═══════════════════════════════════════════════════════════════════════════

def figma_gradient_to_svg(gradient: Dict[str, Any], grad_id: str) -> str:
    """Convert a single Figma API gradient dict to an SVG gradient element.

    Figma stores its gradient transform as a 2×3 affine matrix:
        [[a, b, c],
         [d, e, f]]

    SVG's gradientTransform="matrix(...)" takes values in column-major order:
        matrix(a, d, b, e, c, f)

    Both linear and radial gradients use gradientUnits="objectBoundingBox" so
    the matrix (which already operates in the 0–1 normalised layer space) maps
    correctly without any additional scaling.

    Default reference geometries (overridden by gradientTransform):
      • GRADIENT_LINEAR  → x1=0 y1=0  x2=1 y2=0  (horizontal baseline)
      • GRADIENT_RADIAL  → cx=0.5 cy=0.5 r=0.5   (centred unit circle)

    Args:
        gradient: dict with keys gradient_type, stops, gradient_transform
        grad_id:  the id="" to assign to the produced SVG element

    Returns:
        A single SVG <linearGradient> or <radialGradient> element string.
    """
    gtype     = gradient.get("gradient_type", "GRADIENT_LINEAR")
    stops     = gradient.get("stops", [])
    transform = gradient.get("gradient_transform")

    # Build <stop> children
    stop_lines: list[str] = []
    for s in stops:
        offset  = s.get("offset", "0%")
        color   = s.get("color", "#000000")
        opacity = s.get("opacity", 1)
        stop_lines.append(
            f'  <stop offset="{offset}" stop-color="{color}" stop-opacity="{opacity}"/>'
        )
    stops_str = "\n".join(stop_lines)

    # Convert Figma [[a,b,c],[d,e,f]] → SVG matrix(a, d, b, e, c, f)
    transform_attr = ""
    if (
        transform
        and len(transform) == 2
        and len(transform[0]) == 3
        and len(transform[1]) == 3
    ):
        a, b, c = transform[0]
        d, e, f = transform[1]
        transform_attr = f' gradientTransform="matrix({a}, {d}, {b}, {e}, {c}, {f})"'

    if gtype == "GRADIENT_RADIAL":
        return (
            f'<radialGradient id="{grad_id}" gradientUnits="objectBoundingBox"\n'
            f'                cx="0.5" cy="0.5" r="0.5"{transform_attr}>\n'
            f'{stops_str}\n'
            f'</radialGradient>'
        )

    return (
        f'<linearGradient id="{grad_id}" gradientUnits="objectBoundingBox"\n'
        f'                x1="0" y1="0" x2="1" y2="0"{transform_attr}>\n'
        f'{stops_str}\n'
        f'</linearGradient>'
    )


def figma_gradients_to_svg_defs(gradients: list[Dict[str, Any]]) -> str:
    """Convert a list of Figma gradient dicts to a block of SVG gradient elements.

    Each item in *gradients* must have a "layer_name" key that becomes the
    gradient id (spaces replaced with hyphens, lowercased).

    Returns a multi-line string suitable for insertion inside <defs>…</defs>.
    """
    parts: list[str] = []
    for g in gradients:
        raw_name = g.get("layer_name", f"grad-{len(parts)}")
        grad_id  = raw_name.replace(" ", "-").lower()
        parts.append(figma_gradient_to_svg(g, grad_id))
    return "\n\n".join(parts)


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
    _is_grad = lambda c: "master-gradient" in c or "mg-" in c
    bracket_fill  = "url(#mg-eb)"         if _is_grad(bracket_color) else bracket_color
    wallet_fill   = "url(#mg-w)"          if _is_grad(wallet_color)  else wallet_color
    edge_val_fill = "url(#mg-edge)"       if _is_grad(edge_color)    else edge_color
    yld_val_fill  = "url(#mg-yield)"      if _is_grad(yield_color)   else yield_color
    border_fill   = "url(#border-gradient)" if _is_grad(border_color) else border_color
    grav_val_fill = "url(#mg-grav)"  if _is_grad(gravity_color) else gravity_color
    dotted_fill   = "url(#mg-sep)"   if _is_grad(dotted_color)  else dotted_color

    sig = _sig_attrs(is_signal)

    # ── 5b. Gradient coordinates — top-to-bottom vertical sweep per element.
    #
    # For a vertical gradient x1==x2 (x is irrelevant but set to column centre
    # for clarity).  y spans the cap-height of the glyphs so the full rainbow
    # (gold → white) maps exactly to the visible stroke of each letter.
    #
    # Orbitron Bold cap metrics (dominant-baseline="hanging"):
    #   • The em-box top aligns with the specified y coordinate.
    #   • Cap height ≈ 0.72 em  (visible stroke runs from ~top+1px to top+cap_h)
    #
    # font-size 20 px at Y_METRIC_VALUES=707:
    #   cap top ≈ 708,  cap bottom ≈ 722  →  centre 715,  hh = 7
    # font-size 15 px at Y_BRACKET=654:
    #   cap top ≈ 655,  cap bottom ≈ 666  →  centre 660,  hh = 6

    _p_hh  = 7.5          # half cap-height for 20 px metric values
    _eb_hh = 6.0          # half cap-height for 15 px bracket / 14 px wallet

    p99_cy = Y_METRIC_VALUES + 8   # ≈ 715 — vertical centre of 20 px caps
    eb_cy  = Y_BRACKET    + 6     # ≈ 660 — vertical centre of 15 px caps

    edge_gx1, edge_gy1, edge_gx2, edge_gy2 = _grad_pts(COL_EDGE,    p99_cy, _p_hh, GRAD_ANGLE_P99)
    yld_gx1,  yld_gy1,  yld_gx2,  yld_gy2  = _grad_pts(COL_YIELD,   p99_cy, _p_hh, GRAD_ANGLE_P99)
    grav_gx1, grav_gy1, grav_gx2, grav_gy2  = _grad_pts(COL_GRAVITY, p99_cy, _p_hh, GRAD_ANGLE_P99)

    # Bracket / wallet — compute exact per-word x positions from font metrics
    _blp = _bracket_line_pos(eb, wallet_disp)
    eb_x1, eb_y1, eb_x2, eb_y2 = _grad_pts(_blp["bracket_cx"], eb_cy, _eb_hh, GRAD_ANGLE_ANOMALY)
    w_x1,  w_y1,  w_x2,  w_y2  = _grad_pts(_blp["wallet_cx"],  eb_cy, _eb_hh, GRAD_ANGLE_WALLET)

    # Border gradient — userSpaceOnUse so GRAD_ANGLE_BORDER means the same
    # screen-pixel degrees as the text gradients above.
    _brd_cx  = FRAME_X + FRAME_W / 2          # 258
    _brd_cy  = FRAME_Y + FRAME_H / 2          # 401
    _brd_hh  = FRAME_H / 2                    # 398
    brd_gx1, brd_gy1, brd_gx2, brd_gy2 = _grad_pts(_brd_cx, _brd_cy, _brd_hh, GRAD_ANGLE_BORDER)

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
      text-rendering: geometricPrecision;
    }}
  </style>

  <!-- ── ANOMALY bracket stops — edit colors/offsets here, angle = GRAD_ANGLE_ANOMALY -->
  <linearGradient id="anomaly-stops">
    <stop offset="0%"   stop-color="#FFBF00"/>
    <stop offset="50%"  stop-color="#8A2BE2"/>
    <stop offset="70%"  stop-color="#0051FF"/>
    <stop offset="100%" stop-color="#51FF48"/>
  </linearGradient>

  <!-- ── P99 metric value stops (EDGE / YIELD / GRAVITY) — angle = GRAD_ANGLE_P99 -->
  <linearGradient id="p99-stops">
    <stop offset="0%"   stop-color="#FFBF00"/>
    <stop offset="50%"  stop-color="#8A2BE2"/>
    <stop offset="75%"  stop-color="#0051FF"/>
    <stop offset="100%" stop-color="#51FF48"/>
  </linearGradient>

  <!-- ── Wallet address stops — angle = GRAD_ANGLE_WALLET -->
  <linearGradient id="wallet-stops">
    <stop offset="0%"   stop-color="#FFBF00"/>
    <stop offset="30%"  stop-color="#8A2BE2"/>
    <stop offset="55%"  stop-color="#0051FF"/>
    <stop offset="100%" stop-color="#51FF48"/>
  </linearGradient>

  <!-- ── Card border glow stops — angle = GRAD_ANGLE_BORDER -->
  <linearGradient id="border-gradient" gradientUnits="userSpaceOnUse"
                  x1="{brd_gx1}" y1="{brd_gy1}"
                  x2="{brd_gx2}" y2="{brd_gy2}">
    <stop offset="20%"   stop-color="#FFBF00"/>
    <stop offset="40%"  stop-color="#8A2BE2"/>
    <stop offset="60%"  stop-color="#0051FF"/>
    <stop offset="80%" stop-color="#51FF48"/>
    <stop offset="100%" stop-color="#FFFFFF"/>
  </linearGradient>

  <!-- Per-element gradient instances — inherit stops, apply their own coordinates -->
  <linearGradient id="mg-eb" gradientUnits="userSpaceOnUse"
                  x1="{eb_x1}" y1="{eb_y1}"
                  x2="{eb_x2}" y2="{eb_y2}"
                  xlink:href="#anomaly-stops"/>
  <linearGradient id="mg-w" gradientUnits="userSpaceOnUse"
                  x1="{w_x1}" y1="{w_y1}"
                  x2="{w_x2}" y2="{w_y2}"
                  xlink:href="#wallet-stops"/>
  <linearGradient id="mg-edge" gradientUnits="userSpaceOnUse"
                  x1="{edge_gx1}" y1="{edge_gy1}"
                  x2="{edge_gx2}" y2="{edge_gy2}"
                  xlink:href="#p99-stops"/>
  <linearGradient id="mg-yield" gradientUnits="userSpaceOnUse"
                  x1="{yld_gx1}" y1="{yld_gy1}"
                  x2="{yld_gx2}" y2="{yld_gy2}"
                  xlink:href="#p99-stops"/>
  <linearGradient id="mg-grav" gradientUnits="userSpaceOnUse"
                  x1="{grav_gx1}" y1="{grav_gy1}"
                  x2="{grav_gx2}" y2="{grav_gy2}"
                  xlink:href="#p99-stops"/>

  <!-- Lower dotted separator gradient — 90° horizontal -->
  <linearGradient id="mg-sep" gradientUnits="userSpaceOnUse"
                  x1="{LOWER_SEP_X1}" y1="{Y_LOWER_SEP}"
                  x2="{LOWER_SEP_X2}" y2="{Y_LOWER_SEP}">
    <stop offset="0%"   stop-color="#E7FDFD"/>
    <stop offset="50%"  stop-color="#66FB39"/>
    <stop offset="75%"  stop-color="#DDDD03"/>
    <stop offset="88%"  stop-color="#8A00A6"/>
    <stop offset="100%" stop-color="#009999"/>
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

  <!-- COGNITIVE TELEMETRY radial gradient (Section 7.3) — horizontally stretched ellipse
       Center: rgba(239,226,226) #EFE2E2 / Edge: rgba(104,98,98) #686262
       Intermediate stops at 25%, 50%, 75% per spec -->
  <radialGradient id="cognitive-gradient" cx="0" cy="0" r="1"
                  gradientUnits="userSpaceOnUse"
                  gradientTransform="translate({DZ_CX} {Y_COG_TEL + 8}) scale(140 11.5)">
    <stop offset="0"    stop-color="#EFE2E2"/>
    <stop offset="0.15" stop-color="#CDC2C2"/>
    <stop offset="0.50" stop-color="#ACA2A2"/>
    <stop offset="0.85" stop-color="#8A8282"/>
    <stop offset="1"    stop-color="#686262"/>
  </radialGradient>

  <!-- Border glow blur (2px stdDev = 4px Figma blur) -->
  <filter id="glow" x="-1" y="-1"
          width="{CANVAS_W + 2}" height="{CANVAS_H + 2}"
          filterUnits="userSpaceOnUse" color-interpolation-filters="sRGB">
    <feGaussianBlur stdDeviation="2"/>
  </filter>

  <!--
    Drop shadow — exact Figma settings: X=0 Y=4 Blur=4 Spread=0 #000000 25%
    Figma blur → SVG stdDeviation = Figma_blur / 2 = 2.
    The pipeline puts the blurred shadow beneath SourceGraphic so the
    original <text> node is composited last and stays fully crisp.
  -->
  <filter id="shadow" x="-10%" y="-20%" width="120%" height="150%"
          color-interpolation-filters="sRGB">
    <feFlood flood-color="#000000" flood-opacity="0.25" result="flood"/>
    <feComposite in="flood" in2="SourceAlpha" operator="in" result="shadow-shape"/>
    <feOffset dx="0" dy="4" result="shadow-offset"/>
    <feGaussianBlur in="shadow-offset" stdDeviation="2" result="shadow-blur"/>
    <feMerge>
      <feMergeNode in="shadow-blur"/>
      <feMergeNode in="SourceGraphic"/>
    </feMerge>
  </filter>

  <!-- Same settings, applied to individual text elements in the data zone -->
  <filter id="txt-shadow" x="-10%" y="-20%" width="120%" height="150%"
          color-interpolation-filters="sRGB">
    <feFlood flood-color="#000000" flood-opacity="0.25" result="flood"/>
    <feComposite in="flood" in2="SourceAlpha" operator="in" result="shadow-shape"/>
    <feOffset dx="0" dy="4" result="shadow-offset"/>
    <feGaussianBlur in="shadow-offset" stdDeviation="2" result="shadow-blur"/>
    <feMerge>
      <feMergeNode in="shadow-blur"/>
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
        rx="{FRAME_RX}" fill="{border_fill}"/>
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
<g filter="url(#txt-shadow)">
  <text x="{DZ_CX}" y="{Y_COG_TEL}"
        text-anchor="middle" dominant-baseline="hanging"
        font-size="20"
        fill="url(#cognitive-gradient)"{sig}>
    COGNITIVE TELEMETRY
  </text>
</g>''')

    # ---- Layer 12: Entry bracket + wallet ----
    # Flowing tspans (no absolute x=) — the browser handles text layout, so
    # [ ] always sit exactly around the bracket name regardless of its length.
    # Gradients are vertical (colour by Y only) so the x position of each word
    # is irrelevant for gradient rendering.
    parts.append(f'''
<!-- ══ ENTRY BRACKET + WALLET ══ -->
<g filter="url(#txt-shadow)">
  <text x="{DZ_CX}" y="{Y_BRACKET}"
        text-anchor="middle" dominant-baseline="hanging"
        font-size="15"{sig}>
    <tspan fill="white">[ </tspan>
    <tspan fill="{bracket_fill}">{eb}</tspan>
    <tspan fill="white"> ] :// </tspan>
    <tspan fill="{wallet_fill}" font-size="14">{wallet_disp}</tspan>
  </text>
</g>''')

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
<g filter="url(#txt-shadow)">
  <text x="{COL_EDGE}" y="{Y_METRIC_VALUES}"
        text-anchor="middle" dominant-baseline="hanging"
        font-size="20" fill="{edge_val_fill}"{sig}>
    {edge}
  </text>
  <text x="{COL_YIELD}" y="{Y_METRIC_VALUES}"
        text-anchor="middle" dominant-baseline="hanging"
        font-size="20" fill="{yld_val_fill}"{sig}>
    {yld}
  </text>
  <text x="{COL_GRAVITY}" y="{Y_METRIC_VALUES}"
        text-anchor="middle" dominant-baseline="hanging"
        font-size="20" fill="{grav_val_fill}"{sig}>
    {grav}
  </text>
</g>''')

    # ---- Layer 15: Lower dotted separator (GRAVITY-colored, no filter) ----
    parts.append(f'''
<!-- ══ LOWER SEPARATOR (driven by GRAVITY = {grav}) ══ -->
<line x1="{LOWER_SEP_X1}" y1="{Y_LOWER_SEP}"
      x2="{LOWER_SEP_X2}" y2="{Y_LOWER_SEP}"
      stroke="{dotted_fill}" stroke-width="2"
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
        font-size="16" fill="white"
        stroke="#000000" stroke-width="2" paint-order="stroke fill"{sig}>
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
    "entry_bracket":     "ANOMALY",
    "proxy_wallet":      "0xBb8E703abc123def456",
    "edge":              "P99",
    "yield":             "P99",
    "gravity":           "P99",
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
