"use client";

/**
 * Looping cursor hint shown to the right of the closed pack while idle.
 * Communicates the open gesture: the cursor appears as an open hand, presses
 * down after ~0.5s (switches to a grabbing hand + ring pulse), then drags up a
 * diagonal track (30° to the horizontal) and releases. Purely decorative —
 * pointer-events are disabled so the drag lands on the gesture target beneath.
 */
export function DragHint() {
  return (
    <div className="drag-hint" aria-hidden>
      {/* Everything in the rail is rotated so the travel axis sits at 30° to
          the horizontal; the label stays upright outside it. */}
      <span className="drag-hint-rail">
        <span className="drag-hint-arrow" />
        <span className="drag-hint-track" />
        <span className="drag-hint-pointer">
          <span className="drag-hint-ring" />
          <span className="drag-hint-cursor">
            {/* Open hand — shown for the first ~0.5s */}
            <svg
              className="drag-hint-hand drag-hint-hand--open"
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
              className="drag-hint-hand drag-hint-hand--grab"
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
      </span>
      <span className="drag-hint-label">HOLD &amp; DRAG</span>
    </div>
  );
}
