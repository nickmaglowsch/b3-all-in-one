// Typed fetch wrapper: attaches Bearer, 401 → clear token + bounce to /login.
import type { Bearer } from "./types";

export const API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

const TOKEN_KEY = "b3_token";
const PORTFOLIO_KEY = "b3_portfolio";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_KEY);
}
export function setToken(t: string) {
  window.localStorage.setItem(TOKEN_KEY, t);
  // A new login inherits nothing: the previous account's book selection would
  // otherwise ride along and 404 this account's first requests.
  clearPortfolioId();
}
export function clearToken() {
  window.localStorage.removeItem(TOKEN_KEY);
  clearPortfolioId();
}

// Which book the app is acting on (enterprise tier). Unset => the user's own,
// which is every account below that tier.
//
// sessionStorage, deliberately: an advisor comparing two clients side by side
// opens two tabs, and localStorage is origin-wide, so tab B's switch would
// silently redirect tab A's next write into B's book. Per-tab is the only
// storage that matches how the feature is used.
//
// It is a subscribable store because two components in different subtrees read it
// — the sidebar switcher and the carteiras page — and a switch made in one has to
// be visible in the other. Feed it to useSyncExternalStore; do not copy it into
// component state, which is how the sidebar ends up naming the previous book.
const portfolioListeners = new Set<() => void>();

export function subscribePortfolio(onChange: () => void): () => void {
  portfolioListeners.add(onChange);
  return () => {
    portfolioListeners.delete(onChange);
  };
}

/** Query param carrying the active book, so a book is linkable and a screenshot
 *  identifies itself. */
export const PORTFOLIO_PARAM = "carteira";

// A pasted or bookmarked link must land on the book it names, and it must do so
// before the first query fires — an effect runs after the first render, by which
// point the page has already asked the API for the wrong book. Module scope runs
// once, at import, ahead of everything. sessionStorage stays the store; the URL
// only seeds it, which keeps the per-tab isolation the two-tab advisor flow needs.
const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

if (typeof window !== "undefined") {
  const fromUrl = new URLSearchParams(window.location.search).get(PORTFOLIO_PARAM);
  // Shape-checked before it is trusted. The value rides on X-Portfolio-Id for
  // the rest of the tab session, so a link carrying junk would turn every
  // subsequent request into a 400 with no way back from inside the app.
  if (
    fromUrl &&
    UUID_RE.test(fromUrl) &&
    fromUrl !== window.sessionStorage.getItem(PORTFOLIO_KEY)
  ) {
    window.sessionStorage.setItem(PORTFOLIO_KEY, fromUrl);
  }
}

export function getPortfolioId(): string | null {
  if (typeof window === "undefined") return null;
  return window.sessionStorage.getItem(PORTFOLIO_KEY);
}
export function setPortfolioId(id: string) {
  window.sessionStorage.setItem(PORTFOLIO_KEY, id);
  portfolioListeners.forEach((fn) => fn());
}
export function clearPortfolioId() {
  window.sessionStorage.removeItem(PORTFOLIO_KEY);
  portfolioListeners.forEach((fn) => fn());
}

// Account-scoped, not book-scoped: the registry of books and the subscription
// belong to the login. Exempting them also means a stale selection can always be
// discovered and cleared, instead of 404ing the very call that would fix it.
// /privacy joins them for the same reason: LGPD rights are exercised over the
// login and every book under it, never over the one currently selected.
const ACCOUNT_SCOPED = ["/portfolios", "/billing", "/privacy"];

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

export type ApiOptions = {
  /**
   * Whether a 402 should open the upgrade dialog. Default true, which is right
   * for anything the user clicked. Pass false for a query that fires on page
   * load: a modal nobody asked for, over a page they have not read yet, is an
   * interruption rather than an offer. Those callers render the offer inline.
   */
  paywallDialog?: boolean;
};

export async function api<T>(
  path: string,
  init: RequestInit = {},
  opts: ApiOptions = {},
): Promise<T> {
  const headers = new Headers(init.headers);
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const portfolio = getPortfolioId();
  if (portfolio && !ACCOUNT_SCOPED.some((p) => path.startsWith(p))) {
    headers.set("X-Portfolio-Id", portfolio);
  }
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
    // A lapsed subscription strands an advisor inside a client's book: the book is
    // still listed, so it never looks stale, but every request 402s. Fall back to
    // the book they *are* still entitled to instead of making them find the way
    // out. Dropping the header is what stops this from looping.
    if (
      res.status === 402 &&
      typeof window !== "undefined" &&
      getPortfolioId() &&
      isPlanRequired(detail) &&
      detail.required_plan === "enterprise"
    ) {
      clearPortfolioId();
      window.location.reload();
      throw new ApiError(res.status, errorMessage(detail, res), detail);
    }
    // Same escape hatch for a book that no longer resolves: deleted mid-session,
    // or a pasted link naming someone else's portfolio, which tenancy answers
    // 404. Without this the header rides on every later request and the tab is
    // permanently dead — and the switcher cannot rescue it, because it hides
    // itself for accounts with only one book.
    if (
      res.status === 404 &&
      typeof window !== "undefined" &&
      getPortfolioId() &&
      !ACCOUNT_SCOPED.some((p) => path.startsWith(p))
    ) {
      clearPortfolioId();
      window.location.reload();
      throw new ApiError(res.status, errorMessage(detail, res), detail);
    }
    if (
      res.status === 402 &&
      typeof window !== "undefined" &&
      opts.paywallDialog !== false
    ) {
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

// Google OAuth: ask the API for Google's consent URL, then hand the browser to
// it. Google → API /auth/google/callback → 302 to /auth/callback?token=… (the
// API's RedirectTransport), where the SPA stores the token.
export async function googleLogin(): Promise<void> {
  const res = await fetch(
    `${API_URL}/auth/google/authorize?scopes=openid%20email%20profile`,
    // credentials: the API answers with the oauth_state cookie that binds this
    // browser to the state it just issued. Without this the default
    // "same-origin" drops it (pages.dev → duckdns.org is cross-site) and the
    // callback always 400s with INVALID_STATE_TOKEN.
    { credentials: "include" },
  );
  if (!res.ok) {
    const detail = await errorDetail(res);
    throw new ApiError(res.status, errorMessage(detail, res), detail);
  }
  const { authorization_url } = (await res.json()) as { authorization_url: string };
  window.location.href = authorization_url;
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
