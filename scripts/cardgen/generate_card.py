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
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


# ═══════════════════════════════════════════════════════════════════════════
# LAYOUT CONSTANTS — Figma JSON coordinates, adjusted for SVG text-anchor
# ═══════════════════════════════════════════════════════════════════════════

CANVAS_W, CANVAS_H = 516, 802

# Border glow rect (filled with YIELD color, blurred)
FRAME_X, FRAME_Y, FRAME_W, FRAME_H, FRAME_RX = 3, 3, 510, 796, 0

# Event image
IMG_X, IMG_Y, IMG_W, IMG_H, IMG_RX = 13, 13, 490, 490, 0

# Logo overlay (top-left of image zone)
LOGO_X, LOGO_Y, LOGO_W, LOGO_H = 20, 20, 44, 44
LOGO_HREF = "logo.svg"

# Metadata badge (top-right)
BADGE_X, BADGE_Y, BADGE_W, BADGE_H, BADGE_RX = 314, 22, 180, 62, 0
BADGE_CX = BADGE_X + BADGE_W // 2  # 404

# Title bar (overlaps image/data boundary)
TB_X, TB_Y, TB_W, TB_H, TB_RX = 29, 472, 458, 61, 0
TB_CX = TB_X + TB_W // 2  # 258
TITLE_PAD_X = 25
TITLE_FS = 16.0
# Vertical cadence between title lines: must match tspan `dy` and CSS `line-height` on <text>.
TITLE_LINE_HEIGHT = 22
# Padded text band inside the title rect (25px inset left/right).
TITLE_INNER_X = TB_X + TITLE_PAD_X
TITLE_INNER_W = TB_W - 2 * TITLE_PAD_X
TITLE_INNER_CX = TITLE_INNER_X + TITLE_INNER_W / 2
# Target width for wrapping (inner band minus small margin for subpixel / metric drift).
TITLE_WRAP_W = TITLE_INNER_W - 8
# Treat a line as “fits” only below this (avoids last word clipping when model ≈ reality).
TITLE_WRAP_MEASURE_SLACK = 10.0
# Nudge title block downward in the bar (SVG +y = down).
TITLE_Y_NUDGE = 2.0

# Data zone
DZ_X, DZ_Y, DZ_W, DZ_H, DZ_RX = 13, 503, 490, 286, 0
DZ_CX = DZ_X + DZ_W // 2  # 258

# Metric column centers — computed from Figma label+value bounding boxes
COL_EDGE    = 130   # label x=72 w=116 → center 130; value x=104 w=53 → 130.5
COL_YIELD   = 263   # label x=212 w=103 → 263.5; value x=237 w=53 → 263.5
COL_GRAVITY = 391   # label x=339 w=105 → 391.5; value x=365 w=53 → 391.5

# Y-coordinates for every text anchor (from Figma JSON)
Y_SEASON        = 33
Y_INSTANCE      = 49
Y_OWNERSHIP     = 65
Y_SECTOR        = 543
Y_NODE          = 566
Y_UPPER_SEP     = 589
Y_BRACKET       = 611
Y_METRIC_LABELS = 649
Y_METRIC_VALUES = 667
Y_LOWER_SEP     = 743
Y_ARCHETYPE     = 729
Y_FOOTER        = 754
Y_POLYSTARS     = 777

# Separator endpoints (from reference SVGs)
UPPER_SEP_X1, UPPER_SEP_X2 = 56, 464
LOWER_SEP_X1, LOWER_SEP_X2 = 57, 457

# Orbitron Bold WOFF2 — resolved relative to this script
_FONT_PATH = Path(__file__).resolve().parent / "orbitron-bold.woff2"

# DATA ZONE fixed anchors from latest JSON layout
X_SECTOR_LABEL = 127
X_SECTOR_VALUE = 232
X_NODE         = DZ_CX
X_WALLET_GAP   = 15
X_PE_GAP       = 15
X_PE_DIVIDER   = 258.5
Y_PE_DIVIDER_1 = 606
Y_PE_DIVIDER_2 = 630
# Wallet text block (left half of wallet/P(E) row). mg-w rotates around this block center.
WALLET_BLOCK_X1 = 57
WALLET_BLOCK_X2 = X_PE_DIVIDER
WALLET_BLOCK_Y1 = 611
WALLET_BLOCK_H  = 15
X_EDGE_LABEL   = 72
X_YIELD_LABEL  = 212
X_GRAV_LABEL   = 339
X_EDGE_VALUE   = 94
X_YIELD_VALUE  = 227
X_GRAV_VALUE   = 355
X_ARCH_LABEL   = 59
X_ARCH_VALUE   = 229
Y_ARCH_VALUE   = 706
X_FOOTER_LABEL = 58
X_FOOTER_RANK  = 373
X_POLYSTARS    = 240
DOT_LEFT_X     = 56
DOT_RIGHT_X    = 460
DOT_Y          = 589
DOT_SZ         = 4
DZ_PIN_COLOR   = "#978E8E"
DZ_PIN_R       = 3.5
DZ_PIN_INSET   = 15

# Back-of-card inner olive rect — Figma Frame 122: x=14 y=12, 490×777, 1px inside stroke.
BACK_DZ_X = 13.0   # FRAME_X + 10 = 3 + 10
BACK_DZ_Y = 13.0   # FRAME_Y + 10 = 3 + 10
BACK_DZ_W = 490.0  # FRAME_W - 20 = 510 - 20
BACK_DZ_H = 776.0  # FRAME_H - 20 = 796 - 20
BACK_LINE_HEIGHT = 22
BACK_QR_SIZE = 100
BACK_QR_MARGIN = 14.0
BACK_QR_X = BACK_DZ_X + BACK_DZ_W - BACK_QR_SIZE - BACK_QR_MARGIN
BACK_QR_Y = BACK_DZ_Y + BACK_DZ_H - BACK_QR_SIZE - BACK_QR_MARGIN
BACK_QR_INNER_PAD = 6.0
# Text column: left pad 41px, right pad = half of left (asymmetric).
BACK_TEXT_PAD_X = 41.0
BACK_TEXT_PAD_RIGHT = 30                                 # 30px right pad
BACK_TEXT_X = int(BACK_DZ_X + BACK_TEXT_PAD_X)          # 55
BACK_TEXT_X_HEAD = BACK_TEXT_X + 1                       # 56 — section labels
BACK_TEXT_RIGHT = BACK_DZ_X + BACK_DZ_W - BACK_TEXT_PAD_RIGHT  # 484
# Figma Frame 122 reference (original 408px box); actual wrap width is wider now.
FIGMA_BACK_LORE_X = 55
FIGMA_BACK_LORE_Y = 85
FIGMA_BACK_LORE_W = 408
FIGMA_BACK_LORE_H = 110
BACK_BODY_WRAP_W = int(BACK_TEXT_RIGHT - BACK_TEXT_X)    # 429
# FIGMA_BACK_LORE_X=55 is a reference; BACK_TEXT_X may differ by 1px due to DZ alignment fix.
# Footer rows share vertical band with QR; wrap ends before the QR column.
BACK_META_WRAP_W = max(120, int(BACK_QR_X - BACK_TEXT_X - 12.0))
# Separator: symmetric around card centre (CANVAS_W/2=258).
# x1 = text-left+5 = 60; x2 = CANVAS_W − x1 = 456  →  centre = 258.
BACK_SEP_X1 = BACK_TEXT_X + 5                           # 60
BACK_SEP_X2 = CANVAS_W - BACK_SEP_X1                   # 456
BACK_SEP_CAP_LEFT_X = BACK_SEP_X1 - 4
BACK_SEP_CAP_RIGHT_X = BACK_SEP_X2
# Vertical layout (back) — flow blocks to avoid fixed y collisions (Frame 136 reference).
BACK_SEP_LINE_Y = 198.0             # shifted -18 px total
BACK_LORE_Y = float(FIGMA_BACK_LORE_Y) - 3.0   # 82
# Leave ~4px above the divider; room for lore lines without overflow.
BACK_LORE_BOTTOM_LIMIT = BACK_SEP_LINE_Y - 4.0
BACK_ARCH_HEADER_Y = 217.0          # 220 - 3
BACK_ARCH_Y = 254.0                 # 257 - 3
# Order: ARCHETYPE → body desc → PROBABILITY COHORT (same font size as body).
BACK_DESC_Y = float(BACK_ARCH_Y) + 25.0   # desc starts 25px below archetype (+3 gap)
# Line-height for font-size=10 meta footer rows (SEASON CARD / DATES).
BACK_META_LH = 14
# Extra offset applied to the two footer meta lines (push down from computed pin).
BACK_META_Y_OFFSET = 10.0
# Lore: the analytic width model is slightly narrower than real Orbitron Bold + 0.1em
# tracking (dense caps / dates can touch the right pad). Keep a few px slack so wrap
# breaks before the renderer does.
BACK_LORE_WRAP_SLACK_PX = 30.0
# Desc: keep a tiny positive overflow allowance because the analytic width model
# slightly overestimates some mixed-case lines near the right edge.
BACK_DESC_WRAP_SLACK_PX = 0.0
BACK_DESC_WRAP_ALLOWANCE_PX = 4.0
# Approximate ink below hanging baseline for line-height 22 / Orbitron 14px body.
BACK_BODY_INK_EXT = 16.0
BACK_META_INK_EXT = 12.0
BACK_GAP_SECTION = 18.0
BACK_GAP_STAT_TO_METRICS = 10.0
BACK_GAP_METRICS_TO_META = 12.0
BACK_METRIC_CHUNK_GAP = 0.0

