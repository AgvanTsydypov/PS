const apiBase =
  process.env.NEXT_PUBLIC_USER_API_BASE_URL ??
  (process.env.NODE_ENV === "development" ? "http://localhost:8011" : "/");

const PUBLIC_API_RETRY_DELAYS_MS = [400, 1200, 3000];
const PUBLIC_API_TIMEOUT_MS = 8000;

type PublicFetchOptions = {
  retries?: number;
  origin?: string;
  timeoutMs?: number;
};

export type SiteStatusResponse = { wallet_actions_disabled: boolean };

export type ServerTimeResponse = {
  now_utc_iso?: string;
};

export type SeasonResponse = {
  id: number;
  type: string;
  season_number: number;
  title: string;
  short_description: string;
  total_supply: number;
  remaining_supply: number;
  end_date: string | null;
  is_active: boolean;
  phase: string;
  phase_reason: string;
  phase_ends_at: string | null;
};

export type SeasonCatalogEntry = {
  id: number;
  type: string;
  season_number: number;
  title: string;
  is_active: boolean;
};

export type SeasonsCatalogResponse = {
  seasons: SeasonCatalogEntry[];
};

export type SeasonArchetypeOpensResponse = {
  season_id: number;
  total_opened: number;
  by_archetype: Record<string, number>;
  unknown: number;
};

/** Canonical archetype order (matches user_web_backend CARD_ARCHETYPE_OPTIONS). */
export const ARCHETYPE_DISPLAY_ORDER = [
  "ICARUS",
  "BURNER",
  "BOT",
  "EXTRACTOR",
  "PASSENGER",
  "ANOMALY",
  "INSIDER",
  "SIGNAL",
  "VECTOR",
  "EQUILIBRIUM",
  "GRAVITON",
  "SUBSTRATE",
  "OPERATOR",
] as const;

export type CardTickerItem = {
  slug: string;
  card_title: string;
  front_image_url: string;
  back_image_url?: string | null;
  created_at?: string | null;
};

export type CardTickerResponse = {
  items: CardTickerItem[];
  total: number;
  fetched_at: string;
};

export function buildUserApiUrl(path: string, origin?: string): string {
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  if (apiBase === "/") {
    if (origin) {
      return new URL(normalizedPath, origin).toString();
    }
    return normalizedPath;
  }
  return `${apiBase.replace(/\/$/, "")}${normalizedPath}`;
}

/** Use on user API fetches so the HttpOnly session cookie is sent (cross-origin). */
export const userApiCredentials: RequestCredentials = "include";

function isRetryableStatus(status: number): boolean {
  return status === 429 || status >= 500;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, ms);
  });
}

async function fetchJsonWithTimeout<T>(
  input: string,
  timeoutMs: number,
): Promise<T | null> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => {
    controller.abort();
  }, timeoutMs);

  try {
    const res = await fetch(input, {
      cache: "no-store",
      signal: controller.signal,
    });
    if (!res.ok) {
      if (isRetryableStatus(res.status)) {
        throw new Error(`Retryable status ${res.status}`);
      }
      return null;
    }
    return (await res.json()) as T;
  } catch (error) {
    if (
      error instanceof Error &&
      error.name !== "AbortError" &&
      !error.message.startsWith("Retryable status ")
    ) {
      return null;
    }
    throw error;
  } finally {
    clearTimeout(timeoutId);
  }
}

/**
 * Richer result variant of the public JSON fetcher. Unlike `fetchPublicUserApiJson`
 * (which collapses every failure into `null`), this returns a discriminated union so
 * callers can tell a legit 404 apart from a transient 5xx / network drop that was
 * retried and ultimately gave up. Retries only fire for 429/5xx/network/timeout; 4xx
 * (other than 429) short-circuits immediately.
 */
export type PublicFetchResult<T> =
  | { kind: "ok"; data: T }
  | { kind: "not-found" }
  | { kind: "error"; status?: number; retryable: boolean };

async function fetchJsonResultWithTimeout<T>(
  input: string,
  timeoutMs: number,
): Promise<PublicFetchResult<T>> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => {
    controller.abort();
  }, timeoutMs);

  try {
    const res = await fetch(input, {
      cache: "no-store",
      signal: controller.signal,
    });
    if (res.status === 404) {
      return { kind: "not-found" };
    }
    if (!res.ok) {
      if (isRetryableStatus(res.status)) {
        throw new Error(`Retryable status ${res.status}`);
      }
      return { kind: "error", status: res.status, retryable: false };
    }
    const data = (await res.json()) as T;
    return { kind: "ok", data };
  } catch (error) {
    if (error instanceof Error && error.name === "AbortError") {
      throw error;
    }
    if (error instanceof Error && error.message.startsWith("Retryable status ")) {
      throw error;
    }
    throw Object.assign(new Error("network-error"), { cause: error });
  } finally {
    clearTimeout(timeoutId);
  }
}

