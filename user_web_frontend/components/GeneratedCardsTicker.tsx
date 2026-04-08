"use client";

import Link from "next/link";
import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
} from "react";
import {
  clearFlipTimers,
  handleCardGridMouseLeave,
  handleCardGridMouseMove,
  markCardPressStart,
  navigateToCardIfCenterClick,
  triggerCardFlip,
} from "./cardInteractions";

const apiBase =
  process.env.NEXT_PUBLIC_USER_API_BASE_URL ??
  (process.env.NODE_ENV === "development" ? "http://localhost:8011" : "/");

function buildApiUrl(path: string): string {
  if (apiBase === "/") return path;
  return `${apiBase.replace(/\/$/, "")}${path}`;
}

type CardTickerItem = {
  slug: string;
  card_title: string;
  front_image_url: string;
  back_image_url?: string | null;
  created_at?: string | null;
};

type CardTickerResponse = {
  items: CardTickerItem[];
  total: number;
  fetched_at: string;
};

/** Home ticker card size vs 249×386 artboard (0.96 display scale + 10% bump). */
const HOME_TICKER_CARD_SCALE = 0.96 * 1.1;
const TICKER_THUMB_PX = Math.round(249 * HOME_TICKER_CARD_SCALE);
const TICKER_THUMB_HEIGHT_PX = Math.round(386 * HOME_TICKER_CARD_SCALE);
const TICKER_GAP_PX = 28;
const TICKER_LINK_BORDER_PX = 2;

/** One full loop scrolls one segment; duration = segmentWidth / this → constant px/s regardless of card count. */
const CARD_TICKER_PX_PER_SEC = 2124 / 70;

/** Phones: cap total ticker cells (segments × items) to avoid tab/renderer OOM from hundreds of images + blend layers. */
const TICKER_MAX_CELLS_COARSE = 56;
const TICKER_MAX_CELLS_FINE = 220;
const TICKER_MAX_ITEMS_COARSE = 10;

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

