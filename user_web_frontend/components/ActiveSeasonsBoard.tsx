"use client";

import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useMemo,
  useState,
  type ReactNode,
} from "react";

const apiBase =
  process.env.NEXT_PUBLIC_USER_API_BASE_URL ??
  (process.env.NODE_ENV === "development" ? "http://localhost:8011" : "/");

function buildApiUrl(path: string): string {
  if (apiBase === "/") return path;
  return `${apiBase.replace(/\/$/, "")}${path}`;
}

function extractErrorMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  if (typeof error === "object" && error !== null && "message" in error) {
    return String((error as { message: unknown }).message);
  }
  return JSON.stringify(error);
}

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

export type ActiveSeasonsBoardHandle = {
  refresh: () => Promise<void>;
};

type ActiveSeasonsBoardProps = {
  footer?: ReactNode;
};

export const ActiveSeasonsBoard = forwardRef<
  ActiveSeasonsBoardHandle,
  ActiveSeasonsBoardProps
>(function ActiveSeasonsBoard({ footer }, ref) {
  const [activeSeasons, setActiveSeasons] = useState<SeasonResponse[]>([]);
  const [seasonError, setSeasonError] = useState("");
  const [serverNowBaseMs, setServerNowBaseMs] = useState<number | null>(null);
  const [clientNowAtSyncMs, setClientNowAtSyncMs] = useState<number | null>(
    null,
  );
  const [syncedNowMs, setSyncedNowMs] = useState<number>(() => Date.now());

  async function refreshSeasonsFromApi() {
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

  useImperativeHandle(ref, () => ({
    refresh: refreshSeasonsFromApi,
  }));

  useEffect(() => {
    void refreshSeasonsFromApi();
  }, []);

  useEffect(() => {
    if (serverNowBaseMs == null || clientNowAtSyncMs == null) return;
    const tick = window.setInterval(() => {
      setSyncedNowMs(serverNowBaseMs + (Date.now() - clientNowAtSyncMs));
    }, 1000);
    return () => window.clearInterval(tick);
  }, [serverNowBaseMs, clientNowAtSyncMs]);

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
            timeLeft =
              days > 0
                ? `${days}d ${hours}h ${mins}m`
                : `${hours}h ${mins}m ${secs}s`;
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

  return (
    <section className="season-board season-board-standalone">
      <div className="season-board-title">Active seasons</div>
      {seasonError ? (
        <div className="season-board-muted">
          Unable to load seasons right now.
        </div>
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
                <strong>
                  {season.remaining} / {season.total}
                </strong>
              </div>
              <div className="season-tooltip">
                {season.description}
                {season.phaseReason ? ` ${season.phaseReason}` : ""}
              </div>
            </article>
          ))}
        </div>
      )}
      {footer}
    </section>
  );
});
