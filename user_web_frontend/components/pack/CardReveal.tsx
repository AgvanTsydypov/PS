"use client";

import { useEffect, useRef, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { usePackStore } from "./store";
import { CardRotateHint } from "./CardRotateHint";

const CARD_FRAME_COUNT = 120;
const cardFramePath = (i: number) =>
  `/pack/card-frames/${String(i + 1).padStart(4, "0")}.webp`; // i is 0-based

// Drag sensitivity: pixels of horizontal drag per frame step.
// ~4px/frame keeps a full 360° turn at ~480px of drag despite 120 frames.
const DRAG_PX_PER_FRAME = 4;

export function CardReveal() {
  const phase = usePackStore((s) => s.phase);
  const result = usePackStore((s) => s.result);
  const reset = usePackStore((s) => s.reset);

  const canvasRef = useRef<HTMLCanvasElement>(null);
  const framesRef = useRef<(HTMLImageElement | null)[]>(
    new Array(CARD_FRAME_COUNT).fill(null)
  );
  const frameFloatRef = useRef(0); // continuous frame position
  const currentDrawnRef = useRef(-1);
  const sizedRef = useRef(false);
  const draggingRef = useRef(false);
  const lastXRef = useRef(0);
  const activePointerRef = useRef<number | null>(null);
  const rafRef = useRef(0);
  const [loaded, setLoaded] = useState(0);
  const [hasDragged, setHasDragged] = useState(false);

  // Card is present from "revealing" so the flying-away pack (rendered on top)
  // overlaps and progressively uncovers it.
  const show = phase === "revealing" || phase === "revealed";

  // Preload the card frames
  useEffect(() => {
    if (!show) return;
    let cancelled = false;
    let done = 0;
    for (let i = 0; i < CARD_FRAME_COUNT; i++) {
      const img = new Image();
      img.decoding = "async";
      const finish = () => {
        if (cancelled) return;
        done++;
        setLoaded(done);
      };
      img.onload = () => {
        if (cancelled) return;
        framesRef.current[i] = img;
        finish();
      };
      img.onerror = () => {
        if (cancelled) return;
        // eslint-disable-next-line no-console
        console.warn("[card frames] failed to load", cardFramePath(i));
        finish();
      };
      img.src = cardFramePath(i);
    }
    return () => {
      cancelled = true;
    };
  }, [show]);

  // Size canvas + run the draw/auto-spin loop while visible
  useEffect(() => {
    if (!show) return;
    const canvas = canvasRef.current;
    if (!canvas) return;

    // Every fresh reveal must start with the card facing the user (frame 0).
    // The component returns null while hidden instead of unmounting, so the
    // ref would otherwise persist the last dragged rotation across openings.
    frameFloatRef.current = 0;
    setHasDragged(false); // re-show the rotate hint for the new card

    const sizeCanvas = () => {
      // Use offset* (layout size, ignores the entry scale transform) — measuring
      // with getBoundingClientRect during the scale-in animation would size the
      // backing store too small and the card would look blurry when scaled up.
      const w = canvas.offsetWidth;
      const h = canvas.offsetHeight;
      if (w === 0 || h === 0) return;
      const dpr = window.devicePixelRatio || 1;
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
      sizedRef.current = true;
      currentDrawnRef.current = -1;
    };
    sizeCanvas();
    window.addEventListener("resize", sizeCanvas);

    const tick = () => {
      draw(); // only the user's drag changes the frame; no auto-spin
      rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);

    return () => {
      cancelAnimationFrame(rafRef.current);
      window.removeEventListener("resize", sizeCanvas);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [show]);

  function frameIndex() {
    const n = CARD_FRAME_COUNT;
    return ((Math.round(frameFloatRef.current) % n) + n) % n;
  }

  function resolveImage(idx: number): HTMLImageElement | null {
    if (framesRef.current[idx]) return framesRef.current[idx];
    for (let d = 1; d < CARD_FRAME_COUNT; d++) {
      const a = (idx - d + CARD_FRAME_COUNT) % CARD_FRAME_COUNT;
      if (framesRef.current[a]) return framesRef.current[a];
      const b = (idx + d) % CARD_FRAME_COUNT;
      if (framesRef.current[b]) return framesRef.current[b];
    }
    return null;
  }

  function draw() {
    const canvas = canvasRef.current;
    if (!canvas || !sizedRef.current) return;
    const idx = frameIndex();
    if (currentDrawnRef.current === idx) return;
    const img = resolveImage(idx);
    if (!img) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = "high";
    const cw = canvas.width;
    const ch = canvas.height;
    const scale = Math.min(cw / img.naturalWidth, ch / img.naturalHeight);
    const dw = img.naturalWidth * scale;
    const dh = img.naturalHeight * scale;
    ctx.clearRect(0, 0, cw, ch);
    ctx.drawImage(img, (cw - dw) / 2, (ch - dh) / 2, dw, dh);
    currentDrawnRef.current = idx;
  }

  const onPointerDown = (e: React.PointerEvent) => {
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    activePointerRef.current = e.pointerId;
    draggingRef.current = true;
    lastXRef.current = e.clientX;
  };
  const onPointerMove = (e: React.PointerEvent) => {
    if (!draggingRef.current || activePointerRef.current !== e.pointerId) return;
    const dx = e.clientX - lastXRef.current;
    lastXRef.current = e.clientX;
    frameFloatRef.current += dx / DRAG_PX_PER_FRAME;
    if (dx !== 0 && !hasDragged) setHasDragged(true); // hide the rotate hint
  };
  const endDrag = (e: React.PointerEvent) => {
    if (activePointerRef.current !== e.pointerId) return;
    try {
      (e.currentTarget as HTMLElement).releasePointerCapture(e.pointerId);
    } catch {
      // ignore
    }
    activePointerRef.current = null;
    draggingRef.current = false;
  };

  if (!show || !result) return null;

  const ready = loaded >= CARD_FRAME_COUNT;

  return (
    <AnimatePresence>
      <motion.div
        className="card-reveal-wrapper"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        transition={{ duration: 0.4 }}
      >
        <motion.div
          className="card-turntable"
          initial={{ scale: 0.3, filter: "blur(20px)", opacity: 0, y: 40 }}
          animate={{ scale: 1, filter: "blur(0px)", opacity: 1, y: 0 }}
          transition={{ duration: 0.9, ease: [0.16, 1, 0.3, 1], delay: 0.15 }}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={endDrag}
          onPointerCancel={endDrag}
          style={{ touchAction: "none" }}
        >
          <canvas ref={canvasRef} className="card-turntable-canvas" />
          {!ready && <div className="card-turntable-hint">LOADING…</div>}
          {ready && phase === "revealed" && !hasDragged && <CardRotateHint />}
        </motion.div>

        {phase === "revealed" && (
          <motion.div
            className="card-actions"
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.5, duration: 0.4 }}
          >
            <button className="card-action-btn" onClick={reset}>
              MINT ANOTHER
            </button>
            <a
              className="card-action-btn ghost"
              href={`https://solscan.io/tx/${result.tx_hash}`}
              target="_blank"
              rel="noreferrer"
            >
              VIEW ON SOLSCAN
            </a>
          </motion.div>
        )}
      </motion.div>
    </AnimatePresence>
  );
}
