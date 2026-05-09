"use client";

import { useCallback, useState } from "react";
import type { ReactNode } from "react";

const CONTRACT_ADDRESS = "0x9e68096675578CCcf6eb7AD01350f731DDe633eD";

// Alphakek.ai's footer "Social" block recreated in PolyStars' brutalist
// design language. Pinned to the viewport bottom as a near-transparent
// overlay so the vertical card tickers on the home page remain visible
// through it. The outer wrapper is pointer-events: none so hovers on
// surrounding ticker cards still pass through; only the icon bar itself
// captures pointer events for the actual links.
//
// Icons are the exact SVG markup shipped by alphakek.ai
// (https://alphakek.ai/ → footer .social-block), copied verbatim and
// kept as currentColor so the brutalist hover recolor still works.
// Each link points at the main landing page of the corresponding
// service (no AIKEK-specific deep link).

type SocialItem = {
  key: string;
  label: string;
  href: string;
  icon: ReactNode;
};

const SOCIAL_ITEMS: SocialItem[] = [
  {
    key: "telegram",
    label: "Telegram",
    href: "https://t.me/POLYSTARS_Official",
    icon: (
      <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 27 25" fill="none" aria-hidden="true" focusable="false">
        <path
          fill="currentColor"
          fillRule="evenodd"
          d="M22.604 23.098v-.002l.024-.06 4.03-20.815v-.066c0-.52-.187-.972-.593-1.243-.356-.239-.766-.255-1.054-.233-.267.025-.532.08-.787.165a4.278 4.278 0 0 0-.323.121l-.017.007-22.47 9.03-.007.003a3.026 3.026 0 0 0-.659.345c-.288.204-.838.683-.745 1.443.076.63.5 1.03.786 1.238.169.12.35.223.541.303l.043.019.014.004.01.004 3.931 1.356c-.013.252.011.51.077.764l1.969 7.653c.107.417.34.79.664 1.064a1.979 1.979 0 0 0 2.343.166c.359-.226.639-.562.801-.96l3.075-3.367 5.28 4.146.076.033c.48.215.927.282 1.337.226.41-.058.735-.234.98-.434.283-.235.508-.534.658-.874l.01-.023.005-.009.001-.004Zm-15.25-8.143a.423.423 0 0 1 .174-.454l13.333-8.673s.785-.489.757 0c0 0 .14.085-.281.486-.398.381-9.503 9.386-10.425 10.297a.385.385 0 0 0-.103.188l-1.486 5.81-1.97-7.654Z"
          clipRule="evenodd"
        />
      </svg>
    ),
  },
  {
    key: "x",
    label: "X",
    href: "https://x.com/POLYSTARSAPP",
    icon: (
      <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 23 25" fill="none" aria-hidden="true" focusable="false">
        <path
          fill="currentColor"
          d="m22.779 22.448-7.555-11.49 7.396-7.879c.26-.283.393-.654.372-1.032a1.422 1.422 0 0 0-.484-.988 1.515 1.515 0 0 0-1.062-.373c-.39.016-.759.18-1.025.457L13.575 8.44 8.907 1.34a1.468 1.468 0 0 0-.538-.49 1.525 1.525 0 0 0-.715-.177H1.709c-.266 0-.527.07-.756.2-.23.132-.418.32-.546.547a1.403 1.403 0 0 0 .049 1.465l7.555 11.49-7.402 7.878c-.134.14-.238.303-.306.482a1.398 1.398 0 0 0 .048 1.108c.083.173.201.328.347.456a1.53 1.53 0 0 0 1.645.231c.177-.083.335-.199.466-.341l6.85-7.297 4.668 7.101c.134.204.32.373.539.49.219.116.465.177.715.177h5.944c.267 0 .528-.07.757-.2.23-.132.418-.32.546-.547a1.403 1.403 0 0 0-.05-1.464Zm-6.382-.667L4.417 3.55h2.42l11.981 18.23h-2.421Z"
        />
      </svg>
    ),
  },
  {
    key: "dexscreener",
    label: "DEXSCREENER",
    href: "https://dexscreener.com/",
    icon: (
      <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 252 300" fill="currentColor" aria-hidden="true" focusable="false">
        <g transform="scale(0.85) translate(25,30)">
          <path d="M151.8 106.9c9.2-4.6 20.9-11.3 32.6-20.6 2.4 5.1 2.7 9.6 1.4 13.2-.9 2.6-2.6 4.8-4.8 6.5a22 22 0 0 1-8.8 4c-6.3 1.5-13.7.7-20.4-3.1m1.6 46.9 12.2 7c-24.8 13.9-31.6 39.7-39.6 64.8-8-25.1-14.8-51-39.6-64.8l12.2-7a6 6 0 0 0 3.8-5.9c-1-23.5 5.3-34 13.9-40.5 3-2.3 6.4-3.5 9.7-3.5s6.7 1.2 9.7 3.5c8.6 6.5 15 17 13.9 40.5a6 6 0 0 0 3.8 5.9zM126 0a101.4 101.4 0 0 1 72.7 30.8c7.8.3 19.4-8.5 24.7-16.7-9.2 30.2-51.3 66-80.4 79.6-5.2-4-11.1-6-17-6s-11.8 2-17 6C79.9 80 37.8 44.3 28.6 14.1c5.3 8.2 16.9 17 24.7 16.7A101.4 101.4 0 0 1 126 0m-25.8 106.9a189.4 189.4 0 0 1-32.6-20.6c-2.4 5.1-2.7 9.6-1.4 13.2.9 2.6 2.6 4.8 4.8 6.5a22 22 0 0 0 8.8 4c6.3 1.5 13.7.7 20.4-3.1" />
          <path d="M197.2 75c6.4-6.5 12-13.7 16.6-20l2.3 4.3a101 101 0 0 1 11.4 46.5v26.5l.1 13.8c.5 33.7 7.9 67.9 24.4 99.1l-34.6-27.9-24.5 39.8-25.8-24.2-41.1 66.7-41.1-66.7L59 257l-24.5-39.8-34.6 28A218.5 218.5 0 0 0 24.4 146l.1-13.8v-26.5A101 101 0 0 1 36 59.3l2.3-4.4c4.5 6.4 10.2 13.6 16.6 20.1l-2 4.2a33.4 33.4 0 0 0-2.1 25.8 31 31 0 0 0 10.1 14c4.5 3.5 9.8 5.9 15.4 7.1 3.6.8 7.3 1.2 11 1-1 4.9-1.3 9.9-1.3 15L53.2 161 78.4 175a42 42 0 0 1 5.7 3.9c20.8 18.6 33.3 73.8 41.9 100.8 8.6-27 21-82.2 41.9-100.8a41.9 41.9 0 0 1 5.7-3.9l25.2-14.1-32.7-18.9c0-5.1-.4-10.1-1.2-15 3.6.2 7.3-.2 11-1a38.3 38.3 0 0 0 15.3-7.2 31 31 0 0 0 10.1-14c3-8.5 1.8-17.6-2.1-25.7l-2-4.2z" />
        </g>
      </svg>
    ),
  },
];

