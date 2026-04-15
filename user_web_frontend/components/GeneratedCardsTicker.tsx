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
import {
  clearFlipTimers,
  handleCardGridMouseLeave,
  handleCardGridMouseMove,
  markCardPressStart,
  navigateToCardIfCenterClick,
  triggerCardFlip,
} from "./cardInteractions";
import { fetchCardTicker, type CardTickerItem } from "../lib/userApiBase";

const TICKER_RETRY_DELAYS_MS = [3000, 8000, 15000, 30000];

/** Home ticker card size vs 249×386 artboard (0.96 display scale + 10% bump). */
const HOME_TICKER_CARD_SCALE = 0.96 * 1.1;
const TICKER_THUMB_PX = Math.round(249 * HOME_TICKER_CARD_SCALE);
const TICKER_THUMB_HEIGHT_PX = Math.round(386 * HOME_TICKER_CARD_SCALE);
const TICKER_GAP_PX = 28;
const TICKER_LINK_BORDER_PX = 2;
const TICKER_MAX_ITEMS_DESKTOP = 24;
const TICKER_MAX_ITEMS_COARSE = 10;
const TICKER_MAX_CELLS_COARSE = 56;
const TICKER_MAX_CELLS_FINE = 220;
const TICKER_MAX_SEGMENTS_COARSE = 4;
const CARD_TICKER_PX_PER_SEC = 2124 / 70;
const PANEL_SECONDS_PER_CARD = 6.5;
const PANEL_SECONDS_PER_CARD_LITE = 4.5;

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

