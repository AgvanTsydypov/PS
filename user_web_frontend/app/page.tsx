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
  access_token?: string;
  token_type?: string;
  expires_in?: number;
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
  eligibility_wallet?: string;
  eligibility_mode?: string;
  is_origin_wallet: boolean;
  genesis: EligibilityStream;
  standard: EligibilityStream;
  double_mint: {
    can_claim_genesis: boolean;
    can_claim_standard: boolean;
    can_claim_both_now: boolean;
  };
};

const apiBase =
  process.env.NEXT_PUBLIC_USER_API_BASE_URL ??
  (process.env.NODE_ENV === "development" ? "http://localhost:8011" : "/");
const AUTH_TOKEN_STORAGE_KEY = "polystars_user_access_token";

function buildApiUrl(path: string): string {
  if (apiBase === "/") return path;
  return `${apiBase.replace(/\/$/, "")}${path}`;
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

  // Wallet that was selected by the user in the picker — used for sign-in
  const selectedProviderRef = useRef<EthProvider | null>(null);
  const [selectedWalletName, setSelectedWalletName] = useState<string | null>(null);

  // EIP-6963 discovered providers
  const eip6963Ref = useRef<EIP6963ProviderDetail[]>([]);
  const [eip6963Providers, setEip6963Providers] = useState<EIP6963ProviderDetail[]>([]);

  // Picker modal visibility
  const [showPicker, setShowPicker] = useState(false);

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
    if (token) setAccessToken(token);
  }, []);

  useEffect(() => {
    async function loadSeasons() {
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

    void loadSeasons();
  }, []);

  useEffect(() => {
    if (serverNowBaseMs == null || clientNowAtSyncMs == null) return;
    const tick = window.setInterval(() => {
      setSyncedNowMs(serverNowBaseMs + (Date.now() - clientNowAtSyncMs));
    }, 1000);
    return () => window.clearInterval(tick);
  }, [serverNowBaseMs, clientNowAtSyncMs]);

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
    if (isSignedIn && shortAddress) return shortAddress;
    return "Connect wallet";
  }, [isBusy, isSignedIn, shortAddress]);
  const authHintText = useMemo(() => {
    if (isBusy) return "Approve wallet connection and signature in your wallet.";
    if (isSignedIn) return "Wallet connected. Click to switch wallet.";
    const lowered = statusText.toLowerCase();
    if (lowered.includes("failed") || lowered.includes("cancelled")) return statusText;
    return "Connect wallet to sign in and continue.";
  }, [isBusy, isSignedIn, statusText]);
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

  async function signInWith(provider: EthProvider, address: string) {
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
      setProxyWallet(null);
      const token = String(verify.access_token ?? "");
      if (verify.signed_in && token) {
        setAccessToken(token);
        window.localStorage.setItem(AUTH_TOKEN_STORAGE_KEY, token);
        setProxyWallet(String(verify.proxy_wallet ?? "").trim() || null);
      } else {
        setAccessToken("");
        window.localStorage.removeItem(AUTH_TOKEN_STORAGE_KEY);
        setProxyWallet(null);
      }
    } catch (error) {
      setIsSignedIn(false);
      setStatusText(extractErrorMessage(error));
      setAccessToken("");
      setProxyWallet(null);
      setEligibilityChecked(false);
      setCanMintNow(false);
      setMintResultText("");
      window.localStorage.removeItem(AUTH_TOKEN_STORAGE_KEY);
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
      setEligibilityChecked(false);
      setCanMintNow(false);
      setMintResultText("");
      window.localStorage.removeItem(AUTH_TOKEN_STORAGE_KEY);
      await signInWith(provider, address);
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
        const text = await res.text();
        throw new Error(text || "Eligibility request failed");
      }
      const payload = (await res.json()) as EligibilityResponse;
      const canMintAny =
        Boolean(payload.double_mint?.can_claim_genesis) ||
        Boolean(payload.double_mint?.can_claim_standard);
      setCanMintNow(canMintAny);
      setEligibilityChecked(true);
      setProxyWallet(String(payload.proxy_wallet ?? "").trim() || proxyWallet);

      const details = [
        `Connected wallet: ${walletAddress}`,
        `Proxy wallet (PM): ${String(payload.proxy_wallet ?? "Not found")}`,
        `Eligibility wallet: ${String(payload.eligibility_wallet ?? walletAddress)}`,
        payload.eligibility_mode === "connected_wallet_fallback"
          ? "Eligibility mode: fallback to connected wallet (PM proxy missing)"
          : "Eligibility mode: proxy wallet",
        `Can mint now: ${canMintAny ? "YES" : "NO"}`,
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
          onClick={() => setShowPicker(true)}
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
    </>
  );
}
