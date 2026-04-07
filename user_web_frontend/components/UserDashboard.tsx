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
import { fetchSiteStatus } from "../lib/userApiBase";

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
  access_token?: string;
  token_type?: string;
  expires_in?: number;
};

type JwtPayload = {
  sub?: string;
  exp?: number;
};

type StoredSessionMeta = {
  walletAddress?: string;
  selectedWalletName?: string | null;
  signInCount?: number | null;
  proxyWallet?: string | null;
  traderRank?: string | null;
  challengeId?: string;
};

type EligibilityStream = {
  season_id: number | null;
  phase: string | null;
  eligible_now: boolean;
  ineligible_reason: string | null;
};

type EligibilityResponse = {
  wallet_address: string;
  proxy_wallet?: string;
  trader_rank?: string;
  eligibility_wallet?: string;
  mint_blocked?: boolean;
  mint_block_reason?: string;
  genesis: EligibilityStream;
  standard: EligibilityStream;
  double_mint: {
    can_claim_genesis: boolean;
    can_claim_standard: boolean;
    can_claim_both_now: boolean;
  };
};

type UserNftItem = {
  token_id: string;
  name: string;
  description: string;
  image_url: string;
  owner_address: string;
  collection_name: string;
  token_type: string;
  amount: string;
  explorer_url: string;
  metadata?: {
    attributes?: Array<{
      trait_type?: string;
      value?: string | number | boolean | null;
    }>;
  };
};

