const STATIC_IMAGE_ORIGINS = [
  "https://gateway.pinata.cloud",
  "https://cloudflare-ipfs.com",
  "https://ipfs.io",
  "https://dweb.link",
];

function buildCspImgSrc() {
  const parts = new Set(["'self'", "data:", "blob:", ...STATIC_IMAGE_ORIGINS]);
  const r2Base = String(process.env.R2_PUBLIC_BASE_URL || "").trim().replace(/\/+$/, "");
  if (r2Base.startsWith("https://")) {
    try { parts.add(new URL(r2Base).origin); } catch (_) { /* ignore */ }
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
  "font-src 'self' https://fonts.gstatic.com",
  "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
  `script-src ${scriptSrc}`,
  `connect-src ${buildCspConnectSrc()}`,
].join("; ");

function buildRemotePatterns() {
  const patterns = STATIC_IMAGE_ORIGINS.map((origin) => {
    const { protocol, hostname } = new URL(origin);
    return { protocol: protocol.replace(":", ""), hostname };
  });
  const r2Base = String(process.env.R2_PUBLIC_BASE_URL || "").trim().replace(/\/+$/, "");
  if (r2Base.startsWith("https://")) {
    try {
      const { hostname } = new URL(r2Base);
      patterns.push({ protocol: "https", hostname });
    } catch (_) { /* ignore */ }
  }
  return patterns;
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
};

module.exports = nextConfig;
