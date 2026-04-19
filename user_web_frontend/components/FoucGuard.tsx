"use client";

import { useEffect } from "react";

/**
 * Reveals the document body once React has fully hydrated. The matching
 * `body{visibility:hidden}` style lives in `app/layout.tsx` and is applied
 * synchronously before first paint. We toggle the `app-ready` class only
 * after hydration so that any CSS chunks injected at runtime by Next.js'
 * dev server (HMR runtime, per-route CSS, CSS-in-JS, etc.) have already
 * been applied to the DOM. This prevents the brief "everything stacked at
 * the top-left" flash that happens when the body becomes visible before
 * the layout-defining stylesheets are in place.
 */
export default function FoucGuard() {
  useEffect(() => {
    const reveal = () => {
      if (typeof document === "undefined") return;
      const body = document.body;
      if (!body) return;
      body.classList.add("app-ready");
    };

    // Defer past the initial paint of this effect so that any synchronous
    // style mutations from sibling client components have a chance to land
    // first. Two rAFs guarantee we're at least one paint past hydration.
    let raf2 = 0;
    const raf1 = window.requestAnimationFrame(() => {
      raf2 = window.requestAnimationFrame(reveal);
    });

    return () => {
      window.cancelAnimationFrame(raf1);
      if (raf2) window.cancelAnimationFrame(raf2);
    };
  }, []);

  return null;
}
