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
