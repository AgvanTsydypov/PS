"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";

import {
  ActiveSeasonsBoard,
  type ActiveSeasonView,
  type ActiveSeasonsBoardHandle,
} from "./ActiveSeasonsBoard";
import {
  clearFlipTimers,
  handleCardGridMouseLeave,
  handleCardGridMouseMove,
  markCardPressStart,
  navigateToCardIfCenterClick,
  triggerCardFlip,
} from "./cardInteractions";
import CardImage from "./CardImage";
import SiteLogoLink from "./SiteLogoLink";
import { fetchSiteStatus, userApiCredentials } from "../lib/userApiBase";

// ── EIP-6963 types ────────────────────────────────────────────────────────────
type EIP6963ProviderInfo = {
  uuid: string;
  name: string;
  icon: string;
  rdns: string;
};

type EIP6963ProviderDetail = {
  info: EIP6963ProviderInfo;
  provider: EthProvider;
};

type EIP6963AnnounceProviderEvent = CustomEvent<EIP6963ProviderDetail>;

// ── Generic EVM provider ──────────────────────────────────────────────────────
type EthProvider = {
  request: (args: { method: string; params?: unknown[] }) => Promise<unknown>;
  isMetaMask?: boolean;
  providers?: EthProvider[];
};

declare global {
  interface Window {
    ethereum?: EthProvider;
  }
  interface WindowEventMap {
    "eip6963:announceProvider": EIP6963AnnounceProviderEvent;
  }
}

// ── API types ─────────────────────────────────────────────────────────────────
type ChallengeResponse = {
  challenge_id: string;
  message: string;
  expires_at: string;
};

type VerifyResponse = {
  signed_in: boolean;
  wallet_address: string;
  sign_in_count: number;
  proxy_wallet?: string;
  trader_rank?: string;
  expires_in?: number;
};

type WalletSessionResponse = {
  signed_in: boolean;
  wallet_address?: string;
  sign_in_count?: number;
  proxy_wallet?: string | null;
  trader_rank?: string | null;
};

type PendingClaim = {
  claim_id: number;
  status: string;          // "QUEUED" | "PENDING" | "PROCESSING" | "COMPLETED"
  phase_type?: string | null;
  queued_at?: string | null;
  updated_at?: string | null;
  collection_mint_number?: number | null;
  tx_hash?: string | null;
  asset_address?: string | null;
  card_slug?: string | null;
};

type EligibilityStream = {
  season_id: number | null;
  season_type: string | null;
  phase: string | null;
  phase_reason?: string | null;
  already_claimed: boolean;
  eligible_now: boolean;
  ineligible_reason?: string | null;
  requires_origin?: boolean;
  is_claim_open?: boolean;
  is_origin_wallet?: boolean;
  pending_claim?: PendingClaim | null;
};

type EligibilityResponse = {
  wallet_address: string;
  is_origin_wallet: boolean;
  genesis: EligibilityStream;
  standard: EligibilityStream;
};

type MintApiResult = {
  status?: string;
  claim_id?: number;
  collection_mint_number?: number;
  recipient_address?: string;
  season_id?: number;
  phase?: string;
  mint_result?: {
    asset_address?: string;
    tx_hash?: string;
  };
  warnings?: string[];
  [key: string]: unknown;
};

type StoredSessionMeta = {
  walletAddress?: string;
  selectedWalletName?: string | null;
  signInCount?: number | null;
  proxyWallet?: string | null;
  traderRank?: string | null;
  challengeId?: string;
};

type MyMintedNftItem = {
  claim_id: number;
  asset_address: string;
  tx_hash: string | null;
  metadata_uri: string | null;
  season_id: number | null;
  season_type: string | null;
  season_number: number | null;
  phase: string | null;
  collection_mint_number: number | null;
  name: string | null;
  front_image_url: string | null;
  back_image_url: string | null;
  front_image_fallback_url?: string | null;
  back_image_fallback_url?: string | null;
  card_slug: string | null;
  explorer_asset_url: string | null;
  explorer_tx_url: string | null;
  minted_at: string | null;
};

type MyMintedNftsResponse = {
  wallet_address: string;
  items: MyMintedNftItem[];
  total: number;
  fetched_at: string;
};

const apiBase =
  process.env.NEXT_PUBLIC_USER_API_BASE_URL ??
  (process.env.NODE_ENV === "development" ? "http://localhost:8011" : "/");
// Sentinel trader_rank values returned by the user-web backend that mean the
// wallet has NO real Polymarket leaderboard rank yet. Mirrors
// POLYMARKET_RANK_SENTINEL_VALUES in user_web_backend/main.py — keep in sync.
const POLYMARKET_RANK_SENTINEL_VALUES = new Set<string>([
  "not registered in pm",
  "no trades yet",
]);
function hasPolymarketRank(traderRank: string | null | undefined): boolean {
  if (traderRank == null) return false;
  const value = String(traderRank).trim();
  if (!value) return false;
  return !POLYMARKET_RANK_SENTINEL_VALUES.has(value.toLowerCase());
}

// ── Token-holder mint gate ────────────────────────────────────────────────────
// A wallet may mint if it has a real Polymarket trader rank *or* it holds at
// least TOKEN_GATE_MIN_BALANCE of the PolyStars project ERC-20 token on
// Ethereum mainnet. Authoritative check lives on the backend (uses the paid
// Alchemy RPC + a 30s cache); this helper just relays its decision so the
// "GET STAR" copy and the server-side mint gate never disagree.
//
// Returns ``true`` / ``false`` when the backend confirmed the on-chain read,
// and ``null`` when the read could not be performed (RPC unavailable,
// transient transport error). Callers must treat ``null`` as "don't block" —
// the mint endpoint will still refuse if the wallet really doesn't qualify.
async function fetchGateTokenHolds(
  apiBaseFetcher: (path: string) => string,
): Promise<boolean | null> {
  try {
    const res = await fetch(apiBaseFetcher("/api/me/gate-token-status"), {
      method: "GET",
      credentials: userApiCredentials,
    });
    if (!res.ok) return null;
    const json = (await res.json()) as {
      holds?: boolean | null;
      status?: string;
    };
    const status = String(json?.status ?? "");
    if (status !== "ok") return null;
    if (typeof json?.holds !== "boolean") return null;
    return json.holds;
  } catch {
    return null;
  }
}

/**
 * Map an ineligible eligibility-stream into the user-facing copy on /me.
 *
 * The backend returns a free-form ``ineligible_reason`` plus structured
 * fields (``phase``, ``is_origin_wallet``); this helper translates that
 * into the curated UX strings without duplicating per-call branching at the
 * call site. ``supplyEmpty`` is passed in because remaining-supply is
 * tracked at the season level (in ``ActiveSeasonView``), not on the
 * eligibility stream.
 */
