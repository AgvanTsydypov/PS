"use client";

import Link from "next/link";

/** Cardgen logo (62×67); displayed in an 80×80 box. */
export default function SiteLogoLink({ className }: { className?: string }) {
  return (
    <Link
      href="/"
      className={`site-logo-link${className ? ` ${className}` : ""}`}
      aria-label="PolyStars home"
    >
      {/* eslint-disable-next-line @next/next/no-img-element -- static SVG from /public */}
      <img
        className="site-logo-img"
        src="/logo.svg"
        alt=""
        width={80}
        height={80}
        decoding="async"
      />
    </Link>
  );
}
