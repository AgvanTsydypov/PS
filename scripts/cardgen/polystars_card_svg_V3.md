

---

## 0. How To Read This Document

This document is the **single source of truth** for the SVG card template. The Figma file is a visual mockup — this document is the implementation spec.

The card is assembled by injecting variables into a deterministic SVG template. Three upstream agents (The Quant, The Colorist, The Renderer) produce structured data that this template consumes. A fourth agent (The Analyst) generates the back-of-card narrative at claim time.

---

# PART A: THE FRONT OF THE CARD

---

## 1. Card Dimensions & Structure

```
Canvas:              516 × 802 px
Outer frame:         510 × 796 px, rounded corners 30px, offset 3px from each edge
Background fill:     #0B0C10
Outer frame border:  3px, color is DYNAMIC (see Section 9)
                     2px blur applied to the inner border rectangle
```

Three visual zones:

```
┌──────────────────────────────────┐
│  IMAGE ZONE (top)                │  490 × 490 px, corners 23px
│  - Event artwork                 │
│  - Logo overlay (top-left)       │
│  - Metadata badge (top-right)    │
├──────────────────────────────────┤
│  TITLE BRIDGE (overlapping)      │  460 × 63 px bar
├──────────────────────────────────┤
│  DATA ZONE (bottom)              │  490 × 286 px, corners 22px
│  - Sector / Node                 │  Border: 1px solid #FFFFFF
│  - Cognitive Telemetry header    │  Fill: DYNAMIC (see Section 10)
│  - Entry Bracket / Wallet        │
│  - Edge / Yield / Gravity        │
│  - Polymarket Global Rank        │
└──────────────────────────────────┘
```

---

## 2. Typography

```
Font:       Orbitron Bold (Google Fonts)
Weight:     700 (Bold) — everywhere without exception
Case:       ALL CAPS — enforced programmatically
```

---

## 3. The Master а

Reused for the highest rarity tier (Anomaly / P99). Appears conditionally.

```
Type:           Linear gradient
Angle:          ~12° counter-clockwise from vertical (≈168° CSS)
Color stops:    Equally spaced at 20% intervals, top-to-bottom:

  0%          #FFFFFF     White
  20%         #51FF48     Green
  40%         #0051FF     Blue
  60%         #8A2BE2     Purple
  80%         #FFBF00     Gold
```

```xml
<linearGradient id="master-gradient" x1="0.5" y1="0" x2="0.45" y2="1">
  <stop offset="0%"   stop-color="#FFFFFF"/>
  <stop offset="20%"  stop-color="#51FF48"/>
  <stop offset="40%"  stop-color="#0051FF"/>
  <stop offset="60%"  stop-color="#8A2BE2"/>
  <stop offset="80%"  stop-color="#FFBF00"/>
</linearGradient>
```

The angle must be identical across all gradient-bearing elements.

---

## 4. Tier Color Maps

### 4.1 Entry Bracket Color Map (Absolute Conviction — 5 static tiers)

| CWAP Range | Name | Color |
|-----------|------|-------|
| 0.00 – 0.20 | ANOMALY | `url(#master-gradient)` |
| 0.20 – 0.40 | ORACLE | `#FFBF00` |
| 0.40 – 0.60 | OUTLIER | `#0051FF` |
| 0.60 – 0.80 | VECTOR | `#00FF2F` |
| 0.80 – 0.97 | HARVESTER | `#FFFFFF` |

Entries ≥ 0.97 CWAP are purged entirely (bot filter).

### 4.2 P-Tier Color Map (Edge, Yield, Gravity — 5 dynamic tiers)

| P-Tier | Color |
|--------|-------|
| P99 | `url(#master-gradient)` |
| P90 | `#FFBF00` |
| P70 | `#0051FF` |
| P50 | `#00FF2F` |
| Base | `#FFFFFF` |

### 4.3 Equivalence Table

