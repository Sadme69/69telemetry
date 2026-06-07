import { getToken, clearToken } from "./auth";

// All API calls use relative URLs (same-origin).
export const API_URL = "";

export function apiUrl(path: string): string {
  return path;
}

export function wsUrl(path: string): string {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const base = `${protocol}//${window.location.host}`;
  const token = getToken();
  const separator = path.includes("?") ? "&" : "?";
  const tokenParam = token ? `${separator}token=${encodeURIComponent(token)}` : "";
  return `${base}${path}${tokenParam}`;
}

export async function apiFetch<T>(path: string): Promise<T> {
  const headers: HeadersInit = {};
  const token = getToken();
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }
  const res = await fetch(apiUrl(path), { headers });
  if (res.status === 401) {
    clearToken();
    window.location.reload();
    throw new Error("Unauthorized");
  }
  if (!res.ok) {
    throw new Error(`API error ${res.status}: ${res.statusText}`);
  }
  return res.json();
}
