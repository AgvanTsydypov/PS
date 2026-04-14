"use client";

import Link from "next/link";

/** Cardgen logo (62×67); displayed in an 80×80 box. */
export default function SiteLogoLink({
  className,
  showWordmark = false,
}: {
  className?: string;
  showWordmark?: boolean;
}) {
  const wordmarkClassName = showWordmark
    ? "site-logo-wordmark site-logo-wordmark-hero"
    : "site-logo-wordmark";

  return (
    <Link
      href="/"
      className={`site-logo-link${className ? ` ${className}` : ""}`}
      aria-label="PolyStars home"
    >
      <span className={wordmarkClassName}>POLYSTARS</span>
    </Link>
  );
}
