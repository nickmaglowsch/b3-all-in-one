"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { API_URL, devLogin, getToken, googleLogin } from "@/lib/api";
import { useBrowserValue } from "@/lib/hooks";
import { loginErrorMessage } from "@/lib/loginErrors";
import { postLoginTarget } from "@/lib/routes";
import { Brand } from "@/components/Brand";

// Dev-login only exists when the API runs with ENVIRONMENT=development; in prod
// it 404s, so only show that button when pointed at a local API.
const IS_LOCAL = API_URL.includes("localhost") || API_URL.includes("127.0.0.1");

// Only same-site paths — an absolute `next` would be an open redirect. See
// postLoginTarget in lib/routes.ts, where the rule is tested.
const nextParam = () => new URLSearchParams(window.location.search).get("next");

// A failed Google sign-in comes back as /login?erro=<code> (the API's
// platform_app/oauth_login.py) instead of raw JSON on the API's host.
const oauthError = () =>
  loginErrorMessage(new URLSearchParams(window.location.search).get("erro"));

export default function LoginPage() {
  const router = useRouter();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // The ?erro= sentence stands until the next attempt, which speaks for itself.
  const fromCallback = useBrowserValue(oauthError, null);
  const [retried, setRetried] = useState(false);
  const shownError = error ?? (retried ? null : fromCallback);

  // A session that already exists does not sign in again. This page is public, so
  // AppShell's guard never runs here, and every entrance to it — the landing's
  // "Entrar", a bookmark, the back button after a logout that was not one — ended
  // on a login form the visitor had no reason to fill in.
  //
  // Storage is client-only, so this cannot be an href: the export is prerendered
  // and the guess would be baked in. Effect on mount, before anything is typed.
  useEffect(() => {
    if (getToken()) router.replace(postLoginTarget(nextParam()));
  }, [router]);

  // Back from Google's consent screen restores this page from the bfcache with its
  // state frozen mid-redirect: the button disabled on "Redirecionando…" and
  // nothing left running to undo it, so only a manual reload got out. A restore
  // fires `pageshow` with `persisted`; a fresh load starts unlocked anyway.
  useEffect(() => {
    const onShow = (e: PageTransitionEvent) => {
      if (e.persisted) setLoading(false);
    };
    window.addEventListener("pageshow", onShow);
    return () => window.removeEventListener("pageshow", onShow);
  }, []);

  function handleGoogle() {
    setLoading(true);
    setError(null);
    setRetried(true);
    googleLogin(); // a navigation: the API binds the flow, then on to Google
  }

  async function handleDevLogin() {
    setLoading(true);
    setError(null);
    setRetried(true);
    try {
      await devLogin();
      router.replace(postLoginTarget(nextParam()));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Falha ao entrar.");
      setLoading(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-paper px-4">
      <div className="w-full max-w-sm">
        <div className="mb-6 text-center">
          <Brand className="text-2xl" />
          <p className="mt-1 text-sm text-muted">
            Carteira, IRPF e bench a partir dos seus extratos da B3.
          </p>
        </div>

        <div className="rounded-lg border border-hairline bg-panel p-6">
          <button
            type="button"
            onClick={handleGoogle}
            disabled={loading}
            className="flex w-full items-center justify-center gap-2 rounded-md border border-hairline bg-white px-4 py-2.5 text-sm font-semibold text-ink transition-colors hover:bg-hairline/30 disabled:opacity-60"
          >
            <svg className="h-4 w-4" viewBox="0 0 48 48" aria-hidden="true">
              <path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z" />
              <path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z" />
              <path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z" />
              <path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z" />
            </svg>
            {loading ? "Redirecionando…" : "Entrar com Google"}
          </button>

          {IS_LOCAL && (
            <button
              type="button"
              onClick={handleDevLogin}
              disabled={loading}
              className="mt-3 w-full rounded-md bg-navy px-4 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-navy/90 disabled:opacity-60"
            >
              {loading ? "Entrando…" : "Entrar (dev)"}
            </button>
          )}

          {shownError && (
            <p className="mt-3 text-sm text-loss" role="alert">
              {shownError}
            </p>
          )}
        </div>

        <p className="mt-4 text-center text-xs text-muted">
          Ao entrar você concorda com o tratamento dos seus dados descrito na{" "}
          <Link
            href="/privacidade"
            className="underline underline-offset-2 hover:text-ink"
          >
            Política de Privacidade
          </Link>
          .
        </p>
      </div>
    </main>
  );
}
