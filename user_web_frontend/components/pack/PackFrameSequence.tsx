"use client";

import { useEffect, useRef, useState } from "react";
import { type MotionValue } from "framer-motion";
import { usePackStore, type PackPhase } from "./store";

type Props = {
  progress: MotionValue<number>;
};

const TOTAL = 280;
const FPS = 30;
const OPENING_FPS = 60; // opening plays at a fixed rate, never faster
const PULL_DEADZONE = 0.1; // progress above this = user is pulling (play forward)
const CROSSFADE_MS = 220; // blend across segment boundaries
const FRAME_PATH = (i: number) =>
  `/pack/frames/${String(i).padStart(4, "0")}.webp`; // i is 1-based

// Segments are 1-based inclusive frame ranges.
const SEG = {
  opening: [1, 80] as const, // drag-scrubbed
  openPulse: [81, 159] as const, // loop while minting
  flyAway: [160, 200] as const, // one-shot on reveal -> card appears
  closedPulse: [201, 280] as const, // loop while idle
};

// Load order: idle loop first (shown immediately), then opening, then rest.
function buildLoadOrder(): number[] {
  const order: number[] = [];
  const push = ([a, b]: readonly [number, number]) => {
    for (let i = a; i <= b; i++) order.push(i);
  };
  push(SEG.closedPulse);
  push(SEG.opening);
  push(SEG.openPulse);
  push(SEG.flyAway);
  return order;
}

