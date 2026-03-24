"use client";

import { useEffect, useMemo, useRef, useState } from "react";

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

type SeasonResponse = {
  id: number;
  type: string;
  season_number: number;
  title: string;
  short_description: string;
  total_supply: number;
  remaining_supply: number;
  end_date: string | null;
  is_active: boolean;
  phase: string;
  phase_reason: string;
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
  is_origin_wallet: boolean;
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

type WalletTickerResponse = {
  wallets: string[];
  total: number;
  fetched_at: string;
};

const apiBase =
  process.env.NEXT_PUBLIC_USER_API_BASE_URL ??
  (process.env.NODE_ENV === "development" ? "http://localhost:8011" : "/");
const AUTH_TOKEN_STORAGE_KEY = "polystars_user_access_token";
const AUTH_SESSION_META_STORAGE_KEY = "polystars_user_session_meta";

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

export default function HomePage() {
  const [walletAddress, setWalletAddress] = useState("");
  const [challengeId, setChallengeId] = useState("");
  const [statusText, setStatusText] = useState("Not signed in");
  const [isSignedIn, setIsSignedIn] = useState(false);
  const [isBusy, setIsBusy] = useState(false);
  const [signInCount, setSignInCount] = useState<number | null>(null);
  const [activeSeasons, setActiveSeasons] = useState<SeasonResponse[]>([]);
  const [seasonError, setSeasonError] = useState("");
  const [serverNowBaseMs, setServerNowBaseMs] = useState<number | null>(null);
  const [clientNowAtSyncMs, setClientNowAtSyncMs] = useState<number | null>(null);
  const [syncedNowMs, setSyncedNowMs] = useState<number>(() => Date.now());
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
  const [tickerWallets, setTickerWallets] = useState<string[]>([]);
  const [isWalletButtonHovered, setIsWalletButtonHovered] = useState(false);

  // Wallet that was selected by the user in the picker — used for sign-in
  const selectedProviderRef = useRef<EthProvider | null>(null);
  const [selectedWalletName, setSelectedWalletName] = useState<string | null>(null);

  // EIP-6963 discovered providers
  const eip6963Ref = useRef<EIP6963ProviderDetail[]>([]);
  const [eip6963Providers, setEip6963Providers] = useState<EIP6963ProviderDetail[]>([]);

  // Picker modal visibility
  const [showPicker, setShowPicker] = useState(false);

  async function refreshSeasonsFromApi() {
    try {
      setSeasonError("");
      const timeRes = await fetch(buildApiUrl("/api/server-time"));
      if (timeRes.ok) {
        const timePayload = (await timeRes.json()) as { now_utc_iso?: string };
        const parsedMs = Date.parse(String(timePayload.now_utc_iso ?? ""));
        if (!Number.isNaN(parsedMs)) {
          setServerNowBaseMs(parsedMs);
          setClientNowAtSyncMs(Date.now());
          setSyncedNowMs(parsedMs);
        }
      }

      const res = await fetch(buildApiUrl("/api/seasons/active"));
      if (!res.ok) {
        const text = await res.text();
        throw new Error(text || "Failed to load seasons");
      }
      const allSeasons = (await res.json()) as SeasonResponse[];
      setActiveSeasons(allSeasons);
    } catch (error) {
      setSeasonError(extractErrorMessage(error));
    }
  }

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
    void refreshSeasonsFromApi();
  }, []);

  useEffect(() => {
    if (serverNowBaseMs == null || clientNowAtSyncMs == null) return;
    const tick = window.setInterval(() => {
      setSyncedNowMs(serverNowBaseMs + (Date.now() - clientNowAtSyncMs));
    }, 1000);
    return () => window.clearInterval(tick);
  }, [serverNowBaseMs, clientNowAtSyncMs]);

  useEffect(() => {
    void refreshWalletTicker();
  }, []);

  useEffect(() => {
    if (!isSignedIn || !accessToken) {
      setMyNfts([]);
      setMyNftsError("");
      setMyNftsFetchedAt(null);
      return;
    }
    void refreshMyNfts();
  }, [isSignedIn, accessToken, walletAddress]); // eslint-disable-line react-hooks/exhaustive-deps

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
  const seasonCards = useMemo(() => {
    return activeSeasons.map((season) => {
      const seasonName = season.title;
      const description = season.short_description || "Active season.";
      const total = Number(season.total_supply) || 0;
      const remaining = Number(season.remaining_supply) || 0;

      let timeLeft = "No end date";
      if (season.type !== "genesis" && season.end_date) {
        const endMs = Date.parse(season.end_date);
        if (!Number.isNaN(endMs)) {
          const diffSec = Math.floor((endMs - syncedNowMs) / 1000);
          if (diffSec <= 0) {
            timeLeft = "Ended";
          } else {
            const days = Math.floor(diffSec / 86400);
            const hours = Math.floor((diffSec % 86400) / 3600);
            const mins = Math.floor((diffSec % 3600) / 60);
            const secs = diffSec % 60;
            timeLeft = days > 0 ? `${days}d ${hours}h ${mins}m` : `${hours}h ${mins}m ${secs}s`;
          }
        }
      }

      return {
        id: season.id,
        name: seasonName,
        description,
        timeLeft,
        remaining,
        total,
        phase: season.phase || "unknown",
        phaseReason: season.phase_reason || "",
      };
    });
  }, [activeSeasons, syncedNowMs]);

  function extractErrorMessage(error: unknown): string {
    if (error instanceof Error) return error.message;
    if (typeof error === "object" && error !== null && "message" in error) {
      return String((error as { message: unknown }).message);
    }
    return JSON.stringify(error);
  }

  function updateNftCardTilt(target: HTMLElement, clientX: number, clientY: number) {
    const rect = target.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    const relativeX = Math.min(1, Math.max(0, (clientX - rect.left) / rect.width));
    const relativeY = Math.min(1, Math.max(0, (clientY - rect.top) / rect.height));
    const MAX_TILT_X = 20;
    const MAX_TILT_Y = 20;
    const rotateY = (relativeX - 0.5) * (MAX_TILT_Y * 2);
    const rotateX = (0.5 - relativeY) * (MAX_TILT_X * 2);

    target.classList.add("nft-card-active");
    target.parentElement?.classList.add("nft-card-wrapper-active");
    target.style.setProperty("--nft-tilt-x", `${rotateX.toFixed(2)}deg`);
    target.style.setProperty("--nft-tilt-y", `${rotateY.toFixed(2)}deg`);
    target.style.setProperty("--pointer-x", `${(relativeX * 100).toFixed(2)}%`);
    target.style.setProperty("--pointer-y", `${(relativeY * 100).toFixed(2)}%`);
  }

  function resetNftCardTilt(target: HTMLElement) {
    target.classList.remove("nft-card-active");
    target.parentElement?.classList.remove("nft-card-wrapper-active");
    target.style.setProperty("--nft-tilt-x", "0deg");
    target.style.setProperty("--nft-tilt-y", "0deg");
    target.style.setProperty("--pointer-x", "50%");
    target.style.setProperty("--pointer-y", "50%");
  }

  function handleNftGridMouseMove(event: React.MouseEvent<HTMLDivElement>) {
    const PROXIMITY_PX = 10;
    const clientX = event.clientX;
    const clientY = event.clientY;
    const wrappers = event.currentTarget.querySelectorAll<HTMLElement>(".nft-card-wrapper");

    wrappers.forEach((wrapper) => {
      const card = wrapper.querySelector<HTMLElement>(".nft-card-tilt");
      if (!card) return;
      // Use the transformed card bounds so scaled edges remain interactive.
      const rect = card.getBoundingClientRect();
      const isWithinProximity =
        clientX >= rect.left - PROXIMITY_PX &&
        clientX <= rect.right + PROXIMITY_PX &&
        clientY >= rect.top - PROXIMITY_PX &&
        clientY <= rect.bottom + PROXIMITY_PX;

      if (!isWithinProximity) {
        resetNftCardTilt(card);
        return;
      }

      const clampedX = Math.min(rect.right, Math.max(rect.left, clientX));
      const clampedY = Math.min(rect.bottom, Math.max(rect.top, clientY));
      updateNftCardTilt(card, clampedX, clampedY);
    });
  }

  function handleNftGridMouseLeave(event: React.MouseEvent<HTMLDivElement>) {
    const cards = event.currentTarget.querySelectorAll<HTMLElement>(".nft-card-tilt");
    cards.forEach((card) => resetNftCardTilt(card));
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
          setMyNftsFetchedAt(null);
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

  async function refreshWalletTicker() {
    try {
      const res = await fetch(buildApiUrl("/api/wallet-ticker?limit=100"));
      if (!res.ok) return;
      const payload = (await res.json()) as WalletTickerResponse;
      const wallets = Array.isArray(payload.wallets) ? payload.wallets : [];
      setTickerWallets(wallets);
    } catch {
      setTickerWallets([]);
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
    setEligibilitySummary("");
    setEligibilityChecked(false);
    setCanMintNow(false);
    setMintResultText("");
    setStatusText("Logged out");
    setIsWalletButtonHovered(false);
    selectedProviderRef.current = null;
    window.localStorage.removeItem(AUTH_TOKEN_STORAGE_KEY);
    clearStoredSessionMeta();
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
      setEligibilityChecked(false);
      setCanMintNow(false);
      setMintResultText("");
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
      setEligibilityChecked(false);
      setCanMintNow(false);
      setMintResultText("");
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
      await refreshSeasonsFromApi();
    } catch (error) {
      setMintResultText(`Mint failed: ${extractErrorMessage(error)}`);
    } finally {
      setMintLoading(false);
    }
  }

  return (
    <>
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

      <aside className="auth-info-panel" aria-live="polite">
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

      <main>
        <h1>PS DEVTEST User Sign-in</h1>
        <p>
          Connect an EVM wallet, sign a challenge, and verify on backend.
        </p>
      </main>

      <section className="season-board season-board-standalone">
        <div className="season-board-title">Active seasons</div>
        {seasonError ? (
          <div className="season-board-muted">Unable to load seasons right now.</div>
        ) : null}
        {seasonCards.length === 0 && !seasonError ? (
          <div className="season-board-muted">No active seasons right now.</div>
        ) : (
          <div className="season-list">
            {seasonCards.map((season) => (
              <article key={season.id} className="season-card">
                <div className="season-card-top">
                  <strong>{season.name}</strong>
                  <span>{season.timeLeft}</span>
                </div>
                  <div className="season-card-phase">
                    <span>Phase</span>
                    <strong>{season.phase}</strong>
                  </div>
                <div className="season-card-bottom">
                  <span>NFT left</span>
                  <strong>{season.remaining} / {season.total}</strong>
                </div>
                  <div className="season-tooltip">
                    {season.description}
                    {season.phaseReason ? ` ${season.phaseReason}` : ""}
                  </div>
              </article>
            ))}
          </div>
        )}
        {!isSignedIn ? (
          <p className="season-board-note">
            To receive NFT, you need to connect your wallet.
          </p>
        ) : (
          <div className="season-board-actions">
            <button onClick={() => void checkMintEligibility()} disabled={eligibilityLoading}>
              {eligibilityLoading ? "Checking..." : "Check mint eligibility"}
            </button>
            {eligibilityChecked ? (
              <button onClick={() => void mintOnBaseSepolia()} disabled={!canMintNow || mintLoading}>
                {mintLoading ? "Minting on Base Sepolia..." : "Mint on Base Sepolia"}
              </button>
            ) : null}
            {eligibilitySummary ? (
              <pre className="eligibility-output">{eligibilitySummary}</pre>
            ) : null}
            {mintResultText ? (
              <pre className="eligibility-output">{mintResultText}</pre>
            ) : null}
          </div>
        )}
      </section>

      {tickerWallets.length > 0 ? (
        <section className="wallet-ticker-strip" aria-label="Winner wallets ticker">
          <div className="wallet-ticker-label">origins wallets:</div>
          <div className="wallet-ticker-left-fade" />
          <div className="wallet-ticker-viewport">
            <div className="wallet-ticker-track">
              {[...tickerWallets, ...tickerWallets].map((wallet, index) => (
                <span className="wallet-ticker-item" key={`${wallet}-${index}`}>
                  {wallet}
                </span>
              ))}
            </div>
          </div>
        </section>
      ) : null}

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
                onMouseMove={handleNftGridMouseMove}
                onMouseLeave={handleNftGridMouseLeave}
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
