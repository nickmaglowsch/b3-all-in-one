// Typed fetch wrapper: attaches Bearer, 401 → clear token + bounce to /login.
import type { Bearer } from "./types";

export const API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

const TOKEN_KEY = "b3_token";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_KEY);
}
export function setToken(t: string) {
  window.localStorage.setItem(TOKEN_KEY, t);
}
export function clearToken() {
  window.localStorage.removeItem(TOKEN_KEY);
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  // JSON only for string bodies — leave FormData (multipart) untouched so the
  // browser sets its own boundary.
  if (typeof init.body === "string" && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const res = await fetch(`${API_URL}${path}`, { ...init, headers });

  if (res.status === 401) {
    clearToken();
    if (typeof window !== "undefined" && window.location.pathname !== "/login") {
      window.location.href = "/login";
    }
    throw new ApiError(401, "Sessão expirada — entre novamente.");
  }
  if (!res.ok) {
    throw new ApiError(res.status, await errorDetail(res));
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

async function errorDetail(res: Response): Promise<string> {
  try {
    const j = await res.json();
    if (typeof j.detail === "string") return j.detail;
    if (j.detail) return JSON.stringify(j.detail);
  } catch {
    /* non-JSON body */
  }
  return res.statusText || `HTTP ${res.status}`;
}

// dev-login is public (POST, no body) and returns a JWT in development mode.
export async function devLogin(): Promise<string> {
  const res = await fetch(`${API_URL}/auth/dev-login`, { method: "POST" });
  if (!res.ok) throw new ApiError(res.status, await errorDetail(res));
  const body = (await res.json()) as Bearer;
  setToken(body.access_token);
  return body.access_token;
}