| Color | Entry Bracket | P-Tier | Position |
|-------|--------------|--------|----------|
| Gradient | ANOMALY | P99 | 1st (rarest) |
| #FFBF00 | ORACLE | P90 | 2nd |
| #0051FF | OUTLIER | P70 | 3rd |
| #00FF2F | VECTOR | P50 | 4th |
| #FFFFFF | HARVESTER | Base | 5th |

## 5. Dynamic Fields — Metadata Badge (Top-Right)

A semi-transparent badge in the top-right corner of the image zone. Three lines of metadata stacked vertically.

### 5.0 Badge Background

```
Dimensions:     180 × 62 px, rounded corners 10px
Fill:           #000000 at 70% opacity
Border:         1px solid #666666
Position:       Top-right area of image zone
```

### 5.1 SEASON (Line 1)

**Data source:** `season_type` enum + `season_number` integer

```
Format:     "SEASON: " + [value]
Font-size:  10px
Tracking:   1px

"SEASON: "  → always #FFFFFF
```

| Condition | Value Text | Value Color |
|-----------|-----------|-------------|
| Genesis season | `GENESIS` | #00FFFF (Cyan) |
| Standard season | `STANDARD #[N]` | #B1ABAB (Muted grey) |

`[N]` = season number (integer, starting from 1).

### 5.2 INSTANCE (Line 2)

**Data source:** `series.recurrence` (null | "daily" | "weekly" | "monthly")

```
Format:     "INSTANCE: " + [value]
Font-size:  10px
Tracking:   1px

"INSTANCE: "  → always #FFFFFF
```

| Condition | Value Text | Value Color |
|-----------|-----------|-------------|
| `recurrence` is null or missing | `SINGULAR` | #E8A72F (Amber) |
| `recurrence` has any value | `FRACTAL` | #2A8FEE (Blue) |

We do NOT differentiate between daily/weekly/monthly on the card face. Any non-null recurrence = FRACTAL. The specific recurrence type affects the **border rarity accent** (Section 8), not this field.

### 5.3 OWNERSHIP (Line 3)

**Data source:** `claim_type` enum ("origin" | "looter")

```
Format:     "[ " + [value] + " ]"
Font-size:  10px
Tracking:   1px

Brackets "[ " and " ]"  → always #FFFFFF
```

| Condition | Value Text | Value Color |
|-----------|-----------|-------------|
| Origin wallet | `ORIGIN SECURED` | #06EE6E (Green) |
| Looter wallet | `HOSTILE TAKEOVER` | #FF007F (Hot pink) |

---

## 6. Dynamic Fields — Image & Title Zone

### 6.1 Event Image

```
Source:          image_url from backend (pre-generated 1024×1024 PNG/WebP)
Display size:    490 × 490 px
Corner radius:   23px (applied via clip-path)
Position:        Top of card, centered horizontally
Processing:      NONE — no filters, overlays, or modifications
```

### 6.2 Polystars Logo (Static)

```
Source:          Static asset: polystars_logo.png
Dimensions:     110 × 110 px
Position:       Top-left corner, overlapping the event image
```

### 6.3 Event Title (Terminal Header)

**Data source:** `card_title` from Quant Agent (max 5 words)

```
Container:       460 × 63 px rounded rectangle, centered horizontally
                 Fill: rgba(13, 13, 13, 0.95)
                 Border: 2px solid #333333
                 Corner radius: 10px
                 Overlaps the boundary between image zone and data zone

Text:            color: #FFFFFF
                 font-size: 16px
                 tracking: 1.6px
                 Centered in container
                 Max text width: ~416px
                 ALL CAPS enforced
```

---

## 7. Dynamic Fields — Data Zone

### 7.1 Primary Tag (SECTOR)

**Data source:** `primary_tag` (string) + `primary_tag_color` (hex from DB)

```
Label:       "SECTOR:"      color: #FFFFFF    font-size: 18px    tracking: 1.8px
                             text-shadow: 0 4px 4px black
Value:       [primary_tag]   color: {{primary_tag_color}}    font-size: 18px    tracking: 1.8px
                             ALL CAPS enforced
```

