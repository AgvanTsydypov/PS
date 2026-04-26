# System Manual Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `/system-manual` docs page to `user_web_frontend` with a fixed accordion sidebar (P1/P2 hierarchy), accessible via a "SYSTEM MANUAL" button added under the "Community achievements" block on the home page.

**Architecture:** Next.js App Router nested layout at `app/system-manual/layout.tsx` provides the fixed sidebar shell; `{children}` is the content column. Sidebar is a `"use client"` component that derives accordion open/active state purely from `usePathname()` — no React state. Content renders transparent on the global CRT body background; no card wrappers anywhere.

**Tech Stack:** Next.js 13+ App Router, TypeScript, CSS custom properties (`--bd-*` tokens already defined in `globals.css`), `next/link`, `next/navigation`.

---

## File Map

**New files:**
- `user_web_frontend/components/SystemManualSidebar.tsx`
- `user_web_frontend/app/system-manual/layout.tsx`
- `user_web_frontend/app/system-manual/page.tsx`
- `user_web_frontend/app/system-manual/telemetry-pipeline/page.tsx`
- `user_web_frontend/app/system-manual/telemetry-pipeline/phase-1-volumetric-ingestion/page.tsx`
- `user_web_frontend/app/system-manual/telemetry-pipeline/phase-2-resolution-queues/page.tsx`
- `user_web_frontend/app/system-manual/telemetry-pipeline/phase-3-condition-parsing/page.tsx`
- `user_web_frontend/app/system-manual/telemetry-pipeline/phase-4-redemption-extraction/page.tsx`
- `user_web_frontend/app/system-manual/telemetry-pipeline/phase-5-behavioral-profiling/page.tsx`
- `user_web_frontend/app/system-manual/telemetry-pipeline/phase-6-stellar-initialization/page.tsx`
- `user_web_frontend/app/system-manual/seasonal-architecture/page.tsx`
- `user_web_frontend/app/system-manual/seasonal-architecture/chronological-grid/page.tsx`
- `user_web_frontend/app/system-manual/seasonal-architecture/phase-matrix/page.tsx`
- `user_web_frontend/app/system-manual/seasonal-architecture/modular-initialization/page.tsx`

**Modified files:**
- `user_web_frontend/app/globals.css` — append System Manual CSS section at end of file
- `user_web_frontend/components/SeasonArchetypeOpensBoard.tsx` — add SYSTEM MANUAL button + Link import

---

## Task 1: System Manual CSS

**Files:**
- Modify: `user_web_frontend/app/globals.css` (append after line 2587, end of file)

- [ ] **Step 1: Append System Manual CSS block to end of globals.css**

Add the following block to the very end of `user_web_frontend/app/globals.css`:

```css
/* ── System Manual ───────────────────────────────────────────────────────── */

.sm-layout {
  display: flex;
  min-height: 100vh;
}

.sm-sidebar {
  position: fixed;
  top: 0;
  left: 0;
  width: 260px;
  height: 100vh;
  overflow-y: auto;
  overflow-x: hidden;
  background: var(--bd-bg-elev);
  border-right: 1px solid var(--bd-line);
  padding: 24px 0 48px;
  z-index: 10;
}

.sm-content {
  margin-left: 260px;
  padding: 48px 64px;
  flex: 1;
  background: transparent;
  min-height: 100vh;
}

.sm-content-inner {
  max-width: 860px;
}

.sm-nav-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  padding: 10px 16px;
  text-decoration: none;
  background: transparent;
  border: none;
  border-left: 2px solid transparent;
  color: var(--bd-ink-mute);
  font-family: var(--bd-font-ui);
  font-size: 13px;
  cursor: pointer;
  text-align: left;
  box-sizing: border-box;
  transition: color 0.15s ease, border-left-color 0.15s ease;
}

.sm-nav-item:hover {
  color: var(--bd-ink);
  border-left-color: var(--bd-line-strong);
  background: transparent;
  transform: none;
  box-shadow: none;
}

.sm-nav-item.sm-nav-active {
  color: var(--bd-brand);
  border-left-color: var(--bd-brand);
}

.sm-nav-item.sm-nav-active:hover {
  color: var(--bd-brand);
  border-left-color: var(--bd-brand);
}

.sm-nav-item.sm-nav-p1 {
  font-family: var(--bd-font-display);
  font-size: 11px;
  letter-spacing: 0.08em;
  color: var(--bd-ink-dim);
  padding-top: 14px;
  padding-bottom: 14px;
}

.sm-nav-item.sm-nav-p1:hover {
  color: var(--bd-ink);
}

.sm-nav-item.sm-nav-p1.sm-nav-active {
  color: var(--bd-brand);
}

.sm-nav-item.sm-nav-p2 {
  font-size: 12px;
  padding-left: 28px;
  padding-top: 8px;
  padding-bottom: 8px;
}

.sm-nav-chevron {
  font-style: normal;
  font-size: 10px;
  color: var(--bd-ink-faint);
  flex-shrink: 0;
  margin-left: 6px;
}

.sm-nav-children {
  display: none;
}

.sm-nav-children.sm-nav-open {
  display: block;
}

.sm-page-title {
  font-size: 28px;
  color: var(--bd-ink);
  margin: 0 0 40px;
  letter-spacing: 0.05em;
}

.sm-placeholder {
  min-height: 200px;
}

/* season-mint-button used as <Link> needs inline-block */
a.season-mint-button {
  display: inline-block;
  text-decoration: none;
}
```

