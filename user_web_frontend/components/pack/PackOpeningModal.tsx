"use client";

import { AnimatePresence, motion } from "framer-motion";
import { usePackGesture } from "./usePackGesture";
import { PackFrameSequence } from "./PackFrameSequence";
import { MintingOverlay } from "./MintingOverlay";
import { CardReveal } from "./CardReveal";
import { usePackStore } from "./store";
import "./pack.css";

export function PackOpeningModal() {
  const { progress, handlers, interactionLocked } = usePackGesture();
  const phase = usePackStore((s) => s.phase);

  return (
    <div className="pack-modal-root">
      <div className="pack-stage">
        <motion.div
          className="pack-gesture-target"
          style={{
            touchAction: "none",
            userSelect: "none",
            // While locked (minting/revealing/revealed) let pointer events fall
            // through to the card underneath — the pack stays on top visually.
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

        {phase === "idle" && (
          <div className="pack-hint-strip">
            CLICK · HOLD · DRAG UP TO TEAR
          </div>
        )}
        {phase === "error" && (
          <div className="pack-error-strip">MINT FAILED — TRY AGAIN</div>
        )}
      </div>
    </div>
  );
}
