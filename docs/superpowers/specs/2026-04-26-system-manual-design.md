# System Manual — Design Spec
Date: 2026-04-26

## Overview

Add a "SYSTEM MANUAL" docs-style page to `user_web_frontend` (`/system-manual`), accessible via a button under the "Community achievements" block on the main page. The page has a fixed left sidebar with accordion P1/P2 navigation matching the behavioral pattern of docs.alphakek.ai, using PS' existing design tokens.

---

## 1. Entry Point

**File:** `user_web_frontend/components/SeasonArchetypeOpensBoard.tsx`

Add a `<Link href="/system-manual">` styled with the existing `.season-mint-button` CSS class, placed after the last content block (after the archetype grid / "no cards" message) and before the closing `</section>`. Wrapped in a `div.season-board-actions` for consistent spacing.

---

## 2. Routing Structure

All files live inside `user_web_frontend/app/system-manual/`.

```
app/system-manual/
├── layout.tsx                                         ← sidebar shell
├── page.tsx                                           ← Overview
├── telemetry-pipeline/
│   ├── page.tsx                                       ← TELEMETRY PIPELINE
│   ├── phase-1-volumetric-ingestion/page.tsx
│   ├── phase-2-resolution-queues/page.tsx
│   ├── phase-3-condition-parsing/page.tsx
│   ├── phase-4-redemption-extraction/page.tsx
│   ├── phase-5-behavioral-profiling/page.tsx
│   └── phase-6-stellar-initialization/page.tsx
└── seasonal-architecture/
    ├── page.tsx                                       ← SEASONAL ARCHITECTURE
    ├── chronological-grid/page.tsx
    ├── phase-matrix/page.tsx
    └── modular-initialization/page.tsx
```

Total: 1 layout + 13 page files.

---

## 3. Layout (`layout.tsx`)

Server component. Renders a two-column shell:

```tsx
<div className="sm-layout">
  <SystemManualSidebar />
  <main className="sm-content">{children}</main>
</div>
```

CSS:
- `.sm-layout` — `display: flex; min-height: 100vh;`
- `.sm-content` — `margin-left: 260px; padding: 48px 64px; flex: 1; background: transparent;`
- The global `<body>` CRT background (static grain, scanlines, vignette) shows through the content area — no card/panel wrappers ever appear around page content. Text floats directly on the page background.
- `max-width: 860px` on content prose to match docs.alphakek.ai reading width.
- Responsive (≤768px): sidebar hidden or slide-in (out of scope for skeleton, sidebar collapses to top nav strip).

---

## 4. Sidebar Component (`SystemManualSidebar.tsx`)

**Type:** `"use client"` (needs `usePathname`).

**Location:** `user_web_frontend/components/SystemManualSidebar.tsx`

### Nav tree (hardcoded)

```ts
const NAV = [
  { label: 'Overview', href: '/system-manual', children: [] },
  {
    label: 'TELEMETRY PIPELINE',
    href: '/system-manual/telemetry-pipeline',
    children: [
      { label: 'PHASE 1: VOLUMETRIC INGESTION', href: '/system-manual/telemetry-pipeline/phase-1-volumetric-ingestion' },
      { label: 'PHASE 2: THE RESOLUTION QUEUES', href: '/system-manual/telemetry-pipeline/phase-2-resolution-queues' },
      { label: 'PHASE 3: CONDITION PARSING', href: '/system-manual/telemetry-pipeline/phase-3-condition-parsing' },
      { label: 'PHASE 4: REDEMPTION EXTRACTION & NOISE FILTRATION', href: '/system-manual/telemetry-pipeline/phase-4-redemption-extraction' },
      { label: 'PHASE 5: BEHAVIORAL PROFILING', href: '/system-manual/telemetry-pipeline/phase-5-behavioral-profiling' },
      { label: 'PHASE 6: STELLAR INITIALIZATION', href: '/system-manual/telemetry-pipeline/phase-6-stellar-initialization' },
    ],
  },
  {
    label: 'SEASONAL ARCHITECTURE',
    href: '/system-manual/seasonal-architecture',
    children: [
      { label: '1. THE CHRONOLOGICAL GRID', href: '/system-manual/seasonal-architecture/chronological-grid' },
      { label: '2. THE PHASE MATRIX', href: '/system-manual/seasonal-architecture/phase-matrix' },
      { label: '3. THE MODULAR INITIALIZATION PROTOCOL', href: '/system-manual/seasonal-architecture/modular-initialization' },
    ],
  },
]
```

