"use client";

import { useEffect, useState } from "react";
import { usePackStore } from "./store";

// Kept as a thin progress copy for the legacy "minting" phase (between the
// drag-opening and the card reveal). In the real /me flow the on-chain mint
// completes BEFORE the user even starts dragging — the lock-overlay handles
// that wait — so this rarely renders. Left in for the /pack-test demo path
// and any future legacy callers.
const COPY_ROTATION = [
  "ALLOCATING YOUR CARD FROM THE VAULT",
  "SIGNING TRANSACTION",
  "UPLOADING METADATA TO IPFS",
  "FINALIZING ON-CHAIN",
  "ALMOST THERE",
];

export function MintingOverlay() {
  const phase = usePackStore((s) => s.phase);
  const [copyIdx, setCopyIdx] = useState(0);

  useEffect(() => {
    if (phase !== "minting") return;
    setCopyIdx(0);
    const id = setInterval(() => {
      setCopyIdx((i) => (i + 1) % COPY_ROTATION.length);
    }, 5000);
    return () => clearInterval(id);
  }, [phase]);

  if (phase !== "minting") return null;

  return (
    <div className="minting-overlay">
      <div className="minting-spinner" />
      <div className="minting-copy">{COPY_ROTATION[copyIdx]}</div>
    </div>
  );
}
