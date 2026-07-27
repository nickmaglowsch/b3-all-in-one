"use client";

import { useEffect, useSyncExternalStore } from "react";
import { setToken } from "@/lib/api";

// Landing spot after Google OAuth: the API redirects here with the JWT in the URL
// *fragment* (#token=<jwt>), never the query string — a query string is logged by
// Cloudflare Pages and every proxy on the way, and this token is a full bearer
// credential nobody can revoke. Fragments are never sent to a server.
//
// Reading the hash also means no useSearchParams, so no Suspense boundary: the
// fragment is only ever available client-side.
const readHashToken = () =>
  new URLSearchParams(window.location.hash.slice(1)).get("token");

const neverChanges = () => () => {};

export default function AuthCallbackPage() {
  // Display only. `undefined` while the fragment is unreadable (the prerender),
  // `null` when it carries no token: "we cannot see the URL yet" and "the URL
  // has no token" are different answers, and only the second is a failed login.
  const shown = useSyncExternalStore(neverChanges, readHashToken, () => undefined);

  // The effect reads the hash itself rather than depending on `shown`, and runs
  // once. Storing the credential has to happen on the FIRST effect pass: the
  // shell above this page redirects to /login whenever storage has no token, and
  // child effects are the only thing that run before it. Waiting for a second
  // render to observe the value would hand that race to the redirect.
  useEffect(() => {
    const token = readHashToken();
    if (!token) return;
    setToken(token);
    // Full navigation (not router.replace) so the token never lingers in history
    // and the app re-reads auth from a clean URL.
    window.location.replace("/portfolio");
  }, []);

  return (
    <main className="flex min-h-screen items-center justify-center bg-paper px-4">
      <p className="text-sm text-muted">
        {shown === null ? "Falha no login. Volte e tente de novo." : "Entrando…"}
      </p>
    </main>
  );
}
