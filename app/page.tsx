"use client";

import { useEffect, useMemo, useRef, useState } from "react";

const RAW_API_BASE = process.env.NEXT_PUBLIC_SEASON_API_BASE_URL ?? "http://localhost:8001";
const API_BASE = RAW_API_BASE === "/" ? "" : RAW_API_BASE.replace(/\/$/, "");

type TabKey = "overview" | "eligibility" | "claims" | "seasonClaims" | "scenarios" | "reset";

type Season = {
  id: number;
  type: string;
  season_number: number;
  start_date: string;
  end_date: string;
  total_supply: number;
  remaining_supply: number;
  is_active: boolean;
  is_completed: boolean;
};

type ClaimRow = {
  id: number;
  user_wallet: string;
  recipient_solana_wallet: string;
  phase_type: string;
  status: string;
  tx_hash?: string;
  asset_address?: string;
  timestamp?: string;
  created_at?: string;
};

type LocalCountdownItem = { label: string; value: string };

const tabs: Array<{ key: TabKey; label: string }> = [
  { key: "overview", label: "Overview" },
  { key: "eligibility", label: "Eligibility" },
  { key: "claims", label: "Claims Mint" },
  { key: "seasonClaims", label: "Season Claims" },
  { key: "scenarios", label: "Scenarios" },
  { key: "reset", label: "Reset" },
];

async function fetchJSON<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = (data as { detail?: string }).detail ?? "Request failed";
    throw new Error(detail);
  }
  return data as T;
}

