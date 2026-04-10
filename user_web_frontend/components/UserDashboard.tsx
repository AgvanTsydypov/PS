"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";

import { InlineSvgCard } from "./InlineSvgCard";

import {
  ActiveSeasonsBoard,
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
};

type StoredSessionMeta = {
  walletAddress?: string;
  selectedWalletName?: string | null;
  signInCount?: number | null;
  proxyWallet?: string | null;
  traderRank?: string | null;
  challengeId?: string;
};

type GeneratedCardPayload = {
  card_title?: string;
  primary_tag?: string;
  secondary_tag?: string;
  season_type?: string;
  season_number?: number;
  archetype?: string;
  leaderboard_rank?: number;
  border_color?: string;
  [key: string]: unknown;
};

type GeneratedCardItem = {
  id: number;
  slug: string;
  owner_wallet: string;
  owner_proxy_wallet?: string | null;
  winner_row_id: number;
  season_id: number;
  event_id?: string | null;
  event_slug?: string | null;
  card_title?: string | null;
  primary_tag?: string | null;
  secondary_tag?: string | null;
  pattern?: string | null;
  front_image_url: string;
  back_image_url: string;
  card_payload_json?: GeneratedCardPayload;
  created_at: string;
};

type GeneratedCardsResponse = {
  wallet_address: string;
  items: GeneratedCardItem[];
  total: number;
  total_available: number;
  remaining_available: number;
  fetched_at: string;
};

type GeneratedCardCreateResponse = {
  status: string;
  message: string;
  card: GeneratedCardItem;
  total_available: number;
  remaining_available: number;
};

const apiBase =
  process.env.NEXT_PUBLIC_USER_API_BASE_URL ??
  (process.env.NODE_ENV === "development" ? "http://localhost:8011" : "/");
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