type UserNftsResponse = {
  wallet_address: string;
  contract_address: string;
  items: UserNftItem[];
  total: number;
  source: string;
  fetched_at: string;
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
const AUTH_TOKEN_STORAGE_KEY = "polystars_user_access_token";
const AUTH_SESSION_META_STORAGE_KEY = "polystars_user_session_meta";
const MY_CARDS_FLIP_STORAGE_KEY_PREFIX = "polystars_my_cards_flipped_v1";

function buildApiUrl(path: string): string {
  if (apiBase === "/") return path;
  return `${apiBase.replace(/\/$/, "")}${path}`;
}

function parseJwtPayload(token: string): JwtPayload | null {
  try {
    const parts = token.split(".");
    if (parts.length < 2) return null;
    const base64 = parts[1].replace(/-/g, "+").replace(/_/g, "/");
    const padded = base64 + "=".repeat((4 - (base64.length % 4)) % 4);
    const json = window.atob(padded);
    return JSON.parse(json) as JwtPayload;
  } catch {
    return null;
  }
}

function isJwtExpired(payload: JwtPayload | null): boolean {
  const exp = Number(payload?.exp ?? 0);
  if (!exp) return true;
  return Date.now() >= exp * 1000;
}

function loadStoredSessionMeta(): StoredSessionMeta | null {
  try {
    const raw = window.localStorage.getItem(AUTH_SESSION_META_STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as StoredSessionMeta;
    if (!parsed || typeof parsed !== "object") return null;
    return parsed;
  } catch {
    return null;
  }
}

function saveStoredSessionMeta(meta: StoredSessionMeta): void {
  window.localStorage.setItem(AUTH_SESSION_META_STORAGE_KEY, JSON.stringify(meta));
}

function clearStoredSessionMeta(): void {
  window.localStorage.removeItem(AUTH_SESSION_META_STORAGE_KEY);
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
    const raw = window.localStorage.getItem(storageKey);
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
  window.localStorage.setItem(storageKey, JSON.stringify(out));
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
  const [eligibilityLoading, setEligibilityLoading] = useState(false);
  const [eligibilitySummary, setEligibilitySummary] = useState("");
  const [eligibilityChecked, setEligibilityChecked] = useState(false);
  const [canMintNow, setCanMintNow] = useState(false);
  const [mintLoading, setMintLoading] = useState(false);
  const [mintResultText, setMintResultText] = useState("");
  const [accessToken, setAccessToken] = useState("");
  const [proxyWallet, setProxyWallet] = useState<string | null>(null);
  const [traderRank, setTraderRank] = useState<string | null>(null);
  const [myNfts, setMyNfts] = useState<UserNftItem[]>([]);
  const [myNftsLoading, setMyNftsLoading] = useState(false);
  const [myNftsError, setMyNftsError] = useState("");
  const [myNftsFetchedAt, setMyNftsFetchedAt] = useState<string | null>(null);
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
    const token = window.localStorage.getItem(AUTH_TOKEN_STORAGE_KEY) ?? "";
    if (!token) return;
    const payload = parseJwtPayload(token);
    if (!payload || isJwtExpired(payload)) {
      window.localStorage.removeItem(AUTH_TOKEN_STORAGE_KEY);
      clearStoredSessionMeta();
      return;
    }

    setAccessToken(token);
    setIsSignedIn(true);
    setStatusText("Signed in");

    const meta = loadStoredSessionMeta();
    if (meta?.walletAddress) {
      setWalletAddress(String(meta.walletAddress));
    }
    if (typeof meta?.selectedWalletName === "string" && meta.selectedWalletName.trim()) {
      setSelectedWalletName(meta.selectedWalletName);
    }
    if (typeof meta?.signInCount === "number") {
      setSignInCount(meta.signInCount);
    }
    if (meta?.proxyWallet !== undefined) {
      setProxyWallet(meta.proxyWallet ?? null);
    }
    if (meta?.traderRank !== undefined) {
      setTraderRank(meta.traderRank ?? null);
    }
    if (typeof meta?.challengeId === "string" && meta.challengeId.trim()) {
      setChallengeId(meta.challengeId);
    }

    const subjectWallet = String(payload.sub ?? "").trim();
    if (subjectWallet && !meta?.walletAddress) {
      setWalletAddress(subjectWallet);
    }
  }, []);

  useEffect(() => {
    if (!isSignedIn || !accessToken) {
      setMyNfts([]);
      setMyNftsError("");
      setMyNftsFetchedAt(null);
      setMyCards([]);
      setMyCardsError("");
      setMyCardsFetchedAt(null);
      setGeneratedCardsTotalAvailable(0);
      setGeneratedCardsRemainingAvailable(0);
      setFlippedCardSlugs({});
      return;
    }
    void refreshMyNfts();
    void refreshMyCards();
  }, [isSignedIn, accessToken, walletAddress]); // eslint-disable-line react-hooks/exhaustive-deps

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

  async function refreshMyNfts() {
    if (!accessToken || !isSignedIn) {
      setMyNfts([]);
      setMyNftsError("");
      setMyNftsFetchedAt(null);
      return;
    }

    setMyNftsLoading(true);
    setMyNftsError("");
    try {
      const res = await fetch(buildApiUrl("/api/me/nfts"), {
        method: "GET",
        headers: { Authorization: `Bearer ${accessToken}` },
      });
      if (!res.ok) {
        if (res.status === 401) {
          setIsSignedIn(false);
          setAccessToken("");
          setProxyWallet(null);
          setTraderRank(null);
          setStatusText("Session expired. Please connect wallet again.");
          setMyNfts([]);
          setMyNftsError("");
          setMyNftsFetchedAt(null);
          setMyCards([]);
          setMyCardsError("");
          setMyCardsFetchedAt(null);
          setGeneratedCardsTotalAvailable(0);
          setGeneratedCardsRemainingAvailable(0);
          window.localStorage.removeItem(AUTH_TOKEN_STORAGE_KEY);
          clearStoredSessionMeta();
        }
        const text = await res.text();
        throw new Error(text || "Failed to load NFT collection");
      }
      const payload = (await res.json()) as UserNftsResponse;
      setMyNfts(Array.isArray(payload.items) ? payload.items : []);
      setMyNftsFetchedAt(String(payload.fetched_at ?? ""));
      setMyNftsError("");
    } catch (error) {
      setMyNftsError(extractErrorMessage(error));
      setMyNfts([]);
      setMyNftsFetchedAt(null);
    } finally {
      setMyNftsLoading(false);
    }
  }

  async function refreshMyCards() {
    if (!accessToken || !isSignedIn) {
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
        headers: { Authorization: `Bearer ${accessToken}` },
      });
      if (!res.ok) {
        if (res.status === 401) {
          setIsSignedIn(false);
          setAccessToken("");
          setProxyWallet(null);
          setTraderRank(null);
          setStatusText("Session expired. Please connect wallet again.");
          setMyNfts([]);
          setMyNftsFetchedAt(null);
          setMyCards([]);
          setMyCardsFetchedAt(null);
          window.localStorage.removeItem(AUTH_TOKEN_STORAGE_KEY);
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

  function handleSignOut() {
    setIsSignedIn(false);
    setAccessToken("");
    setProxyWallet(null);
    setTraderRank(null);
    setChallengeId("");
    setSignInCount(null);
    setMyNfts([]);
    setMyNftsError("");
    setMyNftsFetchedAt(null);
    setMyCards([]);
    setMyCardsError("");
    setMyCardsFetchedAt(null);
    setGeneratedCardsTotalAvailable(0);
    setGeneratedCardsRemainingAvailable(0);
    setFlippedCardSlugs({});
    setEligibilitySummary("");
    setEligibilityChecked(false);
    setCanMintNow(false);
    setMintResultText("");
    setGetCardResultText("");
    setStatusText("Logged out");
    setIsWalletButtonHovered(false);
    selectedProviderRef.current = null;
    window.localStorage.removeItem(AUTH_TOKEN_STORAGE_KEY);
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
      handleSignOut();
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
      setEligibilitySummary("");
      setEligibilityChecked(false);
      setCanMintNow(false);
      setMintResultText("");
      setGetCardResultText("");
      const resolvedProxyWallet = String(verify.proxy_wallet ?? "").trim() || null;
      const resolvedTraderRank = String(verify.trader_rank ?? "").trim() || null;
      setProxyWallet(null);
      setTraderRank(null);
      const token = String(verify.access_token ?? "");
      if (verify.signed_in && token) {
        setAccessToken(token);
        window.localStorage.setItem(AUTH_TOKEN_STORAGE_KEY, token);
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
        setAccessToken("");
        window.localStorage.removeItem(AUTH_TOKEN_STORAGE_KEY);
        setProxyWallet(null);
        setTraderRank(null);
        setMyNfts([]);
        setMyNftsError("");
        setMyNftsFetchedAt(null);
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
      setAccessToken("");
      setProxyWallet(null);
      setTraderRank(null);
      setMyNfts([]);
      setMyNftsError("");
      setMyNftsFetchedAt(null);
      setMyCards([]);
      setMyCardsError("");
      setMyCardsFetchedAt(null);
      setGeneratedCardsTotalAvailable(0);
      setGeneratedCardsRemainingAvailable(0);
      setEligibilityChecked(false);
      setCanMintNow(false);
      setMintResultText("");
      setGetCardResultText("");
      window.localStorage.removeItem(AUTH_TOKEN_STORAGE_KEY);
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
      setAccessToken("");
      setProxyWallet(null);
      setTraderRank(null);
      setMyNfts([]);
      setMyNftsError("");
      setMyNftsFetchedAt(null);
      setMyCards([]);
      setMyCardsError("");
      setMyCardsFetchedAt(null);
      setGeneratedCardsTotalAvailable(0);
      setGeneratedCardsRemainingAvailable(0);
      setEligibilityChecked(false);
      setCanMintNow(false);
      setMintResultText("");
      setGetCardResultText("");
      window.localStorage.removeItem(AUTH_TOKEN_STORAGE_KEY);
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

  async function checkMintEligibility() {
    if (!walletAddress) return;
    if (!accessToken) {
      setEligibilitySummary("Please sign in again to refresh your secure session.");
      setEligibilityChecked(true);
      setCanMintNow(false);
      return;
    }
    setEligibilityLoading(true);
    setMintResultText("");
    try {
      const res = await fetch(buildApiUrl("/api/eligibility"), {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${accessToken}`,
        },
        body: JSON.stringify({ wallet: walletAddress }),
      });
      if (!res.ok) {
        if (res.status === 401) {
          setIsSignedIn(false);
          setAccessToken("");
          setProxyWallet(null);
          setTraderRank(null);
          setMyNfts([]);
          setMyNftsError("");
          setMyNftsFetchedAt(null);
          setMyCards([]);
          setMyCardsError("");
          setMyCardsFetchedAt(null);
          setGeneratedCardsTotalAvailable(0);
          setGeneratedCardsRemainingAvailable(0);
          setStatusText("Session expired. Please connect wallet again.");
          window.localStorage.removeItem(AUTH_TOKEN_STORAGE_KEY);
          clearStoredSessionMeta();
        }
        const text = await res.text();
        throw new Error(text || "Eligibility request failed");
      }
      const payload = (await res.json()) as EligibilityResponse;
      const canMintAny =
        Boolean(payload.double_mint?.can_claim_genesis) ||
        Boolean(payload.double_mint?.can_claim_standard);
      setCanMintNow(canMintAny);
      setEligibilityChecked(true);
      const resolvedProxyWallet = String(payload.proxy_wallet ?? "").trim() || proxyWallet;
      const resolvedTraderRank = String(payload.trader_rank ?? "").trim() || traderRank;
      setProxyWallet(resolvedProxyWallet);
      setTraderRank(resolvedTraderRank);
      saveStoredSessionMeta({
        walletAddress,
        selectedWalletName,
        signInCount,
        proxyWallet: resolvedProxyWallet ?? null,
        traderRank: resolvedTraderRank ?? null,
        challengeId,
      });

      const details = [
        `Connected wallet: ${walletAddress}`,
        `Proxy wallet (PM): ${String(payload.proxy_wallet ?? "Not found")}`,
        `Trader rank (overall/all by pnl): ${String(payload.trader_rank ?? "No trades yet")}`,
        `Eligibility wallet: ${String(payload.eligibility_wallet ?? walletAddress)}`,
        `Can mint now: ${canMintAny ? "YES" : "NO"}`,
        ...(payload.mint_block_reason ? [`Mint block reason: ${payload.mint_block_reason}`] : []),
        `Genesis: ${payload.genesis?.eligible_now ? "eligible" : `not eligible${payload.genesis?.ineligible_reason ? ` (${payload.genesis.ineligible_reason})` : ""}`}`,
        `Standard: ${payload.standard?.eligible_now ? "eligible" : `not eligible${payload.standard?.ineligible_reason ? ` (${payload.standard.ineligible_reason})` : ""}`}`,
      ];
      setEligibilitySummary(details.join("\n"));
    } catch (error) {
      setEligibilitySummary(`Eligibility check failed: ${extractErrorMessage(error)}`);
      setEligibilityChecked(true);
      setCanMintNow(false);
    } finally {
      setEligibilityLoading(false);
    }
  }

  async function mintOnBaseSepolia() {
    if (!accessToken) {
      setMintResultText("Mint failed: Please sign in again.");
      return;
    }
    setMintLoading(true);
    try {
      const res = await fetch(buildApiUrl("/api/mint/base-sepolia"), {
        method: "POST",
        headers: { Authorization: `Bearer ${accessToken}` },
      });
      if (!res.ok) {
        if (res.status === 401) {
          setIsSignedIn(false);
          setAccessToken("");
          setProxyWallet(null);
          setTraderRank(null);
          setMyNfts([]);
          setMyNftsError("");
          setMyNftsFetchedAt(null);
          setMyCards([]);
          setMyCardsError("");
          setMyCardsFetchedAt(null);
          setGeneratedCardsTotalAvailable(0);
          setGeneratedCardsRemainingAvailable(0);
          setStatusText("Session expired. Please connect wallet again.");
          window.localStorage.removeItem(AUTH_TOKEN_STORAGE_KEY);
          clearStoredSessionMeta();
        }
        const text = await res.text();
        throw new Error(text || "Mint failed");
      }
      const payload = (await res.json()) as {
        status: string;
        message: string;
        minted_count: number;
        minted_claims: Array<{
          stream: string;
          claim_id: number;
          chain: string;
          tx_hash: string;
          asset_address: string;
          season_id: number;
          phase: string;
        }>;
        failed_claims?: Array<{
          stream: string;
          season_id: number;
          reason: string;
        }>;
      };
      const successLines = payload.minted_claims.flatMap((claim) => [
        `- [${claim.stream}] claim_id=${claim.claim_id} season_id=${claim.season_id} phase=${claim.phase}`,
        `  chain=${claim.chain}`,
        `  tx_hash=${claim.tx_hash}`,
        `  asset_address=${claim.asset_address}`,
      ]);
      const failedLines = (payload.failed_claims ?? []).map(
        (item) => `- [${item.stream}] season_id=${item.season_id}: ${item.reason}`
      );
      setMintResultText(
        [
          payload.message || "Mint completed",
          `minted_count: ${payload.minted_count}`,
          "Minted claims:",
          ...successLines,
          ...(failedLines.length > 0 ? ["Failed claims:", ...failedLines] : []),
        ].join("\n")
      );
      await checkMintEligibility();
      await refreshMyNfts();
      await seasonsBoardRef.current?.refresh();
    } catch (error) {
      setMintResultText(`Mint failed: ${extractErrorMessage(error)}`);
    } finally {
      setMintLoading(false);
    }
  }

  async function getGeneratedCard() {
    if (!accessToken) {
      setGetCardResultText("Get card failed: Please sign in again.");
      return;
    }
    setGetCardLoading(true);
    setGetCardResultText("");
    try {
      const res = await fetch(buildApiUrl("/api/cards/get"), {
        method: "POST",
        headers: { Authorization: `Bearer ${accessToken}` },
      });
      if (!res.ok) {
        if (res.status === 401) {
          setIsSignedIn(false);
          setAccessToken("");
          setProxyWallet(null);
          setTraderRank(null);
          setMyNfts([]);
          setMyNftsError("");
          setMyNftsFetchedAt(null);
          setMyCards([]);
          setMyCardsError("");
          setMyCardsFetchedAt(null);
          setGeneratedCardsTotalAvailable(0);
          setGeneratedCardsRemainingAvailable(0);
          setStatusText("Session expired. Please connect wallet again.");
          window.localStorage.removeItem(AUTH_TOKEN_STORAGE_KEY);
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
              To receive NFT, you need to connect your wallet.
            </p>
          ) : (
            <div className="season-board-actions">
              <div className="season-board-stats">
                <span>Test cards remaining</span>
                <strong>
                  {generatedCardsRemainingAvailable} / {generatedCardsTotalAvailable}
                </strong>
              </div>
              <button onClick={() => void checkMintEligibility()} disabled={eligibilityLoading}>
                {eligibilityLoading ? "Checking..." : "Check mint eligibility"}
              </button>
              {eligibilityChecked ? (
                <button onClick={() => void mintOnBaseSepolia()} disabled={!canMintNow || mintLoading}>
                  {mintLoading ? "Minting on Base Sepolia..." : "Mint on Base Sepolia"}
                </button>
              ) : null}
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
              {eligibilitySummary ? (
                <pre className="eligibility-output">{eligibilitySummary}</pre>
              ) : null}
              {mintResultText ? (
                <pre className="eligibility-output">{mintResultText}</pre>
              ) : null}
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

      <section className="season-board season-board-standalone nft-board-horizontal">
        <div className="season-board-title">My NFT Collection (on-chain)</div>
        {!isSignedIn ? (
          <div className="season-board-muted">Sign in to load your collection.</div>
        ) : (
          <>
            <div className="nft-actions">
              <button onClick={() => void refreshMyNfts()} disabled={myNftsLoading}>
                {myNftsLoading ? "Loading NFT..." : "Reload my NFT"}
              </button>
              {myNftsFetchedAt ? (
                <span className="nft-fetched-at">
                  Updated: {new Date(myNftsFetchedAt).toLocaleString()}
                </span>
              ) : null}
            </div>
            {myNftsError ? (
              <pre className="eligibility-output">NFT load failed: {myNftsError}</pre>
            ) : null}
            {!myNftsLoading && !myNftsError && myNfts.length === 0 ? (
              <div className="season-board-muted">No NFT in this wallet yet.</div>
            ) : null}
            <div className="nft-grid-wrap">
              <div
                className="nft-grid"
                onMouseMove={(event) =>
                  handleCardGridMouseMove(event, {
                    wrapperSelector: ".nft-card-wrapper",
                    cardSelector: ".nft-card-tilt",
                  })
                }
                onMouseLeave={(event) => handleCardGridMouseLeave(event, ".nft-card-tilt")}
              >
                {myNfts.map((item) => {
                  return (
                    <div
                      key={item.token_id}
                      className="nft-card-wrapper"
                    >
                      <article className="nft-card nft-card-tilt theme-vivid">
                        {item.image_url ? (
                          // eslint-disable-next-line @next/next/no-img-element
                          <img className="nft-image" src={item.image_url} alt={item.name || `NFT ${item.token_id}`} />
                        ) : (
                          <div className="nft-image nft-image-empty">No preview</div>
                        )}
                        <div className="nft-card-body">
                          <strong>{item.name || `Token #${item.token_id}`}</strong>
                          <span>Token ID: {item.token_id}</span>
                          {Array.isArray(item.metadata?.attributes) && item.metadata!.attributes.length > 0 ? (
                            <div className="nft-attributes">
                              {item.metadata!.attributes.slice(0, 6).map((attr, index) => {
                                const trait = String(attr?.trait_type ?? "").trim() || "Attribute";
                                const valueRaw = attr?.value;
                                const value =
                                  valueRaw === null || valueRaw === undefined
                                    ? "N/A"
                                    : String(valueRaw);
                                return (
                                  <span className="nft-attr" key={`${item.token_id}:${trait}:${index}`}>
                                    {trait}: {value}
                                  </span>
                                );
                              })}
                            </div>
                          ) : null}
                          {item.explorer_url ? (
                            <a href={item.explorer_url} target="_blank" rel="noreferrer">
                              Open in Blockscout
                            </a>
                          ) : null}
                        </div>
                      </article>
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
