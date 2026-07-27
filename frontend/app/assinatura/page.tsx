"use client";

import { useMutation } from "@tanstack/react-query";
import { useSyncExternalStore } from "react";
import { api } from "@/lib/api";
import { useSubscription } from "@/lib/hooks";
import { formatDate } from "@/lib/format";
import { PLANS, planName, planRank } from "@/lib/plans";
import { PageHeader } from "@/components/ui/PageHeader";
import { Card } from "@/components/ui/Card";
import { IconCheck } from "@/components/icons";

// Stripe hands the browser back with ?checkout=ok|cancelado. Read it once on
// mount (no useSearchParams => no Suspense boundary needed for a static route).
//
// useSyncExternalStore rather than setState-in-an-effect: the value is not React
// state, it is a browser value React has to be told about, and the server
// snapshot is what keeps the static export's HTML matching the first client
// render. Never subscribes — the query string does not change under this page.
const neverChanges = () => () => {};

function useCheckoutOutcome() {
  return useSyncExternalStore(
    neverChanges,
    () => new URLSearchParams(window.location.search).get("checkout"),
    () => null,
  );
}

export default function AssinaturaPage() {
  const outcome = useCheckoutOutcome();
  const sub = useSubscription(outcome === "ok");
  const current = sub.data?.plan ?? "gratis";

  // Both endpoints answer with a Stripe-hosted URL; we only redirect.
  const go = useMutation({
    mutationFn: (body: { path: string; plan?: string }) =>
      api<{ url: string }>(body.path, {
        method: "POST",
        body: body.plan ? JSON.stringify({ plan: body.plan }) : undefined,
      }),
    onSuccess: ({ url }) => {
      window.location.href = url;
    },
  });

  return (
    <>
      <PageHeader title="Assinatura" eyebrow="Conta">
        {sub.data?.manageable && (
          <button
            type="button"
            onClick={() => go.mutate({ path: "/billing/portal" })}
            disabled={go.isPending}
            className="rounded-md border border-hairline px-3 py-2 text-sm font-semibold text-ink transition-colors hover:bg-hairline/40 disabled:opacity-60"
          >
            Gerenciar no Stripe
          </button>
        )}
      </PageHeader>

      {outcome === "ok" && current === "gratis" && (
        <p className="mb-4 rounded-md border border-hairline bg-panel px-4 py-3 text-sm text-muted">
          Pagamento recebido — confirmando com o Stripe…
        </p>
      )}
      {outcome === "cancelado" && (
        <p className="mb-4 rounded-md border border-hairline bg-panel px-4 py-3 text-sm text-muted">
          Checkout cancelado. Nada foi cobrado.
        </p>
      )}
      {go.isError && (
        <p className="mb-4 rounded-md border border-loss/40 bg-panel px-4 py-3 text-sm text-loss" role="alert">
          {go.error instanceof Error ? go.error.message : "Erro inesperado."}
        </p>
      )}

      <Card title="Plano atual" className="mb-6">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <p className="font-display text-2xl font-extrabold tracking-tight text-ink">
            {planName(current)}
          </p>
          <p className="text-sm text-muted">
            {sub.data?.status === "past_due"
              ? "Pagamento pendente — atualize o cartão para não perder o acesso."
              : sub.data?.renews_at
                ? `Renova em ${formatDate(sub.data.renews_at)}`
                : "Sem assinatura ativa."}
          </p>
        </div>
      </Card>

      <div className="grid gap-5 md:grid-cols-2 xl:grid-cols-4">
        {PLANS.map((plan) => {
          const isCurrent = plan.id === current;
          const isDowngrade = planRank(plan.id) < planRank(current);
          return (
            <div
              key={plan.id}
              className={`relative flex h-full flex-col rounded-lg border bg-panel p-6 ${
                isCurrent ? "border-navy ring-1 ring-navy" : "border-hairline"
              }`}
            >
              {isCurrent && (
                <span className="absolute -top-3 left-6 rounded-full bg-navy px-3 py-1 font-mono text-[10px] font-medium uppercase tracking-[0.12em] text-white">
                  Seu plano
                </span>
              )}
              <h3 className="text-sm font-semibold text-ink">{plan.name}</h3>
              <p className="mt-1 text-sm text-muted">{plan.tagline}</p>
              <p className="mt-5 flex items-baseline gap-1">
                <span className="font-display text-4xl font-extrabold tracking-tight text-ink tnum">
                  {plan.price}
                </span>
                <span className="text-sm text-muted">{plan.cadence}</span>
              </p>

              <ul className="mt-6 flex-1 space-y-3">
                {plan.features.map((f) => (
                  <li key={f} className="flex items-start gap-2.5 text-sm text-ink">
                    <IconCheck size={18} className="mt-0.5 shrink-0 text-navy" />
                    <span>{f}</span>
                  </li>
                ))}
              </ul>

              {/* Downgrades and cancellation are Stripe's portal, not ours — it already
                  handles proration and the cancel-at-period-end flow. */}
              {isCurrent ? (
                <p className="mt-8 py-2.5 text-center text-sm font-semibold text-muted">
                  Plano atual
                </p>
              ) : isDowngrade ? (
                <button
                  type="button"
                  onClick={() => go.mutate({ path: "/billing/portal" })}
                  disabled={go.isPending || !sub.data?.manageable}
                  className="mt-8 rounded-md border border-hairline px-4 py-2.5 text-sm font-semibold text-ink transition-colors hover:bg-hairline/40 disabled:opacity-60"
                >
                  Mudar no Stripe
                </button>
              ) : (
                <button
                  type="button"
                  onClick={() => go.mutate({ path: "/billing/checkout", plan: plan.id })}
                  disabled={go.isPending}
                  className="mt-8 rounded-md bg-navy px-4 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-navy/90 disabled:opacity-60"
                >
                  {go.isPending ? "Abrindo…" : plan.cta}
                </button>
              )}
            </div>
          );
        })}
      </div>
    </>
  );
}