function formatBlockedReason(stream: EligibilityStream, supplyEmpty: boolean): string {
  const ineligibleRaw = String(stream?.ineligible_reason || "");
  const phaseReasonRaw = String(stream?.phase_reason || "");
  const phase = String(stream?.phase || "").toLowerCase();
  const isOrigin = Boolean(stream?.is_origin_wallet);

  // Season not yet started — backend reports phase=transmission with the
  // phase_reason "Season has not started yet". Must come BEFORE the
  // supply-exhausted matrix because both states surface as "Claims closed
  // in current phase: transmission" via ``ineligible_reason``.
  if (/has not started yet/i.test(phaseReasonRaw)) {
    return "SEASON HAS NOT STARTED YET. CHECK BACK LATER.";
  }

  if (/Origin allocation already minted/i.test(ineligibleRaw)) {
    return "YOUR CARD WAS LOOTED. HURRY UP NEXT SEASON";
  }

  // Looter trying to mint while phase is Vault — Vault is Origins-only.
  if (/Current phase requires Origin wallet/i.test(ineligibleRaw)) {
    return "VAULT IN PROGRESS. WAIT TILL SCAVENGE.";
  }

  // "No slots" matrix — fires when supply is drained or the phase reports
  // claims closed (which the backend collapses to "Claims closed in current
  // phase: transmission" once supply hits zero).
  const supplyExhausted =
    supplyEmpty ||
    /supply exhausted/i.test(ineligibleRaw) ||
    /Claims closed in current phase/i.test(ineligibleRaw);

  if (supplyExhausted) {
    if (phase === "breach") {
      return isOrigin
        ? "BREACH SUPPLY EXHAUSTED. WAIT TILL VAULT PHASE"
        : "BREACH SUPPLY EXHAUSTED. WAIT TILL SCAVENGE PHASE";
    }
    if (phase === "vault") {
      // Looter at vault is the "phase requires origin" path above; if we get
      // here as a looter the supply is the actual block.
      return isOrigin
        ? "VAULT SUPPLY EXHAUSTED. WAIT TILL NEXT SEASON"
        : "VAULT IN PROGRESS. WAIT TILL SCAVENGE.";
    }
    if (phase === "scavenge") {
      return "SCAVENGE SUPPLY EXHAUSTED. WAIT TILL NEXT SEASON";
    }
    // ``transmission`` or unknown phase — generic fallback.
    return "SEASON SUPPLY EXHAUSTED. WAIT TILL NEXT SEASON";
  }

  return ineligibleRaw || "Wallet not eligible for this season right now.";
}
/** Legacy localStorage JWT (removed); cleared on load for one-time migration. */
const LEGACY_JWT_LOCAL_STORAGE_KEY = "polystars_user_access_token";
const AUTH_SESSION_META_STORAGE_KEY = "polystars_user_session_meta";
const MY_CARDS_FLIP_STORAGE_KEY_PREFIX = "polystars_my_cards_flipped_v1";

const safeLocalStorage = {
  getItem(key: string): string | null {
    try {
      return window.localStorage.getItem(key);
    } catch {
      return null;
    }
  },
  setItem(key: string, value: string): void {
    try {
      window.localStorage.setItem(key, value);
    } catch {
      /* Quota exceeded or private mode — avoid crashing the app */
    }
  },
  removeItem(key: string): void {
    try {
      window.localStorage.removeItem(key);
    } catch {
      /* ignore */
    }
  },
};

function buildApiUrl(path: string): string {
  if (apiBase === "/") return path;
  return `${apiBase.replace(/\/$/, "")}${path}`;
}

