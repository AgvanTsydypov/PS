const RAW_API_BASE = process.env.NEXT_PUBLIC_SEASON_API_BASE_URL ?? "http://localhost:8001";
export const API_BASE = RAW_API_BASE === "/" ? "" : RAW_API_BASE.replace(/\/$/, "");

export async function fetchJSON<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = (data as { detail?: string }).detail ?? "Request failed";
    throw new Error(detail);
  }
  return data as T;
}
