# System Manual — P1 Single-Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render all P2 content for a P1 topic on one scrollable page; sidebar P2 links become in-page anchor links; old P2 routes redirect.

**Architecture:** Extract the nav config to a shared lib so server-side P1 pages can iterate their children. Each P1 page aggregates index.md + all child markdown files in order. Sidebar P2 hrefs change to `parent-route#anchor` format. Old P2 `page.tsx` files become `redirect()` one-liners.

**Tech Stack:** Next.js 14 (App Router), TypeScript, React server components, `next/navigation` redirect

---

## File Map

| Action | File |
|--------|------|
| Create | `user_web_frontend/lib/systemManualNav.ts` |
| Modify | `user_web_frontend/components/SystemManualSidebar.tsx` |
| Modify | `user_web_frontend/app/system-manual/telemetry-pipeline/page.tsx` |
| Modify | `user_web_frontend/app/system-manual/seasonal-architecture/page.tsx` |
| Modify | `user_web_frontend/app/globals.css` |
| Replace | `user_web_frontend/app/system-manual/telemetry-pipeline/phase-1-volumetric-ingestion/page.tsx` |
| Replace | `user_web_frontend/app/system-manual/telemetry-pipeline/phase-2-resolution-queues/page.tsx` |
| Replace | `user_web_frontend/app/system-manual/telemetry-pipeline/phase-3-condition-parsing/page.tsx` |
| Replace | `user_web_frontend/app/system-manual/telemetry-pipeline/phase-4-redemption-extraction/page.tsx` |
| Replace | `user_web_frontend/app/system-manual/telemetry-pipeline/phase-5-behavioral-profiling/page.tsx` |
| Replace | `user_web_frontend/app/system-manual/telemetry-pipeline/phase-6-stellar-initialization/page.tsx` |
| Replace | `user_web_frontend/app/system-manual/seasonal-architecture/chronological-grid/page.tsx` |
| Replace | `user_web_frontend/app/system-manual/seasonal-architecture/phase-matrix/page.tsx` |
| Replace | `user_web_frontend/app/system-manual/seasonal-architecture/modular-initialization/page.tsx` |

---

### Task 1: Extract NAV config to shared lib

**Files:**
- Create: `user_web_frontend/lib/systemManualNav.ts`

The NAV array currently lives inside `SystemManualSidebar.tsx` (a client component). Server-side P1 pages need to iterate their children, so we move NAV to a plain TS module both can import. P2 hrefs are updated here to use anchor format (`parent#anchor`).

- [ ] **Step 1: Create `user_web_frontend/lib/systemManualNav.ts`**

```ts
export type NavChild = {
  label: string;
  href: string;
};

export type NavItem = {
  label: string;
  href: string;
  children: NavChild[];
};

export const NAV: NavItem[] = [
  { label: "Overview", href: "/system-manual", children: [] },
  {
    label: "TELEMETRY PIPELINE",
    href: "/system-manual/telemetry-pipeline",
    children: [
      {
        label: "PHASE 1: VOLUMETRIC INGESTION",
        href: "/system-manual/telemetry-pipeline#phase-1-volumetric-ingestion",
      },
      {
        label: "PHASE 2: THE RESOLUTION QUEUES",
        href: "/system-manual/telemetry-pipeline#phase-2-resolution-queues",
      },
      {
        label: "PHASE 3: CONDITION PARSING",
        href: "/system-manual/telemetry-pipeline#phase-3-condition-parsing",
      },
      {
        label: "PHASE 4: REDEMPTION EXTRACTION & NOISE FILTRATION",
        href: "/system-manual/telemetry-pipeline#phase-4-redemption-extraction",
      },
      {
        label: "PHASE 5: BEHAVIORAL PROFILING",
        href: "/system-manual/telemetry-pipeline#phase-5-behavioral-profiling",
      },
      {
        label: "PHASE 6: STELLAR INITIALIZATION",
        href: "/system-manual/telemetry-pipeline#phase-6-stellar-initialization",
      },
    ],
  },
  {
    label: "SEASONAL ARCHITECTURE",
    href: "/system-manual/seasonal-architecture",
    children: [
      {
        label: "1. THE CHRONOLOGICAL GRID",
        href: "/system-manual/seasonal-architecture#chronological-grid",
      },
      {
        label: "2. THE PHASE MATRIX",
        href: "/system-manual/seasonal-architecture#phase-matrix",
      },
      {
        label: "3. THE MODULAR INITIALIZATION PROTOCOL",
        href: "/system-manual/seasonal-architecture#modular-initialization",
      },
    ],
  },
];
```