# ═══════════════════════════════════════════════════════════════════════════
# GRADIENT ANGLES  — degrees from vertical  (0 = pure top→bottom)
# Positive values tilt clockwise (gold shifts to top-left corner).
# Each group is independent so you can tune them separately.
# ═══════════════════════════════════════════════════════════════════════════

GRAD_ANGLE_ANOMALY = 3.5  # [ BRACKET ] label
# mg-eb only: scales gradient axis vs cap half-height _eb_hh (same idea as WALLET_GRAD_HH_MULT).
BRACKET_GRAD_HH_MULT = 1.6
GRAD_ANGLE_WALLET  = 3.5  # wallet address (matched to reference tilt)
# mg-w only: scales gradient axis half-length vs bracket cap (_eb_hh).  >1 lengthens the
# axis in user space → each glyph samples a narrower slice of #wallet-stops, so 0%/100%
# edge colors appear less on the address (no SVG mask — this is the practical "zoom").
WALLET_GRAD_HH_MULT = 1.6
GRAD_ANGLE_P99     = 5  # EDGE / YIELD / GRAVITY metric values
# mg-edge / mg-yield / mg-grav: scales axis vs metric cap half-height _p_hh.
P99_GRAD_HH_MULT = 1.1
GRAD_ANGLE_BORDER  = 12   # card border glow rect


def _grad_pts(cx: float, cy: float, hh: float, angle_deg: float = 0.0):
    """Return (x1, y1, x2, y2) for a gradient centred at (cx, cy).

    The gradient runs from top to bottom (gold→white) spanning ±hh pixels,
    tilted *angle_deg* degrees from vertical (in screen-pixel space).
    0° = pure vertical, positive = clockwise tilt.
    """
    hw = hh * math.tan(math.radians(angle_deg))
    return round(cx - hw, 2), round(cy - hh, 2), round(cx + hw, 2), round(cy + hh, 2)


def _orbitron_adv(ch: str, fs: float) -> float:
    """Approximate Orbitron Bold glyph advance with global 0.1em tracking.

    Calibrated against Orbitron Bold rendering (letter-spacing 10%):
      uppercase  0.70 em + 0.10 em = 0.80 em/char  → 11.2 px @ 14
      lowercase  0.58 em + 0.10 em = 0.68 em/char  → 9.52 px @ 14
      digits     0.55 em + 0.10 em = 0.65 em/char  → 9.10 px @ 14
      punct/sp   0.45 em + 0.10 em = 0.55 em/char  → 7.70 px @ 14
    """
    ls = fs * 0.10
    if ch in " []:/.-()∈$,'":
        return fs * 0.45 + ls
    if ch.isdigit():
        return fs * 0.55 + ls
    if ch.isupper():
        return fs * 0.70 + ls
    return fs * 0.58 + ls


def _orbitron_width(s: str, fs: float) -> float:
    return sum(_orbitron_adv(c, fs) for c in s)


def _fit_single_line_text(
    text: str,
    max_px: float,
    base_font_size: float,
    *,
    min_font_size: float = 10.0,
    step: float = 0.25,
) -> Tuple[float, Optional[float]]:
    """Return a font size that keeps *text* on one line, plus optional textLength fallback.

    If the text still does not fit at *min_font_size*, keep that size and force-fit
    it with SVG textLength so the renderer preserves a single line.
    """
    normalized = " ".join(str(text or "").replace("\n", " ").split())
    if not normalized:
        return base_font_size, None

    fs = float(base_font_size)
    min_fs = max(1.0, float(min_font_size))
    step = max(0.05, float(step))
    while fs >= min_fs:
        if _orbitron_width(normalized, fs) <= max_px:
            return fs, None
        fs -= step
    return min_fs, float(max_px)


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
    FS_B = 15.0   # font-size for bracket + surrounding punctuation
    FS_W = 14.0   # font-size for wallet address

    w_prefix  = _orbitron_width("[ ",        FS_B)
    w_bracket = _orbitron_width(eb_name,     FS_B)
    w_mid     = _orbitron_width(" ] :// ",   FS_B)
    w_wallet  = _orbitron_width(wallet_disp, FS_W)

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

# Mid-tier blue: UI/text uses the softer shade; card frame keeps DEEP_BLUE_CARD_BORDER (see propagation).
SOFT_TIER_BLUE = "#2C7ECA"
DEEP_BLUE_CARD_BORDER = "#265DD2"

ENTRY_BRACKET_COLORS: Dict[str, str] = {
    "[0.00 - 0.20]": _GRAD,
    "[0.20 - 0.40]": "#FFBF00",
    "[0.40 - 0.60]": SOFT_TIER_BLUE,
    "[0.60 - 0.80]": "#38BE50",
    "[0.80 - 0.97]": "#B6BBC8",
    "[0.97 - 1.00]": "#FFFFFF",
}

PTIER_COLORS: Dict[str, str] = {
    "P999": _GRAD,
    "P99":  _GRAD,
    "P95":  "#FFBF00",
    "P90":  "#FFBF00",
    "P80":  SOFT_TIER_BLUE,
    "P70":  SOFT_TIER_BLUE,
    "P50":  "#38BE50",
    "BASE": "#B6BBC8",
}

# Yield tiers whose outer glow must stay deep blue (all other uses of that hue are SOFT_TIER_BLUE).
_YIELD_TIERS_DEEP_BORDER = frozenset({"P70", "P80"})


_LEGACY_TO_INTERVAL: Dict[str, str] = {
    "ANOMALY": "[0.00 - 0.20]",
    "ORACLE": "[0.20 - 0.40]",
    "OUTLIER": "[0.40 - 0.60]",
    "VECTOR": "[0.60 - 0.80]",
    "EXTRACTOR": "[0.97 - 1.00]",
    "PASSENGER": "[0.97 - 1.00]",
}


def normalize_entry_bracket(raw: Any) -> str:
    v = str(raw or "").strip().upper()
    if not v:
        return "[0.80 - 0.97]"
    if v in _LEGACY_TO_INTERVAL:
        return _LEGACY_TO_INTERVAL[v]
    for interval in ENTRY_BRACKET_COLORS:
        if v == interval.upper():
            return interval
    return "[0.80 - 0.97]"


def get_bracket_color(name: str) -> str:
    return ENTRY_BRACKET_COLORS.get(normalize_entry_bracket(name), "#FFFFFF")


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
# DATA ZONE STYLE RESOLUTION (Archetype-driven)
# ═══════════════════════════════════════════════════════════════════════════

_DZ_ARCHETYPE_STYLES: Dict[str, Tuple[str, str, bool]] = {
    #                     (fill,                        stroke,    is_signal)
    "ANOMALY":     ("url(#anomaly-gradient)",      "#000000", False),
    "ICARUS":      ("url(#icarus-gradient)",       "#000000", False),
    "BOT":         ("url(#bot-gradient)",          "#000000", False),
    "BURNER":      ("url(#burner-gradient)",       "#000000", False),
    "EQUILIBRIUM": ("url(#equilibrium-gradient)",  "#000000", True),
    "GRAVITON":    ("url(#graviton-gradient)",     "#000000", False),
    "VECTOR":      ("url(#vector-gradient)",       "#000000", False),
    "SIGNAL":      ("url(#signal-gradient)",       "#000000", False),
    "EXTRACTOR":   ("url(#extractor-gradient)",    "#000000", False),
    "INSIDER":     ("#1C1B1B",                     "#000000", False),
    "PASSENGER":   ("#474332",                     "#000000", False),
    "OPERATOR":    ("#625F5F",                     "#000000", False),
    "SUBSTRATE":   ("#B8BFCE",                     "#000000", False),
}


def _archetype_style_key(raw: str) -> str:
    s = str(raw or "").strip().upper()
    if s.startswith("THE "):
        s = s[4:].strip()
    return s or "OPERATOR"


def dz_style(archetype: str) -> Tuple[str, str, bool]:
    """Return (fill, stroke, is_signal) for data zone by archetype."""
    key = _archetype_style_key(archetype)
    return _DZ_ARCHETYPE_STYLES.get(key, _DZ_ARCHETYPE_STYLES["OPERATOR"])


# ═══════════════════════════════════════════════════════════════════════════
# METADATA HELPERS (Section 5)
# ═══════════════════════════════════════════════════════════════════════════

def _season(data: Dict[str, Any]) -> Tuple[str, str]:
    if data.get("season_type", "").lower() == "genesis":
        return "GENESIS", "#00FFFF"
    n = data.get("season_number", 1)
    return f"STANDARD #{n}", "#B1ABAB"


def _event_recurrence_is_fractal(recurrence_value: Any) -> bool:
    """True when the event is recurring (INSTANCE FRACTAL).

    event_cards.reccurence is backfilled from series.recurrence; one-off events use 'unique'.
    Anything other than empty / unique / null-like → recurring (FRACTAL).
    """
    s = str(recurrence_value or "").strip().lower()
    if not s or s in ("null", "none", "-"):
        return False
    if s == "unique":
        return False
    return True


def _instance(data: Dict[str, Any]) -> Tuple[str, str]:
    if _event_recurrence_is_fractal(data.get("recurrence")):
        return "FRACTAL", "#2A8FEE"
    return "SINGULAR", "#E8A72F"


def _ownership(data: Dict[str, Any]) -> Tuple[str, str]:
    claim = str(data.get("claim_type", "") or "").strip().lower()
    if claim == "origin":
        return "ORIGIN SECURED", "#FF007F"
    return "LOOTER TAKEOVER", "#40E288"


def _wallet_display(addr: str) -> str:
    # Display format: 0x + 13 wallet characters, then ellipsis.
    value = str(addr)
    return (value[:14] + "...") if len(value) > 14 else value


