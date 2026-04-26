# System Manual — P1 Single-Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** All P2 content renders on its parent P1 page; adding a new section = drop a folder with `.md` files, no code changes needed.

**Architecture:** Filesystem-driven. `lib/systemManualContent.ts` gains three helpers that scan `content/system-manual/` at runtime. The layout builds the nav server-side and passes it to the client sidebar as props. A single dynamic route `[section]/page.tsx` replaces all static P1 page files. All static P1 and P2 `page.tsx` files are deleted.

**Tech Stack:** Next.js 14 (App Router), TypeScript, Node.js `fs`, React server + client components

---

## File Map

| Action | File |
|--------|------|
| Modify | `user_web_frontend/lib/systemManualContent.ts` |
| Modify | `user_web_frontend/app/globals.css` |
| Modify | `user_web_frontend/app/system-manual/layout.tsx` |
| Modify | `user_web_frontend/components/SystemManualSidebar.tsx` |
| Create | `user_web_frontend/app/system-manual/[section]/page.tsx` |
| Delete | `user_web_frontend/app/system-manual/telemetry-pipeline/page.tsx` |
| Delete | `user_web_frontend/app/system-manual/seasonal-architecture/page.tsx` |
| Delete | `user_web_frontend/app/system-manual/telemetry-pipeline/phase-1-volumetric-ingestion/page.tsx` |
| Delete | `user_web_frontend/app/system-manual/telemetry-pipeline/phase-2-resolution-queues/page.tsx` |
| Delete | `user_web_frontend/app/system-manual/telemetry-pipeline/phase-3-condition-parsing/page.tsx` |
| Delete | `user_web_frontend/app/system-manual/telemetry-pipeline/phase-4-redemption-extraction/page.tsx` |
| Delete | `user_web_frontend/app/system-manual/telemetry-pipeline/phase-5-behavioral-profiling/page.tsx` |
| Delete | `user_web_frontend/app/system-manual/telemetry-pipeline/phase-6-stellar-initialization/page.tsx` |
| Delete | `user_web_frontend/app/system-manual/seasonal-architecture/chronological-grid/page.tsx` |
| Delete | `user_web_frontend/app/system-manual/seasonal-architecture/phase-matrix/page.tsx` |
| Delete | `user_web_frontend/app/system-manual/seasonal-architecture/modular-initialization/page.tsx` |

---

### Task 1: Add filesystem helpers to `systemManualContent.ts`

**Files:**
- Modify: `user_web_frontend/lib/systemManualContent.ts`

Three new exports:
- `slugToLabel(slug)` — strips optional `\d+-` prefix, replaces hyphens with spaces, uppercases: `phase-1-volumetric-ingestion` → `PHASE 1 VOLUMETRIC INGESTION`
- `getSections()` — returns sorted directory names under `content/system-manual/`
- `getSectionChildren(section)` — returns sorted `.md` filenames (minus extension, excluding `index.md`) under `content/system-manual/[section]/`

- [ ] **Step 1: Replace the full contents of `user_web_frontend/lib/systemManualContent.ts`**

```ts
import fs from "fs";
import path from "path";

export function getManualContent(slug: string): string | null {
  const filePath = path.join(process.cwd(), "content", "system-manual", `${slug}.md`);
  try {
    return fs.readFileSync(filePath, "utf-8");
  } catch {
    return null;
  }
}

export function slugToLabel(slug: string): string {
  const withoutPrefix = slug.replace(/^\d+-/, "");
  return withoutPrefix.replace(/-/g, " ").toUpperCase();
}

export function getSections(): string[] {
  const contentDir = path.join(process.cwd(), "content", "system-manual");
  try {
    return fs
      .readdirSync(contentDir)
      .filter((name) => fs.statSync(path.join(contentDir, name)).isDirectory())
      .sort();
  } catch {
    return [];
  }
}

export function getSectionChildren(section: string): string[] {
  const sectionDir = path.join(process.cwd(), "content", "system-manual", section);
  try {
    return fs
      .readdirSync(sectionDir)
      .filter((name) => name.endsWith(".md") && name !== "index.md")
      .map((name) => name.replace(/\.md$/, ""))
      .sort();
  } catch {
    return [];
  }
}
```

- [ ] **Step 2: Commit**