- [ ] **Step 2: Verify CSS appended correctly**

Run:
```bash
cd user_web_frontend && tail -20 app/globals.css
```
Expected: last lines show `a.season-mint-button { display: inline-block; text-decoration: none; }` and closing `}`.

- [ ] **Step 3: Commit**

```bash
git add user_web_frontend/app/globals.css
git commit -m "style: add System Manual CSS (sm-* classes, sidebar, layout, a.season-mint-button fix)"
```

---

## Task 2: SystemManualSidebar Component

**Files:**
- Create: `user_web_frontend/components/SystemManualSidebar.tsx`

- [ ] **Step 1: Create the component**

Create `user_web_frontend/components/SystemManualSidebar.tsx` with this content:

```tsx
"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

type NavChild = {
  label: string;
  href: string;
};

type NavItem = {
  label: string;
  href: string;
  children: NavChild[];
};

const NAV: NavItem[] = [
  { label: "Overview", href: "/system-manual", children: [] },
  {
    label: "TELEMETRY PIPELINE",
    href: "/system-manual/telemetry-pipeline",
    children: [
      {
        label: "PHASE 1: VOLUMETRIC INGESTION",
        href: "/system-manual/telemetry-pipeline/phase-1-volumetric-ingestion",
      },
      {
        label: "PHASE 2: THE RESOLUTION QUEUES",
        href: "/system-manual/telemetry-pipeline/phase-2-resolution-queues",
      },
      {
        label: "PHASE 3: CONDITION PARSING",
        href: "/system-manual/telemetry-pipeline/phase-3-condition-parsing",
      },
      {
        label: "PHASE 4: REDEMPTION EXTRACTION & NOISE FILTRATION",
        href: "/system-manual/telemetry-pipeline/phase-4-redemption-extraction",
      },
      {
        label: "PHASE 5: BEHAVIORAL PROFILING",
        href: "/system-manual/telemetry-pipeline/phase-5-behavioral-profiling",
      },
      {
        label: "PHASE 6: STELLAR INITIALIZATION",
        href: "/system-manual/telemetry-pipeline/phase-6-stellar-initialization",
      },
    ],
  },
  {
    label: "SEASONAL ARCHITECTURE",
    href: "/system-manual/seasonal-architecture",
    children: [
      {
        label: "1. THE CHRONOLOGICAL GRID",
        href: "/system-manual/seasonal-architecture/chronological-grid",
      },
      {
        label: "2. THE PHASE MATRIX",
        href: "/system-manual/seasonal-architecture/phase-matrix",
      },
      {
        label: "3. THE MODULAR INITIALIZATION PROTOCOL",
        href: "/system-manual/seasonal-architecture/modular-initialization",
      },
    ],
  },
];

export default function SystemManualSidebar() {
  const pathname = usePathname();

  return (
    <nav className="sm-sidebar" aria-label="System Manual navigation">
      {NAV.map((item) => {
        const hasChildren = item.children.length > 0;
        const isExpanded = hasChildren && pathname.startsWith(item.href);
        const isActive = pathname === item.href;

        const itemClasses = [
          "sm-nav-item",
          hasChildren ? "sm-nav-p1" : "",
          isActive ? "sm-nav-active" : "",
        ]
          .filter(Boolean)
          .join(" ");

        return (
          <div key={item.href}>
            <Link href={item.href} className={itemClasses}>
              <span>{item.label}</span>
              {hasChildren && (
                <span className="sm-nav-chevron" aria-hidden="true">
                  {isExpanded ? "▾" : "▸"}
                </span>
              )}
            </Link>
            {hasChildren && (
              <div
                className={`sm-nav-children${isExpanded ? " sm-nav-open" : ""}`}
              >
                {item.children.map((child) => {
                  const childClasses = [
                    "sm-nav-item",
                    "sm-nav-p2",
                    pathname === child.href ? "sm-nav-active" : "",
                  ]
                    .filter(Boolean)
                    .join(" ");

                  return (
                    <Link key={child.href} href={child.href} className={childClasses}>
                      {child.label}
                    </Link>
                  );
                })}
              </div>
            )}
          </div>
        );
      })}
    </nav>
  );
}
```

