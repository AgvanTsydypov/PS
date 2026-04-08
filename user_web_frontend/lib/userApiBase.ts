const apiBase =
  process.env.NEXT_PUBLIC_USER_API_BASE_URL ??
  (process.env.NODE_ENV === "development" ? "http://localhost:8011" : "/");

export function buildUserApiUrl(path: string): string {
  if (apiBase === "/") return path;
  return `${apiBase.replace(/\/$/, "")}${path}`;
}

/** Use on user API fetches so the HttpOnly session cookie is sent (cross-origin). */
export const userApiCredentials: RequestCredentials = "include";

export type SiteStatusResponse = { wallet_actions_disabled: boolean };

export async function fetchSiteStatus(): Promise<SiteStatusResponse | null> {
  try {
    const res = await fetch(buildUserApiUrl("/api/public/site-status"), {
      cache: "no-store",
      credentials: userApiCredentials,
    });
    if (!res.ok) return null;
    return (await res.json()) as SiteStatusResponse;
  } catch {
    return null;
  }
}
