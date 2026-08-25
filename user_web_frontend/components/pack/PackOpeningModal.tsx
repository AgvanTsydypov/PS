"use client";

import { AnimatePresence, motion } from "framer-motion";
import { usePackGesture } from "./usePackGesture";
import { PackFrameSequence } from "./PackFrameSequence";
import { MintingOverlay } from "./MintingOverlay";
import { CardReveal } from "./CardReveal";
import { DragHint } from "./DragHint";
import { usePackStore } from "./store";
import "./pack.css";

export function PackOpeningModal() {
  const { progress, handlers, interactionLocked } = usePackGesture();
  const phase = usePackStore((s) => s.phase);
  const mintLocked = usePackStore((s) => s.mintLocked);
  const error = usePackStore((s) => s.error);
  const claimStatus = usePackStore((s) => s.claimStatus);
  const closeModal = usePackStore((s) => s.closeModal);

  const waitCopy = (() => {
    switch (claimStatus) {
      case "QUEUED":
        return "WAITING IN MINT QUEUE…";
      case "PENDING":
        return "PREPARING ON-CHAIN MINT…";
      case "PROCESSING":
        return "TX BROADCAST. AWAITING RECEIPT…";
      case "COMPLETED":
        return "MINT CONFIRMED. RENDERING CARD…";
      default:
        return "STAR FORGING ON-CHAIN…";
    }
  })();

  return (
    <div className="pack-modal-root">
      <button
        type="button"
        className="pack-close-btn"
        aria-label="Close"
        onClick={closeModal}
      >
        ×
      </button>
      <div className="pack-stage">
        <motion.div
          className="pack-gesture-target"
          style={{
            touchAction: "none",
            userSelect: "none",
            // While locked (mint waiting / minting / revealing / revealed) let
            // pointer events fall through — the pack stays on top visually.
            pointerEvents: interactionLocked ? "none" : "auto",
          }}
          {...(!interactionLocked ? handlers : {})}
        >
          <PackFrameSequence progress={progress} />
        </motion.div>

        {/* White flash on tear breakthrough */}
        <AnimatePresence>
          {phase === "revealing" && (
            <motion.div
              className="tear-flash"
              initial={{ opacity: 0 }}
              animate={{ opacity: [0, 1, 0] }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.5, times: [0, 0.25, 1] }}
            />
          )}
        </AnimatePresence>

        <MintingOverlay />
        <CardReveal />

        {!mintLocked && phase === "idle" && <DragHint />}
        {!mintLocked && phase === "idle" && (
          <div className="pack-hint-strip">
            CLICK · HOLD · DRAG UP TO TEAR
          </div>
        )}
        {mintLocked && (
          <div className="pack-wait-overlay" aria-live="polite">
            <div className="pack-wait-overlay-dot" />
            <div className="pack-wait-overlay-text">
              {waitCopy}
              <br />
              <span className="pack-wait-overlay-sub">
                PACK UNLOCKS WHEN THE TX SETTLES
              </span>
              <br />
              <span className="pack-wait-overlay-sub">
                PLEASE HOLD ON FOR A MOMENT
              </span>
            </div>
          </div>
        )}
        {phase === "queued_background" && (
          <div className="pack-queued-card" role="status">
            <div className="pack-queued-card-title">MINT QUEUED</div>
            <div className="pack-queued-card-body">
              Your STAR is queued for minting. The network is busy right
              now — we&apos;ll process it in the background. Please check
              your collection a bit later.
            </div>
            <button
              type="button"
              className="pack-queued-card-close"
              onClick={closeModal}
            >
              GOT IT
            </button>
          </div>
        )}
        {phase === "error" && (
          <div className="pack-error-card" role="alert">
            <div className="pack-error-card-title">MINT FAILED</div>
            <div className="pack-error-card-body">
              {error || "Unknown error — try again."}
            </div>
            <button
              type="button"
              className="pack-error-card-close"
              onClick={closeModal}
            >
              CLOSE
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