export default function SiteSocialFooter() {
  const [copied, setCopied] = useState(false);

  const handleCopyContract = useCallback(async () => {
    try {
      if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(CONTRACT_ADDRESS);
      } else if (typeof document !== "undefined") {
        const ta = document.createElement("textarea");
        ta.value = CONTRACT_ADDRESS;
        ta.style.position = "fixed";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        document.body.removeChild(ta);
      }
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1400);
    } catch {
      // ignore
    }
  }, []);

  return (
    <footer className="site-social-footer" aria-label="Social links">
      <nav className="site-social-footer-inner" aria-label="Social">
        <span className="site-social-footer-heading">info@polystars.app</span>
        <ul className="site-social-footer-list">
          {SOCIAL_ITEMS.map((item) => (
            <li key={item.key} className="site-social-footer-item">
              <a
                href={item.href}
                className="site-social-footer-link"
                aria-label={`Visit ${item.label}`}
                title={item.label}
                target="_blank"
                rel="noopener noreferrer"
              >
                <span className="site-social-footer-icon" aria-hidden="true">
                  {item.icon}
                </span>
              </a>
            </li>
          ))}
        </ul>
        <span className="site-social-footer-contract">
          <span className="site-social-footer-contract-label" aria-hidden="true">
            CA
          </span>
          <button
            type="button"
            className={`site-social-footer-contract-value${copied ? " is-copied" : ""}`}
            onClick={handleCopyContract}
            title="Click to copy contract address"
            aria-label={`Copy contract address ${CONTRACT_ADDRESS}`}
          >
            <span className="site-social-footer-contract-text">
              {CONTRACT_ADDRESS}
            </span>
            <span
              className="site-social-footer-contract-toast"
              aria-live="polite"
              aria-hidden={!copied}
            >
              Copied!
            </span>
          </button>
        </span>
      </nav>
    </footer>
  );
}
