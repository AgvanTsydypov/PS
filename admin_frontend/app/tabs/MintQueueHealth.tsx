"use client";

import { useEffect, useState } from "react";
import { fetchJSON } from "../lib/api";

// ─────────────────────────────────────────────────────────────────────────────
// Types — mirror admin_backend.main:mint_queue_health() response shape.
// Each sub-block degrades independently (ok:false on its own), so the widget
// must render partial state without crashing if any one source is down.
// ─────────────────────────────────────────────────────────────────────────────

type MintQueueCounts = {
  queued: number;
  processing: number;
  processing_with_rbf: number;
  stuck: number;
  completed_today: number;
  failed_today: number;
};

type HotWalletBlock =
  | { ok: true; address: string; eth_balance: number; fetched_at: string }
  | { ok: false; error: string; fetched_at: string };

type GasBlock =
  | {
      ok: true;
      rapid_gwei: number;
      rapid_usd_per_mint: number;
      safe_gwei: number;
      safe_usd_per_mint: number;
      eth_usd: number;
      fetched_at: string;
    }
  | { ok: false; error: string };

type HealthPayload = {
  counts: MintQueueCounts;
  last_mint_at: string | null;
  hot_wallet: HotWalletBlock;
  gas: GasBlock;
  fetched_at: string;
  counts_error?: string;
};

// ─────────────────────────────────────────────────────────────────────────────
// Thresholds for color-coding. Tuned for the production economics:
//   * 0.05 ETH ≈ ~250 mints worth of headroom at safe-tier on mainnet,
//     so anything below that is "refill soon".
//   * 0.01 ETH is the "you have minutes, not hours" line.
//   * Stuck > 0 is always red (any badged claim wants human attention).
// ─────────────────────────────────────────────────────────────────────────────

const WALLET_LOW_ETH = 0.05;
const WALLET_CRITICAL_ETH = 0.01;
const POLL_INTERVAL_MS = 30_000;

function fmtEth(eth: number): string {
  if (eth >= 1) return eth.toFixed(4);
  if (eth >= 0.001) return eth.toFixed(5);
  return eth.toExponential(2);
}

function fmtUsd(n: number | null | undefined): string {
  if (n == null) return "—";
  if (n >= 100) return `$${n.toFixed(0)}`;
  if (n >= 1) return `$${n.toFixed(2)}`;
  return `$${n.toFixed(3)}`;
}

function fmtRelative(iso: string | null): string {
  if (!iso) return "never";
  const ts = Date.parse(iso);
  if (Number.isNaN(ts)) return "—";
  const deltaSec = Math.max(0, Math.round((Date.now() - ts) / 1000));
  if (deltaSec < 60) return `${deltaSec}s ago`;
  if (deltaSec < 3600) return `${Math.floor(deltaSec / 60)}m ago`;
  if (deltaSec < 86400) return `${Math.floor(deltaSec / 3600)}h ago`;
  return `${Math.floor(deltaSec / 86400)}d ago`;
}

function walletColorClass(eth: number): string {
  // Reuse the existing supply-meter palette so the widget visually matches
  // the rest of the admin UI without introducing new CSS.
  if (eth < WALLET_CRITICAL_ETH) return "supply-meter-red";
  if (eth < WALLET_LOW_ETH) return "supply-meter-yellow";
  return "supply-meter-green";
}

function gasColorClass(gwei: number): string {
  if (gwei >= 50) return "supply-meter-red";
  if (gwei >= 15) return "supply-meter-yellow";
  return "supply-meter-green";
}

function stuckColorClass(stuck: number): string {
  if (stuck > 0) return "supply-meter-red";
  return "supply-meter-green";
}

// ─────────────────────────────────────────────────────────────────────────────
// Component
//
// Self-polling: kicks an initial fetch on mount, then every 30s. The poll is
// independent of the parent's other refreshers — we don't want operator
// visibility into the queue to be coupled to whether they happened to click
// some other button. 30s matches the cron tick cadence (5 min) coarsely
// enough that the operator sees state transitions as they happen but
// doesn't burn the RPC quota with sub-second polling.
// ─────────────────────────────────────────────────────────────────────────────

