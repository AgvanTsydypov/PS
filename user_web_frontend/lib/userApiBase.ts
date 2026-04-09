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
};

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

export async function fetchCardTicker(
  options?: PublicFetchOptions,
): Promise<CardTickerResponse | null> {
  return fetchPublicUserApiJson<CardTickerResponse>("/api/cards/ticker", options);
}