function loadStoredSessionMeta(): StoredSessionMeta | null {
  try {
    const raw = safeLocalStorage.getItem(AUTH_SESSION_META_STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as StoredSessionMeta;
    if (!parsed || typeof parsed !== "object") return null;
    return parsed;
  } catch {
    return null;
  }
}

function saveStoredSessionMeta(meta: StoredSessionMeta): void {
  safeLocalStorage.setItem(AUTH_SESSION_META_STORAGE_KEY, JSON.stringify(meta));
}

function clearStoredSessionMeta(): void {
  safeLocalStorage.removeItem(AUTH_SESSION_META_STORAGE_KEY);
}

function buildMyCardsFlipStorageKey(wallet: string): string | null {
  const normalized = String(wallet ?? "").trim().toLowerCase();
  if (!normalized) return null;
  return `${MY_CARDS_FLIP_STORAGE_KEY_PREFIX}:${normalized}`;
}

function loadStoredMyCardsFlipped(wallet: string): Record<string, boolean> {
  const storageKey = buildMyCardsFlipStorageKey(wallet);
  if (!storageKey) return {};
  try {
    const raw = safeLocalStorage.getItem(storageKey);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    if (!parsed || typeof parsed !== "object") return {};
    const out: Record<string, boolean> = {};
    Object.entries(parsed).forEach(([slug, flipped]) => {
      if (typeof slug !== "string" || !slug) return;
      if (flipped !== true) return;
      out[slug] = true;
    });
    return out;
  } catch {
    return {};
  }
}

function saveStoredMyCardsFlipped(wallet: string, flippedBySlug: Record<string, boolean>): void {
  const storageKey = buildMyCardsFlipStorageKey(wallet);
  if (!storageKey) return;
  const out: Record<string, boolean> = {};
  Object.entries(flippedBySlug).forEach(([slug, flipped]) => {
    if (!slug || flipped !== true) return;
    out[slug] = true;
  });
  safeLocalStorage.setItem(storageKey, JSON.stringify(out));
}

// ── Legacy fallback: walk window.ethereum ─────────────────────────────────────
function legacyProvider(): EthProvider | null {
  const eth = window.ethereum;
  if (!eth) return null;
  if (Array.isArray(eth.providers)) {
    return eth.providers.find((p) => p.isMetaMask) ?? eth.providers[0] ?? eth;
  }
  return eth;
}

// ── Wallet picker item (union of EIP-6963 and legacy) ────────────────────────
type WalletOption =
  | { kind: "eip6963"; detail: EIP6963ProviderDetail }
  | { kind: "legacy"; provider: EthProvider };

// ─────────────────────────────────────────────────────────────────────────────

// Charset used to cycle characters during the byld.dev-style decode.
// Mix of letters + common punctuation glyphs gives a "terminal decrypt"
// feel similar to the hero copy on byld.dev.
const SCRAMBLE_CHARSET =
  "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789!<>-_[]{}=+*^?#%&@/\\";

/**
 * Byld-style "decode" text effect: each character cycles through random
 * glyphs and locks into the real letter at its own random moment during
 * `duration`, so the line reads like a terminal decryption resolving
 * into the original sentence. Whitespace is preserved.
 *
 * Only runs when `triggerKey > 0` so the component can be safely mounted
 * before the user has actually requested a flash (e.g. on initial page
 * load). Parents are expected to also set a React `key` that changes per
 * flash so the animation restarts cleanly on repeated clicks.
 */
function ScrambleText({
  text,
  triggerKey,
  duration = 2750,
  className,
}: {
  text: string;
  triggerKey: number;
  duration?: number;
  className?: string;
}) {
  const [display, setDisplay] = useState(text);
  const [isScrambling, setIsScrambling] = useState(false);
  const lastTriggerRef = useRef(triggerKey);

  useEffect(() => {
    // Only animate when the user actually bumps triggerKey. On initial
    // mount and on unrelated re-renders (e.g. `text` changes while
    // eligibility loads), the ref already equals the current triggerKey,
    // so we just sync the display and skip the scramble.
    if (triggerKey === lastTriggerRef.current) {
      setDisplay(text);
      setIsScrambling(false);
      return;
    }
    lastTriggerRef.current = triggerKey;
    const prefersReducedMotion =
      typeof window !== "undefined" &&
      typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (prefersReducedMotion) {
      setDisplay(text);
      setIsScrambling(false);
      return;
    }

    const chars = Array.from(text);
    // Each non-whitespace character locks into its real glyph at a
    // random moment within the first 90% of the duration, giving the
    // line a staggered "resolve" rather than a uniform flicker.
    const revealTimes = chars.map(() => Math.random() * duration * 0.9);
    const start =
      typeof performance !== "undefined" ? performance.now() : Date.now();
    let rafId = 0;
    setIsScrambling(true);

    const tick = () => {
      const now =
        typeof performance !== "undefined" ? performance.now() : Date.now();
      const elapsed = now - start;
      const next = chars
        .map((ch, i) => {
          if (/\s/.test(ch)) return ch;
          if (elapsed >= revealTimes[i]) return ch;
          const rand = Math.floor(Math.random() * SCRAMBLE_CHARSET.length);
          return SCRAMBLE_CHARSET[rand];
        })
        .join("");
      setDisplay(next);
      if (elapsed < duration) {
        rafId = requestAnimationFrame(tick);
      } else {
        setDisplay(text);
        setIsScrambling(false);
      }
    };

    rafId = requestAnimationFrame(tick);
    return () => {
      if (rafId) cancelAnimationFrame(rafId);
    };
  }, [triggerKey, text, duration]);

  const classes = [className, isScrambling ? "bd-scramble-active" : null]
    .filter(Boolean)
    .join(" ");
  return <span className={classes || undefined}>{display}</span>;
}

export default function UserDashboard() {
  const [walletAddress, setWalletAddress] = useState("");
  const [challengeId, setChallengeId] = useState("");
  const [statusText, setStatusText] = useState("Not signed in");
  const [isSignedIn, setIsSignedIn] = useState(false);
  const [isBusy, setIsBusy] = useState(false);
  const [signInCount, setSignInCount] = useState<number | null>(null);
  const [proxyWallet, setProxyWallet] = useState<string | null>(null);
  const [traderRank, setTraderRank] = useState<string | null>(null);
  // Token-holder gate: null = not checked yet, true/false = on-chain result.
  const [gateTokenOk, setGateTokenOk] = useState<boolean | null>(null);
  const [myCards, setMyCards] = useState<MyMintedNftItem[]>([]);
  const [myCardsLoading, setMyCardsLoading] = useState(false);
  const [myCardsError, setMyCardsError] = useState("");
  const [myCardsFetchedAt, setMyCardsFetchedAt] = useState<string | null>(null);
  const [flippedCardSlugs, setFlippedCardSlugs] = useState<Record<string, boolean>>({});
  const [eligibility, setEligibility] = useState<EligibilityResponse | null>(null);
  const [eligibilityLoading, setEligibilityLoading] = useState(false);
  const [eligibilityError, setEligibilityError] = useState("");
  const [mintingSeasonId, setMintingSeasonId] = useState<number | null>(null);
  const [mintResultText, setMintResultText] = useState("");
  const [mintError, setMintError] = useState("");
  const [animatingCardSlugs, setAnimatingCardSlugs] = useState<Record<string, boolean>>({});
  const [scrambleNonce, setScrambleNonce] = useState(0);
  const generatedCardFlipTimerRef = useRef<Record<string, number | null>>({});
  const didHydrateMyCardFlipsRef = useRef(false);
  const myCardFlipsWalletRef = useRef("");
  const [isWalletButtonHovered, setIsWalletButtonHovered] = useState(false);
  const [isWalletPanelHovered, setIsWalletPanelHovered] = useState(false);
  const [isWalletPanelCollapsed, setIsWalletPanelCollapsed] = useState(false);
  const walletPanelCollapseTimerRef = useRef<number | null>(null);

  // Wallet that was selected by the user in the picker — used for sign-in
  const selectedProviderRef = useRef<EthProvider | null>(null);
  const [selectedWalletName, setSelectedWalletName] = useState<string | null>(null);

  // EIP-6963 discovered providers
  const eip6963Ref = useRef<EIP6963ProviderDetail[]>([]);
  const [eip6963Providers, setEip6963Providers] = useState<EIP6963ProviderDetail[]>([]);

  // Picker modal visibility
  const [showPicker, setShowPicker] = useState(false);

  const seasonsBoardRef = useRef<ActiveSeasonsBoardHandle>(null);
  const [siteWalletActionsDown, setSiteWalletActionsDown] = useState<boolean | null>(null);

  useEffect(() => {
    void fetchSiteStatus().then((s) => setSiteWalletActionsDown(Boolean(s?.wallet_actions_disabled)));
  }, []);

  // Listen for EIP-6963 announcements and trigger discovery.
  useEffect(() => {
    function onAnnounce(event: EIP6963AnnounceProviderEvent) {
      const detail = event.detail;
      setEip6963Providers((prev) => {
        if (prev.some((d) => d.info.uuid === detail.info.uuid)) return prev;
        const next = [...prev, detail];
        eip6963Ref.current = next;
        return next;
      });
    }

    window.addEventListener("eip6963:announceProvider", onAnnounce);
    window.dispatchEvent(new Event("eip6963:requestProvider"));

    return () => {
      window.removeEventListener("eip6963:announceProvider", onAnnounce);
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    safeLocalStorage.removeItem(LEGACY_JWT_LOCAL_STORAGE_KEY);

    async function hydrateSession() {
      try {
        const res = await fetch(buildApiUrl("/api/auth/wallet/session"), {
          credentials: userApiCredentials,
          cache: "no-store",
        });
        const data = (await res.json()) as WalletSessionResponse;
        if (cancelled) return;
        if (!data.signed_in || !data.wallet_address) {
          return;
        }
        setIsSignedIn(true);
        setStatusText("Signed in");
        setWalletAddress(String(data.wallet_address));
        if (typeof data.sign_in_count === "number") {
          setSignInCount(data.sign_in_count);
        }
        setProxyWallet(data.proxy_wallet ?? null);
        setTraderRank(data.trader_rank ?? null);

        const meta = loadStoredSessionMeta();
        if (typeof meta?.selectedWalletName === "string" && meta.selectedWalletName.trim()) {
          setSelectedWalletName(meta.selectedWalletName);
        }
        if (typeof meta?.challengeId === "string" && meta.challengeId.trim()) {
          setChallengeId(meta.challengeId);
        }
      } catch {
        /* ignore */
      }
    }

    void hydrateSession();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!isSignedIn) {
      setMyCards([]);
      setMyCardsError("");
      setMyCardsFetchedAt(null);
      setFlippedCardSlugs({});
      setEligibility(null);
      setEligibilityError("");
      setMintResultText("");
      setMintError("");
      return;
    }
    void refreshMyCards();
    void refreshEligibility();
  }, [isSignedIn, walletAddress]); // eslint-disable-line react-hooks/exhaustive-deps

  // On-chain token-holder gate: re-check whenever the signed-in wallet changes.
  // Delegates to the backend so the "GET STAR" UI and the server-side mint
  // gate read the same balance via the same RPC. The previous direct
  // public-RPC call from the browser silently treated any RPC error as
  // "not a holder" and greyed out the button for legit holders whenever
  // cloudflare-eth.com rate-limited the request.
  useEffect(() => {
    if (!isSignedIn || !walletAddress) {
      setGateTokenOk(null);
      return;
    }
    let cancelled = false;
    setGateTokenOk(null);
    void fetchGateTokenHolds(buildApiUrl).then((ok) => {
      if (!cancelled) setGateTokenOk(ok);
    });
    return () => {
      cancelled = true;
    };
  }, [isSignedIn, walletAddress]);

  useEffect(() => {
    if (!isSignedIn || !walletAddress) {
      didHydrateMyCardFlipsRef.current = false;
      myCardFlipsWalletRef.current = "";
      return;
    }
    const normalizedWallet = walletAddress.trim().toLowerCase();
    if (myCardFlipsWalletRef.current !== normalizedWallet) {
      didHydrateMyCardFlipsRef.current = false;
      myCardFlipsWalletRef.current = normalizedWallet;
    }
  }, [isSignedIn, walletAddress]);

  useEffect(() => {
    if (!isSignedIn || !walletAddress) return;
    if (myCardsLoading) return;
    // Wait until first cards fetch resolves (success or error) to avoid
    // overwriting persisted flips with an initial empty state on mount.
    if (!myCardsFetchedAt && !myCardsError) return;
    const persisted = loadStoredMyCardsFlipped(walletAddress);
    if (!Array.isArray(myCards) || myCards.length === 0) {
      setFlippedCardSlugs({});
      didHydrateMyCardFlipsRef.current = true;
      return;
    }
    // The flip map is keyed by asset_address (see flipKey = item.asset_address
    // below), not by card_slug. The historical variable name `existingSlugs`
    // is kept to match the surrounding code, but the values are NFT mint
    // (asset) addresses.
    const existingKeys = new Set(myCards.map((item) => item.asset_address));
    const restored: Record<string, boolean> = {};
    Object.entries(persisted).forEach(([key, flipped]) => {
      if (!flipped) return;
      if (!existingKeys.has(key)) return;
      restored[key] = true;
    });
    setFlippedCardSlugs(restored);
    didHydrateMyCardFlipsRef.current = true;
  }, [isSignedIn, walletAddress, myCards, myCardsLoading, myCardsFetchedAt, myCardsError]);

  useEffect(() => {
    if (!isSignedIn || !walletAddress) return;
    if (!didHydrateMyCardFlipsRef.current) return;
    saveStoredMyCardsFlipped(walletAddress, flippedCardSlugs);
  }, [isSignedIn, walletAddress, flippedCardSlugs]);

  useEffect(() => {
    if (walletPanelCollapseTimerRef.current) {
      window.clearTimeout(walletPanelCollapseTimerRef.current);
      walletPanelCollapseTimerRef.current = null;
    }

    if (isWalletPanelHovered || showPicker) {
      setIsWalletPanelCollapsed(false);
      return;
    }

    walletPanelCollapseTimerRef.current = window.setTimeout(() => {
      setIsWalletPanelCollapsed(true);
      walletPanelCollapseTimerRef.current = null;
    }, 750);

    return () => {
      if (walletPanelCollapseTimerRef.current) {
        window.clearTimeout(walletPanelCollapseTimerRef.current);
        walletPanelCollapseTimerRef.current = null;
      }
    };
  }, [isWalletPanelHovered, showPicker]);

  useEffect(() => {
    return () => {
      clearFlipTimers(generatedCardFlipTimerRef);
    };
  }, []);

  // Build the list of wallet options shown in the picker.
  const walletOptions = useMemo<WalletOption[]>(() => {
    const options: WalletOption[] = eip6963Ref.current.map((d) => ({
      kind: "eip6963",
      detail: d,
    }));
    // Add legacy fallback only when no EIP-6963 wallets were announced
    if (options.length === 0) {
      const lp = typeof window !== "undefined" ? legacyProvider() : null;
      if (lp) options.push({ kind: "legacy", provider: lp });
    }
    return options;
  }, [eip6963Providers]); // eslint-disable-line react-hooks/exhaustive-deps

  const shortAddress = useMemo(() => {
    if (!walletAddress) return "";
    return `${walletAddress.slice(0, 6)}...${walletAddress.slice(-4)}`;
  }, [walletAddress]);
  const walletButtonLabel = useMemo(() => {
    if (isBusy) return "Connecting...";
    if (isSignedIn && isWalletButtonHovered) return "Log out";
    if (isSignedIn && shortAddress) return shortAddress;
    return "Connect wallet";
  }, [isBusy, isSignedIn, isWalletButtonHovered, shortAddress]);
  const authHintText = useMemo(() => {
    if (isBusy) return "Approve wallet connection and signature in your wallet.";
    if (isSignedIn && isWalletButtonHovered) return "Click to log out.";
    if (isSignedIn) return "Wallet connected.";
    const lowered = statusText.toLowerCase();
    if (lowered.includes("failed") || lowered.includes("cancelled")) return statusText;
    return "Connect wallet to sign in and continue.";
  }, [isBusy, isSignedIn, isWalletButtonHovered, statusText]);

  function extractErrorMessage(error: unknown): string {
    if (error instanceof Error) return error.message;
    if (typeof error === "object" && error !== null && "message" in error) {
      return String((error as { message: unknown }).message);
    }
    return JSON.stringify(error);
  }

  async function clearServerSessionCookie(): Promise<void> {
    try {
      await fetch(buildApiUrl("/api/auth/wallet/logout"), {
        method: "POST",
        credentials: userApiCredentials,
      });
    } catch {
      /* ignore */
    }
  }

  async function refreshMyCards() {
    if (!isSignedIn) {
      setMyCards([]);
      setMyCardsError("");
      setMyCardsFetchedAt(null);
      return;
    }

    setMyCardsLoading(true);
    setMyCardsError("");
    try {
      const res = await fetch(buildApiUrl("/api/me/cards"), {
        method: "GET",
        credentials: userApiCredentials,
      });
      if (!res.ok) {
        if (res.status === 401) {
          void clearServerSessionCookie();
          setIsSignedIn(false);
          setProxyWallet(null);
          setTraderRank(null);
          setStatusText("Session expired. Please connect wallet again.");
          setMyCards([]);
          setMyCardsFetchedAt(null);
          clearStoredSessionMeta();
        }
        const text = await res.text();
        throw new Error(text || "Failed to load minted STARs");
      }
      const payload = (await res.json()) as MyMintedNftsResponse;
      setMyCards(Array.isArray(payload.items) ? payload.items : []);
      setMyCardsFetchedAt(String(payload.fetched_at ?? ""));
      setMyCardsError("");
    } catch (error) {
      setMyCardsError(extractErrorMessage(error));
      setMyCards([]);
      setMyCardsFetchedAt(null);
    } finally {
      setMyCardsLoading(false);
    }
  }

  async function handleSignOut() {
    await clearServerSessionCookie();
    setIsSignedIn(false);
    setProxyWallet(null);
    setTraderRank(null);
    setChallengeId("");
    setSignInCount(null);
    setMyCards([]);
    setMyCardsError("");
    setMyCardsFetchedAt(null);
    setFlippedCardSlugs({});
    setEligibility(null);
    setEligibilityError("");
    setMintResultText("");
    setMintError("");
    setStatusText("Logged out");
    setIsWalletButtonHovered(false);
    selectedProviderRef.current = null;
    safeLocalStorage.removeItem(LEGACY_JWT_LOCAL_STORAGE_KEY);
    clearStoredSessionMeta();
  }

  function triggerGeneratedCardFlip(slug: string, target: HTMLElement) {
    triggerCardFlip(
      slug,
      target,
      generatedCardFlipTimerRef,
      setAnimatingCardSlugs,
      setFlippedCardSlugs,
    );
  }

  function handleAuthButtonClick() {
    if (isSignedIn && isWalletButtonHovered) {
      void handleSignOut();
      return;
    }
    setShowPicker(true);
  }

  async function signInWith(provider: EthProvider, address: string, providerName: string | null) {
    if (!provider || !address) {
      setStatusText("Connect wallet first");
      return;
    }

    setIsBusy(true);
    try {
      const challengeRes = await fetch(
        buildApiUrl("/api/auth/wallet/challenge"),
        {
          method: "POST",
          credentials: userApiCredentials,
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ wallet_address: address }),
        },
      );
      if (!challengeRes.ok) {
        const challengeErr = await challengeRes.text();
        throw new Error(`Challenge failed: ${challengeErr}`);
      }
      const challenge = (await challengeRes.json()) as ChallengeResponse;
      setChallengeId(challenge.challenge_id);

      const signature = (await provider.request({
        method: "personal_sign",
        params: [challenge.message, address],
      })) as string;

      const verifyRes = await fetch(buildApiUrl("/api/auth/wallet/verify"), {
        method: "POST",
        credentials: userApiCredentials,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          challenge_id: challenge.challenge_id,
          wallet_address: address,
          signature,
        }),
      });
      if (!verifyRes.ok) {
        const verifyErr = await verifyRes.text();
        throw new Error(`Verify failed: ${verifyErr}`);
      }
      const verify = (await verifyRes.json()) as VerifyResponse;
      setIsSignedIn(Boolean(verify.signed_in));
      setSignInCount(verify.sign_in_count);
      setStatusText(verify.signed_in ? "Signed in" : "Not signed in");
      setMintResultText("");
      setMintError("");
      const resolvedProxyWallet = String(verify.proxy_wallet ?? "").trim() || null;
      const resolvedTraderRank = String(verify.trader_rank ?? "").trim() || null;
      setProxyWallet(null);
      setTraderRank(null);
      if (verify.signed_in) {
        setProxyWallet(resolvedProxyWallet);
        setTraderRank(resolvedTraderRank);
        saveStoredSessionMeta({
          walletAddress: String(verify.wallet_address ?? address),
          selectedWalletName: providerName,
          signInCount: verify.sign_in_count,
          proxyWallet: resolvedProxyWallet,
          traderRank: resolvedTraderRank,
          challengeId: challenge.challenge_id,
        });
      } else {
        setProxyWallet(null);
        setTraderRank(null);
        setMyCards([]);
        setMyCardsError("");
        setMyCardsFetchedAt(null);
        clearStoredSessionMeta();
      }
    } catch (error) {
      setIsSignedIn(false);
      setStatusText(extractErrorMessage(error));
      setProxyWallet(null);
      setTraderRank(null);
      setMyCards([]);
      setMyCardsError("");
      setMyCardsFetchedAt(null);
      setMintResultText("");
      setMintError("");
      void clearServerSessionCookie();
      clearStoredSessionMeta();
    } finally {
      setIsBusy(false);
    }
  }

  // Called when user picks a wallet in the modal.
  async function handlePickWallet(option: WalletOption) {
    setShowPicker(false);

    const provider =
      option.kind === "eip6963" ? option.detail.provider : option.provider;
    const name =
      option.kind === "eip6963" ? option.detail.info.name : "Browser wallet";

    setStatusText(`Connecting to ${name}…`);
    setIsBusy(true);
    try {
      const accounts = (await provider.request({
        method: "eth_requestAccounts",
      })) as string[];
      if (!accounts || accounts.length === 0) {
        setStatusText("Wallet connection cancelled (no accounts returned)");
        return;
      }
      const address = accounts[0];
      selectedProviderRef.current = provider;
      setSelectedWalletName(name);
      setWalletAddress(address);
      setIsSignedIn(false);
      setChallengeId("");
      setSignInCount(null);
      setProxyWallet(null);
      setTraderRank(null);
      setMyCards([]);
      setMyCardsError("");
      setMyCardsFetchedAt(null);
      setMintResultText("");
      setMintError("");
      void clearServerSessionCookie();
      clearStoredSessionMeta();
      await signInWith(provider, address, name);
    } catch (error) {
      const raw = extractErrorMessage(error);
      const code =
        typeof error === "object" && error !== null && "code" in error
          ? (error as { code: unknown }).code
          : null;
      if (code === 4001 || raw.toLowerCase().includes("rejected")) {
        setStatusText("Connection cancelled by user");
      } else {
        setStatusText(`Connection failed: ${raw}`);
      }
    } finally {
      setIsBusy(false);
    }
  }

  async function refreshEligibility() {
    setEligibilityLoading(true);
    setEligibilityError("");
    try {
      const res = await fetch(buildApiUrl("/api/me/eligibility"), {
        method: "GET",
        credentials: userApiCredentials,
      });
      if (!res.ok) {
        if (res.status === 401) {
          setIsSignedIn(false);
          clearStoredSessionMeta();
        }
        const text = await res.text();
        throw new Error(text || "Failed to load mint eligibility");
      }
      const payload = (await res.json()) as EligibilityResponse;
      setEligibility(payload);
    } catch (error) {
      setEligibility(null);
      setEligibilityError(extractErrorMessage(error));
    } finally {
      setEligibilityLoading(false);
    }
  }

  async function mintForSeason(seasonId: number) {
    if (!isSignedIn) {
      setMintError("Please sign in again to mint.");
      return;
    }
    // Mint requires either a real Polymarket trader rank OR holding enough of
    // the project token. ``gateTokenOk === null`` means the on-chain check
    // hasn't resolved yet *or* the RPC was unavailable — in both cases let the
    // request through and rely on the authoritative server-side gate.
    if (!hasPolymarketRank(traderRank) && gateTokenOk === false) {
      setMintError("WALLET HAS NO POLYMARKET TRADING HISTORY AND IS NOT A PROJECT TOKEN HOLDER.");
      return;
    }
    setMintingSeasonId(seasonId);
    setMintError("");
    setMintResultText(`[${new Date().toISOString()}] MINT REQUEST STARTED FOR SEASON ${seasonId}...`);
    try {
      const res = await fetch(buildApiUrl("/api/me/mint"), {
        method: "POST",
        credentials: userApiCredentials,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ season_id: seasonId }),
      });
      const text = await res.text();
      let parsed: MintApiResult | null = null;
      try {
        parsed = text ? (JSON.parse(text) as MintApiResult) : null;
      } catch {
        parsed = null;
      }
      if (!res.ok) {
        const detail = parsed && typeof parsed === "object" && "detail" in parsed
          ? String((parsed as { detail?: unknown }).detail ?? "")
          : "";
        throw new Error(detail || text || "Mint failed");
      }
      const summaryLines: string[] = [];
      if (parsed?.status) summaryLines.push(`STATUS: ${parsed.status}`);
      if (parsed?.claim_id != null) summaryLines.push(`CLAIM ID: ${parsed.claim_id}`);
      if (parsed?.collection_mint_number != null) {
        summaryLines.push(`STAR #: ${parsed.collection_mint_number}`);
      }
      if (parsed?.phase) summaryLines.push(`PHASE: ${parsed.phase}`);
      if (parsed?.recipient_address) {
        summaryLines.push(`RECIPIENT: ${parsed.recipient_address}`);
      }
      if (parsed?.mint_result?.asset_address) {
        summaryLines.push(`ASSET: ${parsed.mint_result.asset_address}`);
      }
      if (parsed?.mint_result?.tx_hash) {
        summaryLines.push(`TX: ${parsed.mint_result.tx_hash}`);
      }
      if (parsed?.warnings && parsed.warnings.length > 0) {
        summaryLines.push(`WARNINGS: ${parsed.warnings.join("; ")}`);
      }
      setMintResultText(
        [`[${new Date().toISOString()}] CLAIM QUEUED FOR SEASON ${seasonId}.`, ...summaryLines].join("\n"),
      );
      await Promise.all([refreshEligibility(), refreshMyCards()]);
    } catch (error) {
      setMintError(extractErrorMessage(error));
      setMintResultText("");
    } finally {
      setMintingSeasonId(null);
    }
  }

  function handleRefreshEligibilityClick() {
    setScrambleNonce((n) => n + 1);
    void refreshEligibility();
  }

  function streamForSeason(seasonId: number): EligibilityStream | null {
    if (!eligibility) return null;
    if (eligibility.genesis?.season_id === seasonId) return eligibility.genesis;
    if (eligibility.standard?.season_id === seasonId) return eligibility.standard;
    return null;
  }

  function renderSeasonMintAction(season: ActiveSeasonView) {
    const stream = streamForSeason(season.id);
    const isThisMinting = mintingSeasonId === season.id;
    const isAnyMinting = mintingSeasonId !== null;
    const supplyEmpty = season.remaining <= 0;
    const hasRank = hasPolymarketRank(traderRank);

    // Pending-claim short-circuit: if the backend says this wallet has a
    // QUEUED/PROCESSING (or already-COMPLETED) claim for this season, render
    // a status pill instead of a generic disabled button. Distinguishes the
    // three meaningful states for the user — waiting in queue, on-chain mint
    // in progress, and already done — using the structured ``pending_claim``
    // payload rather than parsing the human ``ineligible_reason`` string.
    const pending = stream?.pending_claim ?? null;
    if (pending) {
      const status = String(pending.status || "").toUpperCase();
      // QUEUED/PENDING/PROCESSING all show the same in-progress copy: from the
      // user's perspective the claim is being processed and they'll get a
      // STAR shortly. The cron worker stamps ``collection_mint_number`` when
      // it finalizes; until then we surface ``claim_id`` so the user has
      // *some* identifier to reference.
      const inProgressNumber = pending.collection_mint_number ?? pending.claim_id;
      const inProgressSuffix = inProgressNumber != null ? ` STAR #${inProgressNumber}.` : "";
      let pillLabel = "";
      let pillClass = "season-mint-pill";
      if (status === "QUEUED" || status === "PENDING" || status === "PROCESSING") {
        pillLabel = `CLAIM IN PROGRESS${inProgressSuffix} YOU'LL RECEIVE A STAR SHORTLY.`;
        pillClass +=
          status === "PROCESSING" ? " season-mint-pill-processing" : " season-mint-pill-queued";
      } else if (status === "COMPLETED") {
        const mintNo = pending.collection_mint_number;
        pillLabel =
          mintNo != null
            ? `WALLET HAS ALREADY CLAIMED A STAR THIS SEASON — STAR #${mintNo}`
            : "WALLET HAS ALREADY CLAIMED A STAR THIS SEASON.";
        pillClass += " season-mint-pill-completed";
      } else {
        const claimSuffix = pending.claim_id != null ? ` (claim #${pending.claim_id})` : "";
        pillLabel = `Active claim${claimSuffix} (${status || "unknown"})`;
      }
      return (
        <div className="season-mint-action">
          <ScrambleText
            text={pillLabel}
            triggerKey={scrambleNonce}
            className={pillClass}
          />
        </div>
      );
    }

    // Resolve the visible block-reason copy. Priorities (top → bottom):
    //   1. Access gating: needs a Polymarket trader rank OR enough project
    //      token. While the on-chain token check is still pending (null) we
    //      show a transient "checking" message instead of a hard block.
    //   2. Loading / transport-error placeholders — unchanged copy.
    //   3. "Looted" (origin's allocation taken by another wallet).
    //   4. Phase × wallet × supply matrix for "no slots" cases.
    //   5. Fallback to the backend's raw ``ineligible_reason``.
    let blockedReason = "";
    if (!hasRank && gateTokenOk === false) {
      blockedReason = "WALLET HAS NO POLYMARKET TRADING HISTORY AND IS NOT A PROJECT TOKEN HOLDER.";
    } else if (eligibilityLoading && !stream) {
      blockedReason = "CHECKING ELIGIBILITY...";
    } else if (eligibilityError) {
      blockedReason = `ELIGIBILITY UNAVAILABLE: ${eligibilityError}`;
    } else if (!stream) {
      blockedReason = "ELIGIBILITY FOR THIS SEASON IS NOT AVAILABLE.";
    } else if (stream && !stream.eligible_now) {
      blockedReason = formatBlockedReason(stream, supplyEmpty);
    } else if (supplyEmpty) {
      // Defensive: backend says eligible but supply already drained.
      blockedReason = formatBlockedReason(stream, true);
    }

    const canMint = !blockedReason && !isAnyMinting;

    return (
      <div className="season-mint-action">
        <button
          className="season-mint-button"
          onClick={() => void mintForSeason(season.id)}
          disabled={!canMint}
          title={blockedReason || "GET STAR for this season"}
        >
          {isThisMinting ? "Getting..." : "GET STAR"}
        </button>
        {blockedReason ? (
          <ScrambleText
            text={blockedReason}
            triggerKey={scrambleNonce}
            className="season-mint-reason"
          />
        ) : (
          <ScrambleText
            text="WALLET ELIGIBLE. GET YOUR STAR."
            triggerKey={scrambleNonce}
            className="season-mint-reason ok"
          />
        )}
      </div>
    );
  }

  if (siteWalletActionsDown === null) {
    return (
      <>
        <nav className="site-nav" aria-label="Site">
          <SiteLogoLink colorful />
          <span className="site-nav-title">DASHBOARD</span>
        </nav>
        <main className="card-detail-page" style={{ padding: "2rem", maxWidth: 480 }}>
          <p className="season-board-muted">Checking site status…</p>
        </main>
      </>
    );
  }

  if (siteWalletActionsDown) {
    return (
      <>
        <nav className="site-nav" aria-label="Site">
          <SiteLogoLink colorful />
          <span className="site-nav-title">DASHBOARD</span>
        </nav>
        <main className="card-detail-page" style={{ padding: "2rem", maxWidth: 520 }}>
          <h1 style={{ fontSize: "1.25rem", marginBottom: "0.75rem" }}>Dashboard unavailable</h1>
          <p className="season-board-muted" style={{ marginBottom: "1.25rem" }}>
            See the maintenance notice at the top of the page. Wallet and card actions are paused.
          </p>
          <Link href="/" className="card-detail-backlink">
            Back to home
          </Link>
        </main>
      </>
    );
  }

  return (
    <>
      <nav className="site-nav" aria-label="Site">
        <SiteLogoLink colorful />
        <span className="site-nav-title">DASHBOARD</span>
      </nav>
      {/* ── Wallet picker modal ── */}
      {showPicker && (
        <div
          className="modal-overlay"
          onClick={() => setShowPicker(false)}
        >
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h2>Select wallet</h2>
            <p>Choose how you want to connect</p>

            {walletOptions.length === 0 ? (
              <div className="wallet-list">
                <p style={{ color: "var(--bd-brand)", margin: 0 }}>
                  No wallets detected.{" "}
                  <a
                    href="https://metamask.io/download/"
                    target="_blank"
                    rel="noopener noreferrer"
                    style={{ color: "var(--bd-ink)", textDecoration: "underline" }}
                  >
                    Install MetaMask
                  </a>
                </p>
              </div>
            ) : (
              <div className="wallet-list">
                {walletOptions.map((opt, i) => {
                  const key =
                    opt.kind === "eip6963" ? opt.detail.info.uuid : "legacy";
                  const name =
                    opt.kind === "eip6963"
                      ? opt.detail.info.name
                      : "Browser wallet";
                  const icon =
                    opt.kind === "eip6963" ? opt.detail.info.icon : null;

                  return (
                    <button
                      key={key ?? i}
                      className="wallet-btn"
                      onClick={() => handlePickWallet(opt)}
                      disabled={isBusy}
                    >
                      {icon ? (
                        // icon is a data-URI from the extension — safe to use directly
                        // eslint-disable-next-line @next/next/no-img-element
                        <img src={icon} alt={name} />
                      ) : (
                        <span style={{ fontSize: 28 }}>🦊</span>
                      )}
                      <span>{name}</span>
                    </button>
                  );
                })}
              </div>
            )}

            <button
              className="modal-cancel"
              onClick={() => setShowPicker(false)}
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      <aside
        className={`auth-info-panel ${isWalletPanelCollapsed ? "is-collapsed" : ""}`}
        aria-live="polite"
        onMouseEnter={() => setIsWalletPanelHovered(true)}
        onMouseLeave={() => setIsWalletPanelHovered(false)}
      >
        <div className="auth-info-title">Wallet</div>
        <button
          className={`auth-connect-btn ${isSignedIn ? "connected" : ""}`}
          onMouseEnter={() => setIsWalletButtonHovered(true)}
          onMouseLeave={() => setIsWalletButtonHovered(false)}
          onClick={handleAuthButtonClick}
          disabled={isBusy}
        >
          {walletButtonLabel}
        </button>
        <p className="auth-info-hint">{authHintText}</p>
        <div className="auth-info-row">
          <span>Address</span>
          <strong>{walletAddress ? shortAddress : "Not connected"}</strong>
        </div>
        {isSignedIn ? (
          <>
            <div className="auth-info-row">
              <span>Provider</span>
              <strong>{selectedWalletName ?? "N/A"}</strong>
            </div>
            <div className="auth-info-row">
              <span>Sign-ins</span>
              <strong>{signInCount ?? "N/A"}</strong>
            </div>
            <div className="auth-info-row">
              <span>Challenge</span>
              <strong>{challengeId ? `${challengeId.slice(0, 10)}...` : "N/A"}</strong>
            </div>
            <div className="auth-info-row">
              <span>Polymarket proxy</span>
              <strong>
                {proxyWallet ?? "Not found"}
              </strong>
            </div>
            <div className="auth-info-row">
              <span>Trader rank</span>
              <strong>{traderRank ?? "No trades yet"}</strong>
            </div>
          </>
        ) : null}
      </aside>

      <div className="dashboard-page-top-gap" aria-hidden="true" />

      <ActiveSeasonsBoard
        ref={seasonsBoardRef}
        renderSeasonAction={
          isSignedIn
            ? (season) => renderSeasonMintAction(season)
            : undefined
        }
        footer={
          !isSignedIn ? (
            <p className="season-board-note">
              <strong>CONNECT YOUR WALLET</strong> TO USE WALLET-LINKED ACTIONS.
            </p>
          ) : (
            <div className="season-board-actions">
              <div className="season-board-actions-row">
                <button
                  onClick={handleRefreshEligibilityClick}
                  disabled={eligibilityLoading}
                >
                  {eligibilityLoading ? "CHECKING ELIGIBILITY..." : "CHECK ELIGIBILITY"}
                </button>
                {eligibility?.is_origin_wallet ? (
                  <span className="nft-fetched-at">Origin wallet</span>
                ) : null}
              </div>
              {eligibilityError ? (
                <pre className="eligibility-output">ELIGIBILITY LOAD FAILED: {eligibilityError}</pre>
              ) : null}
              {mintError ? (
                <pre className="eligibility-output">MINT FAILED: {mintError}</pre>
              ) : null}
              {mintResultText ? (
                <pre className="eligibility-output">{mintResultText}</pre>
              ) : null}
            </div>
          )
        }
      />

      <section className="season-board season-board-standalone nft-board-horizontal">
        <div className="season-board-title">My STARs</div>
        {!isSignedIn ? (
          <div className="season-board-muted">YOUR STARS WILL BE DISPLAYED HERE.</div>
        ) : (
          <>
            <div className="nft-actions">
              <button onClick={() => void refreshMyCards()} disabled={myCardsLoading}>
                {myCardsLoading ? "Loading STARs..." : "Reload my STARs"}
              </button>
              {myCardsFetchedAt ? (
                <span className="nft-fetched-at">
                  Updated: {new Date(myCardsFetchedAt).toLocaleString()}
                </span>
              ) : null}
            </div>
            {myCardsError ? (
              <pre className="eligibility-output">NFT load failed: {myCardsError}</pre>
            ) : null}
            {!myCardsLoading && !myCardsError && myCards.length === 0 ? (
              <div className="season-board-muted">NO STARS YET.</div>
            ) : null}
            <div className="nft-grid-wrap">
              <div
                className="nft-grid generated-card-grid"
                onMouseMove={(event) =>
                  handleCardGridMouseMove(event, {
                    wrapperSelector: ".nft-card-wrapper",
                    cardSelector: ".nft-card-tilt",
                  })
                }
                onMouseLeave={(event) => handleCardGridMouseLeave(event, ".nft-card-tilt")}
              >
                {myCards.map((item) => {
                  const flipKey = item.asset_address;
                  const isFlipped = Boolean(flippedCardSlugs[flipKey]);
                  const isAnimating = Boolean(animatingCardSlugs[flipKey]);
                  const cardSlug = (item.card_slug ?? "").trim();
                  const cardDetailUrl = cardSlug
                    ? `/cards/${encodeURIComponent(cardSlug)}`
                    : null;
                  const titleParts: string[] = [];
                  if (item.name) titleParts.push(item.name);
                  if (item.season_type && item.season_number != null) {
                    titleParts.push(`${item.season_type} #${item.season_number}`);
                  }
                  if (item.collection_mint_number != null) {
                    titleParts.push(`mint #${item.collection_mint_number}`);
                  }
                  const cardLabel = titleParts.join(" · ") || item.asset_address;
                  const ariaActionLabel = cardDetailUrl
                    ? `Open or flip NFT: ${cardLabel}`
                    : `Flip NFT: ${cardLabel}`;
                  return (
                    <div
                      key={item.asset_address}
                      className="generated-card-wrapper"
                    >
                      <div className="nft-card-wrapper generated-card-preview-wrapper">
                        {cardDetailUrl ? (
                          <Link
                            href={cardDetailUrl}
                            className="card-center-hotspot"
                            tabIndex={-1}
                            aria-label={`Open card: ${cardLabel}`}
                          />
                        ) : null}
                        {(["top", "right", "bottom", "left"] as const).map((edge) => (
                          <div
                            key={`${flipKey}-${edge}`}
                            className={`card-flip-hitbox card-flip-hitbox-${edge}`}
                            aria-hidden="true"
                            onPointerDown={(event) => {
                              event.preventDefault();
                              event.stopPropagation();
                            }}
                            onClick={(event) => {
                              event.preventDefault();
                              event.stopPropagation();
                              const card = event.currentTarget.parentElement?.querySelector<HTMLElement>(
                                ".generated-card-preview-card",
                              );
                              if (!card) return;
                              triggerGeneratedCardFlip(flipKey, card);
                            }}
                          />
                        ))}
                        <article
                          className={`nft-card nft-card-tilt theme-vivid generated-card-shell generated-card-preview-card ${isAnimating ? "generated-card-preview-card-flipping" : ""}`}
                          style={{"--card-border-color": "#B6BBC8"} as React.CSSProperties}
                          data-center-navigate={cardDetailUrl ? "1" : undefined}
                          role="button"
                          tabIndex={0}
                          aria-label={ariaActionLabel}
                          onPointerDown={(event) => {
                            markCardPressStart(event.currentTarget, event.clientX, event.clientY);
                          }}
                          onClick={(event) => {
                            if (
                              cardSlug &&
                              navigateToCardIfCenterClick(
                                event.currentTarget,
                                cardSlug,
                                event.clientX,
                                event.clientY,
                              )
                            ) {
                              return;
                            }
                            triggerGeneratedCardFlip(flipKey, event.currentTarget);
                          }}
                          onKeyDown={(event) => {
                            if (event.key !== "Enter" && event.key !== " ") return;
                            event.preventDefault();
                            triggerGeneratedCardFlip(flipKey, event.currentTarget);
                          }}
                        >
                          <div className={`generated-card-flip-inner ${isFlipped ? "is-flipped" : ""}`}>
                            <div className="generated-card-flip-face generated-card-flip-face-front">
                              {item.front_image_url ? (
                                <CardImage
                                  className="generated-card-image"
                                  src={item.front_image_url}
                                  fallbackSrc={item.front_image_fallback_url}
                                  alt={cardLabel}
                                />
                              ) : (
                                <div className="generated-card-image nft-image-empty">No preview</div>
                              )}
                            </div>
                            <div className="generated-card-flip-face generated-card-flip-face-back">
                              {item.back_image_url ? (
                                <CardImage
                                  className="generated-card-image"
                                  src={item.back_image_url}
                                  fallbackSrc={item.back_image_fallback_url}
                                  alt={`${cardLabel} back`}
                                />
                              ) : (
                                <div className="generated-card-image nft-image-empty">No back preview</div>
                              )}
                            </div>
                          </div>
                        </article>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          </>
        )}
      </section>

    </>
  );
}
