"use client";

import Link from "next/link";
import React, { useEffect, useState } from "react";

import SiteLogoLink from "../../../components/SiteLogoLink";
import { isSafeExternalUrl } from "../../../components/cardInteractions";
import { fetchPublicUserApiJsonResult } from "../../../lib/userApiBase";

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
  asset_address?: string | null;
  explorer_asset_url?: string | null;
  magiceden_url?: string | null;
};

type LoadStatus = "loading" | "ok" | "not-found" | "error";

export default function CardDetailPage({ params }: { params: { slug: string } }) {
  const [card, setCard] = useState<GeneratedCardItem | null>(null);
  const [status, setStatus] = useState<LoadStatus>("loading");
  const [reloadTick, setReloadTick] = useState(0);

  useEffect(() => {
    let cancelled = false;

    async function loadCard() {
      setStatus("loading");
      // The upstream sporadically 503s (nginx -> backend), so retry transient
      // failures with exponential backoff before surfacing an error to the user.
      const result = await fetchPublicUserApiJsonResult<{ card?: GeneratedCardItem }>(
        `/api/cards/${encodeURIComponent(params.slug)}`,
        { retries: 3 },
      );
      if (cancelled) return;

      if (result.kind === "ok") {
        setCard(result.data.card ?? null);
        setStatus(result.data.card ? "ok" : "not-found");
        return;
      }
      if (result.kind === "not-found") {
        setCard(null);
        setStatus("not-found");
        return;
      }
      setCard(null);
      setStatus("error");
    }

    void loadCard();
    return () => {
      cancelled = true;
    };
  }, [params.slug, reloadTick]);

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
        {status === "loading" ? (
          <div className="season-board-muted">Loading card...</div>
        ) : status === "error" ? (
          <div className="season-board-muted card-detail-error">
            <p>Card is temporarily unavailable. Please try again in a moment.</p>
            <button
              type="button"
              className="card-detail-retry"
              onClick={() => setReloadTick((n) => n + 1)}
            >
              Retry
            </button>
          </div>
        ) : status === "not-found" || !card ? (
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
              <section
                className="card-detail-image-card card-detail-image-card-front"
                style={{"--card-border-color": (card.card_payload_json as {border_color?: string} | undefined)?.border_color ?? "#B6BBC8"} as React.CSSProperties}
              >
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img className="card-detail-image" src={card.front_image_url} alt={`${title} front`} />
              </section>

              <div className="card-detail-info">
                <section className="card-detail-panel">
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
                    <dt>Solana Explorer</dt>
                    <dd>
                      {isSafeExternalUrl(card.explorer_asset_url) ? (
                        <a
                          href={card.explorer_asset_url!}
                          target="_blank"
                          rel="noopener noreferrer"
                        >
                          {card.asset_address ?? "Open on Explorer"}
                        </a>
                      ) : (
                        "N/A"
                      )}
                    </dd>
                    <dt>Magic Eden</dt>
                    <dd>
                      {isSafeExternalUrl(card.magiceden_url) ? (
                        <a
                          href={card.magiceden_url!}
                          target="_blank"
                          rel="noopener noreferrer"
                        >
                          {card.asset_address ?? "Open on Magic Eden"}
                        </a>
                      ) : (
                        "N/A"
                      )}
                    </dd>
                  </dl>
                </section>
              </div>

              <section
                className="card-detail-image-card card-detail-image-card-back"
                style={{"--card-border-color": (card.card_payload_json as {border_color?: string} | undefined)?.border_color ?? "#B6BBC8"} as React.CSSProperties}
              >
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img className="card-detail-image" src={card.back_image_url} alt={`${title} back`} />
              </section>
            </div>
          </>
        )}
      </section>
    </main>
  );
}