export default function UserDashboard() {
  const [walletAddress, setWalletAddress] = useState("");
  const [challengeId, setChallengeId] = useState("");
  const [statusText, setStatusText] = useState("Not signed in");
  const [isSignedIn, setIsSignedIn] = useState(false);
  const [isBusy, setIsBusy] = useState(false);
  const [signInCount, setSignInCount] = useState<number | null>(null);
  const [proxyWallet, setProxyWallet] = useState<string | null>(null);
  const [traderRank, setTraderRank] = useState<string | null>(null);
  const [myCards, setMyCards] = useState<GeneratedCardItem[]>([]);
  const [myCardsLoading, setMyCardsLoading] = useState(false);
  const [myCardsError, setMyCardsError] = useState("");
  const [myCardsFetchedAt, setMyCardsFetchedAt] = useState<string | null>(null);
  const [generatedCardsTotalAvailable, setGeneratedCardsTotalAvailable] = useState(0);
  const [generatedCardsRemainingAvailable, setGeneratedCardsRemainingAvailable] = useState(0);
  const [getCardLoading, setGetCardLoading] = useState(false);
  const [getCardResultText, setGetCardResultText] = useState("");
  const [flippedCardSlugs, setFlippedCardSlugs] = useState<Record<string, boolean>>({});
  const [animatingCardSlugs, setAnimatingCardSlugs] = useState<Record<string, boolean>>({});
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
      setGeneratedCardsTotalAvailable(0);
      setGeneratedCardsRemainingAvailable(0);
      setFlippedCardSlugs({});
      return;
    }
    void refreshMyCards();
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
    const existingSlugs = new Set(myCards.map((item) => item.slug));
    const restored: Record<string, boolean> = {};
    Object.entries(persisted).forEach(([slug, flipped]) => {
      if (!flipped) return;
      if (!existingSlugs.has(slug)) return;
      restored[slug] = true;
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
      setGeneratedCardsTotalAvailable(0);
      setGeneratedCardsRemainingAvailable(0);
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
        throw new Error(text || "Failed to load generated cards");
      }
      const payload = (await res.json()) as GeneratedCardsResponse;
      setMyCards(Array.isArray(payload.items) ? payload.items : []);
      setMyCardsFetchedAt(String(payload.fetched_at ?? ""));
      setGeneratedCardsTotalAvailable(Number(payload.total_available) || 0);
      setGeneratedCardsRemainingAvailable(Number(payload.remaining_available) || 0);
      setMyCardsError("");
    } catch (error) {
      setMyCardsError(extractErrorMessage(error));
      setMyCards([]);
      setMyCardsFetchedAt(null);
      setGeneratedCardsTotalAvailable(0);
      setGeneratedCardsRemainingAvailable(0);
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
    setGeneratedCardsTotalAvailable(0);
    setGeneratedCardsRemainingAvailable(0);
    setFlippedCardSlugs({});
    setGetCardResultText("");
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
      setGetCardResultText("");
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
        setGeneratedCardsTotalAvailable(0);
        setGeneratedCardsRemainingAvailable(0);
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
      setGeneratedCardsTotalAvailable(0);
      setGeneratedCardsRemainingAvailable(0);
      setGetCardResultText("");
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
      setGeneratedCardsTotalAvailable(0);
      setGeneratedCardsRemainingAvailable(0);
      setGetCardResultText("");
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

  async function getGeneratedCard() {
    if (!isSignedIn) {
      setGetCardResultText("Get card failed: Please sign in again.");
      return;
    }
    setGetCardLoading(true);
    setGetCardResultText("");
    try {
      const res = await fetch(buildApiUrl("/api/cards/get"), {
        method: "POST",
        credentials: userApiCredentials,
      });
      if (!res.ok) {
        if (res.status === 401) {
          void clearServerSessionCookie();
          setIsSignedIn(false);
          setProxyWallet(null);
          setTraderRank(null);
          setMyCards([]);
          setMyCardsError("");
          setMyCardsFetchedAt(null);
          setGeneratedCardsTotalAvailable(0);
          setGeneratedCardsRemainingAvailable(0);
          setStatusText("Session expired. Please connect wallet again.");
          clearStoredSessionMeta();
        }
        const text = await res.text();
        throw new Error(text || "Get card failed");
      }
      const payload = (await res.json()) as GeneratedCardCreateResponse;
      const createdCard = payload.card;
      const title =
        String(createdCard.card_title ?? createdCard.card_payload_json?.card_title ?? "").trim() ||
        "Untitled card";
      setGeneratedCardsTotalAvailable(Number(payload.total_available) || 0);
      setGeneratedCardsRemainingAvailable(Number(payload.remaining_available) || 0);
      setGetCardResultText(
        [
          payload.message || "Card generated",
          `slug: ${createdCard.slug}`,
          `title: ${title}`,
          `remaining_available: ${Number(payload.remaining_available) || 0} / ${Number(payload.total_available) || 0}`,
        ].join("\n")
      );
      await refreshMyCards();
    } catch (error) {
      setGetCardResultText(`Get card failed: ${extractErrorMessage(error)}`);
    } finally {
      setGetCardLoading(false);
    }
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
                <p style={{ color: "#e06c75", margin: 0 }}>
                  No wallets detected.{" "}
                  <a
                    href="https://metamask.io/download/"
                    target="_blank"
                    rel="noopener noreferrer"
                    style={{ color: "#3a86ff" }}
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
        footer={
          !isSignedIn ? (
            <p className="season-board-note">
              Connect your wallet to use wallet-linked actions.
            </p>
          ) : (
            <div className="season-board-actions">
              <div className="season-board-stats">
                <span>Test cards remaining</span>
                <strong>
                  {generatedCardsRemainingAvailable} / {generatedCardsTotalAvailable}
                </strong>
              </div>
              <button
                onClick={() => void getGeneratedCard()}
                disabled={getCardLoading || generatedCardsRemainingAvailable <= 0}
              >
                {getCardLoading
                  ? "Generating card..."
                  : generatedCardsRemainingAvailable <= 0
                    ? "All cards claimed"
                    : "Get card"}
              </button>
              {getCardResultText ? (
                <pre className="eligibility-output">{getCardResultText}</pre>
              ) : null}
            </div>
          )
        }
      />

      <section className="season-board season-board-standalone nft-board-horizontal">
        <div className="season-board-title">My Cards</div>
        {!isSignedIn ? (
          <div className="season-board-muted">Sign in to generate and view your test cards.</div>
        ) : (
          <>
            <div className="nft-actions">
              <button onClick={() => void refreshMyCards()} disabled={myCardsLoading}>
                {myCardsLoading ? "Loading cards..." : "Reload my cards"}
              </button>
              {myCardsFetchedAt ? (
                <span className="nft-fetched-at">
                  Updated: {new Date(myCardsFetchedAt).toLocaleString()}
                </span>
              ) : null}
            </div>
            {myCardsError ? (
              <pre className="eligibility-output">Card load failed: {myCardsError}</pre>
            ) : null}
            {!myCardsLoading && !myCardsError && myCards.length === 0 ? (
              <div className="season-board-muted">No generated cards in this wallet yet.</div>
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
                  const isFlipped = Boolean(flippedCardSlugs[item.slug]);
                  const isAnimating = Boolean(animatingCardSlugs[item.slug]);
                  return (
                    <div
                      key={item.slug}
                      className="generated-card-wrapper"
                    >
                      <div className="nft-card-wrapper generated-card-preview-wrapper">
                        <Link
                          href={`/cards/${encodeURIComponent(item.slug)}`}
                          className="card-center-hotspot"
                          tabIndex={-1}
                          aria-label={`Open card: ${item.slug}`}
                        />
                        <article
                          className={`nft-card nft-card-tilt theme-vivid generated-card-shell generated-card-preview-card ${isAnimating ? "generated-card-preview-card-flipping" : ""}`}
                          style={{"--card-border-color": item.card_payload_json?.border_color ?? "#B6BBC8"} as React.CSSProperties}
                          data-center-navigate="1"
                          onPointerDown={(event) => {
                            markCardPressStart(event.currentTarget, event.clientX, event.clientY);
                          }}
                          onClick={(event) => {
                            if (
                              navigateToCardIfCenterClick(
                                event.currentTarget,
                                item.slug,
                                event.clientX,
                                event.clientY,
                              )
                            ) {
                              return;
                            }
                            triggerGeneratedCardFlip(item.slug, event.currentTarget);
                          }}
                        >
                          <div className={`generated-card-flip-inner ${isFlipped ? "is-flipped" : ""}`}>
                            <div className="generated-card-flip-face generated-card-flip-face-front">
                              {item.front_image_url ? (
                                <InlineSvgCard className="generated-card-image" url={item.front_image_url} alt={item.slug} />
                              ) : (
                                <div className="generated-card-image nft-image-empty">No preview</div>
                              )}
                            </div>
                            <div className="generated-card-flip-face generated-card-flip-face-back">
                              {item.back_image_url ? (
                                <InlineSvgCard className="generated-card-image" url={item.back_image_url} alt={`${item.slug} back`} />
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
