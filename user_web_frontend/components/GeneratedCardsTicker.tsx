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
    return () => {
      clearFlipTimers(flipTimerRef);
    };
  }, []);

  const segmentCount = useMemo(
    () => tickerSegmentCount(items.length, viewportWidth),
    [items.length, viewportWidth],
  );

  const tickerDurationSec = useMemo(() => {
    const w = segmentWidthPx(items.length);
    if (w <= 0) return 70;
    return Math.max(8, w / CARD_TICKER_PX_PER_SEC);
  }, [items.length]);

  const loop = useMemo(() => {
    const out: CardTickerItem[] = [];
    for (let s = 0; s < segmentCount; s += 1) {
      out.push(...items);
    }
    return out;
  }, [items, segmentCount]);

  if (items.length === 0) return null;

  return (
    <div className="card-ticker-section">
      <h2 className="card-ticker-heading">CLAIMED CARDS SHOWCASE</h2>
      <section className="card-ticker-strip" aria-label="CLAIMED CARDS SHOWCASE">
        <div
          className="card-ticker-viewport"
          onMouseMove={(event) =>
            handleCardGridMouseMove(event, {
              wrapperSelector: ".card-ticker-item",
              cardSelector: ".card-ticker-card",
            })
          }
          onMouseLeave={(event) => handleCardGridMouseLeave(event, ".card-ticker-card")}
        >
          <div
            className="card-ticker-track"
            style={
              {
                "--ticker-segments": segmentCount,
                "--ticker-duration": `${tickerDurationSec}s`,
              } as CSSProperties
            }
          >
            {loop.map((item, index) => {
              const cardId = `${item.slug}-${index}`;
              const label =
                item.card_title.trim() ||
                item.slug ||
                "Card";
              const isFlipped = Boolean(flippedCards[cardId]);
              const isAnimating = Boolean(animatingCards[cardId]);
              const backImageUrl = item.back_image_url || item.front_image_url;
              return (
                <div key={cardId} className="card-ticker-item">
                  <Link
                    href={`/cards/${encodeURIComponent(item.slug)}`}
                    className="card-center-hotspot"
                    tabIndex={-1}
                    aria-label={`Open card: ${label}`}
                  />
                  <article
                    className={`nft-card nft-card-tilt theme-vivid card-ticker-card ${isAnimating ? "generated-card-preview-card-flipping" : ""}`}
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
