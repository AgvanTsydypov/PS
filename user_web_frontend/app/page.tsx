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
};

const apiBase = process.env.NEXT_PUBLIC_USER_API_BASE_URL ?? "/";

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

  function extractErrorMessage(error: unknown): string {
    if (error instanceof Error) return error.message;
    if (typeof error === "object" && error !== null && "message" in error) {
      return String((error as { message: unknown }).message);
    }
    return JSON.stringify(error);
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
      selectedProviderRef.current = provider;
      setSelectedWalletName(name);
      setWalletAddress(accounts[0]);
      setStatusText("Wallet connected");
      setIsSignedIn(false);
      setChallengeId("");
      setSignInCount(null);
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

  async function signIn() {
    const provider = selectedProviderRef.current;
    if (!provider) {
      setStatusText("Connect wallet first");
      return;
    }
    if (!walletAddress) {
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
          body: JSON.stringify({ wallet_address: walletAddress }),
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
        params: [challenge.message, walletAddress],
      })) as string;

      const verifyRes = await fetch(buildApiUrl("/api/auth/wallet/verify"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          challenge_id: challenge.challenge_id,
          wallet_address: walletAddress,
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
    } catch (error) {
      setIsSignedIn(false);
      setStatusText(String(error));
    } finally {
      setIsBusy(false);
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

      <main>
        <h1>PolyStars User Sign-in</h1>
        <p>
          Connect an EVM wallet, sign a challenge, and verify on backend.
        </p>

        {selectedWalletName && walletAddress && (
          <div className="card">
            <strong>Connected via:</strong> {selectedWalletName}
          </div>
        )}

        <div className="stack">
          <button onClick={() => setShowPicker(true)} disabled={isBusy}>
            Connect wallet
          </button>
          <button onClick={signIn} disabled={isBusy || !walletAddress}>
            Sign in
          </button>
        </div>

        <div className="card">
          <strong>Status:</strong> {statusText}
        </div>

        <div className="card">
          <strong>Wallet address:</strong> {walletAddress || "Not connected"}
          {walletAddress ? <div>Short: {shortAddress}</div> : null}
        </div>

        <div className="card">
          <strong>Challenge ID:</strong> {challengeId || "No active challenge"}
        </div>

        <div className="card">
          <strong>Sign-in count:</strong> {signInCount ?? "N/A"}
        </div>

        {isSignedIn ? (
          <div className="card">
            <strong>Signed in</strong>
          </div>
        ) : null}
      </main>
    </>
  );
}