- [ ] **Step 2: Run TypeScript check**

```bash
cd user_web_frontend && npx tsc --noEmit
```
Expected: no errors related to `SystemManualSidebar.tsx`.

- [ ] **Step 3: Commit**

```bash
git add user_web_frontend/components/SystemManualSidebar.tsx
git commit -m "feat: add SystemManualSidebar — pathname-driven accordion, P1/P2 nav"
```

---

## Task 3: system-manual Layout

**Files:**
- Create: `user_web_frontend/app/system-manual/layout.tsx`

- [ ] **Step 1: Create the layout**

Create `user_web_frontend/app/system-manual/layout.tsx`:

```tsx
import { type ReactNode } from "react";

import SystemManualSidebar from "../../components/SystemManualSidebar";

export default function SystemManualLayout({ children }: { children: ReactNode }) {
  return (
    <div className="sm-layout">
      <SystemManualSidebar />
      <main className="sm-content">
        <div className="sm-content-inner">{children}</div>
      </main>
    </div>
  );
}
```

- [ ] **Step 2: TypeScript check**

```bash
cd user_web_frontend && npx tsc --noEmit
```
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add user_web_frontend/app/system-manual/layout.tsx
git commit -m "feat: add system-manual layout — sidebar + transparent content shell"
```

---

## Task 4: Overview Page

**Files:**
- Create: `user_web_frontend/app/system-manual/page.tsx`

- [ ] **Step 1: Create Overview page**

Create `user_web_frontend/app/system-manual/page.tsx`:

```tsx
export default function SystemManualOverviewPage() {
  return (
    <>
      <h1 className="sm-page-title">Overview</h1>
      <div className="sm-placeholder" />
    </>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add user_web_frontend/app/system-manual/page.tsx
git commit -m "feat: add /system-manual Overview page skeleton"
```

---

## Task 5: Telemetry Pipeline Pages (P1 + 6 × P2)

**Files:**
- Create: `user_web_frontend/app/system-manual/telemetry-pipeline/page.tsx`
- Create: `user_web_frontend/app/system-manual/telemetry-pipeline/phase-1-volumetric-ingestion/page.tsx`
- Create: `user_web_frontend/app/system-manual/telemetry-pipeline/phase-2-resolution-queues/page.tsx`
- Create: `user_web_frontend/app/system-manual/telemetry-pipeline/phase-3-condition-parsing/page.tsx`
- Create: `user_web_frontend/app/system-manual/telemetry-pipeline/phase-4-redemption-extraction/page.tsx`
- Create: `user_web_frontend/app/system-manual/telemetry-pipeline/phase-5-behavioral-profiling/page.tsx`
- Create: `user_web_frontend/app/system-manual/telemetry-pipeline/phase-6-stellar-initialization/page.tsx`

- [ ] **Step 1: Create P1 landing page**

Create `user_web_frontend/app/system-manual/telemetry-pipeline/page.tsx`:

```tsx
export default function TelemetryPipelinePage() {
  return (
    <>
      <h1 className="sm-page-title">TELEMETRY PIPELINE</h1>
      <div className="sm-placeholder" />
    </>
  );
}
```

- [ ] **Step 2: Create Phase 1 page**

Create `user_web_frontend/app/system-manual/telemetry-pipeline/phase-1-volumetric-ingestion/page.tsx`:

```tsx
export default function Phase1Page() {
  return (
    <>
      <h1 className="sm-page-title">PHASE 1: VOLUMETRIC INGESTION</h1>
      <div className="sm-placeholder" />
    </>
  );
}
```

- [ ] **Step 3: Create Phase 2 page**

Create `user_web_frontend/app/system-manual/telemetry-pipeline/phase-2-resolution-queues/page.tsx`:

```tsx
export default function Phase2Page() {
  return (
    <>
      <h1 className="sm-page-title">PHASE 2: THE RESOLUTION QUEUES</h1>
      <div className="sm-placeholder" />
    </>
  );
}
```

- [ ] **Step 4: Create Phase 3 page**

Create `user_web_frontend/app/system-manual/telemetry-pipeline/phase-3-condition-parsing/page.tsx`:

```tsx
export default function Phase3Page() {
  return (
    <>
      <h1 className="sm-page-title">PHASE 3: CONDITION PARSING</h1>
      <div className="sm-placeholder" />
    </>
  );
}
```

- [ ] **Step 5: Create Phase 4 page**

Create `user_web_frontend/app/system-manual/telemetry-pipeline/phase-4-redemption-extraction/page.tsx`:

```tsx
export default function Phase4Page() {
  return (
    <>
      <h1 className="sm-page-title">PHASE 4: REDEMPTION EXTRACTION &amp; NOISE FILTRATION</h1>
      <div className="sm-placeholder" />
    </>
  );
}
```

- [ ] **Step 6: Create Phase 5 page**

Create `user_web_frontend/app/system-manual/telemetry-pipeline/phase-5-behavioral-profiling/page.tsx`:

```tsx
export default function Phase5Page() {
  return (
    <>
      <h1 className="sm-page-title">PHASE 5: BEHAVIORAL PROFILING</h1>
      <div className="sm-placeholder" />
    </>
  );
}
```

- [ ] **Step 7: Create Phase 6 page**

Create `user_web_frontend/app/system-manual/telemetry-pipeline/phase-6-stellar-initialization/page.tsx`:

```tsx
export default function Phase6Page() {
  return (
    <>
      <h1 className="sm-page-title">PHASE 6: STELLAR INITIALIZATION</h1>
      <div className="sm-placeholder" />
    </>
  );
}
```

- [ ] **Step 8: Commit all Telemetry Pipeline pages**

```bash
git add user_web_frontend/app/system-manual/telemetry-pipeline/
git commit -m "feat: add Telemetry Pipeline skeleton pages (P1 + phases 1–6)"
```

---

## Task 6: Seasonal Architecture Pages (P1 + 3 × P2)

**Files:**
- Create: `user_web_frontend/app/system-manual/seasonal-architecture/page.tsx`
- Create: `user_web_frontend/app/system-manual/seasonal-architecture/chronological-grid/page.tsx`
- Create: `user_web_frontend/app/system-manual/seasonal-architecture/phase-matrix/page.tsx`
- Create: `user_web_frontend/app/system-manual/seasonal-architecture/modular-initialization/page.tsx`

- [ ] **Step 1: Create P1 landing page**

Create `user_web_frontend/app/system-manual/seasonal-architecture/page.tsx`:

```tsx
export default function SeasonalArchitecturePage() {
  return (
    <>
      <h1 className="sm-page-title">SEASONAL ARCHITECTURE</h1>
      <div className="sm-placeholder" />
    </>
  );
}
```

- [ ] **Step 2: Create Chronological Grid page**

Create `user_web_frontend/app/system-manual/seasonal-architecture/chronological-grid/page.tsx`:

```tsx
export default function ChronologicalGridPage() {
  return (
    <>
      <h1 className="sm-page-title">1. THE CHRONOLOGICAL GRID</h1>
      <div className="sm-placeholder" />
    </>
  );
}
```

- [ ] **Step 3: Create Phase Matrix page**

Create `user_web_frontend/app/system-manual/seasonal-architecture/phase-matrix/page.tsx`:

```tsx
export default function PhaseMatrixPage() {
  return (
    <>
      <h1 className="sm-page-title">2. THE PHASE MATRIX</h1>
      <div className="sm-placeholder" />
    </>
  );
}
```

- [ ] **Step 4: Create Modular Initialization page**

Create `user_web_frontend/app/system-manual/seasonal-architecture/modular-initialization/page.tsx`:

```tsx
export default function ModularInitializationPage() {
  return (
    <>
      <h1 className="sm-page-title">3. THE MODULAR INITIALIZATION PROTOCOL</h1>
      <div className="sm-placeholder" />
    </>
  );
}
```

- [ ] **Step 5: Commit all Seasonal Architecture pages**

```bash
git add user_web_frontend/app/system-manual/seasonal-architecture/
git commit -m "feat: add Seasonal Architecture skeleton pages (P1 + 3 sub-sections)"
```

---

## Task 7: Entry Point Button

**Files:**
- Modify: `user_web_frontend/components/SeasonArchetypeOpensBoard.tsx`

The "Community achievements" section lives here. We add a SYSTEM MANUAL button at the bottom of the `<section>`, wrapped in the existing `.season-board-actions` div (which provides `margin-top: 14px`).

- [ ] **Step 1: Add Link import to SeasonArchetypeOpensBoard.tsx**

`SeasonArchetypeOpensBoard.tsx` currently starts with `"use client";` and imports from React. Add the `Link` import after the existing imports.

Current top of file (lines 1–4):
```tsx
"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
```

Change to:
```tsx
"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
```

- [ ] **Step 2: Add the button before closing `</section>`**

The `<section>` in `SeasonArchetypeOpensBoard.tsx` currently ends at line 159 with `</section>`. Add the button block immediately before it.

Find this exact closing line:
```tsx
    </section>
  );
}
```

Replace with:
```tsx
      <div className="season-board-actions">
        <Link href="/system-manual" className="season-mint-button">
          System Manual
        </Link>
      </div>
    </section>
  );
}
```

- [ ] **Step 3: TypeScript check**

```bash
cd user_web_frontend && npx tsc --noEmit
```
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add user_web_frontend/components/SeasonArchetypeOpensBoard.tsx
git commit -m "feat: add SYSTEM MANUAL entry-point button under Community achievements"
```

