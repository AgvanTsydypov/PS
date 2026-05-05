"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

import SiteLogoLink from "./SiteLogoLink";

type NavChild = { label: string; href: string };
type NavItem = { label: string; href: string; children: NavChild[] };

export default function SystemManualSidebar({ nav }: { nav: NavItem[] }) {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);

  useEffect(() => {
    setOpen(false);
  }, [pathname]);

  useEffect(() => {
    if (!open) return;
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.body.style.overflow = prevOverflow;
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const close = () => setOpen(false);

  return (
    <>
      <button
        type="button"
        className={`sm-sidebar-toggle${open ? " sm-sidebar-toggle-open" : ""}`}
        aria-label={open ? "Close manual navigation" : "Open manual navigation"}
        aria-expanded={open}
        aria-controls="sm-sidebar-nav"
        onClick={() => setOpen((o) => !o)}
      >
        <span className="sm-sidebar-toggle-bar" />
        <span className="sm-sidebar-toggle-bar" />
        <span className="sm-sidebar-toggle-bar" />
      </button>
      {open && (
        <div
          className="sm-sidebar-backdrop"
          onClick={close}
          aria-hidden="true"
        />
      )}
      <nav
        id="sm-sidebar-nav"
        className={`sm-sidebar${open ? " sm-sidebar-open" : ""}`}
        aria-label="System Manual navigation"
      >
        <SiteLogoLink className="sm-sidebar-logo" colorful />
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
              <Link href={item.href} className={itemClasses} onClick={close}>
                {item.label}
              </Link>
              {hasChildren && (
                <div className="sm-nav-children sm-nav-open">
                  {item.children.map((child) => (
                    <Link
                      key={child.href}
                      href={child.href}
                      className="sm-nav-item sm-nav-p2"
                      onClick={close}
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
    </>
  );
}
