"use client";

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

import { fetchSiteStatus } from "../lib/userApiBase";

const STRIP_ID = "site-maintenance-strip";
const STRIP_RETRY_DELAYS_MS = [3000, 8000, 15000, 30000];
const STRIP_REFRESH_MS = 30000;
const RELEASE_TARGET_MS = Date.UTC(2026, 4, 15, 12, 0, 0);

function formatReleaseCountdown(nowMs: number): string | null {
  const diff = RELEASE_TARGET_MS - nowMs;
  if (diff <= 0) return null;
  const totalSeconds = Math.floor(diff / 1000);
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  const pad = (n: number) => n.toString().padStart(2, "0");
  return `${pad(hours)}:${pad(minutes)}:${pad(seconds)}`;
}

export default function SiteMaintenanceStrip() {
  const [active, setActive] = useState(false);
  const [checked, setChecked] = useState(false);
  const [countdown, setCountdown] = useState<string | null>(() => formatReleaseCountdown(Date.now()));
  const stripRef = useRef<HTMLDivElement>(null);
  const retryTimerRef = useRef<number | null>(null);
  const retryAttemptRef = useRef(0);

  const scheduleLoad = useCallback((delayMs: number, load: () => void) => {
    if (retryTimerRef.current != null) {
      window.clearTimeout(retryTimerRef.current);
    }
    retryTimerRef.current = window.setTimeout(load, delayMs);
  }, []);

  const loadSiteStatus = useCallback(async () => {
    const status = await fetchSiteStatus({ retries: 3 });
    if (status) {
      retryAttemptRef.current = 0;
      setActive(Boolean(status.wallet_actions_disabled));
      setChecked(true);
      scheduleLoad(STRIP_REFRESH_MS, () => {
        void loadSiteStatus();
      });
      return;
    }

    setChecked(true);
    const delayMs =
      STRIP_RETRY_DELAYS_MS[
        Math.min(retryAttemptRef.current, STRIP_RETRY_DELAYS_MS.length - 1)
      ] ?? STRIP_REFRESH_MS;
    retryAttemptRef.current += 1;
    scheduleLoad(delayMs, () => {
      void loadSiteStatus();
    });
  }, [scheduleLoad]);

  useEffect(() => {
    void loadSiteStatus();

    const handleOnline = () => {
      retryAttemptRef.current = 0;
      void loadSiteStatus();
    };
    const handleVisibilityChange = () => {
      if (document.visibilityState !== "visible") return;
      retryAttemptRef.current = 0;
      void loadSiteStatus();
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
  }, [loadSiteStatus]);

  useEffect(() => {
    if (!active) return;
    const tick = () => setCountdown(formatReleaseCountdown(Date.now()));
    tick();
    const id = window.setInterval(tick, 1000);
    return () => window.clearInterval(id);
  }, [active]);

  useLayoutEffect(() => {
    if (!checked) {
      document.body.classList.remove("has-site-maintenance-strip");
      document.documentElement.style.removeProperty("--site-maintenance-strip-height");
      return undefined;
    }

    const apply = () => {
      const h = stripRef.current ? Math.ceil(stripRef.current.getBoundingClientRect().height) : 48;
      document.documentElement.style.setProperty("--site-maintenance-strip-height", `${h}px`);
      document.body.classList.add("has-site-maintenance-strip");
    };

    apply();
    window.addEventListener("resize", apply);
    const ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver(apply) : null;
    if (stripRef.current && ro) ro.observe(stripRef.current);

    return () => {
      window.removeEventListener("resize", apply);
      ro?.disconnect();
      document.body.classList.remove("has-site-maintenance-strip");
      document.documentElement.style.removeProperty("--site-maintenance-strip-height");
    };
  }, [checked]);

  if (!checked) return null;

  return (
    <div
      ref={stripRef}
      id={STRIP_ID}
      className={`site-maintenance-strip${active ? " site-maintenance-strip-active" : " site-maintenance-strip-beta"}`}
      role="status"
      aria-live="polite"
    >
      {active ? (
        countdown ? (
          <span className="site-maintenance-strip-countdown">
            <span className="site-maintenance-strip-countdown-label">GENESIS LAUNCH IN</span>
            <span className="site-maintenance-strip-countdown-value">{countdown}</span>
          </span>
        ) : (
          "Maintenance in progress: some actions are disabled."
        )
      ) : (
        "WARNING! THIS IS A BETA VERSION. IT DOES NOT REPRESENT THE FINAL QUALITY OF THE PRODUCT."
      )}
    </div>
  );
}