- [ ] **Step 2: Commit**

```bash
cd user_web_frontend
git add lib/systemManualNav.ts
git commit -m "feat: extract systemManualNav to shared lib"
```

---

### Task 2: Update SystemManualSidebar to use shared lib

**Files:**
- Modify: `user_web_frontend/components/SystemManualSidebar.tsx`

Replace the inline type definitions and NAV array with an import from the new lib. Remove the per-child active-state check — P2 items never highlight individually (per design).

- [ ] **Step 1: Replace the full contents of `user_web_frontend/components/SystemManualSidebar.tsx`**

```tsx
"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { NAV } from "../lib/systemManualNav";

export default function SystemManualSidebar() {
  const pathname = usePathname();

  return (
    <nav className="sm-sidebar" aria-label="System Manual navigation">
      {NAV.map((item) => {
        const hasChildren = item.children.length > 0;
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
              {item.label}
            </Link>
            {hasChildren && (
              <div className="sm-nav-children sm-nav-open">
                {item.children.map((child) => (
                  <Link
                    key={child.href}
                    href={child.href}
                    className="sm-nav-item sm-nav-p2"
                  >
                    {child.label}
                  </Link>
                ))}
              </div>
            )}
          </div>
        );
      })}
    </nav>
  );
}
```

- [ ] **Step 2: Verify lint passes**

```bash
cd user_web_frontend && npm run lint
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add components/SystemManualSidebar.tsx
git commit -m "feat: sidebar imports NAV from lib; P2 hrefs become anchors"
```

---

### Task 3: Add `sm-section-title` CSS class

**Files:**
- Modify: `user_web_frontend/app/globals.css`

The P1 pages will render a phase/section heading between the intro and each child's content. This heading needs its own class — it sits visually between the page `h1` (28 px) and the inline markdown `h2` (18 px).

- [ ] **Step 1: Add the class after the `.sm-placeholder` block in `globals.css`**

Find the line:
```css
.sm-placeholder {
  min-height: 200px;
}
```

Insert immediately after it (before the `/* season-mint-button */` comment):

```css
.sm-section-title {
  font-family: var(--bd-font-display);
  font-size: 20px;
  letter-spacing: 0.06em;
  color: var(--bd-ink);
  margin: 64px 0 20px;
  padding-top: 32px;
  border-top: 1px solid var(--bd-line-strong);
}
```

- [ ] **Step 2: Commit**

```bash
git add app/globals.css
git commit -m "feat: add sm-section-title CSS class for P1 section dividers"
```

---

### Task 4: Aggregate content on the TELEMETRY PIPELINE P1 page

**Files:**
- Modify: `user_web_frontend/app/system-manual/telemetry-pipeline/page.tsx`

The page reads `index.md` (intro), then iterates its children from NAV, reads each child's markdown file, and renders heading + content for each.

Anchor id derivation: `child.href.split("#")[1]` — e.g. `/system-manual/telemetry-pipeline#phase-1-volumetric-ingestion` → `"phase-1-volumetric-ingestion"`.

Content slug derivation: `"telemetry-pipeline/" + anchor` — matches the file at `content/system-manual/telemetry-pipeline/phase-1-volumetric-ingestion.md`.

- [ ] **Step 1: Replace the full contents of `user_web_frontend/app/system-manual/telemetry-pipeline/page.tsx`**

