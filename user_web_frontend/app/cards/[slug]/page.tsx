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
  /** Polymarket proxy on the allocation row (claims.proxy_wallet). */
  winner_proxy_wallet?: string | null;
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
  /** Etherscan / L2 block-explorer URL — backend computes it from
   *  ``asset_address`` ("<contract>/<tokenId>") + ``EVM_CHAIN_ID``. */
  explorer_asset_url?: string | null;
  /** OpenSea item page URL — same source as ``explorer_asset_url``; null
   *  on preview rows or chains OpenSea doesn't index. */
  opensea_asset_url?: string | null;
  /** Public Pinata gateway URL for the on-chain metadata JSON, normalized
   *  from ``claims.metadata_uri`` (handles ``ipfs://`` and dedicated
   *  gateways). Null on preview rows. */
  metadata_uri?: string | null;
  /** Backend flag: ``true`` when the slug was found in the preview buffer
   *  rather than the minted ``claims`` table. Drives the conditional render
   *  for minted-only chips (mint number, explorer links). */
  is_preview?: boolean;
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

  const claimerWallet = card?.is_preview ? "preview" : (card?.owner_wallet ?? "N/A");
  const starWallet = card?.winner_proxy_wallet ?? null;
  // Preview rows carry a randomised origin/looter on the rendered SVG so the
  // showcase looks varied, but the page row should advertise the row's actual
  // status — there is no real claim behind it yet.
  const claimTypeRaw = String(
    (payload as { claim_type?: string }).claim_type ?? "",
  ).trim().toUpperCase();
  const claimType = card?.is_preview ? "PREVIEW" : (claimTypeRaw || "—");

  const eventTitle = event.title ?? null;
  const eventId = card?.event_id ?? null;
  const eventSlug = event.slug ?? card?.event_slug ?? null;
  const polymarketUrl = eventSlug ? `https://polymarket.com/event/${encodeURIComponent(eventSlug)}` : null;

  const polygonscanStarUrl = starWallet ? `https://polygonscan.com/address/${starWallet}` : null;
  const explorerUrl = card?.explorer_asset_url ?? null;
  const openseaUrl = card?.opensea_asset_url ?? null;
  const ipfsUrl = card?.metadata_uri ?? null;

  return (
    <main className="card-detail-page">
      <div className="card-detail-backlinks">
        <SiteLogoLink className="card-detail-logo-link" colorful />
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
                {/* Minted-only chip: only the on-chain row carries a mint
                    number. Preview rows have no slot allocated yet. */}
                {!card.is_preview ? (
                  <span className="card-detail-chip">
                    Mint #{card.collection_mint_number ?? "N/A"}
                  </span>
                ) : (
                  <span className="card-detail-chip card-detail-chip-preview">
                    CARD PREVIEW. CAN BE ROLLED TO CLAIM.
                  </span>
                )}
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
                  <h2 className="card-detail-section-heading">IDENTITY</h2>
                  <dl className="card-detail-kv">
                    {/* Preview cards carry a synthetic ``owner_wallet`` (random
                        hex burner the simulator generated to satisfy the CHECK
                        constraint). Showing "preview" keeps the page honest
                        about who hasn't claimed yet. */}
                    <dt>Claimer wallet</dt>
                    <dd>{claimerWallet}</dd>
                    <dt>Star wallet</dt>
                    <dd>{starWallet ?? "N/A"}</dd>
                    <dt>Claim type</dt>
                    <dd>{claimType}</dd>
                  </dl>
                </section>

                <section className="card-detail-panel">
                  <h2 className="card-detail-section-heading">POLYMARKET EVENT</h2>
                  <dl className="card-detail-kv">
                    <dt>Title</dt>
                    <dd>{eventTitle ?? "N/A"}</dd>
                    <dt>ID</dt>
                    <dd>{eventId ?? "N/A"}</dd>
                    <dt>Slug</dt>
                    <dd>{eventSlug ?? "N/A"}</dd>
                    <dt>View on Polymarket</dt>
                    <dd>
                      {isSafeExternalUrl(polymarketUrl) ? (
                        <a href={polymarketUrl!} target="_blank" rel="noopener noreferrer">
                          Open ↗
                        </a>
                      ) : (
                        "N/A"
                      )}
                    </dd>
                  </dl>
                </section>

                {/* Onchain links exist only after the cron worker promotes
                    the preview row to a minted claim. Preview cards have no
                    on-chain identity yet. */}
                {!card.is_preview ? (
                  <section className="card-detail-panel">
                    <h2 className="card-detail-section-heading">ONCHAIN RECORD</h2>
                    <dl className="card-detail-kv">
                      <dt>View Star wallet</dt>
                      <dd>
                        {isSafeExternalUrl(polygonscanStarUrl) ? (
                          <a href={polygonscanStarUrl!} target="_blank" rel="noopener noreferrer">
                            Polygonscan ↗
                          </a>
                        ) : (
                          "N/A"
                        )}
                      </dd>
                      <dt>View mint</dt>
                      <dd>
                        {isSafeExternalUrl(explorerUrl) ? (
                          <a href={explorerUrl!} target="_blank" rel="noopener noreferrer">
                            Etherscan ↗
                          </a>
                        ) : (
                          "N/A"
                        )}
                      </dd>
                      <dt>View on OpenSea</dt>
                      <dd>
                        {isSafeExternalUrl(openseaUrl) ? (
                          <a href={openseaUrl!} target="_blank" rel="noopener noreferrer">
                            OpenSea ↗
                          </a>
                        ) : (
                          "N/A"
                        )}
                      </dd>
                      <dt>View IPFS</dt>
                      <dd>
                        {isSafeExternalUrl(ipfsUrl) ? (
                          <a href={ipfsUrl!} target="_blank" rel="noopener noreferrer">
                            IPFS ↗
                          </a>
                        ) : (
                          "N/A"
                        )}
                      </dd>
                    </dl>
                  </section>
                ) : null}
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
