"use client";

import { useMemo, useState } from "react";

declare global {
  interface Window {
    ethereum?: {
      request: (args: { method: string; params?: unknown[] }) => Promise<unknown>;
      isMetaMask?: boolean;
    };
  }
}

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
  if (apiBase === "/") {
    return path;
  }
  return `${apiBase.replace(/\/$/, "")}${path}`;
}

export default function HomePage() {
  const [walletAddress, setWalletAddress] = useState("");
  const [challengeId, setChallengeId] = useState("");
  const [statusText, setStatusText] = useState("Not signed in");
  const [isSignedIn, setIsSignedIn] = useState(false);
  const [isBusy, setIsBusy] = useState(false);
  const [signInCount, setSignInCount] = useState<number | null>(null);

  const shortAddress = useMemo(() => {
    if (!walletAddress) {
      return "";
    }
    return `${walletAddress.slice(0, 6)}...${walletAddress.slice(-4)}`;
  }, [walletAddress]);

  async function connectWallet() {
    if (!window.ethereum) {
      setStatusText("MetaMask (or compatible wallet) is not available");
      return;
    }
    setIsBusy(true);
    try {
      const accounts = (await window.ethereum.request({
        method: "eth_requestAccounts",
      })) as string[];
      if (!accounts || accounts.length === 0) {
        setStatusText("Wallet connection cancelled");
        return;
      }
      setWalletAddress(accounts[0]);
      setStatusText("Wallet connected");
      setIsSignedIn(false);
      setChallengeId("");
      setSignInCount(null);
    } catch (error) {
      setStatusText(`Connection failed: ${String(error)}`);
    } finally {
      setIsBusy(false);
    }
  }

  async function signIn() {
    if (!window.ethereum) {
      setStatusText("Wallet provider is not available");
      return;
    }
    if (!walletAddress) {
      setStatusText("Connect wallet first");
      return;
    }

    setIsBusy(true);
    try {
      const challengeRes = await fetch(buildApiUrl("/api/auth/wallet/challenge"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ wallet_address: walletAddress }),
      });
      if (!challengeRes.ok) {
        const challengeErr = await challengeRes.text();
        throw new Error(`Challenge failed: ${challengeErr}`);
      }
      const challenge = (await challengeRes.json()) as ChallengeResponse;
      setChallengeId(challenge.challenge_id);

      const signature = (await window.ethereum.request({
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
    <main>
      <h1>PolyStars User Sign-in</h1>
      <p>Connect an EVM wallet (MetaMask and compatible), sign challenge, and verify on backend.</p>

      <div className="stack">
        <button onClick={connectWallet} disabled={isBusy}>
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
  );
}