# ═══════════════════════════════════════════════════════════════════════════
# SVG ASSEMBLY
# ═══════════════════════════════════════════════════════════════════════════

def _esc(text: str) -> str:
    """XML-escape user-supplied content."""
    return html.escape(str(text), quote=True)


def _sig_attrs(is_signal: bool) -> str:
    """Stroke is disabled globally; kept only for rank text."""
    return ""


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
    href = str(href or "").strip()
    if not href:
        return ""
    if href.startswith(("http://", "https://", "data:")):
        return href
    p = _SCRIPT_DIR / href
    if not p.exists():
        return ""
    mime = _MIME_MAP.get(p.suffix.lower(), "application/octet-stream")
    b64 = base64.b64encode(p.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def generate_card_svg(data: Dict[str, Any]) -> str:
    """Build a complete front-of-card SVG string from a card data dict."""

    # ── 1. Data zone style from archetype (pattern detection removed) ─
    archetype_raw = str(data.get("archetype", "") or "").strip().upper()
    if not archetype_raw:
        archetype_raw = "OPERATOR"
    archetype_raw = _archetype_style_key(archetype_raw)
    dz_fill, dz_stroke, is_signal = dz_style(archetype_raw)

    # ── 2. Resolve tier colors ────────────────────────────────────────
    eb   = normalize_entry_bracket(data.get("entry_bracket", "[0.80 - 0.97]"))
    edge = data.get("edge", "BASE").upper()
    yld  = data.get("yield", "BASE").upper()
    grav = data.get("gravity", "BASE").upper()

    bracket_color = get_bracket_color(eb)
    eb_inner = eb[1:-1].strip() if eb.startswith("[") and eb.endswith("]") else eb
    edge_color    = get_ptier_color(edge)
    yield_color   = get_ptier_color(yld)
    gravity_color = get_ptier_color(grav)

    # ── 3. Propagation (Section 9) ────────────────────────────────────
    wallet_color = edge_color           # EDGE → wallet
    # YIELD → outer glow: P70/P80 use deep blue on the frame only; same hue elsewhere is SOFT_TIER_BLUE.
    border_color = DEEP_BLUE_CARD_BORDER if yld in _YIELD_TIERS_DEEP_BORDER else yield_color
    dotted_color = gravity_color        # GRAVITY → lower dotted line

    # ── 4. Metadata ───────────────────────────────────────────────────
    season_val,    season_clr    = _season(data)
    instance_val,  instance_clr  = _instance(data)
    ownership_val, ownership_clr = _ownership(data)

    # ── 5. Text content ──────────────────────────────────────────────
    title_raw = str(data.get("card_title", "UNTITLED EVENT") or "").strip().upper()
    if not title_raw:
        title_raw = "UNTITLED EVENT"
    title_lines = _wrap_card_title_lines(title_raw, TITLE_WRAP_W, TITLE_FS)
    sector      = _esc(data.get("primary_tag", "UNKNOWN").upper())
    sector_clr  = data.get("primary_tag_color", "#FFFFFF")
    node        = _esc(data.get("secondary_tag", "NONE").upper())
    wallet_disp = _esc(_wallet_display(data.get("proxy_wallet", "0x00000000")))
    archetype   = _esc(archetype_raw)
    archetype_fill = "url(#archetype-gradient)"
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
    dotted_rects = []
    x = LOWER_SEP_X1
    while x <= LOWER_SEP_X2 - 2:
        dotted_rects.append(
            f'<rect x="{x}" y="{Y_LOWER_SEP}" width="2" height="2" fill="{dotted_fill}"/>'
        )
        x += 6
    dotted_separator_svg = "".join(dotted_rects)

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

    # Text gradients in stable user-space coordinates:
    # center + axis are tied to fixed text blocks and do not depend on SVG zoom.
    # Use visible cap-height (~0.72em), not full font-size box.
    # This keeps the gradient midpoint on the rendered glyph block
    # and avoids "mostly yellow" when the gradient sits too low.
    # Some SVG renderers treat hanging baseline/CSS transforms differently;
    # this upward em-shift keeps gradient centers aligned with the visible glyphs.
    cap_ratio = 0.72
    grad_center_em_shift = -0.72
    p99_block_h = 20.0 * cap_ratio
    eb_block_h = 15.0 * cap_ratio
    _p_hh = p99_block_h / 2.0
    _eb_hh = eb_block_h / 2.0
    # P99 values block (EDGE / YIELD / GRAVITY)
    p99_cy = Y_METRIC_VALUES + (grad_center_em_shift * 20.0) + _p_hh
    p99_hh = _p_hh * P99_GRAD_HH_MULT
    edge_gx1, edge_gy1, edge_gx2, edge_gy2 = _grad_pts(COL_EDGE,    p99_cy, p99_hh, GRAD_ANGLE_P99)
    yld_gx1,  yld_gy1,  yld_gx2,  yld_gy2  = _grad_pts(COL_YIELD,   p99_cy, p99_hh, GRAD_ANGLE_P99)
    grav_gx1, grav_gy1, grav_gx2, grav_gy2 = _grad_pts(COL_GRAVITY, p99_cy, p99_hh, GRAD_ANGLE_P99)

    # Bracket gradient center: geometric center of the inner [entry_bracket] text block.
    fs_bracket = 15.0
    prefix = "P (E) ∈ ["
    bracket_block_x = X_PE_DIVIDER + X_PE_GAP + _orbitron_width(prefix, fs_bracket)
    bracket_block_w = _orbitron_width(eb_inner, fs_bracket)
    bracket_block_cx = bracket_block_x + (bracket_block_w / 2)
    bracket_block_cy = Y_BRACKET + (grad_center_em_shift * 15.0) + _eb_hh
    # mg-eb: midpoint locked to geometric center of bracket text block (without [ ]).
    eb_hh_g = _eb_hh * BRACKET_GRAD_HH_MULT
    eb_x1, eb_y1, eb_x2, eb_y2 = _grad_pts(bracket_block_cx, bracket_block_cy, eb_hh_g, GRAD_ANGLE_ANOMALY)
    wallet_block_cx = (WALLET_BLOCK_X1 + WALLET_BLOCK_X2) / 2
    wallet_block_cy = WALLET_BLOCK_Y1 + (grad_center_em_shift * 15.0) + _eb_hh
    # mg-w: midpoint locked to geometric center of wallet block; angle rotates around this center.
    w_hh = _eb_hh * WALLET_GRAD_HH_MULT
    w_x1,  w_y1,  w_x2,  w_y2  = _grad_pts(wallet_block_cx, wallet_block_cy, w_hh, GRAD_ANGLE_WALLET)

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
    parts.append(f'''<svg viewBox="0 0 {CANVAS_W} {CANVAS_H}"
     fill="none"
     xml:space="preserve"
     font-family="Orbitron, sans-serif"
     font-weight="700"
     letter-spacing="0.1em"
     xmlns="http://www.w3.org/2000/svg"
     xmlns:xlink="http://www.w3.org/1999/xlink">

<defs>
  <style>
    {font_css}
    text {{
      font-family: 'Orbitron', sans-serif;
      font-stretch: normal;
      font-style: normal;
      font-weight: 700;
      letter-spacing: 0.1em;
      text-rendering: optimizeLegibility;
    }}
    text[dominant-baseline="hanging"] {{
      dominant-baseline: auto;
      transform: translateY(0.72em);
    }}
    .css-drop-shadow {{
      filter: drop-shadow(0px 4px 2px rgba(0,0,0,0.25));
    }}
  </style>

  <!-- ── ANOMALY bracket stops — edit colors/offsets here, angle = GRAD_ANGLE_ANOMALY -->
  <linearGradient id="anomaly-stops">
    <stop offset="0%"  stop-color="#FFBF00"/>
    <stop offset="33%"  stop-color="#8A2BE2"/>
    <stop offset="66%"  stop-color="#0051FF"/>
    <stop offset="100%" stop-color="#51FF48"/>
  </linearGradient>

  <!-- ── P99 metric value stops (EDGE / YIELD / GRAVITY) — angle = GRAD_ANGLE_P99 -->
  <linearGradient id="p99-stops">
    <stop offset="0%"   stop-color="#FFBF00"/>
    <stop offset="33%"  stop-color="#8A2BE2"/>
    <stop offset="66%"  stop-color="#0051FF"/>
    <stop offset="100%" stop-color="#51FF48"/>
  </linearGradient>

  <!-- ── Wallet address stops — angle = GRAD_ANGLE_WALLET -->
  <linearGradient id="wallet-stops">
    <stop offset="0%"   stop-color="#FFBF00"/>
    <stop offset="25%"  stop-color="#8A2BE2"/>
    <stop offset="50%"  stop-color="#0051FF"/>
    <stop offset="75%"  stop-color="#51FF48"/>
    <stop offset="100%" stop-color="#FFFFFF"/>
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
                  x1="{LOWER_SEP_X1}" y1="{Y_LOWER_SEP + 1.5}"
                  x2="{LOWER_SEP_X2}" y2="{Y_LOWER_SEP + 1.5}">
    <stop offset="0%"   stop-color="#E7FDFD"/>
    <stop offset="50%"  stop-color="#66FB39"/>
    <stop offset="75%"  stop-color="#DDDD03"/>
    <stop offset="88%"  stop-color="#8A00A6"/>
    <stop offset="100%" stop-color="#009999"/>
  </linearGradient>

  <!-- Archetype data zone gradients -->
  <linearGradient id="anomaly-gradient" x1="0.5" y1="0" x2="0.5" y2="1">
    <stop offset="15%" stop-color="#28AEAE"/>
    <stop offset="30%" stop-color="#4A99BB"/>
    <stop offset="45%" stop-color="#8C92D1"/>
    <stop offset="60%" stop-color="#C38CCE"/>
    <stop offset="75%" stop-color="#BB7382"/>
    <stop offset="90%" stop-color="#C5CC84"/>
  </linearGradient>
  <linearGradient id="icarus-gradient" x1="0.5" y1="0" x2="0.5" y2="1">
    <stop offset="16%" stop-color="#871919"/>
    <stop offset="32%" stop-color="#FFA20C"/>
    <stop offset="50%" stop-color="#FDDFA9"/>
    <stop offset="68%" stop-color="#FFA20C"/>
    <stop offset="84%" stop-color="#871919"/>
  </linearGradient>
  <linearGradient id="extractor-gradient" x1="0.5" y1="0" x2="0.5" y2="1">
    <stop offset="15%" stop-color="#7F8E3D"/>
    <stop offset="30%" stop-color="#7A5CBE"/>
    <stop offset="45%" stop-color="#9D653E"/>
    <stop offset="60%" stop-color="#2E9F8C"/>
    <stop offset="75%" stop-color="#2F7346"/>
    <stop offset="90%" stop-color="#2E3160"/>
  </linearGradient>
  <linearGradient id="bot-gradient" x1="0.5" y1="0" x2="0.5" y2="1">
    <stop offset="0%"  stop-color="#554467"/>
    <stop offset="50%" stop-color="#554467"/>
    <stop offset="100%" stop-color="#7C6C8D"/>
  </linearGradient>
  <linearGradient id="burner-gradient" x1="0.5" y1="0" x2="0.5" y2="1">
    <stop offset="0%"  stop-color="#983232"/>
    <stop offset="50%" stop-color="#983232"/>
    <stop offset="100%" stop-color="#321010"/>
  </linearGradient>
  <linearGradient id="equilibrium-gradient" x1="0.5" y1="0" x2="0.5" y2="1">
    <stop offset="0%"  stop-color="#915238"/>
    <stop offset="50%" stop-color="#915238"/>
    <stop offset="100%" stop-color="#2B1810"/>
  </linearGradient>
  <linearGradient id="signal-gradient" x1="0.5" y1="0" x2="0.5" y2="1">
    <stop offset="0%"  stop-color="#0A2A2A"/>
    <stop offset="50%" stop-color="#0A2A2A"/>
    <stop offset="100%" stop-color="#134E4E"/>
  </linearGradient>
  <linearGradient id="graviton-gradient" x1="0.5" y1="0" x2="0.5" y2="1">
    <stop offset="0%"  stop-color="#996C2C"/>
    <stop offset="50%" stop-color="#996C2C"/>
    <stop offset="100%" stop-color="#33240F"/>
  </linearGradient>
  <linearGradient id="vector-gradient" x1="0.5" y1="0" x2="0.5" y2="1">
    <stop offset="0%"  stop-color="#1D4191"/>
    <stop offset="50%" stop-color="#1D4191"/>
    <stop offset="100%" stop-color="#08132B"/>
  </linearGradient>

  <!-- Archetype radial gradient (lightened to match reference) -->
  <radialGradient id="archetype-gradient" cx="0" cy="0" r="1"
                  gradientUnits="userSpaceOnUse"
                  gradientTransform="translate(338 721.5) scale(94 12.5)">
    <stop offset="0"    stop-color="#EFE2E2"/>
    <stop offset="1"    stop-color="#686262"/>
  </radialGradient>

  <!-- Border glow blur (2px stdDev = 4px Figma blur) -->
  <filter id="glow" x="-1" y="-1"
          width="{CANVAS_W + 2}" height="{CANVAS_H + 2}"
          filterUnits="userSpaceOnUse" color-interpolation-filters="sRGB">
    <feGaussianBlur stdDeviation="2"/>
  </filter>

  <!-- Image zone clip (rounded corners) -->
  <clipPath id="img-clip">
    <rect x="{IMG_X}" y="{IMG_Y}"
          width="{IMG_W}" height="{IMG_H}" rx="{IMG_RX}"/>
  </clipPath>

  <!-- Title text — 25px horizontal inset from title bar edges -->
  <clipPath id="title-text-clip">
    <rect x="{TITLE_INNER_X}" y="{TB_Y}" width="{TITLE_INNER_W}" height="{TB_H}"/>
  </clipPath>

  <!-- Layer blur for white-grey border frame (matches Figma Layer blur effect) -->
  <filter id="frame-blur" x="-10%" y="-10%" width="120%" height="120%">
    <feGaussianBlur stdDeviation="4"/>
  </filter>
</defs>''')

    # ---- Layer 1: Background canvas ----
    parts.append(f'''
<!-- ══ BACKGROUND ══ -->
<rect width="{CANVAS_W}" height="{CANVAS_H}" rx="0" fill="#0B0C10"/>''')

    # ---- Layer 2: Border base with layer blur ----
    parts.append(f'''
<!-- ══ BORDER GLOW (driven by YIELD = {yld}) ══ -->
<rect x="{FRAME_X}" y="{FRAME_Y}"
      width="{FRAME_W}" height="{FRAME_H}"
      rx="{FRAME_RX}" fill="{border_fill}"
      filter="url(#frame-blur)"/>''')

    # ---- Layer 3: Event image ----
    if image_url:
        parts.append(f'''
<!-- ══ EVENT IMAGE ══ -->
<image x="{IMG_X}" y="{IMG_Y}" width="{IMG_W}" height="{IMG_H}"
       href="{image_url}" clip-path="url(#img-clip)"
       preserveAspectRatio="xMidYMid slice"/>''')
    else:
        parts.append(f'''
<!-- ══ EVENT IMAGE FALLBACK ══ -->
<rect x="{IMG_X}" y="{IMG_Y}" width="{IMG_W}" height="{IMG_H}"
      fill="#111111"/>''')
    parts.append(f'''
<!-- ══ EVENT IMAGE STROKE (matched to DATA ZONE) ══ -->
<rect x="{IMG_X + 0.5}" y="{IMG_Y + 0.5}"
      width="{IMG_W - 1}" height="{IMG_H - 1}"
      rx="{IMG_RX}" fill="none" stroke="#000000"/>''')

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
      rx="{BADGE_RX - 0.5}" fill="black"/>

<text x="{BADGE_CX}" y="{Y_SEASON}"
      text-anchor="middle" dominant-baseline="hanging"
      font-size="10">
  <tspan fill="white">SEASON:&#160;</tspan>
  <tspan fill="{season_clr}">{_esc(season_val)}</tspan>
</text>

<text x="{BADGE_CX}" y="{Y_INSTANCE}"
      text-anchor="middle" dominant-baseline="hanging"
      font-size="10">
  <tspan fill="white">INSTANCE:&#160;</tspan>
  <tspan fill="{instance_clr}">{_esc(instance_val)}</tspan>
</text>

<text x="{BADGE_CX}" y="{Y_OWNERSHIP}"
      text-anchor="middle" dominant-baseline="hanging"
      font-size="10">
  <tspan fill="white">[&#160;</tspan>
  <tspan fill="{ownership_clr}">{_esc(ownership_val)}</tspan>
  <tspan fill="white">&#160;]</tspan>
</text>''')

    # ---- Layer 6: Data zone background ----
    parts.append(f'''
<!-- ══ DATA ZONE (archetype: {archetype_raw}) ══ -->
<rect x="{DZ_X + 0.5}" y="{DZ_Y + 0.5}"
      width="{DZ_W - 1}" height="{DZ_H - 1}"
      rx="{DZ_RX - 0.5}" fill="{dz_fill}" stroke="#000000"/>''')
    parts.append(f'''
<!-- ══ DATA ZONE CORNER PINS ══ -->
<circle cx="{DZ_X + DZ_PIN_INSET}" cy="{DZ_Y + DZ_PIN_INSET}" r="{DZ_PIN_R}" fill="{DZ_PIN_COLOR}"/>
<circle cx="{DZ_X + DZ_W - DZ_PIN_INSET}" cy="{DZ_Y + DZ_PIN_INSET}" r="{DZ_PIN_R}" fill="{DZ_PIN_COLOR}"/>
<circle cx="{DZ_X + DZ_PIN_INSET}" cy="{DZ_Y + DZ_H - DZ_PIN_INSET}" r="{DZ_PIN_R}" fill="{DZ_PIN_COLOR}"/>
<circle cx="{DZ_X + DZ_W - DZ_PIN_INSET}" cy="{DZ_Y + DZ_H - DZ_PIN_INSET}" r="{DZ_PIN_R}" fill="{DZ_PIN_COLOR}"/>''')

    # ---- Layer 7: Title bar ----
    # Center the text block in TB_H: dominant-baseline="hanging" → y0 is the top
    # anchor; glyphs occupy ~TITLE_FS downward; further lines step by TITLE_LINE_HEIGHT.
    if not title_lines:
        title_lines = ["UNTITLED EVENT"]
    n_title = len(title_lines)
    title_block_h = (n_title - 1) * TITLE_LINE_HEIGHT + TITLE_FS
    y_title_first = TB_Y + (TB_H - title_block_h) / 2 + TITLE_Y_NUDGE
    title_tspans: List[str] = []
    for i, ln in enumerate(title_lines):
        esc_ln = _esc(ln)
        dy_attr = "" if i == 0 else f' dy="{int(TITLE_LINE_HEIGHT)}"'
        title_tspans.append(
            f'<tspan x="{int(TITLE_INNER_CX)}" text-anchor="middle"{dy_attr}>{esc_ln}</tspan>'
        )
    title_inner = "".join(title_tspans)

    parts.append(f'''
<!-- ══ TITLE BAR ══ -->
<rect x="{TB_X}" y="{TB_Y}" width="{TB_W}" height="{TB_H}"
      rx="{TB_RX}" fill="#171717" fill-opacity="0.95"
      stroke="#333333" stroke-width="2"/>

<g clip-path="url(#title-text-clip)">
  <text x="{int(TITLE_INNER_CX)}" y="{round(y_title_first, 2)}"
        text-anchor="middle" dominant-baseline="hanging"
        font-size="{int(TITLE_FS)}" fill="white"
        style="text-align:center;font-size:{int(TITLE_FS)}px;line-height:{int(TITLE_LINE_HEIGHT)}px">
    {title_inner}
  </text>
</g>''')

    # ---- Layer 8: SECTOR ----
    parts.append(f'''
<!-- ══ SECTOR ══ -->
<g class="css-drop-shadow">
  <text x="{DZ_CX}" y="{Y_SECTOR}"
        text-anchor="middle" dominant-baseline="hanging"
        font-size="18"{sig}>
    <tspan fill="white">SECTOR:&#160;</tspan>
    <tspan fill="{sector_clr}">{sector}</tspan>
  </text>
</g>''')

    # ---- Layer 9: NODE ----
    parts.append(f'''
<!-- ══ NODE ══ -->
<g class="css-drop-shadow">
  <text x="{X_NODE}" y="{Y_NODE}"
        text-anchor="middle" dominant-baseline="hanging"
        font-size="16" fill="#888888" style="letter-spacing:0em"{sig}>
    NODE: {node}
  </text>
</g>''')

    # ---- Layer 10: Upper separator (exact Frame 95 geometry) ----
    parts.append(f'''
<!-- ══ UPPER SEPARATOR ══ -->
<path d="M60 590H460V589H464V593H460V592H60V593H56V589H60V590Z" fill="#333333"/>''')

    # ---- Layer 11: Wallet + P(E) bracket ----
    parts.append(f'''
<!-- ══ WALLET + PROBABILITY BRACKET ══ -->
<g class="css-drop-shadow">
  <rect x="{X_PE_DIVIDER}" y="{Y_PE_DIVIDER_1}" width="1" height="{Y_PE_DIVIDER_2 - Y_PE_DIVIDER_1}" fill="white"/>
  <text x="{X_PE_DIVIDER - X_WALLET_GAP}" y="{Y_BRACKET}"
        text-anchor="end" dominant-baseline="hanging"
        font-size="15" fill="{wallet_fill}"{sig}>
    {wallet_disp}
  </text>
  <text x="{X_PE_DIVIDER + X_PE_GAP}" y="{Y_BRACKET}"
        text-anchor="start" dominant-baseline="hanging"
        font-size="15"{sig}>
    <tspan fill="white">P&#160;(E)&#160;∈&#160;[</tspan>
    <tspan fill="{bracket_fill}">{eb_inner}</tspan>
    <tspan fill="white">]</tspan>
  </text>
</g>''')

    # ---- Layer 12: Metric labels (EDGE / YIELD / GRAVITY) ----
    parts.append(f'''
<!-- ══ METRIC LABELS ══ -->
<g class="css-drop-shadow">
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

    # ---- Layer 13: Metric values ----
    parts.append(f'''
<!-- ══ METRIC VALUES ══ -->
<g class="css-drop-shadow">
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

    # ---- Layer 14: Lower dotted separator (GRAVITY-colored, no filter) ----
    parts.append(f'''
<!-- ══ LOWER SEPARATOR (driven by GRAVITY = {grav}) ══ -->
{dotted_separator_svg}''')

    # ---- Layer 15: Archetype row ----
    parts.append(f'''
<!-- ══ ARCHETYPE ══ -->
<g class="css-drop-shadow">
  <text x="{DZ_CX}" y="{Y_ARCHETYPE}"
        text-anchor="middle" dominant-baseline="alphabetic"{sig}>
    <tspan font-size="18" fill="white">ARCHETYPE:&#160;</tspan>
    <tspan font-size="20" fill="{archetype_fill}">{archetype}</tspan>
  </text>
</g>''')

    # ---- Layer 16: Footer (POLYMARKET GLOBAL RANK) ----
    parts.append(f'''
<!-- ══ FOOTER ══ -->
<g class="css-drop-shadow">
  <text x="{DZ_CX}" y="{Y_FOOTER}"
        text-anchor="middle" dominant-baseline="hanging"
        font-size="16"{sig}>
    <tspan fill="#2E5CFF">POLYMARKET</tspan><tspan fill="#828181">&#160;GLOBAL&#160;RANK:&#160;</tspan><tspan fill="#FFFFFF" stroke="#000000" stroke-width="1" paint-order="stroke fill">{rank_str}</tspan>
  </text>
</g>

<text x="{X_POLYSTARS}" y="{Y_POLYSTARS}"
      text-anchor="start" dominant-baseline="hanging"
      font-size="6">
  <tspan fill="#2E5CFF">P</tspan><tspan fill="#D5744C">O</tspan><tspan fill="#B8BFCE">L</tspan><tspan fill="#925AB9">Y</tspan><tspan fill="#328ADD">S</tspan><tspan fill="#3E9292">T</tspan><tspan fill="#C08838">A</tspan><tspan fill="#736D51">R</tspan><tspan fill="#BA4040">S</tspan>
</text>''')

    # ---- Layer 17: Structural outer border (always #333333) ----
    parts.append(f'''
<!-- ══ STRUCTURAL BORDER ══ -->
<rect x="1.5" y="1.5" width="513" height="799" rx="0"
      fill="none" stroke="#333333" stroke-width="3"/>

</svg>''')

    return "\n".join(parts)


def _approx_wrap_text(text: str, max_chars: int) -> List[str]:
    words = [w for w in str(text or "").replace("\n", " ").split(" ") if w]
    if not words:
        return []
    lines: List[str] = []
    cur = words[0]
    for w in words[1:]:
        candidate = f"{cur} {w}"
        if len(candidate) <= max_chars:
            cur = candidate
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    return lines


def _wrap_text_by_width(text: str, max_px: float, font_size: float) -> List[str]:
    words = [w for w in str(text or "").replace("\n", " ").split(" ") if w]
    if not words:
        return []
    lines: List[str] = []
    cur = words[0]
    for w in words[1:]:
        candidate = f"{cur} {w}"
        if _orbitron_width(candidate, font_size) <= max_px:
            cur = candidate
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    return lines


def _balanced_wrap_text_by_width(text: str, max_px: float, font_size: float) -> List[str]:
    """Wrap text by width while preferring visually balanced line lengths.

    Unlike the greedy wrapper, this uses dynamic programming to avoid awkward
    ragged endings when a few words can be shifted upward.
    """
    base_words = [w for w in str(text or "").replace("\n", " ").split(" ") if w]
    if not base_words:
        return []

    words: List[str] = []
    for word in base_words:
        if _orbitron_width(word, font_size) <= max_px:
            words.append(word)
        else:
            words.extend(_split_oversized_line(word, max_px, font_size))
    if not words:
        return []

    n = len(words)
    inf = float("inf")
    dp = [inf] * (n + 1)
    nxt = [n] * (n + 1)
    dp[n] = 0.0

    for i in range(n - 1, -1, -1):
        line = ""
        for j in range(i, n):
            line = words[j] if j == i else f"{line} {words[j]}"
            width = _orbitron_width(line, font_size)
            if width > max_px:
                break

            word_count = j - i + 1
            slack_ratio = max(0.0, (max_px - width) / max(max_px, 1.0))
            badness = slack_ratio * slack_ratio

            # Penalize very short lines, especially mid-paragraph widows/orphans.
            if word_count == 1:
                badness += 4.0
            elif word_count == 2:
                badness += 0.75

            # Keep the final line reasonably full too, but not as strict as middle lines.
            if j == n - 1:
                if word_count == 1:
                    badness += 3.0
                elif word_count == 2:
                    badness += 0.9
                else:
                    badness *= 0.6

            total = badness + dp[j + 1]
            if total < dp[i]:
                dp[i] = total
                nxt[i] = j + 1

    out: List[str] = []
    i = 0
    while i < n:
        j = nxt[i]
        if j <= i or j > n:
            j = i + 1
        out.append(" ".join(words[i:j]))
        i = j
    return out


def _rebalance_back_wrap_lines(
    lines: List[str],
    max_px: float,
    font_size: float,
    *,
    min_words_per_line: int = 2,
) -> List[str]:
    """Reduce awkward one-word lines in back-side paragraphs.

    Prefer pulling words from the next line so fragments like ``They`` become
    ``They possess`` instead of standing alone. If that is not possible on the
    final line, borrow one trailing word from the previous line.
    """
    out = [str(line).strip() for line in lines if str(line).strip()]
    if len(out) < 2:
        return out

    def word_count(value: str) -> int:
        return len([w for w in value.split(" ") if w])

    changed = True
    max_iterations = max(16, len(out) * 4)
    iterations = 0
    while changed and iterations < max_iterations:
        iterations += 1
        changed = False
        for i in range(len(out)):
            current_words = word_count(out[i])
            if current_words >= min_words_per_line:
                continue

            if i + 1 < len(out):
                next_words = out[i + 1].split()
                while next_words and word_count(out[i]) < min_words_per_line:
                    candidate_cur = f"{out[i]} {next_words[0]}".strip()
                    candidate_next = " ".join(next_words[1:]).strip()
                    if _orbitron_width(candidate_cur, font_size) > max_px:
                        break
                    if candidate_next and _orbitron_width(candidate_next, font_size) > max_px:
                        break
                    out[i] = candidate_cur
                    if candidate_next:
                        out[i + 1] = candidate_next
                    else:
                        del out[i + 1]
                    next_words = out[i + 1].split() if i + 1 < len(out) else []
                    changed = True
                    if i + 1 >= len(out):
                        break
                if word_count(out[i]) >= min_words_per_line:
                    continue

            if i > 0 and " " in out[i - 1]:
                prev_left, _, prev_right = out[i - 1].rpartition(" ")
                candidate_prev = prev_left.strip()
                candidate_cur = f"{prev_right} {out[i]}".strip()
                if (
                    candidate_prev
                    and _orbitron_width(candidate_prev, font_size) <= max_px
                    and _orbitron_width(candidate_cur, font_size) <= max_px
                ):
                    out[i - 1] = candidate_prev
                    out[i] = candidate_cur
                    changed = True
        out = [line for line in out if line.strip()]
    return out



def _wrap_back_text(
    text: str,
    max_px: float,
    font_size: float,
    *,
    slack_px: Optional[float] = None,
    greedy_max_px: Optional[float] = None,
    cap_px: Optional[float] = None,
) -> List[str]:
    """Word-wrap for back-zone copy.

    *greedy_max_px* — optional wider line-fill budget for the greedy pass (overrides slack).
    *cap_px* — optional max measured width before character-splitting (defaults to *max_px*).
    """
    if greedy_max_px is not None:
        limit = max(40.0, float(greedy_max_px))
    else:
        slack = float(BACK_LORE_WRAP_SLACK_PX if slack_px is None else slack_px)
        limit = max(40.0, float(max_px) - slack)
    raw = _balanced_wrap_text_by_width(text, limit, font_size)
    out: List[str] = []
    cap_ref = float(cap_px) if cap_px is not None else float(max_px)
    cap = cap_ref - 2.0
    for ln in raw:
        if _orbitron_width(ln, font_size) <= cap:
            out.append(ln)
        else:
            out.extend(_split_oversized_line(ln, cap, font_size))
    return _rebalance_back_wrap_lines(out, cap, font_size)


def _title_line_within_band(
    line: str, max_px: float, font_size: float, slack: float = TITLE_WRAP_MEASURE_SLACK
) -> bool:
    """True if measured width stays inside the padded band (with slack against clipping)."""
    return _orbitron_width(line, font_size) <= max_px - slack


def _reflow_title_peel_until_fit(
    lines: List[str], max_px: float, font_size: float, slack: float = TITLE_WRAP_MEASURE_SLACK
) -> List[str]:
    """Repeatedly move the last word of an over-wide line to the next line until stable."""
    out = [ln.strip() for ln in lines if ln.strip()]
    if not out:
        return []
    max_iterations = max(32, sum(len(ln) for ln in out))
    for _ in range(max_iterations):
        changed = False
        i = 0
        while i < len(out):
            while " " in out[i] and not _title_line_within_band(out[i], max_px, font_size, slack):
                left, _, right = out[i].rpartition(" ")
                if not left:
                    break
                out[i] = left.strip()
                if i + 1 < len(out):
                    out[i + 1] = f"{right} {out[i + 1]}".strip()
                else:
                    out.append(right)
                changed = True
            i += 1
        if not changed:
            break
    return [ln for ln in out if ln]


def _title_best_two_line_split(
    words: List[str], max_px: float, font_size: float
) -> Optional[List[str]]:
    """If the title fits on two lines with whole words, return [line1, line2].
    Among all valid splits, prefer the most visually balanced (min width difference).
    """
    if len(words) < 2:
        return None
    slack = TITLE_WRAP_MEASURE_SLACK
    best: Optional[List[str]] = None
    best_diff = float("inf")
    for k in range(1, len(words)):
        l1 = " ".join(words[:k])
        l2 = " ".join(words[k:])
        if _title_line_within_band(l1, max_px, font_size, slack) and _title_line_within_band(
            l2, max_px, font_size, slack
        ):
            diff = abs(_orbitron_width(l1, font_size) - _orbitron_width(l2, font_size))
            if diff < best_diff:
                best_diff = diff
                best = [l1, l2]
    return best


def _split_oversized_line(line: str, max_px: float, font_size: float) -> List[str]:
    """Break a single line into segments that fit max_px (Orbitron width model).

    Tries word-level splits first; character-splits only as a last resort for
    single tokens (no spaces) that are individually wider than max_px.
    """
    if _orbitron_width(line, font_size) <= max_px:
        return [line]
    # Word-level pass: re-wrap at the tighter limit
    if " " in line:
        out: List[str] = []
        for sub in _wrap_text_by_width(line, max_px, font_size):
            if _orbitron_width(sub, font_size) <= max_px:
                out.append(sub)
            else:
                # Single oversized token — fall through to char-split below
                out.extend(_split_oversized_line(sub, max_px, font_size))
        return out
    # Single token with no spaces — character-split as last resort
    out: List[str] = []
    chunk = ""
    for ch in line:
        trial = chunk + ch
        if _orbitron_width(trial, font_size) <= max_px:
            chunk = trial
        else:
            if chunk:
                out.append(chunk)
            chunk = ch
    if chunk:
        out.append(chunk)
    return out if out else [line]


def _wrap_card_title_lines(text: str, max_px: float, font_size: float) -> List[str]:
    """Greedy word-wrap for the title band: accumulate words left-to-right;
    wrap to next line when the next word would cross the padding boundary.
    Character-splits only for a single token that alone exceeds the limit.
    """
    limit = max_px - TITLE_WRAP_MEASURE_SLACK
    lines = _wrap_text_by_width(str(text or ""), limit, font_size)
    result: List[str] = []
    for line in lines:
        if _orbitron_width(line, font_size) <= limit:
            result.append(line)
        else:
            result.extend(_split_oversized_line(line, limit, font_size))
    return result


def _format_archetype_math_lines(raw: str) -> List[str]:
    chunks = [c.strip() for c in str(raw or "").split("|") if c.strip()]
    if not chunks:
        return []
    lines: List[str] = []
    for chunk in chunks:
        lines.extend(_approx_wrap_text(chunk, 44) or [chunk])
    return lines


def _fmt_back_date(raw: Any) -> str:
    s = str(raw or "").strip()
    if not s:
        return "—"
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    return s


def _normalize_probability_cohort_label(raw: Any) -> str:
    """Normalize legacy occurrence labels to the new Probability Cohort copy."""
    value = " ".join(str(raw or "").replace("\n", " ").split())
    if not value:
        return "PROBABILITY COHORT: --"
    value = value.replace("[", "").replace("]", "").strip()
    upper = value.upper()
    if "OCCURENCE" in upper:
        value = value.replace("Occurence", "Probability Cohort")
        value = value.replace("OCCURENCE", "PROBABILITY COHORT")
        upper = value.upper()
    if "OCCURRENCE" in upper:
        value = value.replace("Occurrence", "Probability Cohort")
        value = value.replace("OCCURRENCE", "PROBABILITY COHORT")
        upper = value.upper()
    if upper.startswith("PROBABILITY COHORT:"):
        payload = value.split(":", 1)[1].strip() if ":" in value else value
    else:
        payload = value
    return f"PROBABILITY COHORT: {payload}"


def _qr_modules_svg(payload: str, ox: float, oy: float, draw_size: float) -> str:
    """Black QR modules inside a square; outer stroke/fill drawn by caller."""
    try:
        import qrcode
        from qrcode.constants import ERROR_CORRECT_M
    except ImportError:
        print(
            "Warning: package 'qrcode' not installed; card back QR is omitted. "
            "Install with: pip install qrcode (see requirements.txt).",
            file=sys.stderr,
        )
        return ""
    text = (payload or "").strip() or "polystars"
    qr = qrcode.QRCode(version=None, error_correction=ERROR_CORRECT_M, box_size=1, border=0)
    qr.add_data(text)
    qr.make(fit=True)
    matrix = qr.get_matrix()
    n = len(matrix)
    if not n:
        return ""
    cell = draw_size / n
    parts: List[str] = []
    for r in range(n):
        for c in range(n):
            if matrix[r][c]:
                x = ox + c * cell
                y = oy + r * cell
                parts.append(
                    f'<rect x="{x:.4f}" y="{y:.4f}" width="{cell:.4f}" height="{cell:.4f}" fill="black"/>'
                )
    return "".join(parts)


def _back_block_bottom(start_y: float, line_count: int, lh: int, ink_ext: float) -> float:
    """Last ink bottom for a hanging-baseline multiline <text> with even lh between baselines."""
    if line_count <= 0:
        return start_y
    return start_y + (line_count - 1) * lh + ink_ext


def _back_clamp_line_count(lines: List[str], max_lines: int) -> List[str]:
    """Keep at most max_lines; truncate and add ellipsis on the last line if needed."""
    if max_lines < 1:
        return []
    if len(lines) <= max_lines:
        return lines
    result = list(lines[:max_lines])
    last = result[-1].rstrip()
    if not last.endswith("…"):
        result[-1] = last + "…"
    return result


def generate_card_back_svg(data: Dict[str, Any]) -> str:
    """Build back-of-card SVG."""
    lh = BACK_LINE_HEIGHT
    style_lh = f"line-height:{lh}px"
    arch_ud = (
        "text-decoration:underline;text-decoration-thickness:1px;text-underline-offset:1.66px"
    )

    archetype_raw = _archetype_style_key(str(data.get("archetype", "") or "").strip().upper() or "OPERATOR")
    dz_fill, _, _ = dz_style(archetype_raw)
    yld = str(data.get("yield", "BASE") or "BASE").upper()
    yield_color = get_ptier_color(yld)
    if "url(" in yield_color:
        border_fill = "url(#border-gradient)"
    elif yld in _YIELD_TIERS_DEEP_BORDER:
        border_fill = DEEP_BLUE_CARD_BORDER
    else:
        border_fill = yield_color

    lore = _esc(str(data.get("card_lore", "") or "No topology data available."))
    archetype_desc = _esc(str(data.get("archetype_description", "") or "No archetype description."))
    archetype_math_raw = str(data.get("archetype_math", "") or "No statistical pattern.")
    archetype_math = " ".join(archetype_math_raw.replace("\n", " ").split())
    if not archetype_math:
        archetype_math = "No statistical pattern."

    rarity_raw = str(data.get("rarity_bracket", "") or "").strip()
    rarity_esc = _esc(_normalize_probability_cohort_label(rarity_raw))

    mint = data.get("collection_mint_number")
    mint_str = str(mint).strip() if mint is not None and str(mint).strip() != "" else "—"
    season_size = data.get("season_size")
    if season_size is None:
        season_size = data.get("season_total_supply")
    if season_size is None:
        season_size = data.get("total_supply")
    size_str = str(season_size).strip() if season_size is not None and str(season_size).strip() != "" else "—"
    d1 = _fmt_back_date(data.get("season_start_date"))
    d2 = _fmt_back_date(data.get("season_end_date"))

    qr_payload = str(
        data.get("qr_payload")
        or data.get("qr_url")
        or data.get("card_slug")
        or data.get("proxy_wallet")
        or "polystars"
    ).strip()

    qr_ox = BACK_QR_X + BACK_QR_INNER_PAD
    qr_oy = BACK_QR_Y + BACK_QR_INNER_PAD
    qr_draw = BACK_QR_SIZE - 2 * BACK_QR_INNER_PAD
    qr_modules = _qr_modules_svg(qr_payload, qr_ox, qr_oy, qr_draw)
    qr_block = (
        f'<g id="card-back-qr">'
        f'<rect x="{BACK_QR_X}" y="{BACK_QR_Y}" width="{BACK_QR_SIZE}" height="{BACK_QR_SIZE}" '
        f'fill="#B6BBC8" stroke="black" stroke-width="1"/>'
        f"{qr_modules}</g>"
    )

    font_b64 = _load_font_b64()
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

    lore_lines = _wrap_back_text(lore, float(BACK_BODY_WRAP_W), 14.0, slack_px=BACK_LORE_WRAP_SLACK_PX)

    # Dynamic layout: lore may need more lines than the default zone allows.
    # Push the separator (and everything below it) down by the overflow amount.
    # QR code and the two bottom meta lines always stay pinned to the card bottom.
    #
    # Equal-gap rule: gap above sep == gap below sep.
    # Gap below sep is fixed = BACK_ARCH_HEADER_Y - BACK_SEP_LINE_Y (≈19px).
    # So we place sep at lore_block_bottom + that same gap (floor: BACK_SEP_LINE_Y).
    _sep_half_gap = float(BACK_ARCH_HEADER_Y - BACK_SEP_LINE_Y)
    lore_block_bottom = _back_block_bottom(BACK_LORE_Y, len(lore_lines), lh, BACK_BODY_INK_EXT)
    sep_y = max(float(BACK_SEP_LINE_Y), lore_block_bottom + _sep_half_gap)
    push_down = sep_y - BACK_SEP_LINE_Y
    arch_header_y = BACK_ARCH_HEADER_Y + push_down
    arch_y        = BACK_ARCH_Y        + push_down
    desc_y        = BACK_DESC_Y        + push_down

    desc_lines = _wrap_back_text(
        archetype_desc,
        float(BACK_BODY_WRAP_W),
        14.0,
        slack_px=BACK_DESC_WRAP_SLACK_PX,
        greedy_max_px=float(BACK_BODY_WRAP_W) + BACK_DESC_WRAP_ALLOWANCE_PX,
        cap_px=float(BACK_BODY_WRAP_W) + BACK_DESC_WRAP_ALLOWANCE_PX,
    )

    lore_svg = "".join(
        f'<tspan x="{BACK_TEXT_X}" dy="{0 if i == 0 else lh}">{line}</tspan>'
        for i, line in enumerate(lore_lines)
    )
    desc_svg = "".join(
        f'<tspan x="{BACK_TEXT_X}" dy="{0 if i == 0 else lh}">{line}</tspan>'
        for i, line in enumerate(desc_lines)
    )

    archetype_line_text = _esc(f"ARCHETYPE:\u00a0{archetype_raw}")
    stat_header = "STATISTICAL\u00a0PATTERN:"

    desc_bottom = _back_block_bottom(desc_y, len(desc_lines), lh, BACK_BODY_INK_EXT)
    back_rarity_y = desc_bottom + BACK_GAP_SECTION
    y_stat_header = back_rarity_y + lh + BACK_GAP_STAT_TO_METRICS
    y_first_metric = y_stat_header + lh + BACK_GAP_STAT_TO_METRICS

    inner_bottom = BACK_DZ_Y + BACK_DZ_H

    if "|" in archetype_math_raw:
        metric_lines = _format_archetype_math_lines(archetype_math_raw) or [archetype_math]
        metrics_body = "".join(
            f'<tspan x="{BACK_TEXT_X}" dy="{0 if i == 0 else lh}">{_esc(line)}</tspan>'
            for i, line in enumerate(metric_lines)
        )
        metrics_svg = (
            f'<text x="{BACK_TEXT_X}" y="{round(y_first_metric, 1)}" text-anchor="start" '
            f'dominant-baseline="hanging" font-size="14" fill="white" '
            f'style="{style_lh}">{metrics_body}</text>'
        )
    else:
        metric_font_size, metric_text_length = _fit_single_line_text(
            archetype_math, float(BACK_BODY_WRAP_W), 14.0, min_font_size=10.0
        )
        metric_text_length_attr = (
            f' textLength="{round(metric_text_length, 1)}" lengthAdjust="spacingAndGlyphs"'
            if metric_text_length is not None
            else ""
        )
        metrics_svg = (
            f'<text x="{BACK_TEXT_X}" y="{round(y_first_metric, 1)}" text-anchor="start" '
            f'dominant-baseline="hanging" font-size="{metric_font_size:g}" fill="white" '
            f'style="{style_lh}"{metric_text_length_attr}>{_esc(archetype_math)}</text>'
        )

    meta_y1 = y_first_metric + BACK_BODY_INK_EXT + BACK_GAP_METRICS_TO_META

    season_card_raw = f"SEASON CARD NUMBER:\u00a0{mint_str}/{size_str}"
    season_dates_raw = f"SEASON DATES:\u00a0{d1}\u00a0-\u00a0{d2}"
    sc_wrap = _wrap_text_by_width(season_card_raw, float(BACK_META_WRAP_W), 10.0)
    if not sc_wrap:
        sc_wrap = [season_card_raw]
    sd_wrap = _wrap_text_by_width(season_dates_raw, float(BACK_META_WRAP_W), 10.0)
    if not sd_wrap:
        sd_wrap = [season_dates_raw]
    sc_lines_esc = [_esc(s) for s in sc_wrap]
    sd_lines_esc = [_esc(s) for s in sd_wrap]

    n_meta_lines = len(sc_lines_esc) + len(sd_lines_esc)
    inner_clear = 4.0
    qr_bottom = BACK_QR_Y + BACK_QR_SIZE
    meta_y1_pin = qr_bottom - (n_meta_lines - 1) * lh - BACK_META_INK_EXT
    meta_y1_pin_inner = inner_bottom - inner_clear - (n_meta_lines - 1) * lh - BACK_META_INK_EXT
    # Bottom meta rows are always pinned; they never follow the metrics down.
    meta_y1 = min(meta_y1_pin, meta_y1_pin_inner) + BACK_META_Y_OFFSET

    season_meta_svg = "".join(
        f'<tspan x="{BACK_TEXT_X}" dy="{0 if i == 0 else BACK_META_LH}">{line}</tspan>'
        for i, line in enumerate(sc_lines_esc)
    )
    y_dates_block = meta_y1 + len(sc_lines_esc) * BACK_META_LH
    dates_meta_svg = "".join(
        f'<tspan x="{BACK_TEXT_X}" dy="{0 if i == 0 else BACK_META_LH}">{line}</tspan>'
        for i, line in enumerate(sd_lines_esc)
    )

    return f'''<svg viewBox="0 0 {CANVAS_W} {CANVAS_H}"
    fill="none"
    xml:space="preserve"
    font-family="Orbitron, sans-serif"
    font-weight="700"
    letter-spacing="0.1em"
    xmlns="http://www.w3.org/2000/svg">
<defs>
  <style>
    {font_css}
    text {{
      font-family: 'Orbitron', sans-serif;
      font-stretch: normal;
      font-style: normal;
      font-weight: 700;
      letter-spacing: 0.1em;
      text-rendering: optimizeLegibility;
    }}
    text[dominant-baseline="hanging"] {{
      dominant-baseline: auto;
      transform: translateY(0.72em);
    }}
  </style>
  <linearGradient id="anomaly-gradient" x1="0.5" y1="0" x2="0.5" y2="1">
    <stop offset="15%" stop-color="#28AEAE"/>
    <stop offset="30%" stop-color="#4A99BB"/>
    <stop offset="45%" stop-color="#8C92D1"/>
    <stop offset="60%" stop-color="#C38CCE"/>
    <stop offset="75%" stop-color="#BB7382"/>
    <stop offset="90%" stop-color="#C5CC84"/>
  </linearGradient>
  <linearGradient id="icarus-gradient" x1="0.5" y1="0" x2="0.5" y2="1">
    <stop offset="16%" stop-color="#871919"/>
    <stop offset="32%" stop-color="#FFA20C"/>
    <stop offset="50%" stop-color="#FDDFA9"/>
    <stop offset="68%" stop-color="#FFA20C"/>
    <stop offset="84%" stop-color="#871919"/>
  </linearGradient>
  <linearGradient id="extractor-gradient" x1="0.5" y1="0" x2="0.5" y2="1">
    <stop offset="15%" stop-color="#7F8E3D"/>
    <stop offset="30%" stop-color="#7A5CBE"/>
    <stop offset="45%" stop-color="#9D653E"/>
    <stop offset="60%" stop-color="#2E9F8C"/>
    <stop offset="75%" stop-color="#2F7346"/>
    <stop offset="90%" stop-color="#2E3160"/>
  </linearGradient>
  <linearGradient id="bot-gradient" x1="0.5" y1="0" x2="0.5" y2="1">
    <stop offset="0%" stop-color="#554467"/>
    <stop offset="50%" stop-color="#554467"/>
    <stop offset="100%" stop-color="#7C6C8D"/>
  </linearGradient>
  <linearGradient id="burner-gradient" x1="0.5" y1="0" x2="0.5" y2="1">
    <stop offset="0%" stop-color="#983232"/>
    <stop offset="50%" stop-color="#983232"/>
    <stop offset="100%" stop-color="#321010"/>
  </linearGradient>
  <linearGradient id="equilibrium-gradient" x1="0.5" y1="0" x2="0.5" y2="1">
    <stop offset="0%" stop-color="#915238"/>
    <stop offset="50%" stop-color="#915238"/>
    <stop offset="100%" stop-color="#2B1810"/>
  </linearGradient>
  <linearGradient id="signal-gradient" x1="0.5" y1="0" x2="0.5" y2="1">
    <stop offset="0%" stop-color="#0A2A2A"/>
    <stop offset="50%" stop-color="#0A2A2A"/>
    <stop offset="100%" stop-color="#134E4E"/>
  </linearGradient>
  <linearGradient id="graviton-gradient" x1="0.5" y1="0" x2="0.5" y2="1">
    <stop offset="0%" stop-color="#996C2C"/>
    <stop offset="50%" stop-color="#996C2C"/>
    <stop offset="100%" stop-color="#33240F"/>
  </linearGradient>
  <linearGradient id="vector-gradient" x1="0.5" y1="0" x2="0.5" y2="1">
    <stop offset="0%" stop-color="#1D4191"/>
    <stop offset="50%" stop-color="#1D4191"/>
    <stop offset="100%" stop-color="#08132B"/>
  </linearGradient>
  <linearGradient id="border-gradient" gradientUnits="userSpaceOnUse" x1="170.904" y1="3" x2="345.096" y2="799">
    <stop offset="0.2" stop-color="#FFBF00"/>
    <stop offset="0.4" stop-color="#8A2BE2"/>
    <stop offset="0.6" stop-color="#0051FF"/>
    <stop offset="0.8" stop-color="#51FF48"/>
    <stop offset="1" stop-color="white"/>
  </linearGradient>
  <filter id="frame-blur" x="-10%" y="-10%" width="120%" height="120%">
    <feGaussianBlur stdDeviation="4"/>
  </filter>
</defs>
<rect width="{CANVAS_W}" height="{CANVAS_H}" fill="#0B0C10"/>
<rect x="{FRAME_X}" y="{FRAME_Y}" width="{FRAME_W}" height="{FRAME_H}" fill="{border_fill}"
      filter="url(#frame-blur)"/>
<rect x="{BACK_DZ_X}" y="{BACK_DZ_Y}" width="{BACK_DZ_W}" height="{BACK_DZ_H}" fill="{dz_fill}" stroke="black"/>

<text x="{BACK_TEXT_X_HEAD}" y="40" text-anchor="start" dominant-baseline="hanging" font-size="20" fill="black" style="{style_lh}">[&#160;EVENT&#160;TOPOLOGY&#160;]</text>
<text x="{BACK_TEXT_X}" y="{BACK_LORE_Y}" text-anchor="start" dominant-baseline="hanging" font-size="14" fill="white" style="{style_lh}">{lore_svg}</text>

<line x1="{BACK_SEP_X1}" y1="{round(sep_y, 1)}" x2="{BACK_SEP_X2}" y2="{round(sep_y, 1)}" stroke="#333333" stroke-width="2"/>
<rect x="{BACK_SEP_CAP_LEFT_X}" y="{round(sep_y - 2, 1)}" width="4" height="4" fill="#333333"/>
<rect x="{BACK_SEP_CAP_RIGHT_X}" y="{round(sep_y - 2, 1)}" width="4" height="4" fill="#333333"/>

<text x="{BACK_TEXT_X_HEAD}" y="{round(arch_header_y, 1)}" text-anchor="start" dominant-baseline="hanging" font-size="20" fill="black" style="{style_lh}">[&#160;BEHAVIORAL&#160;SIGNATURE&#160;]</text>
<text x="{BACK_TEXT_X}" y="{round(arch_y, 1)}" text-anchor="start" dominant-baseline="hanging" font-size="14" fill="white" style="{style_lh};{arch_ud}">{archetype_line_text}</text>
<text x="{BACK_TEXT_X}" y="{round(desc_y, 1)}" text-anchor="start" dominant-baseline="hanging" font-size="14" fill="white" style="{style_lh}">{desc_svg}</text>
<text x="{BACK_TEXT_X}" y="{round(back_rarity_y, 1)}" text-anchor="start" dominant-baseline="hanging" font-size="12" fill="white" style="{style_lh}">{rarity_esc}</text>

<text x="{BACK_TEXT_X}" y="{round(y_stat_header, 1)}" text-anchor="start" dominant-baseline="hanging" font-size="14" fill="black" style="{style_lh};{arch_ud}">{stat_header}</text>
{metrics_svg}

<text x="{BACK_TEXT_X}" y="{round(meta_y1, 1)}" text-anchor="start" dominant-baseline="hanging" font-size="10" fill="white" style="line-height:{BACK_META_LH}px">{season_meta_svg}</text>
<text x="{BACK_TEXT_X}" y="{round(y_dates_block, 1)}" text-anchor="start" dominant-baseline="hanging" font-size="10" fill="white" style="line-height:{BACK_META_LH}px">{dates_meta_svg}</text>

{qr_block}

<rect x="1.5" y="1.5" width="513" height="799" stroke="#333333" stroke-width="3"/>
</svg>'''


# ═══════════════════════════════════════════════════════════════════════════
# SAMPLE DATA (mirrors the data contract in Section 11)
# ═══════════════════════════════════════════════════════════════════════════

_SAMPLE_SEASON_TYPE = "genesis"
_GENESIS_START = os.getenv("GENESIS_START_DATE", "2024-06-01")
_GENESIS_END   = os.getenv("GENESIS_END_DATE",   "2026-03-01")
_SAMPLE_START  = _GENESIS_START if _SAMPLE_SEASON_TYPE == "genesis" else "2026-01-15"
_SAMPLE_END    = _GENESIS_END   if _SAMPLE_SEASON_TYPE == "genesis" else "2026-01-25"

SAMPLE_DATA: Dict[str, Any] = {
    "season_type":       _SAMPLE_SEASON_TYPE,
    "season_number":     3,
    "recurrence":        "unique",
    "claim_type":        "origin",
    "image_url":         "sample.jpg",
    "card_title":        "Government Shutdown Duration 2025",
    "primary_tag":       "CELEBRITIES",
    "primary_tag_color": "#51E147",
    "secondary_tag":     "MARCH MADNESS",
    "entry_bracket":     "[0.60 - 0.80]",
    "proxy_wallet":      "0xBb8E703abc123def456",
    "edge":              "BASE",
    "yield":             "P50",
    "gravity":           "BASE",
    "archetype":         "ICARUS",
    "card_lore":         "Standard edition pricing breach at triple digits signals industry inflection. Historical AAA launch data suggests $69.99 baseline holds. Resolution hinges on store listings by Feb 2026 deadline.",
    "archetype_description": "Mechanical routing protocol detected. This entity operates with massive kinetic force but generates zero directional trajectory, executing purely on structural arbitrage and fractional spreads. Devoid of human psychology or predictive bias, they exist solely to bridge conditional markets, merge underlying tokens, and enforce absolute liquidity ceilings. They do not predict the future; they mathematically process the emotions of the swarm.",
    "archetype_math":    "(P(E) ≥ 0.97 & Vol < 50) ∪ Mid-Trend Lag",
    "rarity_bracket":    "PROBABILITY COHORT: 1.0% - 2.0%",
    "leaderboard_rank":  63564,
    "collection_mint_number": 7,
    "season_size":       333,
    "season_start_date": _SAMPLE_START,
    "season_end_date":   _SAMPLE_END,
    "qr_payload":        "https://polystars.app/cards/card-genesis-s1-595f047311a45c524f706f5d1153fdac-f429c3e4c1394d35bc047257fe913fdd",
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
    svg_back = generate_card_back_svg(card_data)

    out_path = "scripts/cardgen/output.svg"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(svg)
    out_back_path = "scripts/cardgen/output_back.svg"
    with open(out_back_path, "w", encoding="utf-8") as f:
        f.write(svg_back)

    arch = _archetype_style_key(str(card_data.get("archetype", "") or "").strip().upper() or "OPERATOR")
    print(f"Archetype: {arch}")
    print(f"SVG written to:   {out_path}")
    print(f"Back SVG written: {out_back_path}")
