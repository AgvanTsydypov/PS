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
  "img-src 'self' data: https: blob:",
  "font-src 'self' https://fonts.gstatic.com",
  "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
  `script-src ${scriptSrc}`,
  `connect-src ${buildCspConnectSrc()}`,
].join("; ");

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "Content-Security-Policy", value: contentSecurityPolicy },
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
