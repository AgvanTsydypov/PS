"use client";

import { useEffect, useMemo, useState } from "react";
import { fetchJSON } from "../lib/api";

type LocalCountdownItem = { label: string; value: string };

type ClaimsMintableSummary = {
  total_mintable: number;
  remaining_mintable: number;
  total_supply: number;
  minted_active: number;
  per_event_cap: number | null;
  top_event_count: number;
  status_counts: {
    queued: number;
    pending: number;
    processing: number;
    completed: number;
    failed: number;
  };
};

type GasTracker =
  | {
      ok: true;
      fetched_at: string;
      cache_ttl_seconds: number;
      api_key_present: boolean;
      gas_estimate: number;
      base_fee_gwei: number;
      safe_gwei: number;
      propose_gwei: number;
      rapid_gwei: number;
      eth_usd: number;
      safe_eth: number;
      safe_usd: number;
      propose_eth: number;
      propose_usd: number;
      rapid_eth: number;
      rapid_usd: number;
    }
  | { ok: false; error: string; fetched_at: string };

function fmtGwei(n: number): string {
  if (n >= 100) return n.toFixed(0);
  if (n >= 10) return n.toFixed(1);
  if (n >= 1) return n.toFixed(2);
  return n.toFixed(3);
}

function fmtEthSmall(n: number): string {
  if (n >= 0.01) return n.toFixed(4);
  if (n >= 0.0001) return n.toFixed(6);
  return n.toExponential(2);
}

function fmtUsd(n: number): string {
  if (n >= 100) return `$${n.toFixed(0)}`;
  if (n >= 1) return `$${n.toFixed(2)}`;
  return `$${n.toFixed(3)}`;
}

function gwei_class(gwei: number): string {
  if (gwei >= 50) return "supply-meter-red";
  if (gwei >= 15) return "supply-meter-yellow";
  return "supply-meter-green";
}

function pctClass(numerator: number, denominator: number | null | undefined): string {
  if (!denominator || denominator <= 0) return "supply-meter-zero";
  const pct = (numerator / denominator) * 100;
  if (pct >= 90) return "supply-meter-red";
  if (pct >= 70) return "supply-meter-yellow";
  return "supply-meter-green";
}

function fmtPct(numerator: number, denominator: number | null | undefined): string {
  if (!denominator || denominator <= 0) return "—";
  return `${((numerator / denominator) * 100).toFixed(1)}%`;
}

function formatInt(n: number): string {
  return n.toLocaleString("en-US");
}

function parseUtcDisplayDate(raw: string): Date | null {
  const value = raw.trim();
  if (!value) return null;
  const normalized = value.endsWith(" UTC")
    ? value.replace(" UTC", "Z").replace(" ", "T")
    : value;
  const ts = Date.parse(normalized);
  if (Number.isNaN(ts)) return null;
  return new Date(ts);
}

function formatDuration(totalSeconds: number): string {
  if (totalSeconds <= 0) return "0s";
  const whole = Math.floor(totalSeconds);
  const days = Math.floor(whole / 86400);
  const hours = Math.floor((whole % 86400) / 3600);
  const minutes = Math.floor((whole % 3600) / 60);
  const seconds = whole % 60;
  const parts: string[] = [];
  if (days) parts.push(`${days}d`);
  if (hours) parts.push(`${hours}h`);
  if (minutes || parts.length > 0) parts.push(`${minutes}m`);
  parts.push(`${seconds}s`);
  return parts.join(" ");
}

function seasonInfoLineClass(line: string): string {
  const trimmed = line.trim();
  if (!trimmed) return "";
  if (trimmed.startsWith("Window:")) return "season-info-blue";
  if (trimmed.includes("- season_alive_for:")) return "ok";
  if (trimmed.startsWith("- time_to_")) return "ok";
  return "";
}

interface ClaimsMintProps {
  seasonOptions: { value: number; label: string }[];
  claimSeasonId: number;
  claimWallet: string;
  wallets: string[];
  walletFilter: string;
  walletsLoading: boolean;
  onSeasonIdChange: (id: number) => void;
  onWalletChange: (w: string) => void;
  onWalletFilterChange: (f: string) => void;
  run: (fn: () => Promise<void>) => Promise<void>;
  refreshSeasonClaims: () => Promise<void>;
  refreshOverview: () => Promise<void>;
}

