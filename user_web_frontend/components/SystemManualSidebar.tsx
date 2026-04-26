"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import SiteLogoLink from "./SiteLogoLink";

type NavChild = { label: string; href: string };
type NavItem = { label: string; href: string; children: NavChild[] };

export default function SystemManualSidebar({ nav }: { nav: NavItem[] }) {
  const pathname = usePathname();

  return (
    <nav className="sm-sidebar" aria-label="System Manual navigation">
      <SiteLogoLink className="sm-sidebar-logo" />
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
