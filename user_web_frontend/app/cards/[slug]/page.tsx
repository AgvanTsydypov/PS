"use client";

import Link from "next/link";
import React, { useEffect, useState } from "react";

import SiteLogoLink from "../../../components/SiteLogoLink";

type GeneratedCardPayload = {
  card_title?: string;
  primary_tag?: string;
  secondary_tag?: string;
  season_type?: string;
  season_number?: number;
  archetype?: string;
  rarity_bracket?: string;
  leaderboard_rank?: number;
  proxy_wallet?: string;
  recurrence?: string | null;
  [key: string]: unknown;
};

type GeneratedCardItem = {
  collection_mint_number?: number | null;
  slug: string;
  owner_wallet: string;
  owner_proxy_wallet?: string | null;
  /** Polymarket proxy on the allocation row (winner_wallets_nft_to_claim.proxy_wallet). */
  winner_proxy_wallet?: string | null;
  winner_row_id: number;
  season_id: number;
  event_id?: string | null;
  event_slug?: string | null;
  card_title?: string | null;
  primary_tag?: string | null;
  secondary_tag?: string | null;
  pattern?: string | null;
  front_image_url: string;
  back_image_url: string;
  card_payload_json?: GeneratedCardPayload;
  event_snapshot?: {
    title?: string | null;
    description?: string | null;
    slug?: string | null;
    volume?: number | string | null;
    volume_24hr?: number | string | null;
    volume_1wk?: number | string | null;
    volume_1mo?: number | string | null;
    liquidity?: number | string | null;
    open_interest?: number | string | null;
    comment_count?: number | null;
    active?: boolean | null;
    closed?: boolean | null;
    start_date?: string | null;
    end_date?: string | null;
    closed_time?: string | null;
  };
  created_at: string;
};

const apiBase =
  process.env.NEXT_PUBLIC_USER_API_BASE_URL ??
  (process.env.NODE_ENV === "development" ? "http://localhost:8011" : "/");

function buildApiUrl(path: string): string {
  if (apiBase === "/") return path;
  return `${apiBase.replace(/\/$/, "")}${path}`;
}

function extractErrorMessage(error: unknown): string {
  if (error instanceof Error && error.message) return error.message;
  return "Request failed";
}

export default function CardDetailPage({ params }: { params: { slug: string } }) {
  const [card, setCard] = useState<GeneratedCardItem | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;

    async function loadCard() {
      setLoading(true);
      setError("");
      try {
        const res = await fetch(buildApiUrl(`/api/cards/${encodeURIComponent(params.slug)}`), {
          cache: "no-store",
        });
        if (!res.ok) {
          const text = await res.text();
          throw new Error(text || "Failed to load card");
        }
        const payload = (await res.json()) as { card?: GeneratedCardItem };
        if (!cancelled) {
          setCard(payload.card ?? null);
        }
      } catch (loadError) {
        if (!cancelled) {
          setError(extractErrorMessage(loadError));
          setCard(null);
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }

    void loadCard();
    return () => {
      cancelled = true;
    };
  }, [params.slug]);

  const payload = card?.card_payload_json ?? {};
  const event = card?.event_snapshot ?? {};
  const title =
    String(card?.card_title ?? payload.card_title ?? "").trim() || "Generated card";

  return (
    <main className="card-detail-page">
      <div className="card-detail-backlinks">
        <SiteLogoLink className="card-detail-logo-link" />
        <Link href="/me" className="card-detail-backlink card-detail-backlink-secondary">
          My dashboard
        </Link>
      </div>

      <section className="card-detail-shell">
        {loading ? (
          <div className="season-board-muted">Loading card...</div>
        ) : error ? (
          <pre className="eligibility-output">Card load failed: {error}</pre>
        ) : !card ? (
          <div className="season-board-muted">Card not found.</div>
        ) : (
          <>
            <div className="card-detail-heading">
              <div>
                <h1>{title}</h1>
                <div className="card-detail-meta">
                  Created: {new Date(card.created_at).toLocaleString()}
                </div>
              </div>
              <div className="card-detail-chip-row">
                <span className="card-detail-chip">Collection mint #{card.collection_mint_number ?? "N/A"}</span>
                <span className="card-detail-chip">
                  {String(payload.season_type ?? "season")} #{String(payload.season_number ?? card.season_id)}
                </span>
              </div>
            </div>

            <div className="card-detail-grid">
              <div className="card-detail-images">
                <section
                  className="card-detail-image-card"
                  style={{"--card-border-color": (card.card_payload_json as {border_color?: string} | undefined)?.border_color ?? "#B6BBC8"} as React.CSSProperties}
                >
                  <h2>Front</h2>
                  {/* Card assets are generated SVG files served by the backend static mount. */}
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img className="card-detail-image" src={card.front_image_url} alt={`${title} front`} />
                </section>
                <section
                  className="card-detail-image-card"
                  style={{"--card-border-color": (card.card_payload_json as {border_color?: string} | undefined)?.border_color ?? "#B6BBC8"} as React.CSSProperties}
                >
                  <h2>Back</h2>
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img className="card-detail-image" src={card.back_image_url} alt={`${title} back`} />
                </section>
              </div>

              <div className="card-detail-info">
                <section className="card-detail-panel">
                  <h2>Card Details</h2>
                  <dl className="card-detail-kv">
                    <dt>Claimer EOA wallet</dt>
                    <dd>{card.owner_wallet}</dd>
                    <dt>Claimer proxy wallet</dt>
                    <dd>{card.owner_proxy_wallet ?? "Not found"}</dd>
                    <dt>Winner proxy wallet</dt>
                    <dd>{card.winner_proxy_wallet ?? "N/A"}</dd>
                    <dt>Event title</dt>
                    <dd>{event.title ?? "N/A"}</dd>
                    <dt>Event ID</dt>
                    <dd>{card.event_id ?? "N/A"}</dd>
                    <dt>Event slug</dt>
                    <dd>{event.slug ?? card.event_slug ?? "N/A"}</dd>
                  </dl>
                </section>

                <section className="card-detail-panel">
                  <h2>Payload Snapshot</h2>
                  <pre>{JSON.stringify(payload, null, 2)}</pre>
                </section>
              </div>
            </div>
          </>
        )}
      </section>
    </main>
  );
}
