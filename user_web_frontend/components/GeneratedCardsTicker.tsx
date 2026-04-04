"use client";

import Link from "next/link";
import { useEffect, useMemo, useState, type CSSProperties } from "react";

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
  created_at?: string | null;
};

type CardTickerResponse = {
  items: CardTickerItem[];
  total: number;
  fetched_at: string;
};

const TICKER_THUMB_PX = Math.round(249 * 0.8);
const TICKER_GAP_PX = 14;
const TICKER_LINK_BORDER_PX = 2;

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

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const res = await fetch(buildApiUrl("/api/cards/ticker"), {
          cache: "no-store",
        });
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

  const segmentCount = useMemo(
    () => tickerSegmentCount(items.length, viewportWidth),
    [items.length, viewportWidth],
  );

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
      <h2 className="card-ticker-heading">Already Generated Cards</h2>
      <section className="card-ticker-strip" aria-label="Already generated cards">
        <div className="card-ticker-viewport">
          <div
            className="card-ticker-track"
            style={
              {
                "--ticker-segments": segmentCount,
              } as CSSProperties
            }
          >
            {loop.map((item, index) => {
              const label =
                item.card_title.trim() ||
                item.slug ||
                "Card";
              return (
                <Link
                  key={`${item.slug}-${index}`}
                  href={`/cards/${encodeURIComponent(item.slug)}`}
                  className="card-ticker-item-link"
                  aria-label={`Open card: ${label}`}
                >
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    className="card-ticker-thumb"
                    src={item.front_image_url}
                    alt=""
                    width={TICKER_THUMB_PX}
                    height={Math.round(386 * 0.8)}
                    loading="lazy"
                    decoding="async"
                  />
                </Link>
              );
            })}
          </div>
        </div>
      </section>
    </div>
  );
}