function splitPanelItems(items: CardTickerItem[]): {
  leftPanelItems: CardTickerItem[];
  rightPanelItems: CardTickerItem[];
} {
  if (items.length <= 1) {
    return {
      leftPanelItems: items,
      rightPanelItems: items,
    };
  }

  const leftPanelItems = items.filter((_, index) => index % 2 === 0);
  const rightPanelItems = items.filter((_, index) => index % 2 === 1);

  return {
    leftPanelItems: leftPanelItems.length > 0 ? leftPanelItems : items,
    rightPanelItems: rightPanelItems.length > 0 ? rightPanelItems : items,
  };
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
  const [flippedCards, setFlippedCards] = useState<Record<string, boolean>>({});
  const [animatingCards, setAnimatingCards] = useState<Record<string, boolean>>({});
  const flipTimerRef = useRef<Record<string, number | null>>({});
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

  useEffect(() => {
    if (typeof document === "undefined") return;
    const className = "home-page-lock-scroll";
    if (tickerLiteTheme) {
      document.body.classList.remove(className);
      return;
    }
    document.body.classList.add(className);
    return () => {
      document.body.classList.remove(className);
    };
  }, [tickerLiteTheme]);

  useEffect(() => {
    return () => {
      clearFlipTimers(flipTimerRef);
    };
  }, []);

  const visibleItems = useMemo(() => {
    if (!tickerLiteTheme) return items.slice(0, TICKER_MAX_ITEMS_DESKTOP);
    return items.slice(0, TICKER_MAX_ITEMS_COARSE);
  }, [items, tickerLiteTheme]);

  const liteSegmentCount = useMemo(() => {
    const raw = tickerSegmentCount(visibleItems.length, viewportWidth);
    if (tickerLiteTheme) {
      const clamped = clampTickerSegments(raw, visibleItems.length, tickerMaxCells);
      return Math.max(2, Math.min(clamped, TICKER_MAX_SEGMENTS_COARSE));
    }
    return clampTickerSegments(raw, visibleItems.length, tickerMaxCells);
  }, [visibleItems.length, viewportWidth, tickerMaxCells, tickerLiteTheme]);

  const liteTickerDurationSec = useMemo(() => {
    const widthPx = segmentWidthPx(visibleItems.length);
    if (widthPx <= 0) return tickerLiteTheme ? 28 : 70;
    if (tickerLiteTheme) {
      return Math.max(14, widthPx / 44);
    }
    return Math.max(8, widthPx / CARD_TICKER_PX_PER_SEC);
  }, [visibleItems.length, tickerLiteTheme]);

  const liteLoopItems = useMemo(() => {
    const out: CardTickerItem[] = [];
    for (let segmentIndex = 0; segmentIndex < liteSegmentCount; segmentIndex += 1) {
      out.push(...visibleItems);
    }
    return out;
  }, [visibleItems, liteSegmentCount]);

  const { leftPanelItems, rightPanelItems } = useMemo(
    () => splitPanelItems(visibleItems),
    [visibleItems],
  );

  const panelDurationSec = useMemo(() => {
    const secondsPerCard = tickerLiteTheme ? PANEL_SECONDS_PER_CARD_LITE : PANEL_SECONDS_PER_CARD;
    return Math.max(tickerLiteTheme ? 18 : 28, visibleItems.length * secondsPerCard);
  }, [visibleItems.length, tickerLiteTheme]);

  function handlePanelCardFlip(
    interactionId: string,
    target: HTMLElement,
  ) {
    triggerCardFlip(
      interactionId,
      target,
      flipTimerRef,
      setAnimatingCards,
      setFlippedCards,
    );
  }

  function renderPanelItem(
    item: CardTickerItem,
    panelSide: "left" | "right",
    itemIndex: number,
    isClone: boolean,
  ) {
    const cardId = `${panelSide}-${isClone ? "clone" : "primary"}-${item.slug}-${itemIndex}`;
    const interactionId = `${panelSide}-${item.slug}-${itemIndex}`;
    const label = item.card_title.trim() || item.slug || "Card";
    const isFlipped = Boolean(flippedCards[interactionId]);
    const isAnimating = Boolean(animatingCards[interactionId]);
    const backImageUrl = item.back_image_url || item.front_image_url;

    if (tickerLiteTheme) {
      const cardMarkup = (
        <article className="nft-card card-ticker-card card-ticker-card-lite home-panel-card">
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
      );

      if (isClone) {
        return (
          <div key={cardId} className="card-ticker-item card-ticker-item-lite home-card-panel-item">
            {cardMarkup}
          </div>
        );
      }

      return (
        <Link
          key={cardId}
          href={`/cards/${encodeURIComponent(item.slug)}`}
          className="card-ticker-item card-ticker-item-lite home-card-panel-item"
          aria-label={`Open card: ${label}`}
        >
          {cardMarkup}
        </Link>
      );
    }

    return (
      <div
        key={cardId}
        className="card-ticker-item home-card-panel-item"
        aria-hidden={isClone || undefined}
      >
        <Link
          href={`/cards/${encodeURIComponent(item.slug)}`}
          className="card-center-hotspot"
          tabIndex={-1}
          aria-label={`Open card: ${label}`}
        />
        {(["top", "right", "bottom", "left"] as const).map((edge) => (
          <div
            key={`${cardId}-${edge}`}
            className={`card-flip-hitbox card-flip-hitbox-${edge}`}
            aria-hidden="true"
            onPointerDown={(event) => {
              event.preventDefault();
              event.stopPropagation();
            }}
            onClick={(event) => {
              event.preventDefault();
              event.stopPropagation();
              const card = event.currentTarget.parentElement?.querySelector<HTMLElement>(".home-panel-card");
              if (!card) return;
              handlePanelCardFlip(interactionId, card);
            }}
          />
        ))}
        <article
          className={`nft-card nft-card-tilt theme-vivid card-ticker-card home-panel-card ${isAnimating ? "generated-card-preview-card-flipping" : ""}`}
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
            handlePanelCardFlip(interactionId, event.currentTarget);
          }}
          onKeyDown={(event) => {
            if (event.key !== "Enter" && event.key !== " ") return;
            event.preventDefault();
            handlePanelCardFlip(interactionId, event.currentTarget);
          }}
          role="button"
          tabIndex={isClone ? -1 : 0}
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
  }

  function renderPanelSegment(
    panelItems: CardTickerItem[],
    panelSide: "left" | "right",
    segmentKey: string,
    isClone: boolean,
  ) {
    return (
      <div
        key={segmentKey}
        className={`home-card-panel-segment${isClone ? " is-clone" : ""}`}
        aria-hidden={isClone}
      >
        {panelItems.map((item, index) => renderPanelItem(item, panelSide, index, isClone))}
      </div>
    );
  }

  function renderPanel(
    panelSide: "left" | "right",
    panelItems: CardTickerItem[],
    direction: "up" | "down",
  ) {
    if (panelItems.length === 0) {
      return null;
    }

    return (
      <aside className={`home-card-panel home-card-panel-${panelSide}`}>
        <div
          className={`home-card-panel-viewport${tickerLiteTheme ? " home-card-panel-viewport-lite" : ""}`}
          onMouseMove={
            tickerLiteTheme
              ? undefined
              : (event) =>
                  handleCardGridMouseMove(event, {
                    wrapperSelector: ".home-card-panel-item",
                    cardSelector: ".home-panel-card",
                  })
          }
          onMouseLeave={
            tickerLiteTheme
              ? undefined
              : (event) => handleCardGridMouseLeave(event, ".home-panel-card")
          }
        >
          <div
            className={`home-card-panel-track home-card-panel-track-${direction}${tickerLiteTheme ? " home-card-panel-track-lite" : ""}`}
            style={
              {
                "--home-panel-duration": `${panelDurationSec}s`,
              } as CSSProperties
            }
          >
            {renderPanelSegment(panelItems, panelSide, `${panelSide}-primary`, false)}
            {renderPanelSegment(panelItems, panelSide, `${panelSide}-clone`, true)}
          </div>
        </div>
      </aside>
    );
  }

  function renderLiteTicker() {
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
                "--ticker-segments": liteSegmentCount,
                "--ticker-duration": `${liteTickerDurationSec}s`,
              } as CSSProperties
            }
          >
            {liteLoopItems.map((item, index) => {
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

  if (tickerLiteTheme) {
    return (
      <section className="card-ticker-section card-ticker-section-home" aria-label="Home showcase">
        <div className="home-showcase-center home-showcase-center-mobile">
          {centerContent}
          {loading && items.length === 0 ? (
            <p className="home-showcase-status season-board-muted">Loading claimed cards...</p>
          ) : null}
        </div>
        {renderLiteTicker()}
      </section>
    );
  }

  return (
    <section className="card-ticker-section card-ticker-section-home" aria-label="Home showcase">
      <div className="home-showcase-shell">
        {renderPanel("left", leftPanelItems, "up")}
        <div className="home-showcase-center">
          {centerContent}
          {loading && items.length === 0 ? (
            <p className="home-showcase-status season-board-muted">Loading claimed cards...</p>
          ) : null}
        </div>
        {renderPanel("right", rightPanelItems, "down")}
      </div>
    </section>
  );
}
