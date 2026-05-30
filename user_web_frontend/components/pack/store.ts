"use client";

import { create } from "zustand";
import { buildUserApiUrl, userApiCredentials } from "../../lib/userApiBase";

export type PackPhase =
  | "idle"
  | "dragging"
  | "minting"
  | "revealing"
  | "revealed"
  | "queued_background"
  | "error";

/** Minimal mint result the pack UI needs. Real mint comes from /api/me/mint;
 *  the demo /pack-test page leaves this null and CardReveal hides the
 *  Solscan/asset-link buttons accordingly. */
export type PackMintResult = {
  tx_hash: string;
  asset_address: string;
  claim_id: number;
  collection_mint_number?: number;
};

type StartMintArgs = {
  seasonId: number;
};

type PackStore = {
  /** Whether the modal is mounted on /me. /pack-test ignores this (renders the modal directly). */
  open: boolean;
  /** True from the moment the user clicks GET STAR until the on-chain mint + turntable compose are both ready. */
  mintLocked: boolean;
  phase: PackPhase;
  claimId: number | null;
  result: PackMintResult | null;
  /** Last-known backend claim status (QUEUED / PROCESSING / COMPLETED / FAILED).
   *  Drives the wait-overlay copy so the user can tell ``waiting in queue`` apart
   *  from ``tx on chain, waiting for receipt``. */
  claimStatus: string | null;
  /** Base URL serving the composed turntable frames as ``{base}/NNNN.webp``. */
  frameBaseUrl: string | null;
  frameCount: number | null;
  error: string | null;

  setPhase: (p: PackPhase) => void;
  startMint: (args: StartMintArgs) => Promise<void>;
  /** Called by PackFrameSequence when the opening animation hits its last frame. */
  triggerReveal: () => void;
  closeModal: () => void;
  reset: () => void;
};

const TURNTABLE_POLL_INTERVAL_MS = 2000;
const TURNTABLE_POLL_BUDGET_MS = 5 * 60_000;
const REVEAL_HOLD_MS = 1500;
// If the claim sits in QUEUED for longer than this, the mint-queue worker
// almost certainly skipped this batch (price gate gas-too-high is the common
// case; a downed worker is rare). Rather than freeze the modal for the full
// 5-minute polling budget, transition to a friendly "queued in background"
// state so the user can close the modal and check their collection later.
// 30s is comfortably above the normal QUEUED→PROCESSING gap on a healthy
// inline mint-kick (1-3s in dev).
const QUEUED_BACKGROUND_THRESHOLD_MS = 30_000;

type MintResponse = {
  claim_id?: number;
  collection_mint_number?: number;
  mint_result?: { tx_hash?: string; asset_address?: string };
  detail?: string;
  [key: string]: unknown;
};

type TurntableResponse = {
  status?: string;
  base_url?: string;
  frame_count?: number;
  claim_status?: string;
};

