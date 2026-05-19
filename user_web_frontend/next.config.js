const STATIC_IMAGE_ORIGINS = [
  "https://*.r2.dev",
  "https://gateway.pinata.cloud",
  "https://cloudflare-ipfs.com",
  "https://ipfs.io",
  "https://dweb.link",
  // Polymarket event/market thumbnails (referenced from the events table)
  "https://polymarket-upload.s3.us-east-2.amazonaws.com",
  "https://polymarket-upload.s3.amazonaws.com",
  "https://polymarket.com",
  "https://*.polymarket.com",
];

function buildCspImgSrc() {
  const parts = new Set(["'self'", "data:", "blob:", ...STATIC_IMAGE_ORIGINS]);
  // Allow backend origin for locally-served asset paths (e.g. http://localhost:8011)
  const apiBase = String(process.env.NEXT_PUBLIC_USER_API_BASE_URL || "").trim();
  if (apiBase.startsWith("http")) {
    try { parts.add(new URL(apiBase).origin); } catch (_) { /* ignore */ }
  }
  if (process.env.NODE_ENV === "development") {
    parts.add("http://localhost:8011");
    parts.add("http://127.0.0.1:8011");
  }
  return Array.from(parts).join(" ");
}

function buildCspConnectSrc() {
  const parts = new Set(["'self'"]);
  const addOrigin = (value) => {
    const raw = String(value || "").trim();
    if (!raw.startsWith("http://") && !raw.startsWith("https://")) return;
    try {
      parts.add(new URL(raw).origin);
    } catch (_) {
      /* ignore */
    }
  };

  addOrigin(process.env.NEXT_PUBLIC_USER_API_BASE_URL);

  // Frontend code falls back to localhost:8011 in dev, so CSP must allow it
  // even when NEXT_PUBLIC_USER_API_BASE_URL is unset.
  if (process.env.NODE_ENV === "development") {
    addOrigin("http://localhost:8011");
    addOrigin("http://127.0.0.1:8011");
  }

  parts.add("https://fonts.googleapis.com");
  parts.add("https://fonts.gstatic.com");
  return Array.from(parts).join(" ");
}

const scriptSrc =
  process.env.NODE_ENV === "development"
    ? "'self' 'unsafe-inline' 'unsafe-eval'"
    : "'self' 'unsafe-inline'";

const contentSecurityPolicy = [
  "default-src 'self'",
  "base-uri 'self'",
  "frame-ancestors 'none'",
  "object-src 'none'",
  `img-src ${buildCspImgSrc()}`,
  "font-src 'self' data: https://fonts.gstatic.com",
  "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
  `script-src ${scriptSrc}`,
  `connect-src ${buildCspConnectSrc()}`,
].join("; ");

function buildRemotePatterns() {
  return [
    { protocol: "https", hostname: "**.r2.dev" },
    { protocol: "https", hostname: "gateway.pinata.cloud" },
    { protocol: "https", hostname: "cloudflare-ipfs.com" },
    { protocol: "https", hostname: "ipfs.io" },
    { protocol: "https", hostname: "dweb.link" },
    { protocol: "https", hostname: "polymarket-upload.s3.us-east-2.amazonaws.com" },
    { protocol: "https", hostname: "polymarket-upload.s3.amazonaws.com" },
    { protocol: "https", hostname: "**.polymarket.com" },
  ];
}

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  images: { remotePatterns: buildRemotePatterns() },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "Content-Security-Policy", value: contentSecurityPolicy },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Strict-Transport-Security", value: "max-age=63072000; includeSubDomains" },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          {
            key: "Permissions-Policy",
            value: "camera=(), microphone=(), geolocation=()",
          },
        ],
      },
    ];
  },
  // Legacy ``/preview/{slug}`` URLs (and any QR codes / external links that
  // still reference them) are permanently redirected to the unified
  // ``/cards/{slug}`` URL space. The backend handles both rows from a
  // single endpoint, so the old preview route is no longer needed.
  async redirects() {
    return [
      {
        source: "/preview/:slug",
        destination: "/cards/:slug",
        permanent: true,
      },
    ];
  },
  // Dev-time proxy so the frontend can be exposed via an HTTPS tunnel
  // (cloudflared / ngrok) without browsers blocking mixed-content calls
  // to ``http://localhost:8011``. In prod ``NEXT_PUBLIC_USER_API_BASE_URL``
  // is set and the frontend issues absolute URLs straight to the backend,
  // so this rewrite is dormant. Only kicks in when the frontend uses
  // relative ``/api/*`` paths (i.e. when ``NEXT_PUBLIC_USER_API_BASE_URL``
  // is empty).
  async rewrites() {
    const target = process.env.USER_WEB_BACKEND_DEV_PROXY_TARGET || "http://localhost:8011";
    return [
      { source: "/api/:path*", destination: `${target}/api/:path*` },
    ];
  },
};

module.exports = nextConfig;