export default function GeneratedCardsTicker() {
  const [items, setItems] = useState<CardTickerItem[]>([]);
  const [viewportWidth, setViewportWidth] = useState(1200);
  const [tickerMaxCells, setTickerMaxCells] = useState(TICKER_MAX_CELLS_FINE);
  const [tickerLiteTheme, setTickerLiteTheme] = useState(false);
  const [flippedCards, setFlippedCards] = useState<Record<string, boolean>>({});
  const [animatingCards, setAnimatingCards] = useState<Record<string, boolean>>({});
  const flipTimerRef = useRef<Record<string, number | null>>({});

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const res = await fetch(buildApiUrl("/api/cards/ticker"), {
          cache: "no-store",
        });
        if (res.status === 503) {
          if (!cancelled) setItems([]);
          return;
        }
        if (!res.ok) return;
        const payload = (await res.json()) as CardTickerResponse;
        const list = Array.isArray(payload.items) ? payload.items : [];
        if (!cancelled) setItems(list);
      } catch {
        if (!cancelled) setItems([]);
      }
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, []);

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

  useEffect(() => {
    return () => {
      clearFlipTimers(flipTimerRef);
    };
  }, []);

  const visibleItems = useMemo(() => {
    if (!tickerLiteTheme) return items;
    return items.slice(0, TICKER_MAX_ITEMS_COARSE);
  }, [items, tickerLiteTheme]);

  const segmentCount = useMemo(() => {
    if (tickerLiteTheme) return 1;
    const raw = tickerSegmentCount(visibleItems.length, viewportWidth);
    return clampTickerSegments(raw, visibleItems.length, tickerMaxCells);
  }, [visibleItems.length, viewportWidth, tickerMaxCells, tickerLiteTheme]);

  const tickerDurationSec = useMemo(() => {
    if (tickerLiteTheme) return 0;
    const w = segmentWidthPx(visibleItems.length);
    if (w <= 0) return 70;
    return Math.max(8, w / CARD_TICKER_PX_PER_SEC);
  }, [visibleItems.length, tickerLiteTheme]);

  const loop = useMemo(() => {
    if (tickerLiteTheme) return visibleItems;
    const out: CardTickerItem[] = [];
    for (let s = 0; s < segmentCount; s += 1) {
      out.push(...visibleItems);
    }
    return out;
  }, [visibleItems, segmentCount, tickerLiteTheme]);

  if (items.length === 0) return null;

  return (
    <div className="card-ticker-section">
      <h2 className="card-ticker-heading">CLAIMED CARDS SHOWCASE</h2>
      <section
        className={`card-ticker-strip${tickerLiteTheme ? " card-ticker-strip-lite" : ""}`}
        aria-label="CLAIMED CARDS SHOWCASE"
      >
        <div
          className={`card-ticker-viewport${tickerLiteTheme ? " card-ticker-viewport-lite" : ""}`}
          onMouseMove={
            tickerLiteTheme
              ? undefined
              : (event) =>
                  handleCardGridMouseMove(event, {
                    wrapperSelector: ".card-ticker-item",
                    cardSelector: ".card-ticker-card",
                  })
          }
          onMouseLeave={
            tickerLiteTheme
              ? undefined
              : (event) => handleCardGridMouseLeave(event, ".card-ticker-card")
          }
        >
          <div
            className={`card-ticker-track${tickerLiteTheme ? " card-ticker-track-lite" : ""}`}
            style={
              tickerLiteTheme
                ? undefined
                : ({
                    "--ticker-segments": segmentCount,
                    "--ticker-duration": `${tickerDurationSec}s`,
                  } as CSSProperties)
            }
          >
            {loop.map((item, index) => {
              const cardId = tickerLiteTheme ? item.slug : `${item.slug}-${index}`;
              const label =
                item.card_title.trim() ||
                item.slug ||
                "Card";
              const isFlipped = Boolean(flippedCards[cardId]);
              const isAnimating = Boolean(animatingCards[cardId]);
              const backImageUrl = item.back_image_url || item.front_image_url;
              if (tickerLiteTheme) {
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
              }
              return (
                <div key={cardId} className="card-ticker-item">
                  <Link
                    href={`/cards/${encodeURIComponent(item.slug)}`}
                    className="card-center-hotspot"
                    tabIndex={-1}
                    aria-label={`Open card: ${label}`}
                  />
                  <article
                    className={`nft-card nft-card-tilt ${tickerLiteTheme ? "theme-subtle" : "theme-vivid"} card-ticker-card ${isAnimating ? "generated-card-preview-card-flipping" : ""}`}
                    data-center-navigate="1"
                    onPointerDown={(event) => {
                      markCardPressStart(event.currentTarget, event.clientX, event.clientY);
                    }}
                    onClick={(event) => {
                      if (
                        navigateToCardIfCenterClick(
                          event.currentTarget,
                          item.slug,
                          event.clientX,
                          event.clientY,
                        )
                      ) {
                        return;
                      }
                      triggerCardFlip(
                        cardId,
                        event.currentTarget,
                        flipTimerRef,
                        setAnimatingCards,
                        setFlippedCards,
                      );
                    }}
                    onKeyDown={(event) => {
                      if (event.key !== "Enter" && event.key !== " ") return;
                      event.preventDefault();
                      triggerCardFlip(
                        cardId,
                        event.currentTarget,
                        flipTimerRef,
                        setAnimatingCards,
                        setFlippedCards,
                      );
                    }}
                    role="button"
                    tabIndex={0}
                    aria-label={`Open or flip card: ${label}`}
                  >
                    <div className={`generated-card-flip-inner ${isFlipped ? "is-flipped" : ""}`}>
                      <div className="generated-card-flip-face generated-card-flip-face-front">
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
                      </div>
                      <div className="generated-card-flip-face generated-card-flip-face-back">
                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        <img
                          className="generated-card-image card-ticker-thumb"
                          src={backImageUrl}
                          alt=""
                          width={TICKER_THUMB_PX}
                          height={TICKER_THUMB_HEIGHT_PX}
                          loading="lazy"
                          decoding="async"
                        />
                      </div>
                    </div>
                  </article>
                </div>
              );
            })}
          </div>
        </div>
      </section>
    </div>
  );
}
