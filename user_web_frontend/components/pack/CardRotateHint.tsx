"use client";

/**
 * Looping cursor hint shown over the revealed card to signal it can be spun.
 * The guide is a curved arc (the front of a turntable seen in perspective) that
 * bends away into depth at both ends, so the gesture reads as a 3D rotation
 * rather than a flat swipe. The cursor (open hand → grab + ring pulse) rides
 * the exact curve via CSS offset-path. Decorative — pointer-events are disabled
 * so the drag lands on the card turntable beneath; hidden by the parent once the
 * user starts dragging.
 *
 * The SVG coordinate space (240×64) maps 1:1 to the container px, so the
 * offset-path below MUST match the <path> d attribute exactly.
 */
const ARC = "M 8 20 C 60 58 180 58 232 20";

export function CardRotateHint() {
  return (
    <div className="rotate-hint" aria-hidden>
      <svg className="rotate-hint-track" viewBox="0 0 240 64" fill="none">
        <defs>
          <linearGradient id="rotateHintFade" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0" stopColor="#fff" stopOpacity="0" />
            <stop offset="0.18" stopColor="#fff" stopOpacity="0.5" />
            <stop offset="0.82" stopColor="#fff" stopOpacity="0.5" />
            <stop offset="1" stopColor="#fff" stopOpacity="0" />
          </linearGradient>
        </defs>
        {/* Front arc of the turntable. */}
        <path
          d={ARC}
          stroke="url(#rotateHintFade)"
          strokeWidth="2"
          strokeLinecap="round"
        />
        {/* End caps hinting the path wraps back into depth. */}
        <path
          d="M 13 15 L 7 20 L 13 25"
          stroke="rgba(255,255,255,0.4)"
          strokeWidth="1.6"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        <path
          d="M 227 15 L 233 20 L 227 25"
          stroke="rgba(255,255,255,0.4)"
          strokeWidth="1.6"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>

      <span className="rotate-hint-pointer">
        <span className="rotate-hint-ring" />
        <span className="rotate-hint-cursor">
          {/* Open hand — shown for the first ~0.5s */}
          <svg
            className="rotate-hint-hand rotate-hint-hand--open"
            width="26"
            height="26"
            viewBox="0 0 24 24"
            fill="none"
            stroke="#fff"
            strokeWidth="1.6"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M18 11V6a2 2 0 0 0-2-2a2 2 0 0 0-2 2" />
            <path d="M14 10V4a2 2 0 0 0-2-2a2 2 0 0 0-2 2v2" />
            <path d="M10 10.5V6a2 2 0 0 0-2-2a2 2 0 0 0-2 2v8" />
            <path d="M18 8a2 2 0 1 1 4 0v6a8 8 0 0 1-8 8h-2c-2.8 0-4.5-.86-5.99-2.34l-3.6-3.6a2 2 0 0 1 2.83-2.82L7 15" />
          </svg>
          {/* Grabbing (clenched) hand — shown once the press registers */}
          <svg
            className="rotate-hint-hand rotate-hint-hand--grab"
            width="26"
            height="26"
            viewBox="0 0 24 24"
            fill="none"
            stroke="#fff"
            strokeWidth="1.6"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M18 12V10.5a2 2 0 0 0-2-2a2 2 0 0 0-2 2V11" />
            <path d="M14 11.5V9.5a2 2 0 0 0-2-2a2 2 0 0 0-2 2V11" />
            <path d="M10 11.5V10a2 2 0 0 0-2-2a2 2 0 0 0-2 2v3" />
            <path d="M18 9a2 2 0 1 1 4 0v5a8 8 0 0 1-8 8h-2c-2.8 0-4.5-.86-5.99-2.34l-3.6-3.6a2 2 0 0 1 2.83-2.82L7 15.5" />
          </svg>
        </span>
      </span>

      <span className="rotate-hint-label">DRAG TO ROTATE</span>
    </div>
  );
}
