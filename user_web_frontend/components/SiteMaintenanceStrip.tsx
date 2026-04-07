"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";

import { fetchSiteStatus } from "../lib/userApiBase";

const STRIP_ID = "site-maintenance-strip";

export default function SiteMaintenanceStrip() {
  const [active, setActive] = useState(false);
  const [checked, setChecked] = useState(false);
  const stripRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    void fetchSiteStatus().then((s) => {
      setActive(Boolean(s?.wallet_actions_disabled));
      setChecked(true);
    });
  }, []);

  useLayoutEffect(() => {
    if (!checked || !active) {
      document.body.classList.remove("has-site-maintenance-strip");
      document.documentElement.style.removeProperty("--site-maintenance-strip-height");
      return undefined;
    }

    const apply = () => {
      const h = stripRef.current ? Math.ceil(stripRef.current.getBoundingClientRect().height) : 48;
      document.documentElement.style.setProperty("--site-maintenance-strip-height", `${h}px`);
      document.body.classList.add("has-site-maintenance-strip");
    };

    apply();
    window.addEventListener("resize", apply);
    const ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver(apply) : null;
    if (stripRef.current && ro) ro.observe(stripRef.current);

    return () => {
      window.removeEventListener("resize", apply);
      ro?.disconnect();
      document.body.classList.remove("has-site-maintenance-strip");
      document.documentElement.style.removeProperty("--site-maintenance-strip-height");
    };
  }, [checked, active]);

  if (!checked || !active) return null;

  return (
    <div
      ref={stripRef}
      id={STRIP_ID}
      className="site-maintenance-strip"
      role="status"
      aria-live="polite"
    >
      Maintenance in progress: some actions are disabled.
    </div>
  );
}