export function PackFrameSequence({ progress }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const framesRef = useRef<(HTMLImageElement | null)[]>(
    new Array(TOTAL + 1).fill(null) // 1-based; index 0 unused
  );
  const currentFrameRef = useRef(-1);
  const sizedRef = useRef(false);
  const phaseRef = useRef<PackPhase>("idle");
  const segStartRef = useRef(0);
  const rafRef = useRef(0);
  const lastImgRef = useRef<HTMLImageElement | null>(null);
  const crossfadeRef = useRef<{ img: HTMLImageElement; start: number } | null>(
    null
  );
  const openingFrameRef = useRef<number>(SEG.opening[0]); // autoplay position (float)
  const triggeredRef = useRef(false);
  const lastNowRef = useRef(0);

  const phase = usePackStore((s) => s.phase);
  const setPhase = usePackStore((s) => s.setPhase);
  const triggerReveal = usePackStore((s) => s.triggerReveal);
  const [loaded, setLoaded] = useState(0);

  // Track phase + reset segment clock on every phase change.
  // Snapshot the last-drawn frame so the new segment can crossfade in,
  // hiding the pose discontinuity between segments (e.g. idle pulse -> opening).
  useEffect(() => {
    phaseRef.current = phase;
    segStartRef.current = performance.now();
    if (phase === "dragging") {
      openingFrameRef.current = SEG.opening[0];
      triggeredRef.current = false;
    }
    if (lastImgRef.current) {
      crossfadeRef.current = { img: lastImgRef.current, start: performance.now() };
      currentFrameRef.current = -1; // force redraw during the crossfade
    }
  }, [phase]);

  // Size canvas (HiDPI aware). ResizeObserver retries automatically the
  // moment the canvas actually gets non-zero layout dimensions — necessary
  // because on /me the modal mounts via a state-flip and the first useEffect
  // can fire before the fixed-overlay gate has laid out, leaving the canvas
  // at 0×0. In that case the rAF loop would spin forever with sizedRef=false
  // and drawFrame would no-op (pack appears frozen).
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const sizeCanvas = () => {
      const rect = canvas.getBoundingClientRect();
      if (rect.width === 0 || rect.height === 0) return;
      const dpr = window.devicePixelRatio || 1;
      canvas.width = Math.round(rect.width * dpr);
      canvas.height = Math.round(rect.height * dpr);
      sizedRef.current = true;
      currentFrameRef.current = -1;
    };
    sizeCanvas();
    const ro = new ResizeObserver(sizeCanvas);
    ro.observe(canvas);
    window.addEventListener("resize", sizeCanvas);
    return () => {
      ro.disconnect();
      window.removeEventListener("resize", sizeCanvas);
    };
  }, []);

  // Preload all WebP frames (browser throttles concurrency per origin)
  useEffect(() => {
    let cancelled = false;
    let done = 0;
    for (const i of buildLoadOrder()) {
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
        console.warn("[pack frames] failed to load", FRAME_PATH(i));
        finish();
      };
      img.src = FRAME_PATH(i);
    }
    return () => {
      cancelled = true;
    };
  }, []);

  // Single rAF render loop; target frame depends on current phase
  useEffect(() => {
    const tick = (now: number) => {
      const dt = lastNowRef.current ? (now - lastNowRef.current) / 1000 : 0;
      lastNowRef.current = now;
      const frame = computeFrame(now, Math.min(dt, 0.1));
      drawFrame(frame, now);
      rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(rafRef.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Returns the 1-based frame number to display this tick
  function computeFrame(now: number, dt: number): number {
    const phaseNow = phaseRef.current;
    const elapsed = now - segStartRef.current;
    switch (phaseNow) {
      case "dragging": {
        const [a, b] = SEG.opening;
        // Autoplay at a fixed FPS: forward while the user pulls, backward when
        // they release. Speed never depends on how fast the mouse moves.
        const pulling = progress.get() > PULL_DEADZONE;
        const step = OPENING_FPS * dt * (pulling ? 1 : -1);
        openingFrameRef.current = Math.max(
          a,
          Math.min(b, openingFrameRef.current + step)
        );
        if (!triggeredRef.current && openingFrameRef.current >= b) {
          triggeredRef.current = true;
          // On-chain mint already settled before unlock; this just hands off to
          // the reveal phase so CardReveal mounts and starts its turntable.
          triggerReveal();
        } else if (!pulling && openingFrameRef.current <= a) {
          setPhase("idle"); // played back to the start without opening
        }
        return Math.round(openingFrameRef.current);
      }
      case "minting":
        return loopFrame(SEG.openPulse, elapsed);
      case "revealing":
        return onceFrame(SEG.flyAway, elapsed);
      case "revealed":
        return SEG.flyAway[1]; // hold last frame; card overlay is on top
      case "error":
        return SEG.opening[0];
      case "idle":
      default:
        return loopFrame(SEG.closedPulse, elapsed);
    }
  }

  function loopFrame([a, b]: readonly [number, number], elapsed: number) {
    const len = b - a + 1;
    const idx = Math.floor((elapsed / 1000) * FPS) % len;
    return a + idx;
  }

  function onceFrame([a, b]: readonly [number, number], elapsed: number) {
    const len = b - a + 1;
    const idx = Math.min(len - 1, Math.floor((elapsed / 1000) * FPS));
    return a + idx;
  }

  // Resolve a 1-based frame to a loaded image, falling back to the nearest
  // loaded neighbour so gaps during loading never show a blank canvas.
  function resolveImage(frame: number): HTMLImageElement | null {
    if (framesRef.current[frame]) return framesRef.current[frame];
    for (let j = frame - 1; j >= 1; j--) {
      if (framesRef.current[j]) return framesRef.current[j];
    }
    for (let j = frame + 1; j <= TOTAL; j++) {
      if (framesRef.current[j]) return framesRef.current[j];
    }
    return null;
  }

  function drawImageFit(
    ctx: CanvasRenderingContext2D,
    img: HTMLImageElement,
    cw: number,
    ch: number
  ) {
    // PACK_ZOOM enlarges the pack within the canvas. 1.4 keeps the resting pack
    // (bottom edge at ~84% of the source) from clipping while making it read a
    // touch larger than the reveal card. Transparent source margins are cropped.
    const PACK_ZOOM = 1.4;
    const scale =
      Math.min(cw / img.naturalWidth, ch / img.naturalHeight) * PACK_ZOOM;
    const dw = img.naturalWidth * scale;
    const dh = img.naturalHeight * scale;
    ctx.drawImage(img, (cw - dw) / 2, (ch - dh) / 2, dw, dh);
  }

  // Draw a 1-based frame, optionally crossfading from a snapshot of the
  // previous segment's last frame.
  function drawFrame(frame: number, now: number) {
    const canvas = canvasRef.current;
    if (!canvas || !sizedRef.current) return;

    const cf = crossfadeRef.current;
    // Without an active crossfade we can skip redundant repaints.
    if (!cf && currentFrameRef.current === frame) return;

    const img = resolveImage(frame);
    if (!img) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const cw = canvas.width;
    const ch = canvas.height;
    ctx.clearRect(0, 0, cw, ch);
    ctx.globalAlpha = 1;
    drawImageFit(ctx, img, cw, ch);

    if (cf) {
      const t = (now - cf.start) / CROSSFADE_MS;
      if (t >= 1) {
        crossfadeRef.current = null;
      } else {
        ctx.globalAlpha = 1 - t; // fade the old segment out over the new one
        drawImageFit(ctx, cf.img, cw, ch);
        ctx.globalAlpha = 1;
      }
    }

    lastImgRef.current = img;
    currentFrameRef.current = crossfadeRef.current ? -1 : frame;
  }

  const pct = Math.round((loaded / TOTAL) * 100);

  return (
    <div
      className="pack-frame-sequence"
      aria-hidden={phase !== "idle" && phase !== "dragging"}
    >
      <canvas ref={canvasRef} className="pack-frame-canvas" />
      {loaded < TOTAL && (
        <div className="pack-frame-loader-pill">LOADING {pct}%</div>
      )}
    </div>
  );
}
