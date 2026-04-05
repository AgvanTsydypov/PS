"use client";

import { useEffect, useState } from "react";

const apiBase =
  process.env.NEXT_PUBLIC_USER_API_BASE_URL ??
  (process.env.NODE_ENV === "development" ? "http://localhost:8011" : "/");

function buildApiUrl(path: string): string {
  if (apiBase === "/") return path;
  return `${apiBase.replace(/\/$/, "")}${path}`;
}

type WalletTickerResponse = {
  wallets: string[];
  total: number;
  fetched_at: string;
};

export default function OriginsWalletTicker() {
  const [tickerWallets, setTickerWallets] = useState<string[]>([]);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const res = await fetch(buildApiUrl("/api/wallet-ticker?limit=100"));
        if (!res.ok) return;
        const payload = (await res.json()) as WalletTickerResponse;
        const wallets = Array.isArray(payload.wallets) ? payload.wallets : [];
        if (!cancelled) setTickerWallets(wallets);
      } catch {
        if (!cancelled) setTickerWallets([]);
      }
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, []);

  if (tickerWallets.length === 0) return null;

  return (
    <section className="wallet-ticker-strip" aria-label="Winner wallets ticker">
      <div className="wallet-ticker-label">Origin wallets:</div>
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
  );
}
