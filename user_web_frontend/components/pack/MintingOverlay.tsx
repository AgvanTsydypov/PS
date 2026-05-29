"use client";

import { useEffect, useState } from "react";
import { usePackStore } from "./store";

const COPY_ROTATION = [
  "ALLOCATING YOUR CARD FROM THE VAULT",
  "SIGNING TRANSACTION ON SOLANA",
  "UPLOADING METADATA TO IPFS",
  "FINALIZING ON-CHAIN",
  "ALMOST THERE",
];

export function MintingOverlay() {
  const phase = usePackStore((s) => s.phase);
  const startedAt = usePackStore((s) => s.mintingStartedAt);
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

  const elapsed = startedAt ? Math.floor((Date.now() - startedAt) / 1000) : 0;

  return (
    <div className="minting-overlay">
      <div className="minting-spinner" />
      <div className="minting-copy">{COPY_ROTATION[copyIdx]}</div>
      <div className="minting-meta">
        <span>~{Math.max(0, 30 - elapsed)}s</span>
        <span className="minting-dot">·</span>
        <span>SOLANA MAINNET</span>
      </div>
    </div>
  );
}
