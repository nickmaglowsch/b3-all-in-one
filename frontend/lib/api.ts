// Typed fetch wrapper: attaches Bearer, 401 → clear token + bounce to /login.
import type { Bearer } from "./types";

export const API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

const TOKEN_KEY = "b3_token";
const PORTFOLIO_KEY = "b3_portfolio";
const REFRESHED_AT_KEY = "b3_token_refreshed_at";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_KEY);
}
export function setToken(t: string) {
  storeToken(t);
  // A new login inherits nothing: the previous account's book selection would
  // otherwise ride along and 404 this account's first requests.
  clearPortfolioId();
}
export function clearToken() {
  window.localStorage.removeItem(TOKEN_KEY);
  window.localStorage.removeItem(REFRESHED_AT_KEY);
  clearPortfolioId();
}

// Write the credential and nothing else. A *renewal* comes through here: it is
// the same session on the same account, so wiping the selected book — which is
// what setToken does, correctly, for a login — would drop an advisor out of the
// client's book they were reading, at an interval, for no reason.
function storeToken(t: string) {
  window.localStorage.setItem(TOKEN_KEY, t);
}

// Which book the app is acting on (módulo empresarial). Unset => the user's own,
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

// Campos declarados e atribuídos à mão, não como parâmetros do construtor: o
// `node --test` roda TypeScript em modo strip-only, que rejeita a forma curta —
// e sem carregar este módulo não há teste para nada que passa por `api()`.
export class ApiError extends Error {
  status: number;
  /** parsed `detail` — an object for structured errors like the 402 paywall */
  detail?: unknown;

  constructor(status: number, message: string, detail?: unknown) {
    super(message);
    this.status = status;
    this.detail = detail;
    this.name = "ApiError";
  }
}

/** What to show for a failed request: the API's own message (written for the
 *  user), else the screen's `fallback` — a network error or a bug has no
 *  sentence worth showing. */
export function errorText(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback;
}

// 402 body from platform_app/billing.gate_detail. `modules` is what the tenant
// *does* have — "you need bench" is only actionable next to it.
export type ModuleRequired = {
  error: "module_required";
  required_module: string;
  modules: string[];
};

export function isModuleRequired(detail: unknown): detail is ModuleRequired {
  return (
    typeof detail === "object" &&
    detail !== null &&
    (detail as ModuleRequired).error === "module_required"
  );
}

/** The module a failed request is gated on, or null when it failed for any
 *  other reason. What an inline offer names and links to — the gate already
 *  says which module it wants, so no screen has to guess. */
export function requiredModuleOf(err: unknown): string | null {
  return err instanceof ApiError && err.status === 402 && isModuleRequired(err.detail)
    ? err.detail.required_module
    : null;
}

// tenancy._acting_tenant's answer when X-Portfolio-Id names no book this login
// owns. It is the one 404 about the header rather than the resource, and the
// detail string is the only thing that tells it apart from every other 404.
export const BOOK_NOT_FOUND = "carteira não encontrada";

/** Whether a failed response says the *selected book* does not resolve — as
 *  opposed to the thing the request asked for not existing inside it. */
export function isBookNotFound(status: number, detail: unknown): boolean {
  return (
    status === 404 &&
    typeof detail === "string" &&
    detail.trim().toLowerCase() === BOOK_NOT_FOUND
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
    onUnauthorized(token);
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
      isModuleRequired(detail) &&
      detail.required_module === "empresarial"
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
    //
    // Only *that* 404, though. A deleted backtest, a code strategy with no spec
    // or a swept curves artifact are 404s too, inside a book that is perfectly
    // fine — and reading them as "the book is gone" reloaded an advisor out of
    // the client they were working on, mid-click.
    if (
      isBookNotFound(res.status, detail) &&
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

/** A 401 speaks for the credential its request *carried*, not for whatever is in
 *  storage by the time it arrives. Only when the two are the same token has the
 *  session actually ended.
 *
 *  Without that check, a request that went out with no token (one fired while
 *  /auth/callback was still storing the token Google had just delivered) or with
 *  an older one (another tab, opened before this login) came back 401 and wiped
 *  the fresh token — the login that only worked "sometimes". */
export function onUnauthorized(sent: string | null): void {
  if (typeof window === "undefined") return;
  if (sent !== null && getToken() === sent) clearToken();
  if (!getToken() && window.location.pathname !== "/login") {
    window.location.href = "/login";
  }
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
  if (isModuleRequired(detail)) return "Recurso de um módulo que você ainda não assina.";
  // Structured errors carry their own pt-BR sentence (the backtest quota's 429,
  // say). Preferred over the blob below, which puts JSON in front of a user.
  if (detail && typeof detail === "object" && "message" in detail
      && typeof detail.message === "string") return detail.message;
  if (detail) return JSON.stringify(detail);
  return res.statusText || `HTTP ${res.status}`;
}

// ── Renovação da sessão ──────────────────────────────────────────────────────
// The token the API issues has a fixed TTL, and until POST /auth/refresh existed
// that TTL *was* the session: a tab that had been open past it simply stopped
// working, and the next click landed on /login. People read that as "the deploy
// logged me out", because a deploy is when they go back and look.
//
// So renew it while the tab is in use. The API slides the same session forward
// (it refuses once the session as a whole is older than its cap — see
// platform_app/users.py), which is why this stores the token without touching
// the selected book: renewing is not signing in as somebody else.
export const REFRESH_EVERY_MS = 60 * 60 * 1000; // TTL is 12h — a dozen chances to renew

// One flight at a time. Mount, window focus and the interval can land together,
// and two in parallel would race to write back the older of the two tokens.
let refreshing: Promise<void> | null = null;

export function refreshSession(force = false): Promise<void> {
  if (typeof window === "undefined" || !getToken()) return Promise.resolve();
  const last = Number(window.localStorage.getItem(REFRESHED_AT_KEY) ?? 0);
  if (!force && Date.now() - last < REFRESH_EVERY_MS) return Promise.resolve();
  refreshing ??= (async () => {
    // Stamped on the attempt, not on the success: while the API is down — which
    // is exactly when a deploy is happening — every window focus would otherwise
    // fire another POST. There are a dozen windows inside one token's life, so
    // sitting out one of them costs nothing.
    window.localStorage.setItem(REFRESHED_AT_KEY, String(Date.now()));
    try {
      const body = await api<Bearer>("/auth/refresh", { method: "POST" });
      storeToken(body.access_token);
    } catch {
      // A 401 already did the right thing inside api(): token cleared, bounce to
      // /login. Everything else — the API mid-deploy, a laptop that woke up on a
      // dead network — is not a reason to end a session that is still valid, so
      // keep the token we have and let the next tick try again.
    } finally {
      refreshing = null;
    }
  })();
  return refreshing;
}

// Google OAuth: a top-level navigation to the API, which binds the flow to this
// browser with a cookie and 302s on to Google. Google → API
// /auth/google/callback → 302 to /auth/callback#token=… (the API's
// RedirectTransport), where the SPA stores the token; a failure comes back to
// /login?erro=<code> instead (lib/loginErrors.ts).
//
// Never a fetch. The binding cookie set on the response to a cross-site fetch
// (pages.dev → duckdns.org) is a third-party cookie, which Safari, every iOS
// browser, Firefox, Brave and any Incognito window refuse to send back — so every
// Google login in them ended on INVALID_STATE_TOKEN. Set on a navigation to the
// API's own host, it is first-party everywhere.
export const GOOGLE_LOGIN_URL = `${API_URL}/auth/google/login`;

export function googleLogin(): void {
  window.location.href = GOOGLE_LOGIN_URL;
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
