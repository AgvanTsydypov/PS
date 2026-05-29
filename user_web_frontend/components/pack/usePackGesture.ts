"use client";

import { useEffect, useRef } from "react";
import { useMotionValue } from "framer-motion";
import { usePackStore } from "./store";

const DRAG_DISTANCE_PX = 260;
const RESISTANCE_CURVE = 1.4;
const PULL_DEADZONE = 0.1; // progress above this counts as "pulling"

/**
 * The gesture only reports *pull intent* via `progress` (0..1). The actual
 * opening playback (autoplay at a fixed FPS), the mint trigger, and the return
 * to idle all live in PackFrameSequence, which reads `progress` each frame.
 */
export function usePackGesture() {
  const progress = useMotionValue(0);
  const startY = useRef<number | null>(null);
  const activePointerId = useRef<number | null>(null);

  const phase = usePackStore((s) => s.phase);
  const setPhase = usePackStore((s) => s.setPhase);

  // Reset pull intent whenever we land back on idle (e.g. after "Mint another")
  useEffect(() => {
    if (phase === "idle") progress.set(0);
  }, [phase, progress]);

  const interactionLocked = phase !== "idle" && phase !== "dragging";

  const onPointerDown = (e: React.PointerEvent) => {
    if (interactionLocked) return;
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    activePointerId.current = e.pointerId;
    startY.current = e.clientY;
  };

  const onPointerMove = (e: React.PointerEvent) => {
    if (interactionLocked) return;
    if (activePointerId.current !== e.pointerId) return;
    if (startY.current == null) return;
    const delta = startY.current - e.clientY;
    const raw = Math.max(0, Math.min(1, delta / DRAG_DISTANCE_PX));
    const eased = Math.pow(raw, RESISTANCE_CURVE);
    progress.set(eased);
    // Begin the opening once the user actually pulls past the deadzone
    if (eased > PULL_DEADZONE && phase === "idle") setPhase("dragging");
  };

  const releasePointer = (e: React.PointerEvent) => {
    if (activePointerId.current !== e.pointerId) return;
    try {
      (e.currentTarget as HTMLElement).releasePointerCapture(e.pointerId);
    } catch {
      // ignore
    }
    activePointerId.current = null;
    startY.current = null;
    // Stop pulling — PackFrameSequence will play the opening back to the start
    // and return to idle on its own.
    progress.set(0);
  };

  return {
    progress,
    handlers: {
      onPointerDown,
      onPointerMove,
      onPointerUp: releasePointer,
      onPointerCancel: releasePointer,
    },
    interactionLocked,
  };
}
