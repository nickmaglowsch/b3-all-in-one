"use client";

import { useEffect } from "react";
import { acceptCallbackToken, setToken } from "@/lib/api";
import { useBrowserValue } from "@/lib/hooks";
import { APP_HOME } from "@/lib/routes";

// Landing spot after Google OAuth: the API redirects here with the JWT in the URL
// *fragment* (#token=<jwt>), never the query string — a query string is logged by
// Cloudflare Pages and every proxy on the way, and this token is a full bearer
// credential nobody can revoke. Fragments are never sent to a server.
//
// Reading the hash also means no useSearchParams, so no Suspense boundary: the
// fragment is only ever available client-side.
const readHashToken = () =>
  new URLSearchParams(window.location.hash.slice(1)).get("token");

export default function AuthCallbackPage() {
  // Display only. `undefined` while the fragment is unreadable (the prerender),
  // `null` when it carries no token: "we cannot see the URL yet" and "the URL
  // has no token" are different answers, and only the second is a failed login.
  const shown = useBrowserValue(readHashToken, undefined);

  // The effect reads the hash itself rather than depending on `shown`, and runs
  // once, on the first effect pass. This page is a public route (lib/routes.ts)
  // precisely so that nothing else runs first: inside the shell, the sidebar's
  // queries fired before this effect, went out with no token, and their 401s
  // wiped the one being stored here. Keep it that way — nothing on this page may
  // call the API before setToken.
  useEffect(() => {
    if (!readHashToken()) return;
    // Only a login this tab started (lib/api.ts); a token from a link someone
    // sent is dropped, never stored over the session already here.
    const token = acceptCallbackToken(window.location.hash);
    // Drop the fragment either way, so React's dev double effect sees no token
    // instead of a consumed marker and a rejection racing the navigation below.
    window.history.replaceState(null, "", window.location.pathname);
    if (!token) {
      window.location.replace("/login");
      return;
    }
    setToken(token);
    // Full navigation (not router.replace) so the token never lingers in history
    // and the app re-reads auth from a clean URL.
    window.location.replace(APP_HOME);
  }, []);

  return (
    <main className="flex min-h-screen items-center justify-center bg-paper px-4">
      <p className="text-sm text-muted">
        {shown === null ? "Falha no login. Volte e tente de novo." : "Entrando…"}
      </p>
    </main>
  );
}