```bash
cd user_web_frontend
git add lib/systemManualContent.ts
git commit -m "feat: add slugToLabel, getSections, getSectionChildren helpers"
```

---

### Task 2: Add `sm-section-title` CSS class

**Files:**
- Modify: `user_web_frontend/app/globals.css`

The dynamic P1 page renders a heading (`<h2 className="sm-section-title">`) between sections. It needs its own class — visually between the page `h1` (28 px) and inline markdown `h2` (18 px), with a top border to divide sections.

- [ ] **Step 1: Find this block in `globals.css` (around line 2707)**

```css
.sm-placeholder {
  min-height: 200px;
}
```

Insert immediately after it:

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
git commit -m "feat: add sm-section-title CSS for P1 section dividers"
```

---

### Task 3: Build nav server-side in layout

**Files:**
- Modify: `user_web_frontend/app/system-manual/layout.tsx`

The layout is a server component — it can call `getSections()` and `getSectionChildren()` directly. It builds the full nav array and passes it as a prop to `SystemManualSidebar`. The sidebar no longer needs to know about the filesystem.

- [ ] **Step 1: Replace the full contents of `user_web_frontend/app/system-manual/layout.tsx`**

```tsx
import { type ReactNode } from "react";

import SystemManualSidebar from "../../components/SystemManualSidebar";
import {
  getSectionChildren,
  getSections,
  slugToLabel,
} from "../../lib/systemManualContent";

type NavChild = { label: string; href: string };
type NavItem = { label: string; href: string; children: NavChild[] };

function buildNav(): NavItem[] {
  return [
    { label: "Overview", href: "/system-manual", children: [] },
    ...getSections().map((section) => ({
      label: slugToLabel(section),
      href: `/system-manual/${section}`,
      children: getSectionChildren(section).map((childSlug) => ({
        label: slugToLabel(childSlug),
        href: `/system-manual/${section}#${childSlug}`,
      })),
    })),
  ];
}