**CRITICAL:** The primary tag color is NOT hardcoded. It is retrieved from the database where the Colorist Agent assigned it using HSL hue-distance constraints. The SVG template must accept an arbitrary hex value for this field.

### 7.2 Secondary Tag (NODE)

**Data source:** `secondary_tag` (string) from backend

```
Full line:   "NODE: [secondary_tag]"
Color:       #888888 — FIXED for both label and value
Font-size:   16px
             text-shadow: 0 4px 4px rgba(0,0,0,0.25)
             ALL CAPS enforced
```

Always #888888 regardless of content.

### 7.3 "COGNITIVE TELEMETRY" Header (Static text, styled)

```
Text:        "COGNITIVE TELEMETRY"
Font-size:   20px
Tracking:    2px
Fill:        Radial gradient, horizontally stretched
               Center: rgba(239, 226, 226, 1)
               Edge:   rgba(104, 98, 98, 1)
               Intermediate stops at 25%, 50%, 75%
```

### 7.4 Entry Bracket + Wallet Address Line

A single composite line with the trader's behavioral archetype in brackets, a separator, then the truncated proxy wallet address.

**Data source:** `entry_bracket`  + `proxy_wallet` (hex address)

```
Visual format:   [ ORACLE ] :// 0xBb8E703...
                 ─┬─────┬─  ─┬─  ─────┬──────
                  │     │    │        └─ wallet_color (= EDGE color)
                  │     │    └─ static white separator
                  │     └─ bracket_color (= EDGE color, from Tier Map)
                  └─ white brackets

Font-size:       Brackets/separator: 15px, tracking 1.5px
                 Wallet: 14px, tracking 1.4px
```

**Bracket color** = Tier Color Map lookup on `entry_bracket` value  
**Wallet color** = always identical to EDGE color (= bracket color)  
**Wallet display** = first 9 characters of `proxy_wallet` + `...`

### 7.5 EDGE (Risk Archetype)

**Data source:** `edge` field (derived from `capital_weighted_vwap`)

```
Label:       "EDGE:"          color: #FFFFFF    font-size: 12px    tracking: 1.2px
                               text-shadow: 0 4px 4px black
Value:       [edge name]       color: Tier Color Map    font-size: 20px    tracking: 2px
```

| CWAP Range | Display Value |
|-----------|--------------|
| 0.00 – 0.10 | ANOMALY |
| 0.10 – 0.30 | ORACLE |
| 0.30 – 0.50 | OUTLIER |
| 0.50 – 0.70 | VECTOR |
| 0.70 – 0.90 | VALIDATOR |
| 0.90 – 1.00 | HARVESTER |

### 7.6 YIELD (Skill / Capital Efficiency)

**Data source:** `skill` field (PERCENT_RANK on `roi_percentage` by `event_slug`)

```
Label:       "YIELD:"         color: #FFFFFF    font-size: 12px    tracking: 1.2px
                               text-shadow: 0 4px 4px black
Value:       [skill tier]      color: Tier Color Map    font-size: 20px    tracking: 2px
```

| Percentile Rank | Display Value |
|----------------|--------------|
| ≥ 0.999 | P999 |
| ≥ 0.99 | P99 |
| ≥ 0.95 | P95 |
| ≥ 0.80 | P80 |
| ≥ 0.50 | P50 |
| < 0.50 | Base |

### 7.7 GRAVITY (Influence / Capital Footprint)

**Data source:** `influence` field (PERCENT_RANK on `total_volume` by `event_slug`)

```
Label:       "GRAVITY:"       color: #FFFFFF    font-size: 12px    tracking: 1.2px
                               text-shadow: 0 4px 4px black
Value:       [influence tier]  color: Tier Color Map    font-size: 20px    tracking: 2px
```

Same tier thresholds and display values as YIELD (Section 7.6).

### 7.8 Polymarket Global Rank

**Data source:** `leaderboard_rank` (integer)