```tsx
import MarkdownContent from "../../../components/MarkdownContent";
import { getManualContent } from "../../../lib/systemManualContent";
import { NAV } from "../../../lib/systemManualNav";

export default function TelemetryPipelinePage() {
  const intro = getManualContent("telemetry-pipeline/index");
  const parent = NAV.find((item) => item.href === "/system-manual/telemetry-pipeline")!;

  return (
    <>
      <h1 className="sm-page-title">TELEMETRY PIPELINE</h1>
      {intro && <MarkdownContent content={intro} />}
      {parent.children.map((child) => {
        const anchor = child.href.split("#")[1];
        const content = getManualContent(`telemetry-pipeline/${anchor}`);
        return (
          <section key={anchor}>
            <h2 id={anchor} className="sm-section-title">
              {child.label}
            </h2>
            {content && <MarkdownContent content={content} />}
          </section>
        );
      })}
    </>
  );
}
```

- [ ] **Step 2: Verify lint passes**

```bash
cd user_web_frontend && npm run lint
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add app/system-manual/telemetry-pipeline/page.tsx
git commit -m "feat: telemetry-pipeline P1 page aggregates all phase content"
```

---

### Task 5: Aggregate content on the SEASONAL ARCHITECTURE P1 page

**Files:**
- Modify: `user_web_frontend/app/system-manual/seasonal-architecture/page.tsx`

Same pattern as Task 4 — intro + iterated children from NAV. Content files are in `content/system-manual/seasonal-architecture/`.

- [ ] **Step 1: Replace the full contents of `user_web_frontend/app/system-manual/seasonal-architecture/page.tsx`**

```tsx
import MarkdownContent from "../../../components/MarkdownContent";
import { getManualContent } from "../../../lib/systemManualContent";
import { NAV } from "../../../lib/systemManualNav";

export default function SeasonalArchitecturePage() {
  const intro = getManualContent("seasonal-architecture/index");
  const parent = NAV.find((item) => item.href === "/system-manual/seasonal-architecture")!;

  return (
    <>
      <h1 className="sm-page-title">SEASONAL ARCHITECTURE</h1>
      {intro && <MarkdownContent content={intro} />}
      {parent.children.map((child) => {
        const anchor = child.href.split("#")[1];
        const content = getManualContent(`seasonal-architecture/${anchor}`);
        return (
          <section key={anchor}>
            <h2 id={anchor} className="sm-section-title">
              {child.label}
            </h2>
            {content && <MarkdownContent content={content} />}
          </section>
        );
      })}
    </>
  );
}
```

- [ ] **Step 2: Verify lint passes**

```bash
cd user_web_frontend && npm run lint
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add app/system-manual/seasonal-architecture/page.tsx
git commit -m "feat: seasonal-architecture P1 page aggregates all section content"
```

---

### Task 6: Replace TELEMETRY PIPELINE P2 pages with redirects

**Files:**
- Replace: all 6 `phase-*/page.tsx` files under `app/system-manual/telemetry-pipeline/`

Each file becomes a one-liner redirect to the anchor on the parent P1 page.

- [ ] **Step 1: Replace `phase-1-volumetric-ingestion/page.tsx`**

```tsx
import { redirect } from "next/navigation";
export default function Phase1Page() {
  redirect("/system-manual/telemetry-pipeline#phase-1-volumetric-ingestion");
}
```

- [ ] **Step 2: Replace `phase-2-resolution-queues/page.tsx`**

```tsx
import { redirect } from "next/navigation";
export default function Phase2Page() {
  redirect("/system-manual/telemetry-pipeline#phase-2-resolution-queues");
}
```

- [ ] **Step 3: Replace `phase-3-condition-parsing/page.tsx`**

```tsx
import { redirect } from "next/navigation";
export default function Phase3Page() {
  redirect("/system-manual/telemetry-pipeline#phase-3-condition-parsing");
}
```

- [ ] **Step 4: Replace `phase-4-redemption-extraction/page.tsx`**

