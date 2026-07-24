"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { devLogin } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleDevLogin() {
    setLoading(true);
    setError(null);
    try {
      await devLogin();
      // Only same-site paths — an absolute `next` would be an open redirect.
      const next = new URLSearchParams(window.location.search).get("next");
      router.replace(next?.startsWith("/") && !next.startsWith("//") ? next : "/portfolio");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Falha ao entrar.");
      setLoading(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-paper px-4">
      <div className="w-full max-w-sm">
        <div className="mb-6 text-center">
          <p className="font-display text-2xl font-extrabold tracking-tight">
            <span className="text-navy">B3</span>
            <span className="text-ink"> · tudo-em-um</span>
          </p>
          <p className="mt-1 text-sm text-muted">
            Carteira, IRPF e bench a partir dos seus extratos da B3.
          </p>
        </div>

        <div className="rounded-lg border border-hairline bg-panel p-6">
          <button
            type="button"
            onClick={handleDevLogin}
            disabled={loading}
            className="w-full rounded-md bg-navy px-4 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-navy/90 disabled:opacity-60"
          >
            {loading ? "Entrando…" : "Entrar (dev)"}
          </button>

          {error && (
            <p className="mt-3 text-sm text-loss" role="alert">
              {error}
            </p>
          )}

          <p className="mt-4 text-center text-xs text-muted">
            Login com Google chega depois.
          </p>
        </div>
      </div>
    </main>
  );
}
