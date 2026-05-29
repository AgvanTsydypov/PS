"use client";

import { create } from "zustand";
import type { MockMintResult } from "../../lib/mockMint";
import { mockMint } from "../../lib/mockMint";

export type PackPhase =
  | "idle"
  | "dragging"
  | "minting"
  | "revealing"
  | "revealed"
  | "error";

type PackStore = {
  phase: PackPhase;
  result: MockMintResult | null;
  error: string | null;
  mintingStartedAt: number | null;
  setPhase: (p: PackPhase) => void;
  triggerMint: (mintDelayMs?: number) => Promise<void>;
  reset: () => void;
};

export const usePackStore = create<PackStore>((set, get) => ({
  phase: "idle",
  result: null,
  error: null,
  mintingStartedAt: null,
  setPhase: (phase) => set({ phase }),
  triggerMint: async (mintDelayMs) => {
    if (get().phase === "minting") return;
    set({ phase: "minting", mintingStartedAt: Date.now(), error: null });
    try {
      const result = await mockMint({ delayMs: mintDelayMs });
      set({ result, phase: "revealing" });
      setTimeout(() => {
        if (get().phase === "revealing") set({ phase: "revealed" });
      }, 1500);
    } catch (e) {
      set({ phase: "error", error: e instanceof Error ? e.message : "mint failed" });
    }
  },
  reset: () =>
    set({ phase: "idle", result: null, error: null, mintingStartedAt: null }),
}));