export function MintQueueHealth() {
  const [data, setData] = useState<HealthPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function fetchOnce() {
      try {
        const payload = await fetchJSON<HealthPayload>("/api/mint-queue/health");
        if (!cancelled) {
          setData(payload);
          setError(null);
        }
      } catch (e) {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : String(e));
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    void fetchOnce();
    const handle = window.setInterval(() => void fetchOnce(), POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(handle);
    };
  }, []);

  if (loading && !data) {
    return (
      <section className="panel">
        <div className="muted">Mint queue · loading…</div>
      </section>
    );
  }

  if (error && !data) {
    return (
      <section className="panel">
        <div className="muted">Mint queue · health unavailable: {error}</div>
      </section>
    );
  }

  if (!data) return null;

  const { counts, last_mint_at, hot_wallet, gas } = data;

  return (
    <section className="panel">
      <div className="row" style={{ alignItems: "baseline", gap: "0.75rem" }}>
        <strong>Mint queue</strong>
        <span className="muted">refreshed {fmtRelative(data.fetched_at)}</span>
      </div>

      <div
        className="row"
        style={{ flexWrap: "wrap", gap: "1.25rem", marginTop: "0.5rem" }}
      >
        {/* ── Hot wallet ──────────────────────────────────────────────── */}
        <div>
          <div className="muted">Hot wallet</div>
          {hot_wallet.ok ? (
            <div>
              <span className={walletColorClass(hot_wallet.eth_balance)}>
                {fmtEth(hot_wallet.eth_balance)} ETH
              </span>
              {gas.ok ? (
                <span className="muted">
                  {" "}({fmtUsd(hot_wallet.eth_balance * gas.eth_usd)})
                </span>
              ) : null}
              {hot_wallet.eth_balance < WALLET_LOW_ETH ? (
                <span style={{ marginLeft: "0.4rem" }} className="supply-meter-yellow">
                  ⚠ low
                </span>
              ) : null}
            </div>
          ) : (
            <div className="muted">unavailable: {hot_wallet.error}</div>
          )}
        </div>

        {/* ── Gas ─────────────────────────────────────────────────────── */}
        <div>
          <div className="muted">Gas (rapid)</div>
          {gas.ok ? (
            <div>
              <span className={gasColorClass(gas.rapid_gwei)}>
                {gas.rapid_gwei.toFixed(1)} gwei
              </span>
              <span className="muted"> ({fmtUsd(gas.rapid_usd_per_mint)}/mint)</span>
            </div>
          ) : (
            <div className="muted">cold cache · open Gas Tracker to warm</div>
          )}
        </div>

        {/* ── Status counts ───────────────────────────────────────────── */}
        <div>
          <div className="muted">Status</div>
          <div>
            <span title="Awaiting pickup">QUEUED {counts.queued}</span>
            {"  ·  "}
            <span title="Currently being minted">
              PROCESSING {counts.processing}
            </span>
            {counts.processing_with_rbf > 0 ? (
              <span
                className="supply-meter-yellow"
                style={{ marginLeft: "0.3rem" }}
                title="Of which doing RBF bumps right now"
              >
                (RBF {counts.processing_with_rbf})
              </span>
            ) : null}
            {"  ·  "}
            <span
              className={stuckColorClass(counts.stuck)}
              title="Hit the [stuck:…] guard — needs operator attention"
            >
              STUCK {counts.stuck}
              {counts.stuck > 0 ? " 🔥" : ""}
            </span>
          </div>
        </div>

        {/* ── Today's throughput ──────────────────────────────────────── */}
        <div>
          <div className="muted">Last 24h</div>
          <div>
            <span className="ok">{counts.completed_today} minted</span>
            {counts.failed_today > 0 ? (
              <span className="supply-meter-red"> · {counts.failed_today} failed</span>
            ) : null}
            <span className="muted"> · last mint {fmtRelative(last_mint_at)}</span>
          </div>
        </div>
      </div>

      {/* Counts query failure is non-fatal but worth surfacing — the rest
          of the widget would render zeros which would be misleading. */}
      {data.counts_error ? (
        <div className="muted" style={{ marginTop: "0.4rem" }}>
          counts query degraded: {data.counts_error}
        </div>
      ) : null}
    </section>
  );
}
