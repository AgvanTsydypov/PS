import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

function parseAllowedHosts(): string[] {
  const raw = process.env.USER_WEB_TRUSTED_HOSTS || "";
  const list = raw
    .split(",")
    .map((h) => h.trim().toLowerCase())
    .filter(Boolean);
  if (list.length > 0) return list;
  if (process.env.NODE_ENV === "development") {
    return ["localhost", "127.0.0.1"];
  }
  return [];
}

function hostMatches(host: string, rule: string): boolean {
  if (rule === host) return true;
  if (rule.startsWith("*.")) {
    const suffix = rule.slice(1); // ".example.com"
    return host.endsWith(suffix) && host.length > suffix.length;
  }
  return false;
}

const ALLOWED = parseAllowedHosts();

export function middleware(request: NextRequest) {
  if (ALLOWED.length === 0) return NextResponse.next();
  const rawHost = (request.headers.get("host") || "").toLowerCase();
  const host = rawHost.split(":")[0];
  if (!host) {
    return new NextResponse("Misdirected Request", { status: 421 });
  }
  if (ALLOWED.some((rule) => hostMatches(host, rule))) {
    return NextResponse.next();
  }
  return new NextResponse("Misdirected Request", { status: 421 });
}

export const config = {
  matcher: [
    "/((?!_next/static|_next/image|favicon.ico).*)",
  ],
};