function parseUtcDisplayDate(raw: string): Date | null {
  const value = raw.trim();
  if (!value) return null;
  // Converts "2026-03-17 00:00:00 UTC" -> Date
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

export default function HomePage() {
  const [tab, setTab] = useState<TabKey>("overview");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [ok, setOk] = useState("");

  const [seasons, setSeasons] = useState<Season[]>([]);
  const [logs, setLogs] = useState<Array<Record<string, unknown>>>([]);
  const [wallets, setWallets] = useState<string[]>([]);
  const [walletFilter, setWalletFilter] = useState("all");
  const [walletsLoading, setWalletsLoading] = useState(false);
  const walletsCacheRef = useRef<Map<string, string[]>>(new Map());

  const [eligWallet, setEligWallet] = useState("");
  const [eligibility, setEligibility] = useState("");

  const [claimWallet, setClaimWallet] = useState("");
  const [claimSeasonId, setClaimSeasonId] = useState<number>(0);
  const [claimPhase, setClaimPhase] = useState("breach");
  const [claimAutoPhase, setClaimAutoPhase] = useState(true);
  const [claimDbOnly, setClaimDbOnly] = useState(false);
  const [claimForceInsert, setClaimForceInsert] = useState(false);
  const [claimBlockchain, setClaimBlockchain] = useState("solana");
  const [claimRecipient, setClaimRecipient] = useState("");
  const [claimSeasonInfo, setClaimSeasonInfo] = useState<string>("");
  const [claimOutput, setClaimOutput] = useState<string>("");
  const [claimMinting, setClaimMinting] = useState(false);
  const [serverNowBaseMs, setServerNowBaseMs] = useState<number | null>(null);
  const [clientNowAtSyncMs, setClientNowAtSyncMs] = useState<number | null>(null);
  const [syncedNowMs, setSyncedNowMs] = useState<number | null>(null);

  const [seasonClaimsSeasonId, setSeasonClaimsSeasonId] = useState<number>(0);
  const [seasonClaimsRows, setSeasonClaimsRows] = useState<ClaimRow[]>([]);
  const [seasonClaimsStats, setSeasonClaimsStats] = useState<Record<string, number>>({});

  const [scenarioSeasonId, setScenarioSeasonId] = useState<number>(0);
  const [scenarioShiftDays, setScenarioShiftDays] = useState("0");
  const [scenarioRemainingSupply, setScenarioRemainingSupply] = useState("");
  const [scenarioSeasonNumber, setScenarioSeasonNumber] = useState("");
  const [scenarioTotalSupply, setScenarioTotalSupply] = useState("");
  const [scenarioRemainingSupplyAdvanced, setScenarioRemainingSupplyAdvanced] = useState("");
  const [scenarioStartDateIso, setScenarioStartDateIso] = useState("");
  const [scenarioEndDateIso, setScenarioEndDateIso] = useState("");
  const [scenarioIsActive, setScenarioIsActive] = useState("true");
  const [scenarioIsCompleted, setScenarioIsCompleted] = useState("false");
  const [scenarioOutput, setScenarioOutput] = useState("");

  const [resetConfirm, setResetConfirm] = useState(false);
  const [resetOutput, setResetOutput] = useState("");
  const [seasonUpdateRunning, setSeasonUpdateRunning] = useState(false);
  const [seasonUpdateOutput, setSeasonUpdateOutput] = useState("");

  const seasonOptions = useMemo(() => seasons.map((s) => ({ value: s.id, label: `id=${s.id} | ${s.type}#${s.season_number}` })), [seasons]);
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

  const choosePreferredSeasonId = (items: Season[]): number => {
    if (items.length === 0) return 0;
    const latestActiveStandard = items.find((s) => s.type === "standard" && s.is_active);
    if (latestActiveStandard) return latestActiveStandard.id;
    const latestAnyStandard = items.find((s) => s.type === "standard");
    if (latestAnyStandard) return latestAnyStandard.id;
    return items[0].id;
  };

  const seasonInfoLineClass = (line: string): string => {
    const trimmed = line.trim();
    if (!trimmed) return "";
    if (trimmed.startsWith("Transition rules (Standard):")) return "";
    if (trimmed.startsWith("Window:")) return "season-info-blue";
    if (trimmed.includes("- season_alive_for:")) return "ok";
    if (trimmed.startsWith("- breach_end:")) return "";
    if (trimmed.startsWith("- vault_end:")) return "";
    if (trimmed.startsWith("- scavenge_end:")) return "";
    if (trimmed.startsWith("- cycle_boundary(day10):")) return "";
    if (trimmed.startsWith("- time_to_")) return "ok";
    if (trimmed.startsWith("- Breach:")) return "";
    if (trimmed.startsWith("- Vault:")) return "";
    if (trimmed.startsWith("- Scavenge:")) return "";
    if (trimmed.startsWith("- Transmission:")) return "";
    if (trimmed.startsWith("Phase timeline (UTC):")) return "";
    if (trimmed.startsWith("Timing checkpoints:")) return "";
    return "";
  };

  const applyError = (e: unknown) => {
    const message = e instanceof Error ? e.message : String(e);
    setError(message);
    setOk("");
  };

  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError("");
    try {
      await fn();
    } catch (e) {
      applyError(e);
    } finally {
      setBusy(false);
    }
  };

  const refreshOverview = async () => {
    const overview = await fetchJSON<{ seasons: Season[]; logs: Array<Record<string, unknown>> }>("/api/overview");
    const seasonsData = await fetchJSON<Season[]>("/api/seasons");
    setSeasons(seasonsData);
    setLogs(overview.logs);

    if (seasonsData.length > 0) {
      const preferredId = choosePreferredSeasonId(seasonsData);
      const knownIds = new Set(seasonsData.map((s) => s.id));
      if (!claimSeasonId || !knownIds.has(claimSeasonId)) setClaimSeasonId(preferredId);
      if (!seasonClaimsSeasonId || !knownIds.has(seasonClaimsSeasonId)) setSeasonClaimsSeasonId(preferredId);
      if (!scenarioSeasonId || !knownIds.has(scenarioSeasonId)) setScenarioSeasonId(preferredId);
    }
  };

  const refreshWallets = async (
    seasonId = claimSeasonId,
    options?: { force?: boolean },
  ) => {
    if (!seasonId) return;
    const cacheKey = `${seasonId}:${walletFilter}`;
    const cachedWallets = walletsCacheRef.current.get(cacheKey);
    if (!options?.force && cachedWallets) {
      setWallets(cachedWallets);
      if (cachedWallets.length > 0) {
        if (!eligWallet || !cachedWallets.includes(eligWallet)) setEligWallet(cachedWallets[0]);
        if (!claimWallet || !cachedWallets.includes(claimWallet)) setClaimWallet(cachedWallets[0]);
      }
      return;
    }

    setWalletsLoading(true);
    try {
      const data = await fetchJSON<{ wallets: string[] }>(
        `/api/wallets?season_id=${seasonId}&wallet_filter=${walletFilter}&include_position_wallets=false&limit=60`,
      );
      setWallets(data.wallets);
      walletsCacheRef.current.set(cacheKey, data.wallets);
      if (data.wallets.length > 0) {
        if (!eligWallet || !data.wallets.includes(eligWallet)) setEligWallet(data.wallets[0]);
        if (!claimWallet || !data.wallets.includes(claimWallet)) setClaimWallet(data.wallets[0]);
      }
    } finally {
      setWalletsLoading(false);
    }
  };

  const refreshClaimSeasonInfo = async () => {
    if (!claimSeasonId) return;
    const data = await fetchJSON<{ lines: string[] }>(
      `/api/claims/season-info?season_id=${claimSeasonId}&wallet=${encodeURIComponent(claimWallet)}&auto_phase=${claimAutoPhase}&manual_phase=${claimPhase}&blockchain=${claimBlockchain}`,
    );
    setClaimSeasonInfo(data.lines.join("\n"));
  };

  const refreshSeasonClaims = async () => {
    if (!seasonClaimsSeasonId) return;
    const data = await fetchJSON<{ rows: ClaimRow[]; stats: Record<string, number> }>(
      `/api/claims/by-season/${seasonClaimsSeasonId}`,
    );
    setSeasonClaimsRows(data.rows);
    setSeasonClaimsStats(data.stats);
  };

  const loadScenarioParams = async () => {
    if (!scenarioSeasonId) return;
    const row = await fetchJSON<Record<string, unknown>>(`/api/scenarios/season/${scenarioSeasonId}`);
    setScenarioSeasonNumber(String(row.season_number ?? ""));
    setScenarioTotalSupply(String(row.total_supply ?? ""));
    setScenarioRemainingSupplyAdvanced(String(row.remaining_supply ?? ""));
    setScenarioStartDateIso(String(row.start_date_iso ?? ""));
    setScenarioEndDateIso(String(row.end_date_iso ?? ""));
    setScenarioIsActive(Boolean(row.is_active) ? "true" : "false");
    setScenarioIsCompleted(Boolean(row.is_completed) ? "true" : "false");
    setScenarioOutput((prev) => `${prev}Loaded params for season ${scenarioSeasonId}\n`);
  };

  useEffect(() => {
    void run(async () => {
      const cfg = await fetchJSON<{ default_solana_recipient: string }>("/api/config");
      const serverTime = await fetchJSON<{ now_utc_iso: string }>("/api/server-time");
      const serverNowMs = Date.parse(serverTime.now_utc_iso);
      setClaimRecipient(cfg.default_solana_recipient);
      if (!Number.isNaN(serverNowMs)) {
        setServerNowBaseMs(serverNowMs);
        setClientNowAtSyncMs(Date.now());
        setSyncedNowMs(serverNowMs);
      }
      await refreshOverview();
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!claimSeasonId) return;
    void run(async () => {
      if (tab === "eligibility" || tab === "claims") {
        await refreshWallets(claimSeasonId);
      }
      if (tab === "claims") {
        await refreshClaimSeasonInfo();
      }
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [claimSeasonId, walletFilter, tab]);

  useEffect(() => {
    if (tab !== "claims") return;
    if (!claimSeasonId) return;
    void run(refreshClaimSeasonInfo);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [claimWallet, claimPhase, claimAutoPhase, claimBlockchain, tab]);

  useEffect(() => {
    if (serverNowBaseMs == null || clientNowAtSyncMs == null) return;
    const timer = window.setInterval(() => {
      setSyncedNowMs(serverNowBaseMs + (Date.now() - clientNowAtSyncMs));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [serverNowBaseMs, clientNowAtSyncMs]);

  useEffect(() => {
    if (tab !== "seasonClaims") return;
    if (!seasonClaimsSeasonId) return;
    void run(refreshSeasonClaims);
    const timer = window.setInterval(() => {
      void refreshSeasonClaims().catch(() => {
        // keep UI responsive during polling
      });
    }, 3000);
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seasonClaimsSeasonId, tab]);

  useEffect(() => {
    const wsUrl = API_BASE.replace(/^http/, "ws") + "/ws/events";
    const ws = new WebSocket(wsUrl);
    ws.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data) as { event: string; payload: { status?: string; message?: string } };
        const msg = `[WS:${payload.event}] ${payload.payload?.status ?? ""} ${payload.payload?.message ?? ""}`.trim();
        setClaimOutput((prev) => `${prev}${msg}\n`);
        if (payload.event === "season_update") {
          setSeasonUpdateOutput((prev) => `${prev}${msg}\n`);
          setSeasonUpdateRunning(false);
        }
      } catch {
        // ignore parse errors
      }
    };
    ws.onopen = () => ws.send("hello");
    const keepAlive = window.setInterval(() => {
      if (ws.readyState === WebSocket.OPEN) ws.send("ping");
    }, 15000);
    return () => {
      window.clearInterval(keepAlive);
      ws.close();
    };
  }, []);

  const title = "PolyStars Seasons Test Workbench (Web)";

  return (
    <main>
      <h1 className="title">{title}</h1>
      <div className="row">
        <button onClick={() => void run(refreshOverview)} disabled={busy}>Refresh All</button>
        <span className="muted">API: {API_BASE}</span>
      </div>
      {ok ? <div className="ok">{ok}</div> : null}
      {error ? <div className="error">{error}</div> : null}

      <div className="tabs">
        {tabs.map((t) => (
          <button key={t.key} className={`tab ${tab === t.key ? "active" : ""}`} onClick={() => setTab(t.key)}>
            {t.label}
          </button>
        ))}
      </div>

      {tab === "overview" ? (
        <section className="panel">
          <div className="row">
            <button
              onClick={() =>
                void run(async () => {
                  setSeasonUpdateRunning(true);
                  setSeasonUpdateOutput((prev) => `${prev}[${new Date().toISOString()}] Season update started...\n`);
                  try {
                    const out = await fetchJSON<{ message: string }>("/api/actions/season-update", { method: "POST" });
                    setSeasonUpdateOutput((prev) => `${prev}[${new Date().toISOString()}] Season update completed: ${out.message}\n`);
                    setOk(out.message);
                    await refreshOverview();
                  } catch (e) {
                    const message = e instanceof Error ? e.message : String(e);
                    setSeasonUpdateOutput((prev) => `${prev}[${new Date().toISOString()}] Season update failed: ${message}\n`);
                    throw e;
                  } finally {
                    setSeasonUpdateRunning(false);
                  }
                })
              }
              disabled={busy || seasonUpdateRunning}
            >
              {seasonUpdateRunning ? "Running --season-update..." : "Run --season-update"}
            </button>
            {seasonUpdateRunning ? <span className="muted">Season update in progress... this can take a while on large DB.</span> : null}
          </div>
          <div className="panel">
            <div className="muted">Season update output</div>
            <div className="mono">{seasonUpdateOutput || "No runs yet."}</div>
          </div>
          <table>
            <thead>
              <tr>
                <th>id</th><th>type</th><th>season_number</th><th>start_date</th><th>end_date</th><th>total</th><th>remaining</th><th>active</th><th>completed</th>
              </tr>
            </thead>
            <tbody>
              {seasons.map((s) => (
                <tr key={s.id}>
                  <td>{s.id}</td><td>{s.type}</td><td>{s.season_number}</td><td>{s.start_date}</td><td>{s.end_date}</td>
                  <td>{s.total_supply}</td><td>{s.remaining_supply}</td><td>{String(s.is_active)}</td><td>{String(s.is_completed)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="panel">
            <div className="muted">Latest season events log</div>
            <div className="mono">
              {logs.map((l, i) => `[${String(l.created_at)}] event=${String(l.event_name)} season_id=${String(l.season_id)} details=${String(l.details ?? "")}`).join("\n")}
            </div>
          </div>
        </section>
      ) : null}

      {tab === "eligibility" ? (
        <section className="panel">
          <div className="row">
            <label>Season</label>
            <select value={claimSeasonId} onChange={(e) => setClaimSeasonId(Number(e.target.value))}>
              {seasonOptions.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
            <label>Wallet filter</label>
            <select value={walletFilter} onChange={(e) => setWalletFilter(e.target.value)}>
              <option value="all">all</option>
              <option value="origin">origin</option>
              <option value="non_origin">non_origin</option>
            </select>
            <button onClick={() => void run(() => refreshWallets(claimSeasonId, { force: true }))} disabled={walletsLoading}>
              {walletsLoading ? "Loading wallets..." : "Reload wallets"}
            </button>
            <select value={eligWallet} onChange={(e) => setEligWallet(e.target.value)} disabled={walletsLoading}>
              <option value="">Select wallet</option>
              {wallets.map((w) => <option key={w} value={w}>{w}</option>)}
            </select>
            {walletsLoading ? <span className="muted">Loading wallets...</span> : null}
            {!walletsLoading ? <span className="muted">Loaded wallets: {wallets.length}</span> : null}
            <button
              onClick={() =>
                void run(async () => {
                  const data = await fetchJSON<Record<string, unknown>>("/api/eligibility", {
                    method: "POST",
                    body: JSON.stringify({ wallet: eligWallet }),
                  });
                  setEligibility(JSON.stringify(data, null, 2));
                })
              }
            >
              Check eligibility
            </button>
          </div>
          <div className="mono">{eligibility}</div>
        </section>
      ) : null}

      {tab === "claims" ? (
        <section className="panel">
          <div className="row">
            <label>Season</label>
            <select value={claimSeasonId} onChange={(e) => setClaimSeasonId(Number(e.target.value))}>
              {seasonOptions.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
            <label>Wallet filter</label>
            <select value={walletFilter} onChange={(e) => setWalletFilter(e.target.value)}>
              <option value="all">all</option>
              <option value="origin">origin</option>
              <option value="non_origin">non_origin</option>
            </select>
            <label>Wallet</label>
            <select value={claimWallet} onChange={(e) => setClaimWallet(e.target.value)} disabled={walletsLoading}>
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
            <label>Chain</label>
            <select
              value={claimBlockchain}
              onChange={(e) => {
                const chain = e.target.value;
                setClaimBlockchain(chain);
                setClaimRecipient(chain === "base_zora" ? "0xdC65DFF7EED4c1C05511395Ccf19CF507066aCe1" : "H1wsggroxpW3LwCCv8dVeiJW73oYPkcDGgSqhiT5Zbz3");
              }}
            >
              <option value="solana">solana</option>
              <option value="base_zora">base_zora</option>
            </select>
            <label>Recipient</label>
            <input value={claimRecipient} onChange={(e) => setClaimRecipient(e.target.value)} style={{ minWidth: 420 }} />
          </div>
          <div className="row">
            <label><input type="checkbox" checked={claimAutoPhase} onChange={(e) => setClaimAutoPhase(e.target.checked)} /> Auto phase</label>
            <label><input type="checkbox" checked={claimDbOnly} onChange={(e) => setClaimDbOnly(e.target.checked)} /> DB only</label>
            <label><input type="checkbox" checked={claimForceInsert} onChange={(e) => setClaimForceInsert(e.target.checked)} /> Force insert</label>
            <button
              disabled={claimMinting || !claimWallet || !claimSeasonId || !claimRecipient.trim()}
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
                        force_insert: claimForceInsert,
                        blockchain: claimBlockchain,
                      }),
                    });
                    setClaimOutput((prev) => `${prev}${JSON.stringify(out, null, 2)}\n`);
                    await refreshClaimSeasonInfo();
                    await refreshSeasonClaims();
                    await refreshOverview();
                  } finally {
                    setClaimMinting(false);
                  }
                })
              }
            >
              {claimMinting ? "Minting..." : "Claim (Mint NFT)"}
            </button>
            <button
              onClick={() =>
                void run(async () => {
                  const data = await fetchJSON<{ address: string }>("/api/master-collection");
                  if (!data.address) throw new Error("MASTER_COLLECTION_ADDRESS is not set");
                  window.open(`https://explorer.solana.com/address/${data.address}?cluster=devnet`, "_blank");
                })
              }
            >
              Open Master Collection
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
      ) : null}

      {tab === "seasonClaims" ? (
        <section className="panel">
          <div className="row">
            <label>Season</label>
            <select value={seasonClaimsSeasonId} onChange={(e) => setSeasonClaimsSeasonId(Number(e.target.value))}>
              {seasonOptions.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
            <button onClick={() => void run(refreshSeasonClaims)}>Refresh now</button>
          </div>
          <div className="mono">{JSON.stringify(seasonClaimsStats, null, 2)}</div>
          <table>
            <thead>
              <tr>
                <th>id</th><th>wallet</th><th>recipient</th><th>phase</th><th>status</th><th>tx_hash</th><th>asset_address</th><th>timestamp</th><th>created_at</th>
              </tr>
            </thead>
            <tbody>
              {seasonClaimsRows.map((r) => (
                <tr key={r.id}>
                  <td>{r.id}</td><td>{r.user_wallet}</td><td>{r.recipient_solana_wallet}</td><td>{r.phase_type}</td>
                  <td>{r.status}</td><td>{r.tx_hash}</td><td>{r.asset_address}</td><td>{r.timestamp}</td><td>{r.created_at}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ) : null}

      {tab === "scenarios" ? (
        <section className="panel">
          <div className="row">
            <label>Target season</label>
            <select value={scenarioSeasonId} onChange={(e) => setScenarioSeasonId(Number(e.target.value))}>
              {seasonOptions.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
            <button onClick={() => void run(loadScenarioParams)}>Load selected season params</button>
          </div>
          <div className="row">
            <button onClick={() => void run(() => fetchJSON("/api/scenarios/quick-phase", { method: "POST", body: JSON.stringify({ season_id: scenarioSeasonId, days_since_start: 1 }) }).then(() => refreshOverview()))}>Set Breach (day 2)</button>
            <button onClick={() => void run(() => fetchJSON("/api/scenarios/quick-phase", { method: "POST", body: JSON.stringify({ season_id: scenarioSeasonId, days_since_start: 4 }) }).then(() => refreshOverview()))}>Set Vault (day 5)</button>
            <button onClick={() => void run(() => fetchJSON("/api/scenarios/quick-phase", { method: "POST", body: JSON.stringify({ season_id: scenarioSeasonId, days_since_start: 7 }) }).then(() => refreshOverview()))}>Set Scavenge (day 8)</button>
            <button onClick={() => void run(() => fetchJSON("/api/scenarios/quick-phase", { method: "POST", body: JSON.stringify({ season_id: scenarioSeasonId, days_since_start: 9 }) }).then(() => refreshOverview()))}>Set Transmission (day 10)</button>
          </div>
          <div className="row">
            <label>Shift start_date by days (from now)</label>
            <input value={scenarioShiftDays} onChange={(e) => setScenarioShiftDays(e.target.value)} />
            <button
              onClick={() =>
                void run(async () => {
                  await fetchJSON("/api/scenarios/manual-date-shift", {
                    method: "POST",
                    body: JSON.stringify({ season_id: scenarioSeasonId, shift_days: Number(scenarioShiftDays) }),
                  });
                  setScenarioOutput((prev) => `${prev}Applied date shift\n`);
                  await refreshOverview();
                })
              }
            >
              Apply date shift
            </button>
          </div>
          <div className="row">
            <label>Set remaining_supply</label>
            <input value={scenarioRemainingSupply} onChange={(e) => setScenarioRemainingSupply(e.target.value)} />
            <button
              onClick={() =>
                void run(async () => {
                  await fetchJSON("/api/scenarios/remaining-supply", {
                    method: "POST",
                    body: JSON.stringify({ season_id: scenarioSeasonId, remaining_supply: Number(scenarioRemainingSupply) }),
                  });
                  setScenarioOutput((prev) => `${prev}Applied remaining_supply\n`);
                  await refreshOverview();
                })
              }
            >
              Apply supply
            </button>
          </div>
          <div className="panel">
            <div className="row">
              <label>season_number</label><input value={scenarioSeasonNumber} onChange={(e) => setScenarioSeasonNumber(e.target.value)} />
              <label>total_supply</label><input value={scenarioTotalSupply} onChange={(e) => setScenarioTotalSupply(e.target.value)} />
              <label>remaining_supply</label><input value={scenarioRemainingSupplyAdvanced} onChange={(e) => setScenarioRemainingSupplyAdvanced(e.target.value)} />
            </div>
            <div className="row">
              <label>start_date (ISO UTC)</label><input value={scenarioStartDateIso} onChange={(e) => setScenarioStartDateIso(e.target.value)} style={{ minWidth: 320 }} />
              <label>end_date (ISO UTC)</label><input value={scenarioEndDateIso} onChange={(e) => setScenarioEndDateIso(e.target.value)} style={{ minWidth: 320 }} />
            </div>
            <div className="row">
              <label>is_active</label>
              <select value={scenarioIsActive} onChange={(e) => setScenarioIsActive(e.target.value)}><option value="true">true</option><option value="false">false</option></select>
              <label>is_completed</label>
              <select value={scenarioIsCompleted} onChange={(e) => setScenarioIsCompleted(e.target.value)}><option value="true">true</option><option value="false">false</option></select>
              <button onClick={() => {
                const now = new Date();
                const end = new Date(now.getTime() + 10 * 24 * 60 * 60 * 1000);
                setScenarioStartDateIso(now.toISOString());
                setScenarioEndDateIso(end.toISOString());
              }}>Set now as start (+10d end)</button>
              <button
                onClick={() =>
                  void run(async () => {
                    await fetchJSON("/api/scenarios/apply-advanced", {
                      method: "POST",
                      body: JSON.stringify({
                        season_id: scenarioSeasonId,
                        season_number: Number(scenarioSeasonNumber),
                        total_supply: Number(scenarioTotalSupply),
                        remaining_supply: Number(scenarioRemainingSupplyAdvanced),
                        start_date_iso: scenarioStartDateIso,
                        end_date_iso: scenarioEndDateIso,
                        is_active: scenarioIsActive === "true",
                        is_completed: scenarioIsCompleted === "true",
                      }),
                    });
                    setScenarioOutput((prev) => `${prev}Applied advanced params\n`);
                    await refreshOverview();
                  })
                }
              >
                Apply advanced params
              </button>
            </div>
          </div>
          <div className="mono">{scenarioOutput}</div>
        </section>
      ) : null}

      {tab === "reset" ? (
        <section className="panel">
          <div className="muted">
            Reset uses sql/queries/clear_seasons_logic.sql and wipes seasons/claims/season_events_log/winner_wallets_nft_to_claim.
          </div>
          <div className="row">
            <label><input type="checkbox" checked={resetConfirm} onChange={(e) => setResetConfirm(e.target.checked)} /> I understand and want to reset test seasons data</label>
            <button
              onClick={() =>
                void run(async () => {
                  const out = await fetchJSON<{ message: string }>("/api/reset", {
                    method: "POST",
                    body: JSON.stringify({ confirm: resetConfirm }),
                  });
                  setResetOutput((prev) => `${prev}${out.message}\n`);
                  await refreshOverview();
                })
              }
            >
              Run reset SQL
            </button>
          </div>
          <div className="mono">{resetOutput}</div>
        </section>
      ) : null}
    </main>
  );
}