---

## Task 8: Build Verification & Visual Check

- [ ] **Step 1: Full build**

```bash
cd user_web_frontend && npm run build
```
Expected: exits 0, no TypeScript errors, no Next.js route errors. You should see the new routes listed in the build output:
```
├ /system-manual
├ /system-manual/seasonal-architecture
├ /system-manual/seasonal-architecture/chronological-grid
├ /system-manual/seasonal-architecture/modular-initialization
├ /system-manual/seasonal-architecture/phase-matrix
├ /system-manual/telemetry-pipeline
├ /system-manual/telemetry-pipeline/phase-1-volumetric-ingestion
...
```

- [ ] **Step 2: Lint**

```bash
cd user_web_frontend && npm run lint
```
Expected: no errors, no warnings about unused imports or missing keys.

- [ ] **Step 3: Start dev server and visual verify**

```bash
cd user_web_frontend && npm run dev -- -p 3001
```

Then verify all of the following in a browser at `http://localhost:3001`:

1. **Home page** (`/`): "SYSTEM MANUAL" button appears below the "Community achievements" section. Clicking it navigates to `/system-manual`.
2. **Overview** (`/system-manual`): Sidebar is fixed on left, "Overview" item is highlighted orange (brand color), content area shows `h1 "Overview"` floating on the dark CRT background (no card/box around it).
3. **Sidebar scroll**: If you resize the browser window shorter than the sidebar content, the sidebar scrolls independently while the content stays fixed.
4. **P1 accordion** (`/system-manual/telemetry-pipeline`): "Telemetry Pipeline" item in sidebar shows `▾` and all 6 Phase children expand below it. Active item is orange.
5. **P2 navigation** (`/system-manual/telemetry-pipeline/phase-3-condition-parsing`): Phase 3 item is highlighted orange in sidebar. Clicking another phase navigates without page reload.
6. **Seasonal Architecture** (`/system-manual/seasonal-architecture`): Telemetry Pipeline section collapses (▸), Seasonal Architecture expands (▾) with its 3 children.
7. **Content area**: No borders, no background boxes — text and h1 float directly on the animated CRT background.

- [ ] **Step 4: Final commit if any minor fixes were made during verification**

```bash
git add -p
git commit -m "fix: visual verification adjustments for system-manual"
```
(Skip this step if no fixes were needed.)
