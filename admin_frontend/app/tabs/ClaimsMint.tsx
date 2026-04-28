"use client";

import { useEffect, useMemo, useState } from "react";
import { fetchJSON } from "../lib/api";

type LocalCountdownItem = { label: string; value: string };

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
  const [claimMintableTotal, setClaimMintableTotal] = useState<number | null>(null);
  const [claimMintableRemaining, setClaimMintableRemaining] = useState<number | null>(null);
  const [serverNowBaseMs, setServerNowBaseMs] = useState<number | null>(null);
  const [clientNowAtSyncMs, setClientNowAtSyncMs] = useState<number | null>(null);
  const [syncedNowMs, setSyncedNowMs] = useState<number | null>(null);

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
    const data = await fetchJSON<{ total_mintable: number; remaining_mintable: number }>(
      `/api/claims/mintable-count?season_id=${claimSeasonId}`,
    );
    setClaimMintableTotal(data.total_mintable);
    setClaimMintableRemaining(data.remaining_mintable);
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
        <select value={claimWallet} onChange={(e) => onWalletChange(e.target.value)} disabled={walletsLoading}>
          <option value="">Select wallet</option>
          {wallets.map((w) => <option key={w} value={w}>{w}</option>)}
        </select>
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
      <div className="row">
        <label><input type="checkbox" checked={claimAutoPhase} onChange={(e) => setClaimAutoPhase(e.target.checked)} /> Auto phase</label>
        <label><input type="checkbox" checked={claimDbOnly} onChange={(e) => setClaimDbOnly(e.target.checked)} /> DB only</label>
{claimMintableTotal !== null && (
          <span className="muted">
            Mintable remaining: {claimMintableRemaining} / {claimMintableTotal}
          </span>
        )}
        <button
          disabled={claimMinting || !claimWallet || !claimSeasonId || !claimRecipient.trim() || (claimMintableRemaining !== null && claimMintableRemaining <= 0)}
          onClick={() =>
            void run(async () => {
              setClaimMinting(true);
              setClaimOutput((prev) => `${prev}[${new Date().toISOString()}] Mint request started...\n`);
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
                setClaimOutput((prev) => `${prev}[${new Date().toISOString()}] Mint failed: ${message}\n`);
                throw e;
              } finally {
                setClaimMinting(false);
              }
            })
          }
        >
          {claimMinting ? "Minting..." : "Claim (Mint STAR)"}
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
      </div>
      {claimMinting ? <div className="muted">Mint in progress... please wait.</div> : null}
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
