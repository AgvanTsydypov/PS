"use client";

import Link from "next/link";
import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
} from "react";
import { fetchCardTicker, type CardTickerItem } from "../lib/userApiBase";

const TICKER_RETRY_DELAYS_MS = [3000, 8000, 15000, 30000];

/** Home ticker card size vs 249×386 artboard (0.96 display scale + 10% bump). */
const HOME_TICKER_CARD_SCALE = 0.96 * 1.1;
const TICKER_THUMB_PX = Math.round(249 * HOME_TICKER_CARD_SCALE);
const TICKER_THUMB_HEIGHT_PX = Math.round(386 * HOME_TICKER_CARD_SCALE);
const TICKER_GAP_PX = 84;
const TICKER_LINK_BORDER_PX = 2;
const TICKER_MAX_ITEMS_DESKTOP = 24;
const TICKER_MAX_ITEMS_COARSE = 10;
const TICKER_MAX_CELLS_COARSE = 56;
const TICKER_MAX_CELLS_FINE = 220;
const TICKER_MAX_SEGMENTS_COARSE = 4;
const TICKER_SPEED_MULT = 2.0;
const CARD_TICKER_PX_PER_SEC = 2124 / 70;

function clampTickerSegments(requested: number, itemCount: number, maxCells: number): number {
  if (itemCount <= 0) return 2;
  const byMemory = Math.max(2, Math.floor(maxCells / itemCount));
  return Math.min(requested, byMemory);
}

function segmentWidthPx(itemCount: number): number {
  if (itemCount <= 0) return 0;
  const cell = TICKER_THUMB_PX + TICKER_LINK_BORDER_PX;
  return itemCount * cell + (itemCount - 1) * TICKER_GAP_PX;
}

function tickerSegmentCount(itemCount: number, viewportWidthPx: number): number {
  if (itemCount <= 0) return 2;
  const seqW = segmentWidthPx(itemCount);
  if (seqW <= 0) return 2;
  const stripW = Math.min(1600, Math.max(320, viewportWidthPx - 32));
  const minTotalW = stripW * 2 + 120;
  const needed = Math.ceil(minTotalW / seqW);
  return Math.max(2, Math.min(needed, 24));
}

type GeneratedCardsTickerProps = {
  initialItems?: CardTickerItem[];
  centerContent?: ReactNode;
};

