"use client";

import { useEffect, useState } from "react";
import { setToken } from "@/lib/api";

// Landing spot after Google OAuth: the API redirects here with the JWT in the URL
// *fragment* (#token=<jwt>), never the query string — a query string is logged by
// Cloudflare Pages and every proxy on the way, and this token is a full bearer
// credential nobody can revoke. Fragments are never sent to a server.
//
// Reading the hash also means no useSearchParams, so no Suspense boundary: the
// fragment is only ever available client-side.
export default function AuthCallbackPage() {
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const token = new URLSearchParams(window.location.hash.slice(1)).get("token");
    if (!token) {
      setFailed(true);
      return;
    }
    setToken(token);
    // Full navigation (not router.replace) so the token never lingers in history
    // and the app re-reads auth from a clean URL.
    window.location.replace("/portfolio");
  }, []);

  return (
    <main className="flex min-h-screen items-center justify-center bg-paper px-4">
      <p className="text-sm text-muted">
        {failed ? "Falha no login. Volte e tente de novo." : "Entrando…"}
      </p>
    </main>
  );
}
