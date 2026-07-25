"use client";

import { Suspense, useEffect } from "react";
import { useSearchParams } from "next/navigation";
import { setToken } from "@/lib/api";

// Landing spot after Google OAuth: the API redirects here with ?token=<jwt>.
// Store it, strip it from the URL, and go to the app. useSearchParams needs the
// Suspense boundary for the static export build.
function Callback() {
  const params = useSearchParams();

  useEffect(() => {
    const token = params.get("token");
    if (!token) return;
    setToken(token);
    // Full navigation (not router.replace) so the token never lingers in history
    // and the app re-reads auth from a clean URL.
    window.location.replace("/portfolio");
  }, [params]);

  const failed = params.get("token") === null;
  return (
    <main className="flex min-h-screen items-center justify-center bg-paper px-4">
      <p className="text-sm text-muted">
        {failed ? "Falha no login. Volte e tente de novo." : "Entrando…"}
      </p>
    </main>
  );
}

export default function AuthCallbackPage() {
  return (
    <Suspense fallback={null}>
      <Callback />
    </Suspense>
  );
}
