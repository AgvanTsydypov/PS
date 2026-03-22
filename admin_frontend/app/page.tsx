"use client";

import { useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { ArrowUpDown, Copy, Loader2, Pencil, RotateCcw } from "lucide-react";

const RAW_API_BASE = process.env.NEXT_PUBLIC_SEASON_API_BASE_URL ?? "http://localhost:8001";
const API_BASE = RAW_API_BASE === "/" ? "" : RAW_API_BASE.replace(/\/$/, "");

type TabKey = "overview" | "eligibility" | "claims" | "seasonClaims" | "winners" | "eventCards" | "scenarios" | "reset";

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

type WinnerWalletRow = {
  id: number;
  season_id: number;
  wallet_address: string;
  source: string;
  total_pnl_window?: number | null;
  pnl_rank?: number | null;
  window_start: string;
  window_end: string;
  snapshot_at: string;
  created_at?: string;
  event_id?: string | null;
  market_id?: string | null;
  condition_id?: string | null;
  event_slug?: string | null;
  event_title?: string | null;
  is_minted: boolean;
  minted_at?: string | null;
  minted_to_wallet?: string | null;
  minted_to_solana_wallet?: string | null;
  minted_claim_id?: number | null;
  minted_tx_hash?: string | null;
  minted_asset_address?: string | null;
};

type WinnerWalletForm = {
  season_id: string;
  wallet_address: string;
  source: string;
  total_pnl_window: string;
  pnl_rank: string;
  window_start_iso: string;
  window_end_iso: string;
  snapshot_at_iso: string;
  event_id: string;
  market_id: string;
  condition_id: string;
  event_slug: string;
  event_title: string;
  is_minted: boolean;
  minted_at_iso: string;
  minted_to_wallet: string;
  minted_to_solana_wallet: string;
  minted_claim_id: string;
  minted_tx_hash: string;
  minted_asset_address: string;
};

type EventCardRow = {
  event_id: string;
  event_ticker?: string | null;
  event_slug?: string | null;
  event_title?: string | null;
  event_description?: string | null;
  card_title?: string | null;
  card_lore?: string | null;
  primary_tag?: string | null;
  secondary_tag?: string | null;
  primary_tag_hex_color?: string | null;
  secondary_tag_hex_color?: string | null;
  agent_name: string;
  model_name: string;
  prompt_version: string;
  status: "ok" | "error";
  error_text?: string | null;
  generated_at: string;
  updated_at: string;
};

type EventCardForm = {
  event_id: string;
  card_title: string;
  card_lore: string;
  primary_tag: string;
  secondary_tag: string;
  agent_name: string;
  model_name: string;
  prompt_version: string;
  status: "ok" | "error";
  error_text: string;
};

type EventCardPromptPreview = {
  event_id: string;
  agent_name: string;
  model_name: string;
  prompt_version: string;
  prompt_text: string;
  system_instruction: string;
  user_prompt: string;
  prompt_parts?: {
    event_title?: string;
    event_description?: string;
    series?: unknown;
    tags?: unknown;
    recurring_rule?: string;
    system_instruction?: string;
    user_prompt?: string;
    full_prompt?: string;
  };
};

type EventCardPromptDraft = {
  system_instruction: string;
  user_prompt: string;
};

type EventCardRegenerateFields = {
  card_title: boolean;
  card_lore: boolean;
  primary_tag: boolean;
  secondary_tag: boolean;
};

type EventCardsSortKey =
  | "event_id"
  | "event_ticker"
  | "event_slug"
  | "event_title"
  | "primary_tag"
  | "secondary_tag"
  | "status"
  | "model_name"
  | "prompt_version"
  | "generated_at"
  | "updated_at";

type LocalCountdownItem = { label: string; value: string };

const tabs: Array<{ key: TabKey; label: string }> = [
  { key: "overview", label: "Overview" },
  { key: "eligibility", label: "Eligibility" },
  { key: "claims", label: "Claims Mint" },
  { key: "seasonClaims", label: "Season Claims" },
  { key: "winners", label: "Winner Wallets" },
  { key: "eventCards", label: "Event Cards" },
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

function formatDateTimeHuman(raw: string): string {
  const ts = Date.parse(raw);
  if (Number.isNaN(ts)) return raw;
  const dt = new Date(ts);
  const dd = String(dt.getDate()).padStart(2, "0");
  const mm = String(dt.getMonth() + 1).padStart(2, "0");
  const yy = String(dt.getFullYear()).slice(-2);
  const hh = String(dt.getHours()).padStart(2, "0");
  const mi = String(dt.getMinutes()).padStart(2, "0");
  return `${dd}.${mm}.${yy} ${hh}:${mi}`;
}

function tagChipStyle(hexColor?: string | null): CSSProperties | undefined {
  const safe = (hexColor ?? "").trim();
  if (!/^#[0-9a-fA-F]{6}$/.test(safe)) return undefined;
  return {
    borderColor: safe,
    color: safe,
    backgroundColor: `${safe}20`,
  };
}

function promptPartsToDraft(parts?: EventCardPromptPreview["prompt_parts"] | null): EventCardPromptDraft {
  return {
    system_instruction: String(parts?.system_instruction ?? ""),
    user_prompt: String(parts?.user_prompt ?? ""),
  };
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
  const [winnerRows, setWinnerRows] = useState<WinnerWalletRow[]>([]);
  const [winnerSeasonFilterId, setWinnerSeasonFilterId] = useState<number>(0);
  const [winnerFormRowId, setWinnerFormRowId] = useState<number | null>(null);
  const [winnerForm, setWinnerForm] = useState<WinnerWalletForm>({
    season_id: "",
    wallet_address: "",
    source: "manual_admin",
    total_pnl_window: "",
    pnl_rank: "",
    window_start_iso: "",
    window_end_iso: "",
    snapshot_at_iso: "",
    event_id: "",
    market_id: "",
    condition_id: "",
    event_slug: "",
    event_title: "",
    is_minted: false,
    minted_at_iso: "",
    minted_to_wallet: "",
    minted_to_solana_wallet: "",
    minted_claim_id: "",
    minted_tx_hash: "",
    minted_asset_address: "",
  });
  const [eventCardRows, setEventCardRows] = useState<EventCardRow[]>([]);
  const [eventCardsLimit, setEventCardsLimit] = useState("500");
  const [eventCardsStatusFilter, setEventCardsStatusFilter] = useState("all");
  const [eventCardsEventIdFilter, setEventCardsEventIdFilter] = useState("");
  const [eventCardsPage, setEventCardsPage] = useState(1);
  const [eventCardsSortKey, setEventCardsSortKey] = useState<EventCardsSortKey>("generated_at");
  const [eventCardsSortDir, setEventCardsSortDir] = useState<"asc" | "desc">("desc");
  const [eventCardRegeneratingEventId, setEventCardRegeneratingEventId] = useState("");
  const [eventCardForm, setEventCardForm] = useState<EventCardForm>({
    event_id: "",
    card_title: "",
    card_lore: "",
    primary_tag: "",
    secondary_tag: "",
    agent_name: "agent_1_quant",
    model_name: "",
    prompt_version: "v1",
    status: "ok",
    error_text: "",
  });
  const [eventCardPromptText, setEventCardPromptText] = useState("");
  const [eventCardPromptMeta, setEventCardPromptMeta] = useState<Pick<
    EventCardPromptPreview,
    "event_id" | "agent_name" | "model_name" | "prompt_version"
  > | null>(null);
  const [eventCardPromptLoading, setEventCardPromptLoading] = useState(false);
  const [eventCardPromptContextParts, setEventCardPromptContextParts] = useState<
    EventCardPromptPreview["prompt_parts"] | null
  >(null);
  const [eventCardPromptDraft, setEventCardPromptDraft] = useState<EventCardPromptDraft>(
    promptPartsToDraft(null),
  );
  const [eventCardRegenerateFields, setEventCardRegenerateFields] = useState<EventCardRegenerateFields>({
    card_title: true,
    card_lore: true,
    primary_tag: true,
    secondary_tag: true,
  });
  const eventCardsRowsPerPage = 20;

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

  const buildEmptyWinnerForm = (seasonId: number): WinnerWalletForm => ({
    season_id: seasonId ? String(seasonId) : "",
    wallet_address: "",
    source: "manual_admin",
    total_pnl_window: "",
    pnl_rank: "",
    window_start_iso: "",
    window_end_iso: "",
    snapshot_at_iso: "",
    event_id: "",
    market_id: "",
    condition_id: "",
    event_slug: "",
    event_title: "",
    is_minted: false,
    minted_at_iso: "",
    minted_to_wallet: "",
    minted_to_solana_wallet: "",
    minted_claim_id: "",
    minted_tx_hash: "",
    minted_asset_address: "",
  });

  const mapWinnerRowToForm = (row: WinnerWalletRow): WinnerWalletForm => ({
    season_id: String(row.season_id),
    wallet_address: row.wallet_address ?? "",
    source: row.source ?? "manual_admin",
    total_pnl_window: row.total_pnl_window == null ? "" : String(row.total_pnl_window),
    pnl_rank: row.pnl_rank == null ? "" : String(row.pnl_rank),
    window_start_iso: row.window_start ?? "",
    window_end_iso: row.window_end ?? "",
    snapshot_at_iso: row.snapshot_at ?? "",
    event_id: row.event_id ?? "",
    market_id: row.market_id ?? "",
    condition_id: row.condition_id ?? "",
    event_slug: row.event_slug ?? "",
    event_title: row.event_title ?? "",
    is_minted: Boolean(row.is_minted),
    minted_at_iso: row.minted_at ?? "",
    minted_to_wallet: row.minted_to_wallet ?? "",
    minted_to_solana_wallet: row.minted_to_solana_wallet ?? "",
    minted_claim_id: row.minted_claim_id == null ? "" : String(row.minted_claim_id),
    minted_tx_hash: row.minted_tx_hash ?? "",
    minted_asset_address: row.minted_asset_address ?? "",
  });

  const buildEmptyEventCardForm = (): EventCardForm => ({
    event_id: "",
    card_title: "",
    card_lore: "",
    primary_tag: "",
    secondary_tag: "",
    agent_name: "agent_1_quant",
    model_name: "",
    prompt_version: "v1",
    status: "ok",
    error_text: "",
  });

  const mapEventCardRowToForm = (row: EventCardRow): EventCardForm => ({
    event_id: row.event_id,
    card_title: row.card_title ?? "",
    card_lore: row.card_lore ?? "",
    primary_tag: row.primary_tag ?? "",
    secondary_tag: row.secondary_tag ?? "",
    agent_name: row.agent_name ?? "agent_1_quant",
    model_name: row.model_name ?? "",
    prompt_version: row.prompt_version ?? "v1",
    status: row.status ?? "ok",
    error_text: row.error_text ?? "",
  });
  const getEventCardsSortValue = (row: EventCardRow, key: EventCardsSortKey): string => {
    const value = row[key];
    if (value == null) return "";
    return String(value).toLowerCase();
  };
  const toggleEventCardsSort = (key: EventCardsSortKey) => {
    if (eventCardsSortKey === key) {
      setEventCardsSortDir((prev) => (prev === "asc" ? "desc" : "asc"));
      return;
    }
    setEventCardsSortKey(key);
    setEventCardsSortDir("asc");
  };
  const copyText = async (text: string) => {
    const value = text.trim();
    if (!value) return;
    await navigator.clipboard.writeText(value);
    setOk(`Copied: ${value}`);
  };
  const sortedEventCardRows = useMemo(() => {
    const rows = [...eventCardRows];
    rows.sort((a, b) => {
      const left = getEventCardsSortValue(a, eventCardsSortKey);
      const right = getEventCardsSortValue(b, eventCardsSortKey);
      if (left === right) return 0;
      const result = left > right ? 1 : -1;
      return eventCardsSortDir === "asc" ? result : -result;
    });
    return rows;
  }, [eventCardRows, eventCardsSortDir, eventCardsSortKey]);
  const eventCardsTotalPages = useMemo(
    () => Math.max(1, Math.ceil(sortedEventCardRows.length / eventCardsRowsPerPage)),
    [sortedEventCardRows.length],
  );
  const pagedEventCardRows = useMemo(() => {
    const start = (eventCardsPage - 1) * eventCardsRowsPerPage;
    return sortedEventCardRows.slice(start, start + eventCardsRowsPerPage);
  }, [eventCardsPage, sortedEventCardRows]);

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

  const appendScenarioLog = (message: string) => {
    setScenarioOutput((prev) => `${prev}[${new Date().toISOString()}] ${message}\n`);
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
      if (!winnerSeasonFilterId || !knownIds.has(winnerSeasonFilterId)) {
        setWinnerSeasonFilterId(preferredId);
        if (!winnerForm.season_id) setWinnerForm((prev) => ({ ...prev, season_id: String(preferredId) }));
      }
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
      const includePositionWallets = walletFilter === "non_origin";
      const data = await fetchJSON<{ wallets: string[] }>(
        `/api/wallets?season_id=${seasonId}&wallet_filter=${walletFilter}&include_position_wallets=${includePositionWallets}&limit=60`,
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
    appendScenarioLog(`Loaded params for season ${scenarioSeasonId}`);
  };

  const refreshWinnerRows = async () => {
    const query = winnerSeasonFilterId ? `?season_id=${winnerSeasonFilterId}&limit=400` : "?limit=400";
    const data = await fetchJSON<{ rows: WinnerWalletRow[] }>(`/api/winners${query}`);
    setWinnerRows(data.rows);
  };

  const refreshEventCardRows = async () => {
    const safeLimit = Math.max(1, Number(eventCardsLimit) || 500);
    const params = new URLSearchParams();
    params.set("limit", String(safeLimit));
    if (eventCardsStatusFilter !== "all") params.set("status", eventCardsStatusFilter);
    if (eventCardsEventIdFilter.trim()) params.set("event_id", eventCardsEventIdFilter.trim());
    const data = await fetchJSON<{ rows: EventCardRow[] }>(`/api/event-cards?${params.toString()}`);
    setEventCardRows(data.rows);
    setEventCardsPage(1);
  };

  const refreshEventCardPrompt = async (eventIdRaw?: string) => {
    const eventId = (eventIdRaw ?? eventCardForm.event_id).trim();
    if (!eventId) {
      setEventCardPromptText("");
      setEventCardPromptMeta(null);
      setEventCardPromptContextParts(null);
      setEventCardPromptDraft(promptPartsToDraft(null));
      return;
    }
    setEventCardPromptLoading(true);
    try {
      const data = await fetchJSON<EventCardPromptPreview>(
        `/api/event-cards/${encodeURIComponent(eventId)}/prompt`,
      );
      setEventCardPromptText(data.prompt_text);
      setEventCardPromptMeta({
        event_id: data.event_id,
        agent_name: data.agent_name,
        model_name: data.model_name,
        prompt_version: data.prompt_version,
      });
      const nextParts = data.prompt_parts ?? null;
      setEventCardPromptContextParts(nextParts);
      setEventCardPromptDraft(promptPartsToDraft(nextParts));
    } finally {
      setEventCardPromptLoading(false);
    }
  };

  const buildPromptPartsOverrideFromDraft = () => {
    if (!eventCardPromptMeta) {
      throw new Error("Load prompt first before using custom prompt override");
    }
    if (!eventCardPromptContextParts) {
      throw new Error("Prompt context is missing. Click Load prompt first.");
    }
    return {
      event_title: String(eventCardPromptContextParts?.event_title ?? ""),
      event_description: String(eventCardPromptContextParts?.event_description ?? ""),
      series: eventCardPromptContextParts?.series ?? null,
      tags: Array.isArray(eventCardPromptContextParts?.tags)
        ? eventCardPromptContextParts?.tags
        : [],
      recurring_rule: String(eventCardPromptContextParts?.recurring_rule ?? ""),
      system_instruction: eventCardPromptDraft.system_instruction,
      user_prompt: eventCardPromptDraft.user_prompt,
    };
  };

  const buildRegenerateRequestBody = (eventId: string) => {
    const hasLoadedPromptForEvent = eventCardPromptMeta?.event_id === eventId;
    if (!hasLoadedPromptForEvent) return {};
    return { prompt_parts: buildPromptPartsOverrideFromDraft() };
  };

  const mergeRegeneratedEventCardForm = (
    previous: EventCardForm,
    regeneratedRow: EventCardRow,
  ): EventCardForm => {
    const next = mapEventCardRowToForm(regeneratedRow);
    if (!eventCardRegenerateFields.card_title) next.card_title = previous.card_title;
    if (!eventCardRegenerateFields.card_lore) next.card_lore = previous.card_lore;
    if (!eventCardRegenerateFields.primary_tag) next.primary_tag = previous.primary_tag;
    if (!eventCardRegenerateFields.secondary_tag) next.secondary_tag = previous.secondary_tag;
    return next;
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
    if (tab !== "winners") return;
    void run(async () => {
      if (!winnerForm.season_id && winnerSeasonFilterId) {
        setWinnerForm((prev) => ({ ...prev, season_id: String(winnerSeasonFilterId) }));
      }
      await refreshWinnerRows();
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, winnerSeasonFilterId]);

  useEffect(() => {
    if (tab !== "eventCards") return;
    void run(refreshEventCardRows);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab]);

  useEffect(() => {
    if (eventCardsPage > eventCardsTotalPages) {
      setEventCardsPage(eventCardsTotalPages);
    }
  }, [eventCardsPage, eventCardsTotalPages]);

  useEffect(() => {
    const wsUrl = API_BASE.replace(/^http/, "ws") + "/ws/events";
    const ws = new WebSocket(wsUrl);
    ws.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data) as {
          event: string;
          payload: { status?: string; message?: string; error?: string };
        };
        const wsDetails = payload.payload?.message || payload.payload?.error || "";
        const msg = `[WS:${payload.event}] ${payload.payload?.status ?? ""} ${wsDetails}`.trim();
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
                    body: JSON.stringify({ wallet: eligWallet, season_id: claimSeasonId }),
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
                        blockchain: claimBlockchain,
                      }),
                    });
                    setClaimOutput((prev) => `${prev}${JSON.stringify(out, null, 2)}\n`);
                    await refreshClaimSeasonInfo();
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

      {tab === "winners" ? (
        <section className="panel">
          <div className="row">
            <label>Season filter</label>
            <select value={winnerSeasonFilterId} onChange={(e) => setWinnerSeasonFilterId(Number(e.target.value))}>
              {seasonOptions.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
            <button onClick={() => void run(refreshWinnerRows)}>Refresh rows</button>
            <button
              onClick={() => {
                const seedSeasonId = winnerSeasonFilterId || claimSeasonId || 0;
                setWinnerFormRowId(null);
                setWinnerForm(buildEmptyWinnerForm(seedSeasonId));
              }}
            >
              Clear form
            </button>
          </div>

          <div className="panel">
            <div className="muted">{winnerFormRowId ? `Edit row #${winnerFormRowId}` : "Create new winner row"}</div>
            <div className="row">
              <label>season_id</label>
              <input
                value={winnerForm.season_id}
                onChange={(e) => setWinnerForm((prev) => ({ ...prev, season_id: e.target.value }))}
              />
              <label>wallet_address</label>
              <input
                value={winnerForm.wallet_address}
                onChange={(e) => setWinnerForm((prev) => ({ ...prev, wallet_address: e.target.value }))}
                style={{ minWidth: 300 }}
              />
              <label>source</label>
              <input value={winnerForm.source} onChange={(e) => setWinnerForm((prev) => ({ ...prev, source: e.target.value }))} />
              <label>is_minted</label>
              <input
                type="checkbox"
                checked={winnerForm.is_minted}
                onChange={(e) => setWinnerForm((prev) => ({ ...prev, is_minted: e.target.checked }))}
              />
            </div>
            <div className="row">
              <label>total_pnl_window</label>
              <input value={winnerForm.total_pnl_window} onChange={(e) => setWinnerForm((prev) => ({ ...prev, total_pnl_window: e.target.value }))} />
              <label>pnl_rank</label>
              <input value={winnerForm.pnl_rank} onChange={(e) => setWinnerForm((prev) => ({ ...prev, pnl_rank: e.target.value }))} />
              <label>window_start_iso</label>
              <input value={winnerForm.window_start_iso} onChange={(e) => setWinnerForm((prev) => ({ ...prev, window_start_iso: e.target.value }))} style={{ minWidth: 280 }} />
              <label>window_end_iso</label>
              <input value={winnerForm.window_end_iso} onChange={(e) => setWinnerForm((prev) => ({ ...prev, window_end_iso: e.target.value }))} style={{ minWidth: 280 }} />
              <label>snapshot_at_iso</label>
              <input value={winnerForm.snapshot_at_iso} onChange={(e) => setWinnerForm((prev) => ({ ...prev, snapshot_at_iso: e.target.value }))} style={{ minWidth: 280 }} />
            </div>
            <div className="row">
              <label>event_id</label>
              <input value={winnerForm.event_id} onChange={(e) => setWinnerForm((prev) => ({ ...prev, event_id: e.target.value }))} />
              <label>market_id</label>
              <input value={winnerForm.market_id} onChange={(e) => setWinnerForm((prev) => ({ ...prev, market_id: e.target.value }))} />
              <label>condition_id</label>
              <input value={winnerForm.condition_id} onChange={(e) => setWinnerForm((prev) => ({ ...prev, condition_id: e.target.value }))} />
              <label>event_slug</label>
              <input value={winnerForm.event_slug} onChange={(e) => setWinnerForm((prev) => ({ ...prev, event_slug: e.target.value }))} />
              <label>event_title</label>
              <input value={winnerForm.event_title} onChange={(e) => setWinnerForm((prev) => ({ ...prev, event_title: e.target.value }))} />
            </div>
            <div className="row">
              <label>minted_at_iso</label>
              <input value={winnerForm.minted_at_iso} onChange={(e) => setWinnerForm((prev) => ({ ...prev, minted_at_iso: e.target.value }))} style={{ minWidth: 280 }} />
              <label>minted_to_wallet</label>
              <input value={winnerForm.minted_to_wallet} onChange={(e) => setWinnerForm((prev) => ({ ...prev, minted_to_wallet: e.target.value }))} style={{ minWidth: 220 }} />
              <label>minted_to_solana_wallet</label>
              <input value={winnerForm.minted_to_solana_wallet} onChange={(e) => setWinnerForm((prev) => ({ ...prev, minted_to_solana_wallet: e.target.value }))} style={{ minWidth: 260 }} />
              <label>minted_claim_id</label>
              <input value={winnerForm.minted_claim_id} onChange={(e) => setWinnerForm((prev) => ({ ...prev, minted_claim_id: e.target.value }))} />
              <label>minted_tx_hash</label>
              <input value={winnerForm.minted_tx_hash} onChange={(e) => setWinnerForm((prev) => ({ ...prev, minted_tx_hash: e.target.value }))} style={{ minWidth: 220 }} />
              <label>minted_asset_address</label>
              <input value={winnerForm.minted_asset_address} onChange={(e) => setWinnerForm((prev) => ({ ...prev, minted_asset_address: e.target.value }))} style={{ minWidth: 220 }} />
            </div>
            <div className="row">
              <button
                onClick={() =>
                  void run(async () => {
                    const payload = {
                      season_id: Number(winnerForm.season_id),
                      wallet_address: winnerForm.wallet_address.trim(),
                      source: winnerForm.source.trim() || "manual_admin",
                      total_pnl_window: winnerForm.total_pnl_window.trim() ? Number(winnerForm.total_pnl_window) : null,
                      pnl_rank: winnerForm.pnl_rank.trim() ? Number(winnerForm.pnl_rank) : null,
                      window_start_iso: winnerForm.window_start_iso.trim(),
                      window_end_iso: winnerForm.window_end_iso.trim(),
                      snapshot_at_iso: winnerForm.snapshot_at_iso.trim() || null,
                      event_id: winnerForm.event_id.trim() || null,
                      market_id: winnerForm.market_id.trim() || null,
                      condition_id: winnerForm.condition_id.trim() || null,
                      event_slug: winnerForm.event_slug.trim() || null,
                      event_title: winnerForm.event_title.trim() || null,
                      is_minted: winnerForm.is_minted,
                      minted_at_iso: winnerForm.minted_at_iso.trim() || null,
                      minted_to_wallet: winnerForm.minted_to_wallet.trim() || null,
                      minted_to_solana_wallet: winnerForm.minted_to_solana_wallet.trim() || null,
                      minted_claim_id: winnerForm.minted_claim_id.trim() ? Number(winnerForm.minted_claim_id) : null,
                      minted_tx_hash: winnerForm.minted_tx_hash.trim() || null,
                      minted_asset_address: winnerForm.minted_asset_address.trim() || null,
                    };
                    if (!payload.season_id || !payload.wallet_address || !payload.window_start_iso || !payload.window_end_iso) {
                      throw new Error("season_id, wallet_address, window_start_iso and window_end_iso are required");
                    }
                    if (winnerFormRowId == null) {
                      await fetchJSON<{ row: WinnerWalletRow }>("/api/winners", {
                        method: "POST",
                        body: JSON.stringify(payload),
                      });
                      setOk("Winner row created");
                    } else {
                      await fetchJSON<{ row: WinnerWalletRow }>(`/api/winners/${winnerFormRowId}`, {
                        method: "PUT",
                        body: JSON.stringify(payload),
                      });
                      setOk(`Winner row ${winnerFormRowId} updated`);
                    }
                    await refreshWinnerRows();
                    setWinnerFormRowId(null);
                    setWinnerForm(buildEmptyWinnerForm(payload.season_id));
                  })
                }
              >
                {winnerFormRowId == null ? "Create row" : "Update row"}
              </button>
              {winnerFormRowId != null ? (
                <button
                  onClick={() => {
                    const seedSeasonId = winnerSeasonFilterId || claimSeasonId || 0;
                    setWinnerFormRowId(null);
                    setWinnerForm(buildEmptyWinnerForm(seedSeasonId));
                  }}
                >
                  Cancel edit
                </button>
              ) : null}
            </div>
          </div>

          <table>
            <thead>
              <tr>
                <th>id</th><th>season</th><th>wallet</th><th>rank</th><th>total_pnl</th><th>window_start</th><th>window_end</th><th>minted</th><th>actions</th>
              </tr>
            </thead>
            <tbody>
              {winnerRows.map((row) => (
                <tr key={row.id}>
                  <td>{row.id}</td>
                  <td>{row.season_id}</td>
                  <td>{row.wallet_address}</td>
                  <td>{row.pnl_rank}</td>
                  <td>{row.total_pnl_window}</td>
                  <td>{row.window_start}</td>
                  <td>{row.window_end}</td>
                  <td>{String(row.is_minted)}</td>
                  <td>
                    <button onClick={() => {
                      setWinnerFormRowId(row.id);
                      setWinnerForm(mapWinnerRowToForm(row));
                    }}>Edit</button>
                    <button
                      onClick={() =>
                        void run(async () => {
                          await fetchJSON<{ status: string }>(`/api/winners/${row.id}`, { method: "DELETE" });
                          if (winnerFormRowId === row.id) {
                            const seedSeasonId = winnerSeasonFilterId || claimSeasonId || 0;
                            setWinnerFormRowId(null);
                            setWinnerForm(buildEmptyWinnerForm(seedSeasonId));
                          }
                          setOk(`Winner row ${row.id} deleted`);
                          await refreshWinnerRows();
                        })
                      }
                    >
                      Delete
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ) : null}

      {tab === "eventCards" ? (
        <section className="panel">
          <div className="row">
            <label>status</label>
            <select value={eventCardsStatusFilter} onChange={(e) => setEventCardsStatusFilter(e.target.value)}>
              <option value="all">all</option>
              <option value="ok">ok</option>
              <option value="error">error</option>
            </select>
            <label>event_id</label>
            <input
              value={eventCardsEventIdFilter}
              onChange={(e) => setEventCardsEventIdFilter(e.target.value)}
              placeholder="optional exact event_id"
              style={{ minWidth: 300 }}
            />
            <label>limit</label>
            <input value={eventCardsLimit} onChange={(e) => setEventCardsLimit(e.target.value)} style={{ width: 90 }} />
            <button onClick={() => void run(refreshEventCardRows)}>Refresh rows</button>
            <button
              onClick={() => {
                setEventCardForm(buildEmptyEventCardForm());
                setEventCardPromptText("");
                setEventCardPromptMeta(null);
                setEventCardPromptContextParts(null);
                setEventCardPromptDraft(promptPartsToDraft(null));
              }}
            >
              Clear form
            </button>
          </div>

          <div className="event-cards-top-grid">
          <div className="panel">
            <div className="muted">Edit selected event card (existing logic)</div>
            <div className="row">
              <label>event_id</label>
              <input
                value={eventCardForm.event_id}
                onChange={(e) => setEventCardForm((prev) => ({ ...prev, event_id: e.target.value }))}
                style={{ minWidth: 340 }}
              />
              <label>status</label>
              <select
                value={eventCardForm.status}
                onChange={(e) => setEventCardForm((prev) => ({ ...prev, status: e.target.value as "ok" | "error" }))}
              >
                <option value="ok">ok</option>
                <option value="error">error</option>
              </select>
              <label>primary_tag</label>
              <input
                value={eventCardForm.primary_tag}
                onChange={(e) => setEventCardForm((prev) => ({ ...prev, primary_tag: e.target.value }))}
              />
              <label>secondary_tag</label>
              <input
                value={eventCardForm.secondary_tag}
                onChange={(e) => setEventCardForm((prev) => ({ ...prev, secondary_tag: e.target.value }))}
              />
            </div>
            <div className="row">
              <label>card_title</label>
              <input
                value={eventCardForm.card_title}
                onChange={(e) => setEventCardForm((prev) => ({ ...prev, card_title: e.target.value }))}
                style={{ minWidth: 360 }}
              />
            </div>
            <div className="row" style={{ alignItems: "flex-start" }}>
              <label>card_lore</label>
              <textarea
                value={eventCardForm.card_lore}
                onChange={(e) => setEventCardForm((prev) => ({ ...prev, card_lore: e.target.value }))}
                rows={8}
                style={{ minWidth: 760, width: "100%", maxWidth: 1100 }}
              />
            </div>
            <div className="row">
              <label>agent_name</label>
              <input
                value={eventCardForm.agent_name}
                onChange={(e) => setEventCardForm((prev) => ({ ...prev, agent_name: e.target.value }))}
              />
              <label>model_name</label>
              <input
                value={eventCardForm.model_name}
                onChange={(e) => setEventCardForm((prev) => ({ ...prev, model_name: e.target.value }))}
                style={{ minWidth: 260 }}
              />
              <label>prompt_version</label>
              <input
                value={eventCardForm.prompt_version}
                onChange={(e) => setEventCardForm((prev) => ({ ...prev, prompt_version: e.target.value }))}
              />
              <label>error_text</label>
              <input
                value={eventCardForm.error_text}
                onChange={(e) => setEventCardForm((prev) => ({ ...prev, error_text: e.target.value }))}
                style={{ minWidth: 420 }}
              />
            </div>
            <div className="row">
              <button
                onClick={() =>
                  void run(async () => {
                    const eventId = eventCardForm.event_id.trim();
                    if (!eventId) throw new Error("event_id is required");
                    await fetchJSON<{ row: EventCardRow }>(`/api/event-cards/${encodeURIComponent(eventId)}`, {
                      method: "PUT",
                      body: JSON.stringify({
                        card_title: eventCardForm.card_title.trim() || null,
                        card_lore: eventCardForm.card_lore.trim() || null,
                        primary_tag: eventCardForm.primary_tag.trim() || null,
                        secondary_tag: eventCardForm.secondary_tag.trim() || null,
                        agent_name: eventCardForm.agent_name.trim() || null,
                        model_name: eventCardForm.model_name.trim() || null,
                        prompt_version: eventCardForm.prompt_version.trim() || null,
                        status: eventCardForm.status,
                        error_text: eventCardForm.error_text.trim() || null,
                      }),
                    });
                    setOk(`Event card ${eventId} updated`);
                    await refreshEventCardRows();
                    await refreshEventCardPrompt(eventId);
                  })
                }
              >
                Save changes
              </button>
              <label>
                <input
                  type="checkbox"
                  checked={eventCardRegenerateFields.card_title}
                  onChange={(e) =>
                    setEventCardRegenerateFields((prev) => ({ ...prev, card_title: e.target.checked }))
                  }
                />{" "}
                regen `card_title`
              </label>
              <label>
                <input
                  type="checkbox"
                  checked={eventCardRegenerateFields.card_lore}
                  onChange={(e) =>
                    setEventCardRegenerateFields((prev) => ({ ...prev, card_lore: e.target.checked }))
                  }
                />{" "}
                regen `card_lore`
              </label>
              <label>
                <input
                  type="checkbox"
                  checked={eventCardRegenerateFields.primary_tag}
                  onChange={(e) =>
                    setEventCardRegenerateFields((prev) => ({ ...prev, primary_tag: e.target.checked }))
                  }
                />{" "}
                regen `primary_tag`
              </label>
              <label>
                <input
                  type="checkbox"
                  checked={eventCardRegenerateFields.secondary_tag}
                  onChange={(e) =>
                    setEventCardRegenerateFields((prev) => ({ ...prev, secondary_tag: e.target.checked }))
                  }
                />{" "}
                regen `secondary_tag`
              </label>
              <button
                disabled={Boolean(eventCardRegeneratingEventId)}
                onClick={() =>
                  void run(async () => {
                    const eventId = eventCardForm.event_id.trim();
                    if (!eventId) throw new Error("event_id is required for regenerate");
                    const previousForm = { ...eventCardForm };
                    setEventCardRegeneratingEventId(eventId);
                    try {
                      const out = await fetchJSON<{
                        status: string;
                        row: EventCardRow;
                        prompt_text?: string;
                        prompt_parts?: EventCardPromptPreview["prompt_parts"];
                      }>(
                        `/api/event-cards/${encodeURIComponent(eventId)}/regenerate`,
                        {
                          method: "POST",
                          body: JSON.stringify(buildRegenerateRequestBody(eventId)),
                        },
                      );
                      setEventCardForm(mergeRegeneratedEventCardForm(previousForm, out.row));
                      if (out.prompt_text) {
                        setEventCardPromptText(out.prompt_text);
                        setEventCardPromptMeta({
                          event_id: eventId,
                          agent_name: out.row.agent_name ?? "agent_1_quant",
                          model_name: out.row.model_name ?? "",
                          prompt_version: out.row.prompt_version ?? "v1",
                        });
                        const nextParts = out.prompt_parts ?? null;
                        setEventCardPromptContextParts(nextParts);
                        setEventCardPromptDraft(promptPartsToDraft(nextParts));
                      }
                      setOk(`Preview regenerated for ${eventId}. Click Save changes to persist.`);
                      await refreshEventCardRows();
                    } finally {
                      setEventCardRegeneratingEventId("");
                    }
                  })
                }
              >
                {eventCardRegeneratingEventId === eventCardForm.event_id.trim() ? "Regenerating..." : "Regenerate selected event"}
              </button>
              {eventCardRegeneratingEventId ? (
                <span className="muted">Regeneration in progress for: {eventCardRegeneratingEventId}</span>
              ) : null}
            </div>
          </div>
          <div className="panel">
            <div className="muted">Current model prompt (all Python-used parts)</div>
            <div className="row">
              <label>event_id</label>
              <input value={eventCardForm.event_id} readOnly style={{ minWidth: 340, opacity: 0.8 }} />
              <button
                onClick={() => void run(async () => refreshEventCardPrompt())}
                disabled={eventCardPromptLoading}
              >
                {eventCardPromptLoading ? "Loading prompt..." : "Load prompt"}
              </button>
            </div>
            <div className="row">
              <span className="muted">
                {eventCardPromptMeta
                  ? `agent=${eventCardPromptMeta.agent_name} | model=${eventCardPromptMeta.model_name} | prompt_version=${eventCardPromptMeta.prompt_version}`
                  : "Prompt metadata will appear here."}
              </span>
            </div>
            <div className="row">
              <span className="muted">
                Only `system_instruction` and `user_prompt` are sent to model override. DB is updated only via Save changes.
              </span>
            </div>
            <div className="row" style={{ alignItems: "flex-start" }}>
              <label>event_title</label>
              <textarea
                readOnly
                rows={2}
                className="mono readonly-prompt-field"
                value={String(eventCardPromptContextParts?.event_title ?? "")}
                style={{ width: "100%" }}
              />
            </div>
            <div className="row" style={{ alignItems: "flex-start" }}>
              <label>event_description</label>
              <textarea
                readOnly
                rows={3}
                className="mono readonly-prompt-field"
                value={String(eventCardPromptContextParts?.event_description ?? "")}
                style={{ width: "100%" }}
              />
            </div>
            <div className="row" style={{ alignItems: "flex-start" }}>
              <label>series</label>
              <textarea
                readOnly
                rows={4}
                className="mono readonly-prompt-field"
                value={JSON.stringify(eventCardPromptContextParts?.series ?? null, null, 2)}
                style={{ width: "100%" }}
              />
            </div>
            <div className="row" style={{ alignItems: "flex-start" }}>
              <label>tags</label>
              <textarea
                readOnly
                rows={4}
                className="mono readonly-prompt-field"
                value={JSON.stringify(eventCardPromptContextParts?.tags ?? [], null, 2)}
                style={{ width: "100%" }}
              />
            </div>
            <div className="row" style={{ alignItems: "flex-start" }}>
              <label>recurring_rule</label>
              <textarea
                readOnly
                rows={3}
                className="mono readonly-prompt-field"
                value={String(eventCardPromptContextParts?.recurring_rule ?? "")}
                style={{ width: "100%" }}
              />
            </div>
            <div className="row" style={{ alignItems: "flex-start" }}>
              <label>system_instruction</label>
              <textarea
                rows={3}
                className="mono"
                value={eventCardPromptDraft.system_instruction}
                onChange={(e) => setEventCardPromptDraft((prev) => ({ ...prev, system_instruction: e.target.value }))}
                style={{ width: "100%" }}
              />
            </div>
            <div className="row" style={{ alignItems: "flex-start" }}>
              <label>user_prompt</label>
              <textarea
                rows={10}
                className="mono"
                value={eventCardPromptDraft.user_prompt}
                onChange={(e) => setEventCardPromptDraft((prev) => ({ ...prev, user_prompt: e.target.value }))}
                style={{ width: "100%" }}
              />
            </div>
            <div className="row" style={{ alignItems: "flex-start" }}>
              <label>full_prompt</label>
              <textarea
                readOnly
                value={eventCardPromptText}
                rows={10}
                className="mono readonly-prompt-field"
                placeholder="full_prompt (system + user)"
                style={{ width: "100%", minHeight: 200 }}
              />
            </div>
          </div>
          </div>

          <div className="overflow-auto rounded-xl border border-slate-700 bg-slate-900 shadow-sm">
            <table className="event-cards-table w-full border-collapse text-xs text-slate-200">
              <thead className="sticky top-0 z-30 bg-slate-800">
                <tr>
                  <th className="sticky left-0 z-40 border-b border-r border-slate-700 bg-slate-800 px-3 py-2 text-left font-semibold">
                    <button className="inline-flex items-center gap-1" onClick={() => toggleEventCardsSort("event_id")}>
                      event_id <ArrowUpDown size={13} />
                    </button>
                  </th>
                  <th className="border-b border-slate-700 px-3 py-2 text-left font-semibold">
                    <button className="inline-flex items-center gap-1" onClick={() => toggleEventCardsSort("event_ticker")}>
                      ticker <ArrowUpDown size={13} />
                    </button>
                  </th>
                  <th className="border-b border-slate-700 px-3 py-2 text-left font-semibold">
                    <button className="inline-flex items-center gap-1" onClick={() => toggleEventCardsSort("event_slug")}>
                      slug <ArrowUpDown size={13} />
                    </button>
                  </th>
                  <th className="border-b border-slate-700 px-3 py-2 text-left font-semibold">
                    <button className="inline-flex items-center gap-1" onClick={() => toggleEventCardsSort("event_title")}>
                      event_title <ArrowUpDown size={13} />
                    </button>
                  </th>
                  <th className="border-b border-slate-700 px-3 py-2 text-left font-semibold">event_description</th>
                  <th className="border-b border-slate-700 px-3 py-2 text-left font-semibold">card_title</th>
                  <th className="border-b border-slate-700 px-3 py-2 text-left font-semibold">card_lore</th>
                  <th className="border-b border-slate-700 px-3 py-2 text-left font-semibold">
                    <button className="inline-flex items-center gap-1" onClick={() => toggleEventCardsSort("primary_tag")}>
                      primary_tag <ArrowUpDown size={13} />
                    </button>
                  </th>
                  <th className="border-b border-slate-700 px-3 py-2 text-left font-semibold">
                    <button className="inline-flex items-center gap-1" onClick={() => toggleEventCardsSort("secondary_tag")}>
                      secondary_tag <ArrowUpDown size={13} />
                    </button>
                  </th>
                  <th className="border-b border-slate-700 px-3 py-2 text-left font-semibold">
                    <button className="inline-flex items-center gap-1" onClick={() => toggleEventCardsSort("status")}>
                      status <ArrowUpDown size={13} />
                    </button>
                  </th>
                  <th className="border-b border-slate-700 px-3 py-2 text-left font-semibold">
                    <button className="inline-flex items-center gap-1" onClick={() => toggleEventCardsSort("model_name")}>
                      model <ArrowUpDown size={13} />
                    </button>
                  </th>
                  <th className="border-b border-slate-700 px-3 py-2 text-left font-semibold">
                    <button className="inline-flex items-center gap-1" onClick={() => toggleEventCardsSort("prompt_version")}>
                      prompt <ArrowUpDown size={13} />
                    </button>
                  </th>
                  <th className="border-b border-slate-700 px-3 py-2 text-left font-semibold">
                    <button className="inline-flex items-center gap-1" onClick={() => toggleEventCardsSort("generated_at")}>
                      generated_at <ArrowUpDown size={13} />
                    </button>
                  </th>
                  <th className="border-b border-slate-700 px-3 py-2 text-left font-semibold">
                    <button className="inline-flex items-center gap-1" onClick={() => toggleEventCardsSort("updated_at")}>
                      updated_at <ArrowUpDown size={13} />
                    </button>
                  </th>
                  <th className="sticky right-0 z-40 border-b border-l border-slate-700 bg-slate-800 px-3 py-2 text-left font-semibold">
                    actions
                  </th>
                </tr>
              </thead>
              <tbody>
                {pagedEventCardRows.map((row, idx) => {
                  const stripe = idx % 2 === 0 ? "bg-slate-900" : "bg-slate-800/60";
                  const statusClass = row.status === "ok"
                    ? "bg-emerald-900/40 text-emerald-300 border-emerald-700"
                    : "bg-rose-900/40 text-rose-300 border-rose-700";
                  return (
                    <tr key={row.event_id} className={`${stripe} hover:bg-slate-800`}>
                      <td className={`sticky left-0 z-20 border-b border-r border-slate-700 px-3 py-2 ${stripe}`}>
                        <div className="flex h-8 items-center gap-1.5">
                          <span className="max-w-[180px] truncate font-medium" title={row.event_id}>{row.event_id}</span>
                          <button
                            title="Copy event_id"
                            className="rounded p-1 text-slate-400 hover:bg-slate-700 hover:text-slate-100"
                            onClick={() => void copyText(row.event_id)}
                          >
                            <Copy size={13} />
                          </button>
                        </div>
                      </td>
                      <td className="border-b border-slate-700 px-3 py-2"><div className="h-8 truncate" title={row.event_ticker ?? ""}>{row.event_ticker ?? ""}</div></td>
                      <td className="border-b border-slate-700 px-3 py-2">
                        <div className="flex h-8 items-center gap-1.5">
                          <span className="max-w-[140px] truncate" title={row.event_slug ?? ""}>{row.event_slug ?? ""}</span>
                          {(row.event_slug ?? "").trim() ? (
                            <button
                              title="Copy slug"
                              className="rounded p-1 text-slate-400 hover:bg-slate-700 hover:text-slate-100"
                              onClick={() => void copyText(row.event_slug ?? "")}
                            >
                              <Copy size={13} />
                            </button>
                          ) : null}
                        </div>
                      </td>
                      <td className="border-b border-slate-700 px-3 py-2"><div className="h-8 max-w-[220px] truncate" title={row.event_title ?? ""}>{row.event_title ?? ""}</div></td>
                      <td className="border-b border-slate-700 px-3 py-2"><div className="h-8 max-w-[260px] truncate" title={row.event_description ?? ""}>{row.event_description ?? ""}</div></td>
                      <td className="border-b border-slate-700 px-3 py-2"><div className="h-8 max-w-[220px] truncate" title={row.card_title ?? ""}>{row.card_title ?? ""}</div></td>
                      <td className="border-b border-slate-700 px-3 py-2"><div className="h-8 max-w-[260px] truncate" title={row.card_lore ?? ""}>{row.card_lore ?? ""}</div></td>
                      <td className="border-b border-slate-700 px-3 py-2">
                        {row.primary_tag ? (
                          <span
                            className="inline-flex rounded-full border px-2 py-1 text-[11px] font-medium"
                            style={tagChipStyle(row.primary_tag_hex_color)}
                          >
                            {row.primary_tag}
                          </span>
                        ) : null}
                      </td>
                      <td className="border-b border-slate-700 px-3 py-2">
                        {row.secondary_tag ? (
                          <span
                            className="inline-flex rounded-full border px-2 py-1 text-[11px] font-medium"
                            style={tagChipStyle(row.secondary_tag_hex_color)}
                          >
                            {row.secondary_tag}
                          </span>
                        ) : null}
                      </td>
                      <td className="border-b border-slate-700 px-3 py-2">
                        <span className={`inline-flex rounded-full border px-2 py-1 text-[11px] font-semibold ${statusClass}`}>{row.status}</span>
                      </td>
                      <td className="border-b border-slate-700 px-3 py-2"><span className="font-mono text-[11px] text-slate-300">{row.model_name}</span></td>
                      <td className="border-b border-slate-700 px-3 py-2"><div className="h-8 max-w-[100px] truncate" title={row.prompt_version}>{row.prompt_version}</div></td>
                      <td className="border-b border-slate-700 px-3 py-2"><div className="h-8 whitespace-nowrap text-slate-300">{formatDateTimeHuman(row.generated_at)}</div></td>
                      <td className="border-b border-slate-700 px-3 py-2"><div className="h-8 whitespace-nowrap text-slate-300">{formatDateTimeHuman(row.updated_at)}</div></td>
                      <td className={`sticky right-0 z-20 border-b border-l border-slate-700 px-3 py-2 ${stripe}`}>
                        <div className="flex h-8 items-center gap-1">
                          <button
                            title="Edit row"
                            className="rounded p-1 text-slate-400 hover:bg-slate-700 hover:text-slate-100"
                            onClick={() => {
                              setEventCardForm(mapEventCardRowToForm(row));
                              void refreshEventCardPrompt(row.event_id).catch(() => {
                                // prompt load errors are handled by global run when used explicitly
                              });
                            }}
                          >
                            <Pencil size={14} />
                          </button>
                          <button
                            title="Regenerate row"
                            disabled={Boolean(eventCardRegeneratingEventId)}
                            className="rounded p-1 text-slate-400 hover:bg-slate-700 hover:text-slate-100"
                            onClick={() =>
                              void run(async () => {
                                // Immediately show currently selected row values in editor form.
                                const previousForm = mapEventCardRowToForm(row);
                                setEventCardForm(previousForm);
                                await refreshEventCardPrompt(row.event_id);
                                setEventCardRegeneratingEventId(row.event_id);
                                try {
                                  const out = await fetchJSON<{
                                    status: string;
                                    row: EventCardRow;
                                    prompt_text?: string;
                                    prompt_parts?: EventCardPromptPreview["prompt_parts"];
                                  }>(
                                    `/api/event-cards/${encodeURIComponent(row.event_id)}/regenerate`,
                                    {
                                      method: "POST",
                                      body: JSON.stringify(buildRegenerateRequestBody(row.event_id)),
                                    },
                                  );
                                  setEventCardForm(mergeRegeneratedEventCardForm(previousForm, out.row));
                                  if (out.prompt_text) {
                                    setEventCardPromptText(out.prompt_text);
                                    setEventCardPromptMeta({
                                      event_id: row.event_id,
                                      agent_name: out.row.agent_name ?? "agent_1_quant",
                                      model_name: out.row.model_name ?? "",
                                      prompt_version: out.row.prompt_version ?? "v1",
                                    });
                                    const nextParts = out.prompt_parts ?? null;
                                    setEventCardPromptContextParts(nextParts);
                                    setEventCardPromptDraft(promptPartsToDraft(nextParts));
                                  }
                                  setOk(`Preview regenerated for ${row.event_id}. Click Save changes to persist.`);
                                  await refreshEventCardRows();
                                } finally {
                                  setEventCardRegeneratingEventId("");
                                }
                              })
                            }
                          >
                            {eventCardRegeneratingEventId === row.event_id ? (
                              <Loader2 size={14} className="animate-spin" />
                            ) : (
                              <RotateCcw size={14} />
                            )}
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <div className="row" style={{ justifyContent: "space-between" }}>
            <span className="muted">
              Rows: {eventCardRows.length} | Page {eventCardsPage}/{eventCardsTotalPages} | 20 per page
            </span>
            <div className="row" style={{ marginBottom: 0 }}>
              <button
                onClick={() => setEventCardsPage((prev) => Math.max(1, prev - 1))}
                disabled={eventCardsPage <= 1}
              >
                Prev
              </button>
              <button
                onClick={() => setEventCardsPage((prev) => Math.min(eventCardsTotalPages, prev + 1))}
                disabled={eventCardsPage >= eventCardsTotalPages}
              >
                Next
              </button>
            </div>
          </div>
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
            <button
              onClick={() =>
                void run(async () => {
                  try {
                    await fetchJSON("/api/scenarios/quick-phase", {
                      method: "POST",
                      body: JSON.stringify({ season_id: scenarioSeasonId, days_since_start: 1 }),
                    });
                    appendScenarioLog(`Quick phase: season=${scenarioSeasonId} -> Breach (day 2)`);
                    await refreshOverview();
                  } catch (e) {
                    const message = e instanceof Error ? e.message : String(e);
                    appendScenarioLog(`Quick phase failed (Breach/day2): ${message}`);
                    throw e;
                  }
                })
              }
            >
              Set Breach (day 2)
            </button>
            <button
              onClick={() =>
                void run(async () => {
                  try {
                    await fetchJSON("/api/scenarios/quick-phase", {
                      method: "POST",
                      body: JSON.stringify({ season_id: scenarioSeasonId, days_since_start: 4 }),
                    });
                    appendScenarioLog(`Quick phase: season=${scenarioSeasonId} -> Vault (day 5)`);
                    await refreshOverview();
                  } catch (e) {
                    const message = e instanceof Error ? e.message : String(e);
                    appendScenarioLog(`Quick phase failed (Vault/day5): ${message}`);
                    throw e;
                  }
                })
              }
            >
              Set Vault (day 5)
            </button>
            <button
              onClick={() =>
                void run(async () => {
                  try {
                    await fetchJSON("/api/scenarios/quick-phase", {
                      method: "POST",
                      body: JSON.stringify({ season_id: scenarioSeasonId, days_since_start: 7 }),
                    });
                    appendScenarioLog(`Quick phase: season=${scenarioSeasonId} -> Scavenge (day 8)`);
                    await refreshOverview();
                  } catch (e) {
                    const message = e instanceof Error ? e.message : String(e);
                    appendScenarioLog(`Quick phase failed (Scavenge/day8): ${message}`);
                    throw e;
                  }
                })
              }
            >
              Set Scavenge (day 8)
            </button>
            <button
              onClick={() =>
                void run(async () => {
                  try {
                    await fetchJSON("/api/scenarios/quick-phase", {
                      method: "POST",
                      body: JSON.stringify({ season_id: scenarioSeasonId, days_since_start: 9 }),
                    });
                    appendScenarioLog(`Quick phase: season=${scenarioSeasonId} -> Transmission (day 10)`);
                    await refreshOverview();
                  } catch (e) {
                    const message = e instanceof Error ? e.message : String(e);
                    appendScenarioLog(`Quick phase failed (Transmission/day10): ${message}`);
                    throw e;
                  }
                })
              }
            >
              Set Transmission (day 10)
            </button>
          </div>
          <div className="row">
            <label>Shift start_date by days (from now)</label>
            <input value={scenarioShiftDays} onChange={(e) => setScenarioShiftDays(e.target.value)} />
            <button
              onClick={() =>
                void run(async () => {
                  try {
                    await fetchJSON("/api/scenarios/manual-date-shift", {
                      method: "POST",
                      body: JSON.stringify({ season_id: scenarioSeasonId, shift_days: Number(scenarioShiftDays) }),
                    });
                    appendScenarioLog(`Applied date shift: season=${scenarioSeasonId}, shift_days=${Number(scenarioShiftDays)}`);
                    await refreshOverview();
                  } catch (e) {
                    const message = e instanceof Error ? e.message : String(e);
                    appendScenarioLog(`Date shift failed: ${message}`);
                    throw e;
                  }
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
                  try {
                    await fetchJSON("/api/scenarios/remaining-supply", {
                      method: "POST",
                      body: JSON.stringify({ season_id: scenarioSeasonId, remaining_supply: Number(scenarioRemainingSupply) }),
                    });
                    appendScenarioLog(
                      `Applied remaining_supply: season=${scenarioSeasonId}, remaining_supply=${Number(scenarioRemainingSupply)}`
                    );
                    await refreshOverview();
                  } catch (e) {
                    const message = e instanceof Error ? e.message : String(e);
                    appendScenarioLog(`Apply supply failed: ${message}`);
                    throw e;
                  }
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
                    try {
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
                      appendScenarioLog(
                        `Applied advanced params: season=${scenarioSeasonId}, season_number=${Number(scenarioSeasonNumber)}, total=${Number(scenarioTotalSupply)}, remaining=${Number(scenarioRemainingSupplyAdvanced)}, active=${scenarioIsActive}, completed=${scenarioIsCompleted}`
                      );
                      await refreshOverview();
                    } catch (e) {
                      const message = e instanceof Error ? e.message : String(e);
                      appendScenarioLog(`Apply advanced params failed: ${message}`);
                      throw e;
                    }
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