export function ClaimsMint({
  seasonOptions,
  claimSeasonId,
  claimWallet,
  wallets,
  walletFilter,
  walletsLoading,
  onSeasonIdChange,
  onWalletChange,
  onWalletFilterChange,
  run,
  refreshSeasonClaims,
  refreshOverview,
}: ClaimsMintProps) {
  const [claimPhase, setClaimPhase] = useState("breach");
  const [claimAutoPhase, setClaimAutoPhase] = useState(true);
  const [claimDbOnly, setClaimDbOnly] = useState(false);
const [claimRecipient, setClaimRecipient] = useState("");
  const [claimSeasonInfo, setClaimSeasonInfo] = useState("");
  const [claimOutput, setClaimOutput] = useState("");
  const [claimMinting, setClaimMinting] = useState(false);
  const [mintQueueRunning, setMintQueueRunning] = useState(false);
  const [claimMintableSummary, setClaimMintableSummary] = useState<ClaimsMintableSummary | null>(null);
  const claimMintableTotal = claimMintableSummary?.total_mintable ?? null;
  const claimMintableRemaining = claimMintableSummary?.remaining_mintable ?? null;
  const [serverNowBaseMs, setServerNowBaseMs] = useState<number | null>(null);
  const [clientNowAtSyncMs, setClientNowAtSyncMs] = useState<number | null>(null);
  const [syncedNowMs, setSyncedNowMs] = useState<number | null>(null);
  const [gasTracker, setGasTracker] = useState<GasTracker | null>(null);
  const [gasTrackerLoading, setGasTrackerLoading] = useState(false);

  const refreshGasTracker = async () => {
    setGasTrackerLoading(true);
    try {
      const data = await fetchJSON<GasTracker>("/api/gas-tracker/eth-mint");
      setGasTracker(data);
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e);
      setGasTracker({ ok: false, error: message, fetched_at: new Date().toISOString() });
    } finally {
      setGasTrackerLoading(false);
    }
  };

  const claimSeasonInfoLines = useMemo(() => claimSeasonInfo.split("\n"), [claimSeasonInfo]);

  const liveCountdown = useMemo<LocalCountdownItem[]>(() => {
    if (syncedNowMs == null) return [];
    const windowLine = claimSeasonInfoLines.find((line) => line.startsWith("Window:"));
    if (!windowLine) return [];
    const startMatch = windowLine.match(/start=(.*?) \| end=/);
    const startDate = startMatch ? parseUtcDisplayDate(startMatch[1]) : null;
    if (!startDate) return [];

    const now = new Date(syncedNowMs);
    const entries: LocalCountdownItem[] = [];
    entries.push({ label: "Synced now (local timer)", value: now.toISOString().replace("T", " ").replace(".000Z", " UTC") });
    entries.push({
      label: "season_alive_for",
      value: formatDuration(Math.max((now.getTime() - startDate.getTime()) / 1000, 0)),
    });

    const checkpoints = [
      { prefix: "- breach_end:", label: "time_to_vault_window" },
      { prefix: "- vault_end:", label: "time_to_scavenge_window" },
      { prefix: "- scavenge_end:", label: "time_to_transmission_window" },
      { prefix: "- cycle_boundary(day10):", label: "time_to_cycle_rollover" },
    ];
    for (const checkpoint of checkpoints) {
      const line = claimSeasonInfoLines.find((l) => l.trim().startsWith(checkpoint.prefix));
      if (!line) continue;
      const rawValue = line.replace(checkpoint.prefix, "").trim();
      const checkpointDate = parseUtcDisplayDate(rawValue);
      if (!checkpointDate) continue;
      const remainingSeconds = (checkpointDate.getTime() - now.getTime()) / 1000;
      if (remainingSeconds > 0) {
        entries.push({ label: checkpoint.label, value: formatDuration(remainingSeconds) });
      }
    }
    return entries;
  }, [claimSeasonInfoLines, syncedNowMs]);

  const refreshClaimSeasonInfo = async () => {
    if (!claimSeasonId) return;
    const data = await fetchJSON<{ lines: string[] }>(
      `/api/claims/season-info?season_id=${claimSeasonId}&wallet=${encodeURIComponent(claimWallet)}&auto_phase=${claimAutoPhase}&manual_phase=${claimPhase}`,
    );
    setClaimSeasonInfo(data.lines.join("\n"));
  };

  const refreshClaimMintableCount = async () => {
    if (!claimSeasonId) return;
    const data = await fetchJSON<ClaimsMintableSummary>(
      `/api/claims/mintable-count?season_id=${claimSeasonId}`,
    );
    setClaimMintableSummary(data);
  };

  useEffect(() => {
    void (async () => {
      try {
        const cfg = await fetchJSON<{ default_evm_recipient: string }>("/api/config");
        setClaimRecipient(cfg.default_evm_recipient ?? "");
      } catch {}
      try {
        const serverTime = await fetchJSON<{ now_utc_iso: string }>("/api/server-time");
        const serverNowMs = Date.parse(serverTime.now_utc_iso);
        if (!Number.isNaN(serverNowMs)) {
          setServerNowBaseMs(serverNowMs);
          setClientNowAtSyncMs(Date.now());
          setSyncedNowMs(serverNowMs);
        }
      } catch {}
    })();
  }, []);

  useEffect(() => {
    if (!claimSeasonId) return;
    void run(async () => {
      await refreshClaimSeasonInfo();
      await refreshClaimMintableCount();
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [claimSeasonId]);

  useEffect(() => {
    if (!claimSeasonId) return;
    void run(refreshClaimSeasonInfo);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [claimWallet, claimPhase, claimAutoPhase]);

  useEffect(() => {
    if (serverNowBaseMs == null || clientNowAtSyncMs == null) return;
    const timer = window.setInterval(() => {
      setSyncedNowMs(serverNowBaseMs + (Date.now() - clientNowAtSyncMs));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [serverNowBaseMs, clientNowAtSyncMs]);

  useEffect(() => {
    void refreshGasTracker();
    const timer = window.setInterval(() => {
      void refreshGasTracker();
    }, 15000);
    return () => window.clearInterval(timer);
  }, []);

  return (
    <section className="panel">
      <div className="row">
        <label>Season</label>
        <select value={claimSeasonId} onChange={(e) => onSeasonIdChange(Number(e.target.value))}>
          {seasonOptions.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
        <label>Wallet filter</label>
        <select value={walletFilter} onChange={(e) => onWalletFilterChange(e.target.value)}>
          <option value="all">all</option>
          <option value="origin">origin</option>
          <option value="non_origin">non_origin</option>
        </select>
        <label>Wallet</label>
        <input
          list="claim-wallet-options"
          value={claimWallet}
          onChange={(e) => onWalletChange(e.target.value)}
          disabled={walletsLoading}
          placeholder="Select or paste wallet address"
          style={{ minWidth: 420 }}
        />
        <datalist id="claim-wallet-options">
          {wallets.map((w) => <option key={w} value={w} />)}
        </datalist>
        {walletsLoading ? <span className="muted">Loading wallets...</span> : null}
        {!walletsLoading ? <span className="muted">Loaded wallets: {wallets.length}</span> : null}
        <label>Phase</label>
        <select value={claimPhase} onChange={(e) => setClaimPhase(e.target.value)} disabled={claimAutoPhase}>
          <option value="breach">breach</option>
          <option value="vault">vault</option>
          <option value="scavenge">scavenge</option>
        </select>
        <label>Recipient</label>
        <input value={claimRecipient} onChange={(e) => setClaimRecipient(e.target.value)} style={{ minWidth: 420 }} />
      </div>
      <div className="mint-summary" style={{ marginTop: 8 }}>
        <div className="mint-summary-row">
          <div
            className="mint-summary-label"
            title="Live Ethereum mainnet gas prices from Etherscan, multiplied by the configured gas estimate for one mintTo() call. Set EVM_MINT_GAS_ESTIMATE to override the default 165 000 units. Cached server-side for 15 s."
          >
            Mainnet mint cost (rapid)
          </div>
          {gasTracker == null ? (
            <div className="mint-summary-value muted">
              {gasTrackerLoading ? "Loading gas tracker..." : "—"}
            </div>
          ) : !gasTracker.ok ? (
            <div className="mint-summary-value">
              <span className="supply-meter-red mint-summary-pct">tracker unavailable</span>
              <span className="muted" style={{ marginLeft: 8 }}>{gasTracker.error}</span>
              <button
                style={{ marginLeft: 8 }}
                disabled={gasTrackerLoading}
                onClick={() => void refreshGasTracker()}
              >
                {gasTrackerLoading ? "..." : "Retry"}
              </button>
            </div>
          ) : (
            <>
              <div className="mint-summary-value">
                <strong>{fmtEthSmall(gasTracker.rapid_eth)} ETH</strong>
                <span className={`mint-summary-pct ${gwei_class(gasTracker.rapid_gwei)}`}>
                  {fmtUsd(gasTracker.rapid_usd)}
                </span>
              </div>
              <div className="mint-summary-detail muted">
                <span className={gwei_class(gasTracker.rapid_gwei).replace("supply-meter-", "")}>
                  rapid {fmtGwei(gasTracker.rapid_gwei)} gwei
                </span>
                {" · "}
                propose {fmtGwei(gasTracker.propose_gwei)} gwei
                {" · "}
                safe {fmtGwei(gasTracker.safe_gwei)} gwei
                {" · "}
                base {fmtGwei(gasTracker.base_fee_gwei)} gwei
                {" · "}
                ETH ${gasTracker.eth_usd.toLocaleString("en-US", { maximumFractionDigits: 2 })}
                {" · "}
                gas units {gasTracker.gas_estimate.toLocaleString("en-US")}
                {" · "}
                <span title={`Last fetched ${gasTracker.fetched_at}`}>
                  refreshes every 15s
                  {gasTracker.api_key_present ? "" : " (no ETHERSCAN_API_KEY)"}
                </span>
                {gasTrackerLoading ? <span style={{ marginLeft: 6 }}>↻</span> : null}
              </div>
            </>
          )}
        </div>
      </div>
      {claimMintableSummary ? (
        <div className="mint-summary">
          <div className="mint-summary-row">
            <div className="mint-summary-label">Season supply</div>
            <div className="mint-summary-value">
              <strong>{formatInt(claimMintableSummary.minted_active)}</strong>
              <span className="muted"> / {formatInt(claimMintableSummary.total_supply)}</span>
              <span className={`mint-summary-pct ${pctClass(claimMintableSummary.minted_active, claimMintableSummary.total_supply)}`}>
                {fmtPct(claimMintableSummary.minted_active, claimMintableSummary.total_supply)}
              </span>
            </div>
            <div className="mint-summary-meter">
              <div
                className={`mint-summary-meter-fill ${pctClass(claimMintableSummary.minted_active, claimMintableSummary.total_supply)}`}
                style={{
                  width: claimMintableSummary.total_supply > 0
                    ? `${Math.min(100, (claimMintableSummary.minted_active / claimMintableSummary.total_supply) * 100)}%`
                    : "0%",
                }}
              />
            </div>
            <div className="mint-summary-detail muted">
              {claimMintableSummary.status_counts.queued} queued ·{" "}
              {claimMintableSummary.status_counts.pending} pending ·{" "}
              {claimMintableSummary.status_counts.processing} processing ·{" "}
              {claimMintableSummary.status_counts.completed} completed ·{" "}
              {claimMintableSummary.status_counts.failed} failed
            </div>
          </div>

          <div className="mint-summary-row">
            <div
              className="mint-summary-label"
              title="How many distinct Origin proxy_wallets remain in the allocation pool, out of the partition's total. Looter mints draw from this; once a wallet is minted in this season, it leaves the pool."
            >
              Pool remaining
            </div>
            <div className="mint-summary-value">
              <strong>{formatInt(claimMintableSummary.remaining_mintable)}</strong>
              <span className="muted"> / {formatInt(claimMintableSummary.total_mintable)}</span>
            </div>
          </div>

          <div className="mint-summary-row">
            <div
              className="mint-summary-label"
              title="Largest single-event bucket of active claims in this season vs per_event_cap. When this hits the cap, looter retries skip rows tied to that event."
            >
              Per-event cap (max)
            </div>
            <div className="mint-summary-value">
              <strong>{formatInt(claimMintableSummary.top_event_count)}</strong>
              <span className="muted"> / {claimMintableSummary.per_event_cap == null ? "∞" : formatInt(claimMintableSummary.per_event_cap)}</span>
              <span className={`mint-summary-pct ${pctClass(claimMintableSummary.top_event_count, claimMintableSummary.per_event_cap)}`}>
                {fmtPct(claimMintableSummary.top_event_count, claimMintableSummary.per_event_cap)}
              </span>
            </div>
          </div>
        </div>
      ) : null}
      <div className="row">
        <label><input type="checkbox" checked={claimAutoPhase} onChange={(e) => setClaimAutoPhase(e.target.checked)} /> Auto phase</label>
        <label><input type="checkbox" checked={claimDbOnly} onChange={(e) => setClaimDbOnly(e.target.checked)} /> DB only</label>
        <button
          disabled={
            claimMinting ||
            !claimWallet ||
            !claimSeasonId ||
            !claimRecipient.trim() ||
            (claimMintableRemaining !== null && claimMintableRemaining <= 0) ||
            (claimMintableSummary !== null &&
              claimMintableSummary.total_supply > 0 &&
              claimMintableSummary.minted_active >= claimMintableSummary.total_supply)
          }
          onClick={() =>
            void run(async () => {
              setClaimMinting(true);
              setClaimOutput((prev) => `${prev}[${new Date().toISOString()}] Queueing mint request...\n`);
              try {
                const out = await fetchJSON<Record<string, unknown>>("/api/claims/mint", {
                  method: "POST",
                  body: JSON.stringify({
                    wallet: claimWallet,
                    recipient_address: claimRecipient,
                    season_id: claimSeasonId,
                    phase: claimPhase,
                    auto_phase: claimAutoPhase,
                    db_only: claimDbOnly,
                  }),
                });
                setClaimOutput((prev) => `${prev}${JSON.stringify(out, null, 2)}\n`);
                await refreshClaimSeasonInfo();
                await refreshClaimMintableCount();
                await refreshSeasonClaims();
                await refreshOverview();
              } catch (e) {
                const message = e instanceof Error ? e.message : String(e);
                setClaimOutput((prev) => `${prev}[${new Date().toISOString()}] Queue failed: ${message}\n`);
                throw e;
              } finally {
                setClaimMinting(false);
              }
            })
          }
        >
          {claimMinting ? "Queueing..." : "Add to Mint Queue"}
        </button>
        <button
          onClick={() =>
            void run(async () => {
              const data = await fetchJSON<{ address: string; explorer_url: string }>("/api/master-collection");
              if (!data.address) throw new Error("EVM_CONTRACT_ADDRESS is not set");
              window.open(data.explorer_url, "_blank");
            })
          }
        >
          Open Contract on Etherscan
        </button>
        <button
          disabled={mintQueueRunning}
          title="Equivalent to running: scripts/daily_scheduler_simple.py --process-mint-queue --mint-queue-batch-size 5"
          onClick={() =>
            void run(async () => {
              setMintQueueRunning(true);
              setClaimOutput(
                (prev) =>
                  `${prev}[${new Date().toISOString()}] Running mint queue worker (batch=5)...\n`,
              );
              try {
                const out = await fetchJSON<Record<string, unknown>>(
                  "/api/actions/process-mint-queue?batch_size=5",
                  { method: "POST" },
                );
                setClaimOutput((prev) => `${prev}${JSON.stringify(out, null, 2)}\n`);
                await refreshClaimMintableCount();
                await refreshSeasonClaims();
                await refreshOverview();
              } catch (e) {
                const message = e instanceof Error ? e.message : String(e);
                setClaimOutput(
                  (prev) =>
                    `${prev}[${new Date().toISOString()}] Mint queue run failed: ${message}\n`,
                );
                throw e;
              } finally {
                setMintQueueRunning(false);
              }
            })
          }
        >
          {mintQueueRunning ? "Running..." : "Run Mint Worker (5)"}
        </button>
      </div>
      {claimMinting ? <div className="muted">Queueing mint... on-chain transaction will run in the next batch.</div> : null}
      <div className="claims-columns">
        <div className="claims-left">
          {liveCountdown.length > 0 ? (
            <div className="panel">
              <div className="muted">Live local timer (synced once from server)</div>
              <div className="mono">
                {liveCountdown.map((item) => (
                  <div key={item.label} className="ok">- {item.label}: {item.value}</div>
                ))}
              </div>
            </div>
          ) : null}
          <div className="panel"><div className="muted">Mint/claim output</div><div className="mono">{claimOutput}</div></div>
        </div>
        <div className="claims-right">
          <div className="panel">
            <div className="muted">Selected season context</div>
            <div className="mono">
              {claimSeasonInfoLines.map((line, idx) => (
                <div key={`${idx}-${line.slice(0, 32)}`} className={seasonInfoLineClass(line)}>
                  {line || " "}
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