export default function GeneratedCardsTicker({
  initialItems = [],
  centerContent = null,
}: GeneratedCardsTickerProps) {
  const hasInitialItems = initialItems.length > 0;
  const [items, setItems] = useState<CardTickerItem[]>(initialItems);
  const [loading, setLoading] = useState(!hasInitialItems);
  const [viewportWidth, setViewportWidth] = useState(1200);
  const [tickerMaxCells, setTickerMaxCells] = useState(TICKER_MAX_CELLS_FINE);
  const [tickerLiteTheme, setTickerLiteTheme] = useState(false);
  const retryTimerRef = useRef<number | null>(null);
  const retryAttemptRef = useRef(0);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      if (retryTimerRef.current != null) {
        window.clearTimeout(retryTimerRef.current);
        retryTimerRef.current = null;
      }

      const payload = await fetchCardTicker({ retries: 3 });
      if (cancelled) {
        return;
      }

      if (payload) {
        retryAttemptRef.current = 0;
        const list = Array.isArray(payload.items) ? payload.items : [];
        setItems(list);
      } else {
        if (!hasInitialItems) {
          setItems([]);
        }
        const delayMs =
          TICKER_RETRY_DELAYS_MS[
            Math.min(retryAttemptRef.current, TICKER_RETRY_DELAYS_MS.length - 1)
          ] ?? 30000;
        retryAttemptRef.current += 1;
        retryTimerRef.current = window.setTimeout(() => {
          void load();
        }, delayMs);
      }
      setLoading(false);
    }

    void load();

    const handleOnline = () => {
      retryAttemptRef.current = 0;
      void load();
    };
    const handleVisibilityChange = () => {
      if (document.visibilityState !== "visible") return;
      retryAttemptRef.current = 0;
      void load();
    };

    window.addEventListener("online", handleOnline);
    document.addEventListener("visibilitychange", handleVisibilityChange);

    return () => {
      cancelled = true;
      window.removeEventListener("online", handleOnline);
      document.removeEventListener("visibilitychange", handleVisibilityChange);
      if (retryTimerRef.current != null) {
        window.clearTimeout(retryTimerRef.current);
        retryTimerRef.current = null;
      }
    };
  }, [hasInitialItems]);

  useEffect(() => {
    function onResize() {
      setViewportWidth(window.innerWidth);
    }
    onResize();
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const mqCoarse = window.matchMedia("(pointer: coarse)");
    const mqNarrow = window.matchMedia("(max-width: 768px)");
    const apply = () => {
      const reduceLoad = mqCoarse.matches || mqNarrow.matches;
      setTickerMaxCells(reduceLoad ? TICKER_MAX_CELLS_COARSE : TICKER_MAX_CELLS_FINE);
      setTickerLiteTheme(reduceLoad);
    };
    apply();
    mqCoarse.addEventListener("change", apply);
    mqNarrow.addEventListener("change", apply);
    return () => {
      mqCoarse.removeEventListener("change", apply);
      mqNarrow.removeEventListener("change", apply);
    };
  }, []);

  const visibleItems = useMemo(() => {
    if (!tickerLiteTheme) return items.slice(0, TICKER_MAX_ITEMS_DESKTOP);
    return items.slice(0, TICKER_MAX_ITEMS_COARSE);
  }, [items, tickerLiteTheme]);

  const tickerSegments = useMemo(() => {
    const raw = tickerSegmentCount(visibleItems.length, viewportWidth);
    if (tickerLiteTheme) {
      const clamped = clampTickerSegments(raw, visibleItems.length, tickerMaxCells);
      return Math.max(2, Math.min(clamped, TICKER_MAX_SEGMENTS_COARSE));
    }
    return clampTickerSegments(raw, visibleItems.length, tickerMaxCells);
  }, [visibleItems.length, viewportWidth, tickerMaxCells, tickerLiteTheme]);

  const tickerDurationSec = useMemo(() => {
    const widthPx = segmentWidthPx(visibleItems.length);
    let durationSec: number;
    if (widthPx <= 0) {
      durationSec = tickerLiteTheme ? 28 : 70;
    } else if (tickerLiteTheme) {
      durationSec = Math.max(14, widthPx / 44);
    } else {
      durationSec = Math.max(8, widthPx / CARD_TICKER_PX_PER_SEC);
    }
    return durationSec / TICKER_SPEED_MULT;
  }, [visibleItems.length, tickerLiteTheme]);

  const loopItems = useMemo(() => {
    const out: CardTickerItem[] = [];
    for (let segmentIndex = 0; segmentIndex < tickerSegments; segmentIndex += 1) {
      out.push(...visibleItems);
    }
    return out;
  }, [visibleItems, tickerSegments]);

  function renderHorizontalTicker() {
    if (visibleItems.length === 0) {
      return null;
    }

    return (
      <section className="card-ticker-strip card-ticker-strip-lite" aria-label="Home showcase">
        <div className="card-ticker-viewport card-ticker-viewport-lite">
          <div
            className="card-ticker-track card-ticker-track-lite"
            style={
              {
                "--ticker-segments": tickerSegments,
                "--ticker-duration": `${tickerDurationSec}s`,
              } as CSSProperties
            }
          >
            {loopItems.map((item, index) => {
              const cardId = `${item.slug}-${index}`;
              const label = item.card_title.trim() || item.slug || "Card";
              return (
                <Link
                  key={cardId}
                  href={`/cards/${encodeURIComponent(item.slug)}`}
                  className="card-ticker-item card-ticker-item-lite"
                  aria-label={`Open card: ${label}`}
                >
                  <article className="nft-card card-ticker-card card-ticker-card-lite">
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img
                      className="generated-card-image card-ticker-thumb"
                      src={item.front_image_url}
                      alt=""
                      width={TICKER_THUMB_PX}
                      height={TICKER_THUMB_HEIGHT_PX}
                      loading="lazy"
                      decoding="async"
                    />
                  </article>
                </Link>
              );
            })}
          </div>
        </div>
      </section>
    );
  }

  return (
    <section className="card-ticker-section card-ticker-section-home" aria-label="Home showcase">
      <div className="home-showcase-center home-showcase-center-mobile">
        {centerContent}
        {loading && items.length === 0 ? (
          <p className="home-showcase-status season-board-muted">Loading claimed cards...</p>
        ) : null}
      </div>
      {renderHorizontalTicker()}
    </section>
  );
}