```
"POLYMARKET"         → color: #2E5CFF     font-size: 16px    tracking: 1.6px
" GLOBAL RANK:"      → color: #FFFFFF      font-size: 16px    tracking: 1.6px
" #[rank]"           → color: #FFFFFF      font-size: 16px    tracking: 1.6px
                       text-shadow: 0 4px 4px black
```

---


## 8. Border Rarity System

### 8.1 Recurrence Accent

| Recurrence | Rarity | Accent |
|-----------|--------|--------|
| null/missing | Tier 1 | Gold accent |
| "daily" | Tier 2 | Silver accent |
| "weekly"/"monthly" | Tier 3 | Bronze accent |

### 8.2 Primary Border Color

Driven by YIELD via propagation (Section 9).

---

## 9. Color Propagation Rules

| Metric | Colors Its Own Value | Also Propagates To |
|--------|---------------------|-------------------|
| **ENTRY BRACKET** | Bracket name text only | *(nothing else)* |
| **EDGE** | EDGE value text | **Wallet address text** |
| **YIELD** | YIELD value text | **Outer card frame/border** |
| **GRAVITY** | GRAVITY value text | **Lower dotted separator line** |

Entry Bracket ≠ Edge. They are independent axes.

---

## 10. Data Zone Background — The 6-Background Pattern System (NEW in v3.0)

The Data Zone background fill is **conditionally determined** by the trader's 4-axis signature. Patterns are evaluated in priority order — first match wins. All patterns are mutually exclusive.

### 10.1 Pattern Evaluation Order (Highest Priority First)

```
Priority 1: UNIFORM     →  Check first (rarest, 1.36%)
Priority 2: SIGNAL      →  Check second (3.40%)
Priority 3: CONTRARIAN  →  Check third (2.82%)
Priority 4: EQUILIBRIUM →  Check fourth (3.38%)
Priority 5: LIQUIDATOR  →  Check fifth (5.56%)
Priority 6: DEFAULT     →  Fallback (83.48%)
```

### 10.2 Pattern Definitions

#### UNIFORM (1.36% of supply)
**Trigger:** All 4 axes at their equivalent tier position AND tier ≠ Base.

The entry bracket's position in its scale must match all three dynamic metrics' position in the P-Tier scale:

| entry_bracket | Must match edge=yield=gravity= |
|--------------|-------------------------------|
| Anomaly | P99 |
| Oracle | P90 |
| Outlier | P70 |
| Vector | P50 |

Harvester + Base/Base/Base does NOT qualify (that is the absence of a pattern, not a pattern).

```
SQL: WHERE (
    (entry_bracket = 'Anomaly'  AND edge = 'P99' AND yield = 'P99' AND gravity = 'P99') OR
    (entry_bracket = 'Oracle'   AND edge = 'P90' AND yield = 'P90' AND gravity = 'P90') OR
    (entry_bracket = 'Outlier'  AND edge = 'P70' AND yield = 'P70' AND gravity = 'P70') OR
    (entry_bracket = 'Vector'   AND edge = 'P50' AND yield = 'P50' AND gravity = 'P50')
)
```

**Background:** Vertical linear gradient:
```
Direction: top to bottom
15% → #28AEAE
30% → #4A99BB
45% → #8C92D1
60% → #C38CCE
75% → #BB7382
90% → #C5CC84
```

