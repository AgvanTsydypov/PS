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
  solana_wallet?: string | null;
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
};

type EligibilityResponse = {
  wallet_address: string;
  is_origin_wallet: boolean;
  genesis: EligibilityStream;
  standard: EligibilityStream;
  double_mint?: {
    can_claim_genesis: boolean;
    can_claim_standard: boolean;
    can_claim_both_now: boolean;
  };
};

type SolanaWalletResponse = {
  wallet_address?: string;
  solana_wallet: string | null;
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
  collection_address?: string;
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
  recipient_solana_wallet: string | null;
  season_id: number | null;
  season_type: string | null;
  season_number: number | null;
  phase: string | null;
  collection_mint_number: number | null;
  name: string | null;
  front_image_url: string | null;
  back_image_url: string | null;
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
// Sentinel proxy_wallet value persisted by the backend when Polymarket has no
// profile for the EVM wallet. Mirrors PM_NOT_REGISTERED_VALUE in
// user_web_backend/main.py — keep in sync.
const PM_NOT_REGISTERED_VALUE = "Not registered in PM";

function hasPolymarketRank(traderRank: string | null | undefined): boolean {
  if (traderRank == null) return false;
  const value = String(traderRank).trim();
  if (!value) return false;
  return !POLYMARKET_RANK_SENTINEL_VALUES.has(value.toLowerCase());
}

function isRegisteredOnPolymarket(proxyWallet: string | null | undefined): boolean {
  if (proxyWallet == null) return false;
  const value = String(proxyWallet).trim();
  if (!value) return false;
  return value.toLowerCase() !== PM_NOT_REGISTERED_VALUE.toLowerCase();
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

// Text shown in the Solana hint paragraph. Extracted so it can be fed
// through ScrambleText without a JSX fragment / whitespace ambiguity.
const SOLANA_NOTE_TEXT =
  "Your minted PolyStars STARs are issued on Solana. Provide the Solana address that should receive them. Minting is disabled until a Solana wallet is saved.";

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
  duration = 4000,
}: {
  text: string;
  triggerKey: number;
  duration?: number;
}) {
  const [display, setDisplay] = useState(text);
  const [isScrambling, setIsScrambling] = useState(false);

  useEffect(() => {
    if (triggerKey <= 0) {
      setDisplay(text);
      setIsScrambling(false);
      return;
    }
    // Respect users who opted out of motion — just render the real text
    // with no cycling animation.
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

  return (
    <span className={isScrambling ? "bd-scramble-active" : undefined}>
      {display}
    </span>
  );
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
  const [myCards, setMyCards] = useState<MyMintedNftItem[]>([]);
  const [myCardsLoading, setMyCardsLoading] = useState(false);
  const [myCardsError, setMyCardsError] = useState("");
  const [myCardsFetchedAt, setMyCardsFetchedAt] = useState<string | null>(null);
  const [flippedCardSlugs, setFlippedCardSlugs] = useState<Record<string, boolean>>({});
  const [solanaWallet, setSolanaWallet] = useState<string | null>(null);
  const [solanaWalletInput, setSolanaWalletInput] = useState("");
  const [solanaWalletLoading, setSolanaWalletLoading] = useState(false);
  const [solanaWalletSaving, setSolanaWalletSaving] = useState(false);
  const [solanaWalletError, setSolanaWalletError] = useState("");
  const [solanaWalletNotice, setSolanaWalletNotice] = useState("");
  const [eligibility, setEligibility] = useState<EligibilityResponse | null>(null);
  const [eligibilityLoading, setEligibilityLoading] = useState(false);
  const [eligibilityError, setEligibilityError] = useState("");
  const [mintingSeasonId, setMintingSeasonId] = useState<number | null>(null);
  const [mintResultText, setMintResultText] = useState("");
  const [mintError, setMintError] = useState("");
  const [animatingCardSlugs, setAnimatingCardSlugs] = useState<Record<string, boolean>>({});
  const generatedCardFlipTimerRef = useRef<Record<string, number | null>>({});
  const didHydrateMyCardFlipsRef = useRef(false);
  const myCardFlipsWalletRef = useRef("");
  const [isWalletButtonHovered, setIsWalletButtonHovered] = useState(false);
  const [isWalletPanelHovered, setIsWalletPanelHovered] = useState(false);
  const [isWalletPanelCollapsed, setIsWalletPanelCollapsed] = useState(false);
  const walletPanelCollapseTimerRef = useRef<number | null>(null);

  // Incremented each time the user hits "Refresh eligibility" without a
  // saved Solana wallet. Used as a React `key` on the two Solana hint
  // elements so they remount and restart the CSS ignition/fade animation.
  // 0 means the flash has never fired yet → hint renders without the
  // `.warn-flash` class.
  const [warnFlashKey, setWarnFlashKey] = useState(0);

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
        const initialSolana = (data.solana_wallet ?? "").trim() || null;
        setSolanaWallet(initialSolana);
        setSolanaWalletInput(initialSolana ?? "");

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
      setSolanaWallet(null);
      setSolanaWalletInput("");
      setSolanaWalletError("");
      setSolanaWalletNotice("");
      setEligibility(null);
      setEligibilityError("");
      setMintResultText("");
      setMintError("");
      return;
    }
    void refreshMyCards();
    void refreshSolanaWallet();
    void refreshEligibility();
  }, [isSignedIn, walletAddress]); // eslint-disable-line react-hooks/exhaustive-deps

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
    setSolanaWallet(null);
    setSolanaWalletInput("");
    setSolanaWalletError("");
    setSolanaWalletNotice("");
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

  async function refreshSolanaWallet() {
    setSolanaWalletLoading(true);
    setSolanaWalletError("");
    try {
      const res = await fetch(buildApiUrl("/api/me/solana-wallet"), {
        method: "GET",
        credentials: userApiCredentials,
      });
      if (!res.ok) {
        if (res.status === 401) {
          setIsSignedIn(false);
          clearStoredSessionMeta();
        }
        const text = await res.text();
        throw new Error(text || "Failed to load Solana wallet");
      }
      const payload = (await res.json()) as SolanaWalletResponse;
      const value = (payload.solana_wallet ?? "").trim() || null;
      setSolanaWallet(value);
      setSolanaWalletInput(value ?? "");
    } catch (error) {
      setSolanaWalletError(extractErrorMessage(error));
    } finally {
      setSolanaWalletLoading(false);
    }
  }

  async function saveSolanaWallet(overrideValue?: string | null) {
    // The Clear button passes "" explicitly because React state updates are
    // async — reading solanaWalletInput from the closure right after a
    // setState would still see the stale (non-empty) value and re-save it.
    const rawValue =
      overrideValue !== undefined ? overrideValue ?? "" : solanaWalletInput;
    const trimmed = String(rawValue).trim();
    setSolanaWalletSaving(true);
    setSolanaWalletError("");
    setSolanaWalletNotice("");
    try {
      const res = await fetch(buildApiUrl("/api/me/solana-wallet"), {
        method: "PUT",
        credentials: userApiCredentials,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ solana_wallet: trimmed || null }),
      });
      if (!res.ok) {
        const text = await res.text();
        let detail = text;
        try {
          const parsed = JSON.parse(text) as { detail?: string };
          if (parsed?.detail) detail = parsed.detail;
        } catch {
          /* keep raw text */
        }
        throw new Error(detail || "Failed to save Solana wallet");
      }
      const payload = (await res.json()) as SolanaWalletResponse;
      const value = (payload.solana_wallet ?? "").trim() || null;
      setSolanaWallet(value);
      setSolanaWalletInput(value ?? "");
      setSolanaWalletNotice(value ? "Solana wallet saved." : "Solana wallet cleared.");
      // Once a recipient wallet is saved successfully, mirror what the
      // user would do manually: trigger the "Reload my STARs" action and
      // refresh mint eligibility, so the dashboard reflects the new
      // recipient without an extra click.
      if (value) {
        void refreshMyCards();
        void refreshEligibility();
      }
    } catch (error) {
      setSolanaWalletError(extractErrorMessage(error));
    } finally {
      setSolanaWalletSaving(false);
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
    if (!isRegisteredOnPolymarket(proxyWallet)) {
      setMintError("Wallet is not registered on Polymarket — minting is not allowed.");
      return;
    }
    if (!solanaWallet) {
      setMintError("Set your Solana recipient wallet first.");
      return;
    }
    setMintingSeasonId(seasonId);
    setMintError("");
    setMintResultText(`[${new Date().toISOString()}] Mint request started for season ${seasonId}...`);
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
      if (parsed?.status) summaryLines.push(`status: ${parsed.status}`);
      if (parsed?.claim_id != null) summaryLines.push(`claim_id: ${parsed.claim_id}`);
      if (parsed?.collection_mint_number != null) {
        summaryLines.push(`mint #: ${parsed.collection_mint_number}`);
      }
      if (parsed?.phase) summaryLines.push(`phase: ${parsed.phase}`);
      if (parsed?.recipient_address) {
        summaryLines.push(`recipient: ${parsed.recipient_address}`);
      }
      if (parsed?.mint_result?.asset_address) {
        summaryLines.push(`asset: ${parsed.mint_result.asset_address}`);
      }
      if (parsed?.mint_result?.tx_hash) {
        summaryLines.push(`tx: ${parsed.mint_result.tx_hash}`);
      }
      if (parsed?.warnings && parsed.warnings.length > 0) {
        summaryLines.push(`warnings: ${parsed.warnings.join("; ")}`);
      }
      setMintResultText(
        [`[${new Date().toISOString()}] Mint completed for season ${seasonId}.`, ...summaryLines].join("\n"),
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
    // When the user clicks "Refresh eligibility" without a saved Solana
    // recipient wallet, briefly ignite the two blocking hints in red so
    // the reason minting is locked is visually unmissable (0.5s of glow
    // + 2s fade handled entirely in CSS via the .warn-flash keyframe).
    if (!solanaWallet) {
      setWarnFlashKey((k) => k + 1);
    }
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
    const hasSolanaWallet = Boolean(solanaWallet);
    const supplyEmpty = season.remaining <= 0;
    const isPmRegistered = isRegisteredOnPolymarket(proxyWallet);

    let blockedReason = "";
    if (!isPmRegistered) {
      blockedReason = "Wallet is not registered on Polymarket — minting is not allowed.";
    } else if (!hasSolanaWallet) {
      blockedReason = "Set your Solana recipient wallet to enable minting.";
    } else if (supplyEmpty) {
      blockedReason = "No supply remaining for this season.";
    } else if (eligibilityLoading && !stream) {
      blockedReason = "Checking eligibility...";
    } else if (eligibilityError) {
      blockedReason = `Eligibility unavailable: ${eligibilityError}`;
    } else if (stream && !stream.eligible_now) {
      blockedReason = stream.ineligible_reason || "Wallet not eligible for this season right now.";
    } else if (!stream) {
      blockedReason = "Eligibility for this season is not available.";
    }

    const canMint = !blockedReason && !isAnyMinting;
    // Only the "Solana wallet not set" reason participates in the
    // ignition/fade flash. Other blockers (PM not registered, supply,
    // eligibility loading/errors) are unrelated to the Solana button.
    const isSolanaMissingReason =
      blockedReason === "Set your Solana recipient wallet to enable minting.";
    const shouldFlashReason = isSolanaMissingReason && warnFlashKey > 0;

    return (
      <div className="season-mint-action">
        <button
          className="season-mint-button"
          onClick={() => void mintForSeason(season.id)}
          disabled={!canMint}
          title={blockedReason || "Mint STAR for this season"}
        >
          {isThisMinting ? "Minting..." : "Mint STAR"}
        </button>
        {blockedReason ? (
          shouldFlashReason ? (
            <span
              key={`flash-${warnFlashKey}`}
              className="season-mint-reason warn-flash"
            >
              <ScrambleText text={blockedReason} triggerKey={warnFlashKey} />
            </span>
          ) : (
            <span className="season-mint-reason">{blockedReason}</span>
          )
        ) : (
          <span className="season-mint-reason ok">Eligible to mint now</span>
        )}
      </div>
    );
  }

  if (siteWalletActionsDown === null) {
    return (
      <>
        <nav className="site-nav" aria-label="Site">
          <SiteLogoLink />
          <span className="site-nav-title">My dashboard</span>
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
          <SiteLogoLink />
          <span className="site-nav-title">My dashboard</span>
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
        <SiteLogoLink />
        <span className="site-nav-title">My dashboard</span>
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
            <div className="auth-info-row">
              <span>Solana wallet</span>
              <strong>
                {solanaWallet
                  ? `${solanaWallet.slice(0, 4)}...${solanaWallet.slice(-4)}`
                  : "Not set"}
              </strong>
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
              Connect your wallet to use wallet-linked actions.
            </p>
          ) : (
            <div className="season-board-actions">
              <div className="season-board-actions-row">
                <button
                  onClick={handleRefreshEligibilityClick}
                  disabled={eligibilityLoading}
                >
                  {eligibilityLoading ? "Refreshing eligibility..." : "Refresh eligibility"}
                </button>
                {eligibility?.is_origin_wallet ? (
                  <span className="nft-fetched-at">Origin wallet</span>
                ) : null}
              </div>
              {eligibilityError ? (
                <pre className="eligibility-output">Eligibility load failed: {eligibilityError}</pre>
              ) : null}
              {mintError ? (
                <pre className="eligibility-output">Mint failed: {mintError}</pre>
              ) : null}
              {mintResultText ? (
                <pre className="eligibility-output">{mintResultText}</pre>
              ) : null}
            </div>
          )
        }
      />

      <section className="season-board season-board-standalone solana-wallet-board">
        <div className="season-board-title">Solana recipient wallet</div>
        {!isSignedIn ? (
          <div className="season-board-muted">
            Sign in with your EVM wallet to set the Solana address that will receive your minted STARs.
          </div>
        ) : (
          <>
            {!solanaWallet ? (
              warnFlashKey > 0 ? (
                <p
                  key={`flash-${warnFlashKey}`}
                  className="season-board-note warn-flash"
                >
                  <ScrambleText text={SOLANA_NOTE_TEXT} triggerKey={warnFlashKey} />
                </p>
              ) : (
                <p className="season-board-note">{SOLANA_NOTE_TEXT}</p>
              )
            ) : null}
            <div className="solana-wallet-row">
              <input
                type="text"
                className="solana-wallet-input"
                placeholder="Your Solana wallet address (base58)"
                value={solanaWalletInput}
                onChange={(e) => {
                  setSolanaWalletInput(e.target.value);
                  setSolanaWalletNotice("");
                  setSolanaWalletError("");
                }}
                spellCheck={false}
                autoComplete="off"
                disabled={solanaWalletSaving || solanaWalletLoading}
              />
              <button
                onClick={() => void saveSolanaWallet()}
                disabled={
                  solanaWalletSaving ||
                  solanaWalletLoading ||
                  solanaWalletInput.trim() === (solanaWallet ?? "")
                }
              >
                {solanaWalletSaving ? "Saving..." : "Save"}
              </button>
              {solanaWallet ? (
                <button
                  onClick={() => {
                    setSolanaWalletInput("");
                    void saveSolanaWallet("");
                  }}
                  disabled={solanaWalletSaving || solanaWalletLoading}
                >
                  Clear
                </button>
              ) : null}
            </div>
            <div className="solana-wallet-status">
              <span>Saved address</span>
              <strong className="solana-wallet-saved">
                {solanaWalletLoading ? "Loading..." : solanaWallet ?? "Not set"}
              </strong>
            </div>
            {solanaWalletError ? (
              <pre className="eligibility-output">{solanaWalletError}</pre>
            ) : null}
            {solanaWalletNotice ? (
              <p className="season-board-note">{solanaWalletNotice}</p>
            ) : null}
          </>
        )}
      </section>

      <section className="season-board season-board-standalone nft-board-horizontal">
        <div className="season-board-title">My STARs</div>
        {!isSignedIn ? (
          <div className="season-board-muted">Sign in to view your minted STARs.</div>
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
              <div className="season-board-muted">No minted STARs for this wallet yet.</div>
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
                                // eslint-disable-next-line @next/next/no-img-element
                                <img className="generated-card-image" src={item.front_image_url} alt={cardLabel} />
                              ) : (
                                <div className="generated-card-image nft-image-empty">No preview</div>
                              )}
                            </div>
                            <div className="generated-card-flip-face generated-card-flip-face-back">
                              {item.back_image_url ? (
                                // eslint-disable-next-line @next/next/no-img-element
                                <img className="generated-card-image" src={item.back_image_url} alt={`${cardLabel} back`} />
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