export const usePackStore = create<PackStore>((set, get) => ({
  open: false,
  mintLocked: false,
  phase: "idle",
  claimId: null,
  result: null,
  claimStatus: null,
  frameBaseUrl: null,
  frameCount: null,
  error: null,

  setPhase: (phase) => set({ phase }),

  startMint: async ({ seasonId }) => {
    // Mount the modal immediately with the pack locked so the user sees the
    // closed-pack idle pulse while the chain settles.
    set({
      open: true,
      mintLocked: true,
      phase: "idle",
      claimId: null,
      result: null,
      claimStatus: "QUEUED",
      frameBaseUrl: null,
      frameCount: null,
      error: null,
    });

    try {
      const mintRes = await fetch(buildUserApiUrl("/api/me/mint"), {
        method: "POST",
        credentials: userApiCredentials,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ season_id: seasonId }),
      });
      const mintText = await mintRes.text();
      let mintJson: MintResponse | null = null;
      try {
        mintJson = mintText ? (JSON.parse(mintText) as MintResponse) : null;
      } catch {
        mintJson = null;
      }
      if (!mintRes.ok) {
        const detail = mintJson?.detail ?? mintText ?? "Mint failed";
        throw new Error(String(detail));
      }
      const claimId = Number(mintJson?.claim_id);
      if (!Number.isFinite(claimId)) {
        throw new Error("Mint returned no claim_id");
      }
      set({
        claimId,
        result: {
          tx_hash: String(mintJson?.mint_result?.tx_hash ?? ""),
          asset_address: String(mintJson?.mint_result?.asset_address ?? ""),
          claim_id: claimId,
          collection_mint_number: mintJson?.collection_mint_number ?? undefined,
        },
      });

      const deadline = Date.now() + TURNTABLE_POLL_BUDGET_MS;
      const pollStart = Date.now();
      let lastClaimStatus = "QUEUED";
      while (Date.now() < deadline) {
        // Long-tail QUEUED: mint-kick skipped (price gate, worker down, …).
        // Bail with a friendly "we'll process it in the background" state
        // instead of trapping the user behind a frozen modal for 5 minutes.
        // PROCESSING/COMPLETED/FAILED never trip this — they advance lastClaimStatus.
        if (
          lastClaimStatus === "QUEUED" &&
          Date.now() - pollStart > QUEUED_BACKGROUND_THRESHOLD_MS
        ) {
          set({
            phase: "queued_background",
            mintLocked: false,
            claimStatus: "QUEUED",
          });
          return;
        }
        const ttRes = await fetch(
          buildUserApiUrl(`/api/me/claims/${claimId}/turntable`),
          { credentials: userApiCredentials, cache: "no-store" },
        );
        if (ttRes.ok) {
          const tt = (await ttRes.json()) as TurntableResponse;
          if (tt?.status === "ready" && tt.base_url && tt.frame_count) {
            set({
              frameBaseUrl: tt.base_url,
              frameCount: Number(tt.frame_count),
              mintLocked: false,
            });
            return;
          }
          if (tt?.claim_status && tt.claim_status !== lastClaimStatus) {
            // eslint-disable-next-line no-console
            console.info(
              `[pack] claim ${claimId} status: ${lastClaimStatus} → ${tt.claim_status}`,
            );
            lastClaimStatus = tt.claim_status;
            set({ claimStatus: tt.claim_status });
            // Surface terminal failure immediately instead of polling until the
            // 5-minute budget runs out (e.g. on-chain receipt = reverted, or
            // worker marked the row FAILED at Pinata / RPC).
            if (tt.claim_status === "FAILED") {
              throw new Error(
                "Mint failed on the server (check user_web_backend logs).",
              );
            }
          }
        } else {
          // Non-2xx from /turntable means compose blew up (template missing,
          // image download failed, …) — surface the body so we don't poll
          // forever against a broken endpoint.
          const errText = await ttRes.text().catch(() => "");
          // eslint-disable-next-line no-console
          console.error(
            `[pack] /turntable ${ttRes.status}: ${errText.slice(0, 400)}`,
          );
          // 4xx is a permanent failure; bail. 5xx might be transient (compose
          // race, RPC blip) so keep polling but stop after 3 consecutive.
          if (ttRes.status >= 400 && ttRes.status < 500) {
            throw new Error(
              `Turntable endpoint returned ${ttRes.status}: ${errText.slice(0, 200)}`,
            );
          }
        }
        await new Promise((r) => setTimeout(r, TURNTABLE_POLL_INTERVAL_MS));
      }
      throw new Error(
        `Mint timed out (last claim status: ${lastClaimStatus}). ` +
          "Check user_web_backend logs and that the mint-queue worker is running.",
      );
    } catch (e) {
      const msg = e instanceof Error ? e.message : "mint failed";
      set({ phase: "error", error: msg, mintLocked: false });
    }
  },

  triggerReveal: () => {
    const phase = get().phase;
    if (phase === "revealing" || phase === "revealed") return;
    set({ phase: "revealing" });
    setTimeout(() => {
      if (get().phase === "revealing") set({ phase: "revealed" });
    }, REVEAL_HOLD_MS);
  },

  closeModal: () => {
    // If the user is closing AFTER a successful reveal, drop the cached
    // turntable frames server-side too (they're local-disk only, not R2).
    // Fire-and-forget — failure here must not block the UI close.
    const { claimId, phase } = get();
    if (claimId && phase === "revealed") {
      void fetch(buildUserApiUrl(`/api/me/claims/${claimId}/turntable`), {
        method: "DELETE",
        credentials: userApiCredentials,
      }).catch(() => {
        /* best-effort; backend TTL sweep will catch this dir eventually */
      });
    }
    set({ open: false, phase: "idle", mintLocked: false, error: null });
  },

  reset: () => {
    const { claimId, phase } = get();
    if (claimId && (phase === "revealed" || phase === "revealing")) {
      void fetch(buildUserApiUrl(`/api/me/claims/${claimId}/turntable`), {
        method: "DELETE",
        credentials: userApiCredentials,
      }).catch(() => {
        /* best-effort; backend TTL sweep will catch this dir eventually */
      });
    }
    set({
      open: false,
      mintLocked: false,
      phase: "idle",
      claimId: null,
      result: null,
      claimStatus: null,
      frameBaseUrl: null,
      frameCount: null,
      error: null,
    });
  },
}));