export async function fetchPublicUserApiJsonResult<T>(
  path: string,
  { retries = 3, origin, timeoutMs = PUBLIC_API_TIMEOUT_MS }: PublicFetchOptions = {},
): Promise<PublicFetchResult<T>> {
  const attemptCount = Math.max(1, retries + 1);
  const url = buildUserApiUrl(path, origin);

  for (let attempt = 0; attempt < attemptCount; attempt += 1) {
    try {
      return await fetchJsonResultWithTimeout<T>(url, timeoutMs);
    } catch {
      if (attempt === attemptCount - 1) {
        return { kind: "error", retryable: true };
      }
    }

    const delayMs =
      PUBLIC_API_RETRY_DELAYS_MS[
        Math.min(attempt, PUBLIC_API_RETRY_DELAYS_MS.length - 1)
      ] ?? 0;
    if (delayMs > 0) {
      await sleep(delayMs);
    }
  }

  return { kind: "error", retryable: true };
}

async function fetchPublicUserApiJson<T>(
  path: string,
  { retries = 2, origin, timeoutMs = PUBLIC_API_TIMEOUT_MS }: PublicFetchOptions = {},
): Promise<T | null> {
  const attemptCount = Math.max(1, retries + 1);

  for (let attempt = 0; attempt < attemptCount; attempt += 1) {
    try {
      return await fetchJsonWithTimeout<T>(buildUserApiUrl(path, origin), timeoutMs);
    } catch {
      if (attempt === attemptCount - 1) {
        return null;
      }
    }

    const delayMs =
      PUBLIC_API_RETRY_DELAYS_MS[
        Math.min(attempt, PUBLIC_API_RETRY_DELAYS_MS.length - 1)
      ] ?? 0;
    if (delayMs > 0) {
      await sleep(delayMs);
    }
  }

  return null;
}

export async function fetchSiteStatus(
  options?: PublicFetchOptions,
): Promise<SiteStatusResponse | null> {
  return fetchPublicUserApiJson<SiteStatusResponse>("/api/public/site-status", options);
}

export async function fetchServerTime(
  options?: PublicFetchOptions,
): Promise<ServerTimeResponse | null> {
  return fetchPublicUserApiJson<ServerTimeResponse>("/api/server-time", options);
}

export async function fetchActiveSeasons(
  options?: PublicFetchOptions,
): Promise<SeasonResponse[] | null> {
  return fetchPublicUserApiJson<SeasonResponse[]>("/api/seasons/active", options);
}

export async function fetchSeasonsCatalog(
  options?: PublicFetchOptions,
): Promise<SeasonsCatalogResponse | null> {
  return fetchPublicUserApiJson<SeasonsCatalogResponse>("/api/seasons/catalog", options);
}

export type SeasonsListEntry = {
  id: number;
  type: string;
  season_number: number;
  title: string;
  start_date: string | null;
  end_date: string | null;
  is_active: boolean;
  is_completed: boolean;
};

export async function fetchSeasonsList(
  options?: PublicFetchOptions,
): Promise<SeasonsListEntry[] | null> {
  return fetchPublicUserApiJson<SeasonsListEntry[]>("/api/seasons/list", options);
}

export type SeasonEventEntry = {
  event_id: string | null;
  slug: string | null;
  title: string | null;
  image_url: string | null;
  end_date: string | null;
  closed: boolean | null;
  participant_count: number;
};

export type SeasonEventsResponse = {
  season_id: number;
  season_title: string;
  events: SeasonEventEntry[];
};

export async function fetchSeasonEvents(
  seasonId: number,
  options?: PublicFetchOptions,
): Promise<SeasonEventsResponse | null> {
  return fetchPublicUserApiJson<SeasonEventsResponse>(
    `/api/seasons/${seasonId}/events`,
    options,
  );
}

export async function fetchSeasonArchetypeOpens(
  seasonId: number,
  options?: PublicFetchOptions,
): Promise<SeasonArchetypeOpensResponse | null> {
  const path = `/api/seasons/${seasonId}/opened-archetypes`;
  return fetchPublicUserApiJson<SeasonArchetypeOpensResponse>(path, options);
}

export async function fetchCardTicker(
  options?: PublicFetchOptions,
): Promise<CardTickerResponse | null> {
  return fetchPublicUserApiJson<CardTickerResponse>("/api/cards/ticker", options);
}
