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
