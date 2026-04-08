import { NextRequest, NextResponse } from "next/server";
import DOMPurify from "isomorphic-dompurify";

import { CARD_SVG_PURIFY_CONFIG } from "../../../lib/svgDOMPurifyConfig";
import {
  getSvgProxyAllowedOrigins,
  isLikelySvgDocument,
  isSvgProxyUrlAllowed,
  MAX_SVG_BYTES,
} from "../../../lib/svgProxyPolicy";

export async function GET(request: NextRequest) {
  const urlParam = request.nextUrl.searchParams.get("url");
  if (!urlParam) {
    return new NextResponse("Missing url parameter", { status: 400 });
  }

  let target: URL;
  try {
    target = new URL(urlParam);
  } catch {
    return new NextResponse("Invalid URL", { status: 400 });
  }

  if (target.protocol !== "http:" && target.protocol !== "https:") {
    return new NextResponse("Invalid URL", { status: 400 });
  }

  if (target.username || target.password) {
    return new NextResponse("Invalid URL", { status: 400 });
  }

  const allowed = getSvgProxyAllowedOrigins();
  if (!isSvgProxyUrlAllowed(target, allowed)) {
    return new NextResponse("URL origin not allowed", { status: 403 });
  }

  try {
    const upstream = await fetch(target.toString(), {
      redirect: "error",
      headers: {
        Accept: "image/svg+xml, text/xml, application/xml, */*;q=0.1",
      },
      next: { revalidate: 3600 },
    });
    if (!upstream.ok) {
      return new NextResponse("Upstream fetch failed", { status: 502 });
    }
    const buf = await upstream.arrayBuffer();
    if (buf.byteLength > MAX_SVG_BYTES) {
      return new NextResponse("Payload too large", { status: 413 });
    }
    const text = new TextDecoder("utf-8", { fatal: false }).decode(buf);
    if (!isLikelySvgDocument(text)) {
      return new NextResponse("Not an SVG document", { status: 422 });
    }
    const safe = DOMPurify.sanitize(text, CARD_SVG_PURIFY_CONFIG);
    if (!safe.trim() || !isLikelySvgDocument(safe)) {
      return new NextResponse("SVG sanitization rejected payload", { status: 422 });
    }
    return new NextResponse(safe, {
      headers: {
        "Content-Type": "image/svg+xml; charset=utf-8",
        "Cache-Control": "public, max-age=3600, s-maxage=3600",
        "X-Content-Type-Options": "nosniff",
      },
    });
  } catch {
    return new NextResponse("Internal error", { status: 500 });
  }
}
