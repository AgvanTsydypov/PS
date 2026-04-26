"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ARCHETYPE_DISPLAY_ORDER,
  fetchSeasonArchetypeOpens,
  fetchSeasonsCatalog,
  type SeasonArchetypeOpensResponse,
  type SeasonCatalogEntry,
} from "../lib/userApiBase";

type ArchetypeStatRow = {
  key: (typeof ARCHETYPE_DISPLAY_ORDER)[number] | "UNKNOWN";
  count: number;
};

export function SeasonArchetypeOpensBoard() {
  const [seasons, setSeasons] = useState<SeasonCatalogEntry[]>([]);
  const [seasonId, setSeasonId] = useState<number | null>(null);
  const [stats, setStats] = useState<SeasonArchetypeOpensResponse | null>(null);
  const [catalogError, setCatalogError] = useState("");
  const [statsError, setStatsError] = useState("");
  const [catalogLoading, setCatalogLoading] = useState(true);
  const [statsLoading, setStatsLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;

    async function loadCatalog() {
      setCatalogLoading(true);
      setCatalogError("");
      const payload = await fetchSeasonsCatalog({ retries: 3 });
      if (cancelled) {
        return;
      }
      if (!payload || !Array.isArray(payload.seasons)) {
        setCatalogError("Unable to load seasons.");
        setSeasons([]);
        setSeasonId(null);
      } else {
        setSeasons(payload.seasons);
        setSeasonId((prev) => {
          if (prev != null && payload.seasons.some((s) => s.id === prev)) {
            return prev;
          }
          return payload.seasons[0]?.id ?? null;
        });
      }
      setCatalogLoading(false);
    }

    void loadCatalog();
    return () => {
      cancelled = true;
    };
  }, []);

  const loadStats = useCallback(async (id: number) => {
    setStatsLoading(true);
    setStatsError("");
    const payload = await fetchSeasonArchetypeOpens(id, { retries: 3 });
    if (!payload) {
      setStats(null);
      setStatsError("Unable to load archetype counts.");
    } else {
      setStats(payload);
    }
    setStatsLoading(false);
  }, []);

  useEffect(() => {
    if (seasonId == null) {
      return;
    }
    void loadStats(seasonId);
  }, [seasonId, loadStats]);

  const rows = useMemo(() => {
    if (!stats) {
      return [];
    }
    const list: ArchetypeStatRow[] = ARCHETYPE_DISPLAY_ORDER.map((key) => ({
      key,
      count: stats.by_archetype[key] ?? 0,
    }));
    if (stats.unknown > 0) {
      list.push({ key: "UNKNOWN", count: stats.unknown });
    }
    return list;
  }, [stats]);

  return (
    <section className="season-board season-board-standalone season-archetype-opens-board">
      <div className="season-board-title">Community achievements</div>
      <p className="season-archetype-opens-lede season-board-muted">
        Minted STARs (completed claims) in the selected season, grouped by card archetype.
      </p>

      {catalogLoading ? (
        <div className="season-board-muted">Loading seasons…</div>
      ) : null}
      {catalogError ? <div className="season-board-muted">{catalogError}</div> : null}

      {!catalogLoading && seasons.length > 0 ? (
        <div className="season-archetype-opens-toolbar">
          <label className="season-archetype-opens-label" htmlFor="season-archetype-season">
            Season
          </label>
          <select
            id="season-archetype-season"
            className="season-archetype-opens-select"
            value={seasonId ?? ""}
            onChange={(e) => {
              const next = Number(e.target.value);
              if (!Number.isNaN(next)) {
                setSeasonId(next);
              }
            }}
          >
            {seasons.map((s) => (
              <option key={s.id} value={s.id}>
                {s.title}
                {!s.is_active ? " (ended)" : ""}
              </option>
            ))}
          </select>
        </div>
      ) : null}

      {!catalogLoading && !catalogError && seasons.length === 0 ? (
        <div className="season-board-muted">No seasons in the catalog yet.</div>
      ) : null}

      {statsLoading ? (
        <div className="season-board-muted">Loading counts…</div>
      ) : null}
      {statsError ? <div className="season-board-muted">{statsError}</div> : null}

      {!statsLoading && stats && !statsError ? (
        <>
          <div className="season-board-stats season-archetype-opens-total">
            <span>Total opened</span>
            <strong>{stats.total_opened}</strong>
          </div>
          {stats.total_opened === 0 ? (
            <div className="season-board-muted">No minted cards for this season yet.</div>
          ) : (
            <ul className="season-archetype-opens-grid" aria-label="Archetype counts">
              {rows.map(({ key, count }) => (
                <li key={key} className="season-archetype-opens-row">
                  <span className="season-archetype-opens-name">{key}</span>
                  <span className="season-archetype-opens-count">{count}</span>
                </li>
              ))}
            </ul>
          )}
        </>
      ) : null}

    </section>
  );
}
