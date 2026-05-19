"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import {
  fetchSeasonEvents,
  fetchSeasonsList,
  type SeasonEventEntry,
  type SeasonsListEntry,
} from "../lib/userApiBase";

const POLYMARKET_EVENT_BASE = "https://polymarket.com/event/";

export default function EventsBrowser() {
  const [seasons, setSeasons] = useState<SeasonsListEntry[]>([]);
  const [seasonsLoading, setSeasonsLoading] = useState(true);
  const [seasonsError, setSeasonsError] = useState("");

  const [selectedSeasonId, setSelectedSeasonId] = useState<number | null>(null);
  const [events, setEvents] = useState<SeasonEventEntry[]>([]);
  const [eventsLoading, setEventsLoading] = useState(false);
  const [eventsError, setEventsError] = useState("");

  useEffect(() => {
    let cancelled = false;
    setSeasonsLoading(true);
    fetchSeasonsList()
      .then((data) => {
        if (cancelled) return;
        if (!data) {
          setSeasonsError("Could not load seasons.");
          setSeasons([]);
        } else {
          setSeasonsError("");
          setSeasons(data);
          if (data.length > 0) {
            const activeSeason = data.find((s) => s.is_active);
            setSelectedSeasonId((activeSeason ?? data[0]).id);
          }
        }
      })
      .finally(() => {
        if (!cancelled) setSeasonsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (selectedSeasonId == null) {
      setEvents([]);
      return;
    }
    let cancelled = false;
    setEventsLoading(true);
    setEventsError("");
    fetchSeasonEvents(selectedSeasonId)
      .then((data) => {
        if (cancelled) return;
        if (!data) {
          setEventsError("Could not load events for this season.");
          setEvents([]);
        } else {
          setEvents(data.events);
        }
      })
      .finally(() => {
        if (!cancelled) setEventsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedSeasonId]);

  const selectedSeason = useMemo(
    () => seasons.find((s) => s.id === selectedSeasonId) ?? null,
    [seasons, selectedSeasonId],
  );

  return (
    <div className="events-page">
      <header className="events-page-header">
        <Link href="/" className="events-back-link">
          ← Home
        </Link>
        <h1 className="events-page-title">Season Events</h1>
        <p className="events-page-lede">
          Pick a season to see the events whose participants are eligible to mint inside it.
        </p>
      </header>

      <section className="events-selector-row">
        <label htmlFor="events-season-select" className="events-selector-label">
          Season
        </label>
        <select
          id="events-season-select"
          className="events-season-select"
          value={selectedSeasonId ?? ""}
          onChange={(e) => {
            const v = e.target.value;
            setSelectedSeasonId(v === "" ? null : Number(v));
          }}
          disabled={seasonsLoading || seasons.length === 0}
        >
          {seasonsLoading ? (
            <option value="">Loading…</option>
          ) : seasons.length === 0 ? (
            <option value="">No seasons available</option>
          ) : (
            seasons.map((s) => {
              const tag = s.is_active
                ? " · ACTIVE"
                : s.is_completed
                ? " · COMPLETED"
                : "";
              return (
                <option key={s.id} value={s.id}>
                  {s.title}
                  {tag}
                </option>
              );
            })
          )}
        </select>
        {selectedSeason ? (
          <span className="events-selector-meta">
            {selectedSeason.start_date
              ? new Date(selectedSeason.start_date).toLocaleDateString()
              : "—"}{" "}
            →{" "}
            {selectedSeason.end_date
              ? new Date(selectedSeason.end_date).toLocaleDateString()
              : "—"}
          </span>
        ) : null}
      </section>

      {seasonsError ? <p className="events-error">{seasonsError}</p> : null}

      <section className="events-list-section">
        {selectedSeasonId == null ? (
          <p className="events-empty">Select a season to view its events.</p>
        ) : eventsLoading ? (
          <p className="events-empty">Loading events…</p>
        ) : eventsError ? (
          <p className="events-error">{eventsError}</p>
        ) : events.length === 0 ? (
          <p className="events-empty">No events found for this season.</p>
        ) : (
          <ul className="events-list">
            {events.map((ev, idx) => {
              const key = ev.event_id ?? ev.slug ?? `row-${idx}`;
              const title = ev.title ?? ev.slug ?? "Untitled event";
              const polymarketUrl = ev.slug
                ? `${POLYMARKET_EVENT_BASE}${ev.slug}`
                : null;
              const volumeLabel =
                ev.volume != null
                  ? `Historical Vol. $${Math.round(ev.volume).toLocaleString()}`
                  : null;
              const startStr = ev.start_date
                ? new Date(ev.start_date).toLocaleDateString()
                : null;
              const endStr = ev.end_date
                ? new Date(ev.end_date).toLocaleDateString()
                : null;
              const dateLabel = startStr && endStr
                ? `${startStr} → ${endStr}`
                : startStr ?? endStr;
              return (
                <li key={key} className="events-list-item">
                  <div className="events-list-body">
                    <div className="events-list-title">{title}</div>
                    <div className="events-list-meta">
                      <span>
                        {ev.participant_count.toLocaleString()} Stars in the Event
                      </span>
                    </div>
                  </div>
                  <div className="events-list-stats">
                    {volumeLabel ? (
                      <span className="events-list-stat">{volumeLabel}</span>
                    ) : null}
                    {dateLabel ? (
                      <span className="events-list-stat events-list-stat-muted">
                        {dateLabel}
                      </span>
                    ) : null}
                  </div>
                  {polymarketUrl ? (
                    <a
                      className="events-list-link"
                      href={polymarketUrl}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      View ↗
                    </a>
                  ) : null}
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </div>
  );
}
