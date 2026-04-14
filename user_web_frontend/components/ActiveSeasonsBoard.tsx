"use client";

import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  fetchActiveSeasons,
  fetchServerTime,
  type SeasonResponse,
} from "../lib/userApiBase";

const SEASONS_RETRY_DELAYS_MS = [3000, 8000, 15000, 30000];

export type ActiveSeasonsBoardHandle = {
  refresh: () => Promise<void>;
};

type ActiveSeasonsBoardProps = {
  footer?: ReactNode;
  initialSeasons?: SeasonResponse[];
  initialServerNowIso?: string | null;
  title?: string;
};

export const ActiveSeasonsBoard = forwardRef<
  ActiveSeasonsBoardHandle,
  ActiveSeasonsBoardProps
>(function ActiveSeasonsBoard(
  { footer, initialSeasons = [], initialServerNowIso = null, title = "Active seasons" },
  ref,
) {
  const initialServerNowMs = initialServerNowIso
    ? Date.parse(initialServerNowIso)
    : Number.NaN;
  const hasInitialServerTime = !Number.isNaN(initialServerNowMs);
  const [activeSeasons, setActiveSeasons] = useState<SeasonResponse[]>(initialSeasons);
  const [seasonError, setSeasonError] = useState("");
  const [loading, setLoading] = useState(initialSeasons.length === 0);
  const [serverNowBaseMs, setServerNowBaseMs] = useState<number | null>(
    hasInitialServerTime ? initialServerNowMs : null,
  );
  const [clientNowAtSyncMs, setClientNowAtSyncMs] = useState<number | null>(
    hasInitialServerTime ? Date.now() : null,
  );
  const [syncedNowMs, setSyncedNowMs] = useState<number>(
    hasInitialServerTime ? initialServerNowMs : Date.now(),
  );
  const activeSeasonsRef = useRef(activeSeasons);
  const retryTimerRef = useRef<number | null>(null);
  const retryAttemptRef = useRef(0);

  useEffect(() => {
    activeSeasonsRef.current = activeSeasons;
  }, [activeSeasons]);

  const refreshSeasonsFromApi = useCallback(async () => {
    if (retryTimerRef.current != null) {
      window.clearTimeout(retryTimerRef.current);
      retryTimerRef.current = null;
    }

    const hadSeasons = activeSeasonsRef.current.length > 0;
    if (!hadSeasons) {
      setLoading(true);
    }
    setSeasonError("");

    const [timePayload, allSeasons] = await Promise.all([
      fetchServerTime({ retries: 3 }),
      fetchActiveSeasons({ retries: 3 }),
    ]);

    const parsedMs = Date.parse(String(timePayload?.now_utc_iso ?? ""));
    if (!Number.isNaN(parsedMs)) {
      setServerNowBaseMs(parsedMs);
      setClientNowAtSyncMs(Date.now());
      setSyncedNowMs(parsedMs);
    }

    if (allSeasons) {
      retryAttemptRef.current = 0;
      setActiveSeasons(allSeasons);
      setLoading(false);
      return;
    }

    if (!hadSeasons) {
      setSeasonError("Unable to load seasons right now.");
    }
    setLoading(false);

    const delayMs =
      SEASONS_RETRY_DELAYS_MS[
        Math.min(retryAttemptRef.current, SEASONS_RETRY_DELAYS_MS.length - 1)
      ] ?? 30000;
    retryAttemptRef.current += 1;
    retryTimerRef.current = window.setTimeout(() => {
      void refreshSeasonsFromApi();
    }, delayMs);
  }, []);

  useImperativeHandle(ref, () => ({
    refresh: refreshSeasonsFromApi,
  }), [refreshSeasonsFromApi]);

  useEffect(() => {
    void refreshSeasonsFromApi();

    const handleOnline = () => {
      retryAttemptRef.current = 0;
      void refreshSeasonsFromApi();
    };
    const handleVisibilityChange = () => {
      if (document.visibilityState !== "visible") return;
      retryAttemptRef.current = 0;
      void refreshSeasonsFromApi();
    };

    window.addEventListener("online", handleOnline);
    document.addEventListener("visibilitychange", handleVisibilityChange);

    return () => {
      window.removeEventListener("online", handleOnline);
      document.removeEventListener("visibilitychange", handleVisibilityChange);
      if (retryTimerRef.current != null) {
        window.clearTimeout(retryTimerRef.current);
        retryTimerRef.current = null;
      }
    };
  }, [refreshSeasonsFromApi]);

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
      <div className="season-board-title">{title}</div>
      {seasonError && seasonCards.length === 0 ? (
        <div className="season-board-muted">
          Unable to load seasons right now.
        </div>
      ) : null}
      {loading && seasonCards.length === 0 ? (
        <div className="season-board-muted">Loading active seasons...</div>
      ) : null}
      {seasonCards.length === 0 && !seasonError && !loading ? (
        <div className="season-board-muted">No active seasons right now.</div>
      ) : null}
      {seasonCards.length > 0 ? (
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
      ) : null}
      {footer}
    </section>
  );
});