```tsx
import { redirect } from "next/navigation";
export default function Phase4Page() {
  redirect("/system-manual/telemetry-pipeline#phase-4-redemption-extraction");
}
```

- [ ] **Step 5: Replace `phase-5-behavioral-profiling/page.tsx`**

```tsx
import { redirect } from "next/navigation";
export default function Phase5Page() {
  redirect("/system-manual/telemetry-pipeline#phase-5-behavioral-profiling");
}
```

- [ ] **Step 6: Replace `phase-6-stellar-initialization/page.tsx`**

```tsx
import { redirect } from "next/navigation";
export default function Phase6Page() {
  redirect("/system-manual/telemetry-pipeline#phase-6-stellar-initialization");
}
```

- [ ] **Step 7: Verify lint passes**

```bash
cd user_web_frontend && npm run lint
```

Expected: no errors.

- [ ] **Step 8: Commit**

```bash
git add app/system-manual/telemetry-pipeline/phase-*/page.tsx
git commit -m "feat: redirect telemetry-pipeline P2 routes to parent anchor"
```

---

### Task 7: Replace SEASONAL ARCHITECTURE P2 pages with redirects

**Files:**
- Replace: 3 P2 pages under `app/system-manual/seasonal-architecture/`

- [ ] **Step 1: Replace `chronological-grid/page.tsx`**

```tsx
import { redirect } from "next/navigation";
export default function ChronologicalGridPage() {
  redirect("/system-manual/seasonal-architecture#chronological-grid");
}
```

- [ ] **Step 2: Replace `phase-matrix/page.tsx`**

```tsx
import { redirect } from "next/navigation";
export default function PhaseMatrixPage() {
  redirect("/system-manual/seasonal-architecture#phase-matrix");
}
```

- [ ] **Step 3: Replace `modular-initialization/page.tsx`**

```tsx
import { redirect } from "next/navigation";
export default function ModularInitializationPage() {
  redirect("/system-manual/seasonal-architecture#modular-initialization");
}
```

- [ ] **Step 4: Verify lint passes**

```bash
cd user_web_frontend && npm run lint
```

Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add app/system-manual/seasonal-architecture/*/page.tsx
git commit -m "feat: redirect seasonal-architecture P2 routes to parent anchor"
```

---

### Task 8: Build verification + manual browser test

**Files:** none

- [ ] **Step 1: Run full build**

```bash
cd user_web_frontend && npm run build
```

Expected: exits 0, no TypeScript errors, no missing module errors.

- [ ] **Step 2: Start dev server**

```bash
cd user_web_frontend && npm run dev -- -p 3001
```

- [ ] **Step 3: Manual checks — navigate to `/system-manual/telemetry-pipeline`**

Verify:
- Page title "TELEMETRY PIPELINE" is visible
- Intro text and pipeline image appear
- All 6 phase headings (PHASE 1 … PHASE 6) appear as section titles with a top border
- Each phase's content renders below its heading
- Sidebar "TELEMETRY PIPELINE" item is highlighted (active state)
- P2 sidebar links are visible and not highlighted

- [ ] **Step 4: Test anchor links**

Click each P2 sidebar link (e.g. "PHASE 1: VOLUMETRIC INGESTION"). Verify the page scrolls to the correct section (browser URL bar shows `#phase-1-volumetric-ingestion`).

- [ ] **Step 5: Test old P2 routes redirect**

Navigate directly to `/system-manual/telemetry-pipeline/phase-1-volumetric-ingestion`. Verify it redirects to `/system-manual/telemetry-pipeline#phase-1-volumetric-ingestion`.

- [ ] **Step 6: Repeat checks for `/system-manual/seasonal-architecture`**

Verify all 3 sections (Chronological Grid, Phase Matrix, Modular Initialization Protocol) appear in sequence with section headings and content.

- [ ] **Step 7: Verify Overview page is unaffected**

Navigate to `/system-manual`. Verify it still shows only the overview content with no regressions.