export default function SystemManualLayout({ children }: { children: ReactNode }) {
  const nav = buildNav();
  return (
    <div className="sm-layout">
      <SystemManualSidebar nav={nav} />
      <main className="sm-content">
        <div className="sm-content-inner">{children}</div>
      </main>
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add app/system-manual/layout.tsx
git commit -m "feat: build system manual nav server-side in layout"
```

---

### Task 4: Update sidebar to accept nav as prop

**Files:**
- Modify: `user_web_frontend/components/SystemManualSidebar.tsx`

The sidebar receives the nav array as a prop instead of importing a hardcoded config. The `usePathname()` usage stays — active state logic is unchanged (P1 item highlights when `pathname === item.href`; P2 items never highlight individually).

- [ ] **Step 1: Replace the full contents of `user_web_frontend/components/SystemManualSidebar.tsx`**

```tsx
"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

type NavChild = { label: string; href: string };
type NavItem = { label: string; href: string; children: NavChild[] };

export default function SystemManualSidebar({ nav }: { nav: NavItem[] }) {
  const pathname = usePathname();

  return (
    <nav className="sm-sidebar" aria-label="System Manual navigation">
      {nav.map((item) => {
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
git commit -m "feat: sidebar accepts nav as prop, removes hardcoded config"
```

---

### Task 5: Create dynamic `[section]/page.tsx`

**Files:**
- Create: `user_web_frontend/app/system-manual/[section]/page.tsx`

This single file handles every P1 route. It renders:
1. `<h1>` — section title derived from slug
2. `index.md` content — the intro
3. For each child `.md` file (sorted alphabetically): `<h2 id={childSlug}>` + child markdown content

`generateStaticParams` tells Next.js which section slugs exist at build time.

- [ ] **Step 1: Create `user_web_frontend/app/system-manual/[section]/page.tsx`**

```tsx
import MarkdownContent from "../../../components/MarkdownContent";
import {
  getManualContent,
  getSectionChildren,
  getSections,
  slugToLabel,
} from "../../../lib/systemManualContent";

export function generateStaticParams() {
  return getSections().map((section) => ({ section }));
}

export default function SectionPage({ params }: { params: { section: string } }) {
  const { section } = params;
  const intro = getManualContent(`${section}/index`);
  const children = getSectionChildren(section);

  return (
    <>
      <h1 className="sm-page-title">{slugToLabel(section)}</h1>
      {intro && <MarkdownContent content={intro} />}
      {children.map((childSlug) => {
        const content = getManualContent(`${section}/${childSlug}`);
        return (
          <section key={childSlug}>
            <h2 id={childSlug} className="sm-section-title">
              {slugToLabel(childSlug)}
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
git add app/system-manual/\[section\]/page.tsx
git commit -m "feat: dynamic [section]/page.tsx aggregates all P2 content"
```

---

### Task 6: Delete static P1 and P2 page files

**Files:**
- Delete: 2 static P1 pages + 9 static P2 pages

Static P1 files conflict with the new dynamic route (Next.js prefers static over dynamic, so they'd shadow `[section]/page.tsx`). P2 files are no longer reachable from the sidebar — delete them so those URLs cleanly 404.

- [ ] **Step 1: Delete all static P1 and P2 page files**

```bash
cd user_web_frontend
rm app/system-manual/telemetry-pipeline/page.tsx
rm app/system-manual/seasonal-architecture/page.tsx
rm app/system-manual/telemetry-pipeline/phase-1-volumetric-ingestion/page.tsx
rm app/system-manual/telemetry-pipeline/phase-2-resolution-queues/page.tsx
rm app/system-manual/telemetry-pipeline/phase-3-condition-parsing/page.tsx
rm app/system-manual/telemetry-pipeline/phase-4-redemption-extraction/page.tsx
rm app/system-manual/telemetry-pipeline/phase-5-behavioral-profiling/page.tsx
rm app/system-manual/telemetry-pipeline/phase-6-stellar-initialization/page.tsx
rm app/system-manual/seasonal-architecture/chronological-grid/page.tsx
rm app/system-manual/seasonal-architecture/phase-matrix/page.tsx
rm app/system-manual/seasonal-architecture/modular-initialization/page.tsx
```

- [ ] **Step 2: Verify lint passes**

```bash
npm run lint
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "chore: remove static P1 and P2 page files (replaced by dynamic route)"
```

---

### Task 7: Build verification + manual browser test

**Files:** none

- [ ] **Step 1: Run full build**

```bash
cd user_web_frontend && npm run build
```

Expected: exits 0, no TypeScript errors.

- [ ] **Step 2: Start dev server**

```bash
npm run dev -- -p 3001
```

- [ ] **Step 3: Check sidebar auto-populates**

Open `http://localhost:3001/system-manual`. Verify:
- "TELEMETRY PIPELINE" and "SEASONAL ARCHITECTURE" appear in sidebar with their P2 entries listed
- No hardcoded labels in source — all derived from filesystem

- [ ] **Step 4: Check TELEMETRY PIPELINE page**

Navigate to `/system-manual/telemetry-pipeline`. Verify:
- `TELEMETRY PIPELINE` h1 appears
- Intro text and pipeline image appear
- All 6 phase headings appear with a top border between them
- Each phase's content renders below its heading
- Sidebar TELEMETRY PIPELINE item is highlighted

- [ ] **Step 5: Test anchor links**

Click each P2 sidebar link (e.g. "PHASE 1 VOLUMETRIC INGESTION"). Verify the page scrolls to the correct heading.

- [ ] **Step 6: Check SEASONAL ARCHITECTURE page**

Navigate to `/system-manual/seasonal-architecture`. Verify all 3 sections render in alphabetical order.

- [ ] **Step 7: Verify overview is unaffected**

Navigate to `/system-manual`. Verify it still shows only the overview content.

- [ ] **Step 8: Verify adding a new section works**

Create a test directory and file:

```bash
mkdir -p user_web_frontend/content/system-manual/test-section
echo "Test intro." > user_web_frontend/content/system-manual/test-section/index.md
echo "Test child content." > user_web_frontend/content/system-manual/test-section/01-test-child.md
```

Restart dev server, navigate to `/system-manual/test-section`. Verify:
- Page renders with title "TEST SECTION"
- "01 TEST CHILD" section heading and content appear
- Sidebar automatically shows "TEST SECTION" with "01 TEST CHILD" child link

Delete test files after verifying:

```bash
rm -rf user_web_frontend/content/system-manual/test-section
```
