# System Manual — P1 Single-Page Design

**Date:** 2026-04-26  
**Status:** Approved

## Problem

Currently each P2 entry (e.g. Phase 1, Phase 2 …) lives on its own route under its P1 parent. Navigating to a P1 page (e.g. TELEMETRY PIPELINE) shows only a short intro, requiring the user to click through child pages one at a time.

## Goal

All content belonging to one P1 topic is rendered on a single scrollable page. Sidebar P2 links become in-page anchor links.

---

## Design

### 1. Shared nav config — `lib/systemManualNav.ts`

Extract the `NAV` array from `components/SystemManualSidebar.tsx` into a new shared module:

```
user_web_frontend/lib/systemManualNav.ts
```

Both the sidebar (client component) and the P1 page server components import from here. No behavior changes — just a move.

### 2. P1 page rendering

Each P1 page (e.g. `app/system-manual/telemetry-pipeline/page.tsx`) becomes a server component that:

1. Reads and renders `index.md` for the P1 intro (existing behavior).
2. Looks up its own children from the shared nav config by matching `href`.
3. For each child in order:
   - Renders an `<h2>` with `id` set to the last path segment of the child's `href` (e.g. `id="phase-1-volumetric-ingestion"`). Text is the child's `label`.
   - Renders the child's markdown content via `<MarkdownContent>`.

The `id` derivation: `child.href.split("/").pop()` — e.g. `/system-manual/telemetry-pipeline/phase-1-volumetric-ingestion` → `phase-1-volumetric-ingestion`.

### 3. Sidebar anchor links

P2 `href` values in the nav config change from standalone routes to anchors on the parent route:

- Before: `/system-manual/telemetry-pipeline/phase-1-volumetric-ingestion`
- After: `/system-manual/telemetry-pipeline#phase-1-volumetric-ingestion`

**Active state:** P2 items never highlight individually. The P1 item highlights when `pathname` matches the P1 route (`pathname === item.href`). This is already how it works for non-P2 items; no new logic needed.

### 4. P2 route redirects

Each P2 `page.tsx` becomes a redirect to the P1 page with the matching anchor:

```ts
import { redirect } from "next/navigation";
export default function Phase1Page() {
  redirect("/system-manual/telemetry-pipeline#phase-1-volumetric-ingestion");
}
```

This handles direct URL access to former P2 routes.

---

## Files Changed

| File | Change |
|------|--------|
| `lib/systemManualNav.ts` | New — extracted NAV config |
| `components/SystemManualSidebar.tsx` | Import NAV from lib; update P2 hrefs to anchors; remove per-child active logic |
| `app/system-manual/telemetry-pipeline/page.tsx` | Aggregate index + all phase content |
| `app/system-manual/seasonal-architecture/page.tsx` | Aggregate index + all section content |
| `app/system-manual/telemetry-pipeline/phase-*/page.tsx` (×6) | Replace with `redirect()` |
| `app/system-manual/seasonal-architecture/*/page.tsx` (×3) | Replace with `redirect()` |

---

## Out of Scope

- Smooth-scroll behavior (browser default anchor jump is acceptable)
- Per-child active highlighting via `window.location.hash`
- Any changes to markdown content files
