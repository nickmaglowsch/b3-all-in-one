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
    /** parsed `detail` — an object for structured errors like the 402 paywall */
    public detail?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

// 402 body from platform_app/billing.require_plan.
export type PlanRequired = {
  error: "plan_required";
  required_plan: string;
  current_plan: string;
};

export function isPlanRequired(detail: unknown): detail is PlanRequired {
  return (
    typeof detail === "object" &&
    detail !== null &&
    (detail as PlanRequired).error === "plan_required"
  );
}

/** Fired on any 402 so one dialog in AppShell can answer every gated call —
 *  no page needs its own upgrade UI. */
export const PAYWALL_EVENT = "b3:paywall";

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
    const detail = await errorDetail(res);
    if (res.status === 402 && typeof window !== "undefined") {
      window.dispatchEvent(new CustomEvent(PAYWALL_EVENT, { detail }));
    }
    throw new ApiError(res.status, errorMessage(detail, res), detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

async function errorDetail(res: Response): Promise<unknown> {
  try {
    return (await res.json()).detail ?? null;
  } catch {
    return null; // non-JSON body
  }
}

function errorMessage(detail: unknown, res: Response): string {
  if (typeof detail === "string") return detail;
  if (isPlanRequired(detail)) return "Recurso de um plano pago.";
  if (detail) return JSON.stringify(detail);
  return res.statusText || `HTTP ${res.status}`;
}

// dev-login is public (POST, no body) and returns a JWT in development mode.
export async function devLogin(): Promise<string> {
  const res = await fetch(`${API_URL}/auth/dev-login`, { method: "POST" });
  if (!res.ok) {
    const detail = await errorDetail(res);
    throw new ApiError(res.status, errorMessage(detail, res), detail);
  }
  const body = (await res.json()) as Bearer;
  setToken(body.access_token);
  return body.access_token;
}
