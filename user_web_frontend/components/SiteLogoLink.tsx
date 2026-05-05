"use client";

import Link from "next/link";

const WORDMARK = "POLYSTARS";

// Per-letter colors — must match the footer wordmark on the generated card
// (scripts/cardgen/generate_card.py, "FOOTER" block).
const LETTER_COLORS = [
  "#2E5CFF",
  "#D5744C",
  "#B8BFCE",
  "#925AB9",
  "#328ADD",
  "#3E9292",
  "#C08838",
  "#736D51",
  "#BA4040",
];

/** Cardgen logo (62×67); displayed in an 80×80 box. */
export default function SiteLogoLink({
  className,
  showWordmark = false,
  colorful = false,
}: {
  className?: string;
  showWordmark?: boolean;
  colorful?: boolean;
}) {
  const wordmarkClassName = [
    "site-logo-wordmark",
    showWordmark ? "site-logo-wordmark-hero" : null,
    colorful ? "site-logo-wordmark-colorful" : null,
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <Link
      href="/"
      className={`site-logo-link${className ? ` ${className}` : ""}`}
      aria-label="PolyStars home"
    >
      <span className={wordmarkClassName}>
        {colorful
          ? WORDMARK.split("").map((letter, idx) => (
              <span
                key={idx}
                className="site-logo-wordmark-letter"
                style={{ color: LETTER_COLORS[idx] }}
              >
                {letter}
              </span>
            ))
          : WORDMARK}
      </span>
    </Link>
  );
}