**Stroke:** Black (#000000) on Data Zone border.

---

#### SIGNAL (3.40% of supply)
**Trigger:** Entry bracket is Anomaly OR Oracle, AND Edge ≥ P90, AND Yield ≥ P90. Any Gravity.

```
SQL: WHERE entry_bracket IN ('Anomaly', 'Oracle')
     AND edge IN ('P99', 'P90')
     AND yield IN ('P99', 'P90')
```

**Background:** Solid #CDD2DE (cool steel blue-grey).  
**Stroke:** Black (#000000) on Data Zone border.

**Text treatment:** White text on light background requires a thin black stroke/outline on text elements within the Data Zone for legibility.

---

#### CONTRARIAN (2.82% of supply)
**Trigger:** Entry bracket is Outlier, AND Edge ≥ P90, AND Yield ≥ P90. Any Gravity.

```
SQL: WHERE entry_bracket = 'Outlier'
     AND edge IN ('P99', 'P90')
     AND yield IN ('P99', 'P90')
```

**Background:** Solid #0A2A2A (deep midnight teal).  
**Stroke:** Default white (#FFFFFF) on Data Zone border.

---

#### EQUILIBRIUM (3.38% of supply)
**Trigger:** Edge ≥ P70, AND Yield ≥ P70, AND Gravity ≥ P70. Excludes any cards already matched by Uniform, Signal, or Contrarian.

```
SQL: WHERE edge IN ('P99', 'P90', 'P70')
     AND yield IN ('P99', 'P90', 'P70')
     AND gravity IN ('P99', 'P90', 'P70')
     AND NOT matched_by_uniform
     AND NOT matched_by_signal
     AND NOT matched_by_contrarian
```

**Background:** Solid #474332 (warm dark earth).  
**Stroke:** Default white (#FFFFFF) on Data Zone border.

---

#### LIQUIDATOR (5.56% of supply)
**Trigger:** Entry bracket is Vector OR Harvester, AND Gravity ≥ P90, AND Edge ≤ P50, AND Yield ≤ P50.

```
SQL: WHERE entry_bracket IN ('Vector', 'Harvester')
     AND gravity IN ('P99', 'P90')
     AND edge IN ('Base', 'P50')
     AND yield IN ('Base', 'P50')
```

**Background:** Solid #3B2647 (deep purple).  
**Stroke:** Default white (#FFFFFF) on Data Zone border.

---

#### DEFAULT (83.48% of supply)
**Trigger:** No other pattern matched.

**Background:** Solid #1C1B1B (standard dark terminal).  
**Stroke:** White (#FFFFFF) on Data Zone border.

---

### 10.3 Supply Allocation Summary

| Pattern | Mints | % | Background | Narrative |
|---------|-------|---|-----------|-----------|
| Default | ~378,162 | 83.48% | #1C1B1B | Standard terminal |
| Liquidator | ~25,164 | 5.56% | #3B2647 | Whale liquidity engine |
| Equilibrium | ~15,327 | 3.38% | #474332 | Balanced all-round excellence |
| Signal | ~15,390 | 3.40% | #CDD2DE | Deep conviction alpha |
| Contrarian | ~12,752 | 2.82% | #0A2A2A | Called it from the coin-flip zone |
| Uniform | ~6,177 | 1.36% | Gradient | Perfect 4-axis alignment |

### 10.4 Data zone from Archetype (implementation)

Pattern-detection pseudocode (former Section 10.4) is **removed**. The front and back **Data Zone** fill and stroke come only from **`data.archetype`** via `dz_style()` in `scripts/cardgen/generate_card.py` (`_DZ_ARCHETYPE_STYLES`). If `archetype` is missing or empty, the renderer uses **`THE OPERATOR`**.

The historical pattern names in Sections 10.1–10.3 (UNIFORM, SIGNAL, LIQUIDATOR, etc.) describe **supply / narrative** context only; they are **not** computed at render time anymore.

---

## 11. Backend Data Contract

```json
{
  "season_type":        "genesis | standard",
  "season_number":      3,
  "recurrence":         "null | daily | weekly | monthly",
  "claim_type":         "origin | looter",
  "image_url":          "https://pub-c88ecf8bdbaf40c088df5b1c7ffe2f7b.r2.dev/prod/event-images/194107/c2e16d6bb8b44bd598141542c8dd82bd.jpg",
  "card_title":         "ZELENSKYY SUIT WATCH JUN 2025",
  "primary_tag":        "CELEBRITIES",
  "primary_tag_color":  "#51E147",
  "secondary_tag":      "NONE",
  "entry_bracket":      "ORACLE",
  "archetype":          "THE ANOMALY",
  "proxy_wallet":       "0xBb8E703abc123def456...",
  "edge":               "P99",
  "yield":              "P90",
  "gravity":            "P50",
  "leaderboard_rank":   63564,
  "card_lore":          "Fed pricing probability shifted...",
  "event_volume":       125000000,
  "total_volume":       8500.00,
  "total_pnl":          4200.00,
  "roi_percentage":     49.41,
  "entry_cwap":         0.1832
}
```

Fields below `leaderboard_rank` are used by the back-of-card Agent (Part B).

---

## 12. SVG Assembly Pseudocode

```
function buildCard(data):
    // 1. Resolve Data Zone background from archetype (dz_style in generate_card.py)
    archetype = normalizeArchetype(data.archetype)  // default THE OPERATOR if missing
    (bg, stroke, text_stroke) = dzStyle(archetype)    // map per _DZ_ARCHETYPE_STYLES

    // 2. Resolve tier colors (4 independent axes)
    bracket_color   = tierColor_EntryBracket(data.entry_bracket)
    edge_color      = tierColor_PTier(data.edge)
    yield_color     = tierColor_PTier(data.yield)
    gravity_color   = tierColor_PTier(data.gravity)

    // 3. Resolve propagated colors
    wallet_color    = edge_color
    border_color    = yield_color
    dotted_line_clr = gravity_color
    sector_color    = data.primary_tag_color

    // 4. Resolve conditional text
    // ... (same as v2.0)

    // 5. Inject into SVG front template
    // 6. Generate back-of-card via Agent (Part B)
    // 7. Assemble final dual-sided SVG/asset
```

---

# PART B: THE BACK OF THE CARD

---

## 13. Back-of-Card Structure

The back shares the outer frame with the front (same dimensions, same border color driven by YIELD, same rounded corners, same blur effect). The interior is a single Data Zone block that fills the entire frame.

```
┌──────────────────────────────────────────┐
│  OUTER FRAME (identical to front)         │
│  ┌──────────────────────────────────────┐ │
│  │  DATA ZONE (full height)             │ │
│  │  Same background as front (archetype)  │ │
│  │  Same border gap as front            │ │
│  │                                      │ │
│  │  ┌─ UPPER THIRD ──────────────────┐  │ │
│  │  │  Event Description (card_lore)  │  │ │
│  │  │  Max 50 words, Quant Agent      │  │ │
│  │  └────────────────────────────────┘  │ │
│  │                                      │ │
│  │  ┌─ LOWER TWO-THIRDS ────────────┐  │ │
│  │  │  Trader Profile Narrative       │  │ │
│  │  │  Generated by Analyst Agent     │  │ │
│  │  │  at claim time                  │  │ │
│  │  └────────────────────────────────┘  │ │
│  │                                      │ │
│  └──────────────────────────────────────┘ │
└──────────────────────────────────────────┘
```

### 13.1 Outer Frame (Back)
- Identical to front: 516×802 canvas, 510×796 inner, 30px corners, 3px border
- Border color: same as front (driven by YIELD propagation)
- Border blur: same 2px blur
- Background: #0B0C10

### 13.2 Data Zone (Back)
- Fills the full inner frame area (same gap between outer frame and data zone as front)
- Background: **same archetype-driven fill as front** (see `_DZ_ARCHETYPE_STYLES` in `generate_card.py`)
- Border: same stroke color as front data zone
- Corner radius: same as front data zone (22px)

### 13.3 Upper Section — Event Description
- Content: `card_lore` from the Quant Agent (max 50 words)
- This is the "Market Telemetry" readout — the cold analytical description of the event
- Typography: Orbitron Bold, white, size TBD during implementation
- Occupies approximately the top 1/3 of the data zone

### 13.4 Lower Section — Trader Profile Narrative
- Content: Generated by the Analyst Agent (Claude Opus) at claim time
- Occupies the lower 2/3 of the data zone
- Typography: Orbitron Bold, white, size TBD during implementation

---

## 14. The Analyst Agent — Trader Profile Generation

### 14.1 When It Runs

The Analyst Agent is invoked **once per card, at claim time**. When a wallet claims their NFT, the backend:
1. Assembles the full trader data payload
2. Calls Claude Opus with the prompt below
3. Stores the generated narrative in the database
4. Injects it into the back-of-card SVG template

The narrative is generated once and cached permanently — it does not regenerate.

### 14.2 Data Payload for the Agent

The Agent receives:

```json
{
  "event_title": "FED RATES CUT IN FEBRUARY 2026",
  "event_description": "The market priced the probability of...",
  "primary_tag": "ECONOMIC POLICY",
  "secondary_tag": "FED RATES",
  "entry_bracket": "ORACLE",
  "edge": "P90",
  "yield": "P99",
  "gravity": "P50",
  "entry_cwap": 0.2834,
  "total_volume": 1420.00,
  "total_pnl": 3550.00,
  "roi_percentage": 250.00,
  "pattern": "SIGNAL",
  "claim_type": "origin",
  "leaderboard_rank": 12450
}
```

### 14.3 The Agent Prompt

```
You are The Analyst — a cold, precise behavioral profiler embedded in the Polystars terminal system. Your task is to generate a concise trader profile narrative for the back of a collectible NFT card.

You will receive a JSON payload containing a trader's performance data for a specific Polymarket prediction market event. Your output will be permanently inscribed on the card.

## THE 4-AXIS SIGNATURE SYSTEM

Every trader is profiled across 4 axes:

1. ENTRY BRACKET (Absolute Conviction): What probability did they buy at?
   - Anomaly (0-20%): Extreme contrarian. Entered when the market said "impossible."
   - Oracle (20-40%): Deep foresight. Saw the signal before the narrative formed.
   - Outlier (40-60%): Coin-flip zone. Deployed capital when the outcome was maximally uncertain.
   - Vector (60-80%): Trend rider. Backed the prevailing consensus with directional capital.
   - Harvester (80-97%): Late-stage. Locked in near-certain outcomes for incremental yield.

2. EDGE (Relative Timing): How early vs peers in this event?
   - P99: Top 1% earliest. Moved before almost everyone.
   - P90: Top 10%. Ahead of the crowd.
   - P70: Top 30%. Early side of the bell curve.
   - P50: Above average. Slightly ahead.
   - Base: Bottom 50%. Late to the party.

3. YIELD (Capital Efficiency): ROI vs peers in this event?
   - P99/P90/P70/P50/Base: Same percentile scale as Edge.

4. GRAVITY (Capital Footprint): Volume deployed vs peers?
   - P99/P90/P70/P50/Base: Same scale. High gravity = market mover.

## PATTERN ARCHETYPES

The combination of axes produces behavioral patterns. The trader's detected pattern is provided in the payload. Use it to inform your narrative tone:

- SIGNAL: Deep conviction + early timing + high ROI. These traders saw what others didn't.
- CONTRARIAN: Entered at maximum uncertainty (Outlier bracket) but timed it perfectly. Called it from the noise.
- EQUILIBRIUM: Balanced excellence across all three dynamic axes. No single spike — consistent all-round performance.
- LIQUIDATOR: Massive capital, late entry, poor returns. The market's liquidity engine. Not an insult — without these traders, prediction markets don't function.
- UNIFORM: All 4 axes perfectly aligned at their equivalent tier. Internally consistent profile — the trader IS their tier.
- DEFAULT: No special pattern. Standard market participant.

## KEY BEHAVIORAL INSIGHTS TO WEAVE IN

Analyze the specific data points:
- entry_cwap: The exact price they entered at. A CWAP of 0.15 means they bought when the market said "15% chance." If the event resolved YES, they were right when 85% of the money was wrong.
- roi_percentage: Their actual return. 250% ROI means they turned $1 into $3.50.
- total_volume vs total_pnl: The relationship tells the story. $500 deployed → $2000 profit = surgical. $50,000 deployed → $500 profit = whale churn.
- edge vs gravity anti-correlation: If Edge is high but Gravity is low, this is a small-capital sniper. If both are high, this is a whale who also timed it perfectly — exceptionally rare.
- entry_bracket vs edge divergence: If bracket is Anomaly but Edge is only P50, it means many others ALSO entered at extreme odds in this event — the trader's absolute conviction was extreme but their relative timing was average.

## OUTPUT RULES

1. Write in the voice of a high-frequency trading terminal generating a behavioral audit. Cold, precise, analytical — but with an undertone of respect for what the data reveals.
2. Maximum 80 words. Every word must earn its place.
3. Do NOT repeat the raw numbers. Transform them into behavioral insight. Don't say "ROI was 250%." Say "capital efficiency suggests precision deployment with asymmetric conviction."
4. Reference the specific event context. A trader who called an election outcome is a different story than one who called a crypto price target.
5. The tone should match the pattern:
   - SIGNAL/CONTRARIAN: Respect. These traders demonstrated genuine alpha.
   - EQUILIBRIUM: Clinical admiration. Balanced operators.
   - LIQUIDATOR: Neutral, factual. No mockery, but the data speaks.
   - UNIFORM: Note the internal consistency. Everything aligned.
   - DEFAULT: Brief, standard readout.
6. Output ONLY the narrative text. No JSON, no labels, no preamble.
```

### 14.4 Integration Architecture

```
CLAIM EVENT
    │
    ├─► Verify wallet eligibility (existing flow)
    ├─► Determine card assignment (Origin/Looter logic)
    ├─► Assemble trader data payload
    │
    ├─► Call Analyst Agent (Claude Opus API)
    │   Input:  trader data JSON
    │   Output: narrative string (max 80 words)
    │
    ├─► Store narrative in DB (linked to card_id)
    │
    ├─► Build front SVG (existing template injection)
    ├─► Build back SVG (inject card_lore + narrative)
    │
    └─► Mint NFT with front + back metadata
```

The Agent call is a single API request per card. At ~65 events/month with varying trader counts per event, the volume is well within API rate limits.

---

## 15. Visual Validation Checklist

### Front of Card
- [ ] Font is Orbitron Bold everywhere, all text uppercase
- [ ] Season/Instance/Ownership badge: correct conditional colors
- [ ] Event image: 490×490, rounded corners, no processing
- [ ] Title bar: overlaps boundary, dark background, white text
- [ ] SECTOR label white, value uses `primary_tag_color` from DB
- [ ] NODE: both label and value #888888
- [ ] Entry bracket in white `[ ]`, name colored per Entry Bracket Map
- [ ] Wallet = first 9 chars + "...", color matches **EDGE**
- [ ] EDGE/YIELD/GRAVITY: white 12px labels, colored 20px values per P-Tier Map
- [ ] **Entry bracket → own text only** (no propagation)
- [ ] **EDGE → wallet text** (propagation)
- [ ] **YIELD → outer border** (propagation)
- [ ] **GRAVITY → lower dotted line** (propagation)
- [ ] Upper separator always #333333
- [ ] "POLYMARKET" is #2E5CFF
- [ ] Only 5 tiers exist, CWAP ≥ 0.97 never present

### Data Zone Background
- [ ] Pattern detection follows priority order (Uniform > Signal > Contrarian > Equilibrium > Liquidator > Default)
- [ ] DEFAULT: #1C1B1B, white stroke
- [ ] LIQUIDATOR: #3B2647, white stroke
- [ ] EQUILIBRIUM: #474332, white stroke
- [ ] SIGNAL: #CDD2DE, **black stroke**, text has black outline for legibility
- [ ] CONTRARIAN: #0A2A2A, white stroke
- [ ] UNIFORM: vertical gradient (15%:#28AEAE → 30%:#4A99BB → 45%:#8C92D1 → 60%:#C38CCE → 75%:#BB7382 → 90%:#C5CC84), **black stroke**

### Back of Card
- [ ] Outer frame identical to front (border color, blur, corners)
- [ ] Data zone fills full frame, same background as front
- [ ] Upper 1/3: event description (card_lore, max 50 words)
- [ ] Lower 2/3: Analyst Agent narrative (max 80 words)
- [ ] Narrative generated once at claim time, then cached
