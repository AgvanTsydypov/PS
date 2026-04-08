/**
 * Server-only policy for /api/svg-proxy: which origins may be fetched (SSRF mitigation).
 *
 * Set SVG_PROXY_ALLOWED_ORIGINS to a comma-separated list of origins, e.g.:
 *   https://pub-xxxx.r2.dev,https://user-api.example.com
 *
 * Additionally, if NEXT_PUBLIC_USER_API_BASE_URL is an absolute http(s) URL, its origin is allowed.
 */

const MAX_SVG_BYTES = 2 * 1024 * 1024; // 2 MiB

function normalizeOriginList(raw: string | undefined): string[] {
  if (!raw?.trim()) return [];
  const out = new Set<string>();
  for (const part of raw.split(",")) {
    const t = part.trim();
    if (!t) continue;
    try {
      out.add(new URL(t).origin);
    } catch {
      /* skip invalid */
    }
  }
  return [...out];
}

export function getSvgProxyAllowedOrigins(): string[] {
  const fromEnv = normalizeOriginList(process.env.SVG_PROXY_ALLOWED_ORIGINS);
  const merged = new Set<string>(fromEnv);

  const apiBase = process.env.NEXT_PUBLIC_USER_API_BASE_URL?.trim();
  if (apiBase?.startsWith("http://") || apiBase?.startsWith("https://")) {
    try {
      merged.add(new URL(apiBase).origin);
    } catch {
      /* ignore */
    }
  }

  const r2 =
    process.env.SVG_PROXY_R2_PUBLIC_ORIGIN?.trim() ||
    process.env.R2_PUBLIC_BASE_URL?.trim() ||
    process.env.NEXT_PUBLIC_R2_PUBLIC_ORIGIN?.trim();
  if (r2?.startsWith("http://") || r2?.startsWith("https://")) {
    try {
      merged.add(new URL(r2).origin);
    } catch {
      /* ignore */
    }
  }

  return [...merged];
}

/**
 * True if the URL's origin is allowed. Compares protocol, hostname, and port.
 */
export function isSvgProxyUrlAllowed(
  url: URL,
  allowedOrigins: string[],
): boolean {
  for (const allowed of allowedOrigins) {
    try {
      const a = new URL(allowed.includes("://") ? allowed : `https://${allowed}`);
      if (url.protocol !== a.protocol) continue;
      if (url.hostname !== a.hostname) continue;
      const port = url.port || (url.protocol === "https:" ? "443" : "80");
      const aPort = a.port || (a.protocol === "https:" ? "443" : "80");
      if (port !== aPort) continue;
      return true;
    } catch {
      /* ignore */
    }
  }

  if (process.env.NODE_ENV === "development") {
    const h = url.hostname;
    if (
      (h === "localhost" || h === "127.0.0.1" || h === "::1") &&
      (url.protocol === "http:" || url.protocol === "https:")
    ) {
      return true;
    }
  }

  return false;
}

export function isLikelySvgDocument(body: string): boolean {
  const s = body.trimStart();
  if (s.startsWith("<?xml")) {
    const rest = s.replace(/^<\?xml[^>]*\?>/, "").trimStart();
    return rest.startsWith("<svg");
  }
  return s.startsWith("<svg");
}

export { MAX_SVG_BYTES };