### Accordion logic

No React state. Expansion is derived purely from `usePathname()`:

```ts
const pathname = usePathname()
const isExpanded = (item) => pathname.startsWith(item.href) && item.children.length > 0
const isActive = (href) => pathname === href
```

- P1 item with children: renders as `<Link>` (navigates to P1 page) + chevron `▾`/`▸` derived from `isExpanded`.
- Chevron is visual only — the entire row is a link. There is no separate toggle button.
- Children div: `display: block` when `isExpanded`, `display: none` when not.

### Scroll behavior

```css
.sm-sidebar {
  position: fixed;
  top: 0;
  left: 0;
  width: 260px;
  height: 100vh;
  overflow-y: auto;
  overflow-x: hidden;
}
```

The sidebar scrolls its own overflow. Page content (`sm-content`) scrolls via normal document flow.

---

## 5. CSS — Design Token Mapping

All new classes added to `globals.css` under a `/* ── System Manual ──` section.

| Element | CSS |
|---|---|
| Sidebar background | `var(--bd-bg-elev)` |
| Sidebar right border | `1px solid var(--bd-line)` |
| P1 label font | `var(--bd-font-display)`, `font-size: 11px`, `letter-spacing: 0.08em`, `var(--bd-ink-dim)` |
| P2 label font | `var(--bd-font-ui)`, `font-size: 12px`, `var(--bd-ink-mute)` |
| Active item text | `var(--bd-brand)` |
| Active item left accent | `border-left: 2px solid var(--bd-brand)` |
| Hover text | `var(--bd-ink)` |
| P1 padding | `12px 16px` |
| P2 indent | `padding-left: 28px; padding-right: 16px; padding-top/bottom: 8px` |
| Chevron | inline unicode `▾` / `▸`, `var(--bd-ink-faint)` |
| Content padding | `48px 64px` |
| Content max-width | `860px` (prose reads like docs.alphakek.ai) |
| Content background | `transparent` — global CRT body bg shows through |
| Page `<h1>` | `var(--bd-font-display)`, `font-size: 28px`, `var(--bd-ink)`, `margin: 0 0 40px` |

---

## 6. Page Content Policy (No Content)

Every page file contains only:
1. An `<h1>` with the section title in `var(--bd-font-display)`
2. A single `<div className="sm-placeholder">` — invisible spacer, `min-height: 200px`, **no border, no background, no box** — just vertical space for future content

**Critical:** No card wrappers, no `<section>` with borders, no panel backgrounds around any content element. Everything renders directly on the transparent page background so the global CRT effect shows through. This matches docs.alphakek.ai's "text floating on background" appearance.

No paragraphs, no descriptions, no real content.

---

## 7. No-Content Policy for Button

The "SYSTEM MANUAL" button on the main page:
- Text: `SYSTEM MANUAL`
- Element: `<Link href="/system-manual" className="season-mint-button">`
- Wrapping: `<div className="season-board-actions">` inside the `<section>` of `SeasonArchetypeOpensBoard` (existing class already sets `margin-top: 14px`)

---

## 8. Out of Scope

- Mobile sidebar (responsive toggle) — skeleton only, no hamburger menu
- Real content in any section page
- Search functionality
- Breadcrumbs
- Prev/Next navigation links
