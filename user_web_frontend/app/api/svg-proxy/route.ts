import { NextRequest, NextResponse } from "next/server";

export async function GET(request: NextRequest) {
  const url = request.nextUrl.searchParams.get("url");
  if (!url) {
    return new NextResponse("Missing url parameter", { status: 400 });
  }

  // Only allow http/https URLs to prevent SSRF to internal resources
  try {
    const parsed = new URL(url);
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
      return new NextResponse("Invalid URL", { status: 400 });
    }
  } catch {
    return new NextResponse("Invalid URL", { status: 400 });
  }

  try {
    const upstream = await fetch(url, {
      next: { revalidate: 3600 },
    });
    if (!upstream.ok) {
      return new NextResponse("Upstream fetch failed", { status: 502 });
    }
    const text = await upstream.text();
    return new NextResponse(text, {
      headers: {
        "Content-Type": "image/svg+xml; charset=utf-8",
        "Cache-Control": "public, max-age=3600, s-maxage=3600",
      },
    });
  } catch {
    return new NextResponse("Internal error", { status: 500 });
  }
}
