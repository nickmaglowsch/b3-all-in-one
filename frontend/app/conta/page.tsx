"use client";

import Link from "next/link";
import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { api, clearToken } from "@/lib/api";
import { usePrivacySummary } from "@/lib/hooks";
import { planName } from "@/lib/plans";
import { PageHeader } from "@/components/ui/PageHeader";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { IconTrash } from "@/components/icons";

// The API reports counts keyed by table name (it derives them from the schema, so
// a new table shows up here on its own). Unlabelled keys fall back to the raw
// name rather than disappearing — a number the titular can't see isn't an answer.
const LABELS: Record<string, string> = {
  imports: "Extratos importados",
  trades: "Negociações",
  cash_events: "Eventos de caixa (proventos, amortizações)",
  positions: "Posições consolidadas",
  cost_basis_overrides: "Ajustes de custo",
  darf_payments: "DARFs",
  backtest_runs: "Backtests",
  portfolio_sleeves: "Sleeves da carteira",
  asset_class_overrides: "Marcações de classe de ativo",
  rebalance_settings: "Preferências de rebalanceamento",
  user_strategies: "Estratégias que você criou",
};

const CONFIRM_WORD = "EXCLUIR";

function label(key: string) {
  return LABELS[key] ?? key;
}

export default function ContaPage() {
  const summary = usePrivacySummary();
  const [confirming, setConfirming] = useState(false);
  const [typed, setTyped] = useState("");

  // The export is plain JSON, so the same fetch wrapper every other call uses
  // gets it; only the saving is different.
  const download = useMutation({
    mutationFn: () => api<unknown>("/privacy/export"),
    onSuccess: (data) => {
      const url = URL.createObjectURL(
        new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }),
      );
      const a = document.createElement("a");
      a.href = url;
      a.download = "quantax-meus-dados.json";
      a.click();
      // Revoking in the same tick cancels the download in Safari and older Firefox.
      setTimeout(() => URL.revokeObjectURL(url), 0);
    },
  });

  const erase = useMutation({
    mutationFn: () => api<{ status: string }>("/privacy/account", { method: "DELETE" }),
    onSuccess: () => {
      // The account is gone; keeping its token around would only bounce the next
      // request off a 401.
      clearToken();
      window.location.href = "/";
    },
  });

  const contact = summary.data?.contact;

  return (
    <>
      <PageHeader title="Meus dados" eyebrow="Conta">
        <button
          type="button"
          onClick={() => download.mutate()}
          disabled={download.isPending}
          className="rounded-md bg-navy px-3 py-2 text-sm font-semibold text-white transition-colors hover:bg-navy/90 disabled:opacity-60"
        >
          {download.isPending ? "Preparando…" : "Baixar meus dados"}
        </button>
      </PageHeader>

      <p className="mb-6 max-w-3xl text-sm leading-relaxed text-muted">
        A Lei Geral de Proteção de Dados (LGPD, Lei 13.709/2018) garante a você
        confirmação do tratamento, acesso, portabilidade e eliminação dos seus
        dados. Esta página exerce esses direitos na hora, sem abrir chamado.
        Consulte também a{" "}
        <Link href="/privacidade" className="text-navy underline underline-offset-2">
          Política de Privacidade
        </Link>
        .
      </p>

      {download.isError && (
        <p className="mb-4 rounded-md border border-loss/40 bg-panel px-4 py-3 text-sm text-loss" role="alert">
          {download.error instanceof Error ? download.error.message : "Erro inesperado."}
        </p>
      )}

      {summary.isPending ? (
        <Skeleton className="h-64" />
      ) : summary.isError ? (
        <EmptyState title="Não foi possível carregar seus dados." />
      ) : (
        summary.data && (
          <>
            <Card title="Cadastro" className="mb-6">
              <dl className="grid gap-x-8 gap-y-3 sm:grid-cols-2">
                <Field label="Email" value={summary.data.account.email} />
                <Field label="Plano" value={planName(summary.data.account.plan)} />
                <Field
                  label="Login social"
                  value={
                    summary.data.account.oauth_providers.length
                      ? summary.data.account.oauth_providers.join(", ")
                      : "nenhum (email e senha)"
                  }
                />
                <Field
                  label="Dados no provedor de pagamento"
                  value={
                    summary.data.account.has_stripe_customer
                      ? "sim — cadastro de cobrança no Stripe"
                      : "nenhum"
                  }
                />
              </dl>
            </Card>

            {summary.data.books.map((book) => (
              <Card
                key={book.id}
                title={
                  <>
                    {book.label}
                    {!book.is_self && (
                      <span className="ml-2 font-normal text-muted">
                        (carteira de cliente)
                      </span>
                    )}
                  </>
                }
                className="mb-6"
              >
                <dl className="grid gap-x-8 gap-y-2 sm:grid-cols-2">
                  {Object.entries(book.records).map(([key, count]) => (
                    <div
                      key={key}
                      className="flex items-baseline justify-between gap-4 border-b border-hairline/60 py-1.5"
                    >
                      <dt className="text-sm text-muted">{label(key)}</dt>
                      <dd className="font-mono text-sm text-ink tnum">{count}</dd>
                    </div>
                  ))}
                </dl>
              </Card>
            ))}
          </>
        )
      )}

      <Card title="Excluir minha conta">
        <p className="max-w-2xl text-sm leading-relaxed text-muted">
          A exclusão apaga o login e <strong className="text-ink">todas</strong> as
          carteiras sob ele — extratos enviados, negociações, posições, backtests e
          estratégias — sem possibilidade de recuperação. Se você tem uma assinatura
          ativa, ela é cancelada no provedor de pagamento. Baixe seus dados antes,
          se quiser guardá-los.
        </p>

        {erase.isError && (
          <p className="mt-4 text-sm text-loss" role="alert">
            {erase.error instanceof Error ? erase.error.message : "Erro inesperado."}
          </p>
        )}

        {!confirming ? (
          <button
            type="button"
            onClick={() => setConfirming(true)}
            className="mt-4 inline-flex items-center gap-2 rounded-md border border-loss/50 px-3 py-2 text-sm font-semibold text-loss transition-colors hover:bg-loss/5"
          >
            <IconTrash size={16} />
            Excluir minha conta
          </button>
        ) : (
          <div className="mt-4 rounded-md border border-loss/40 bg-loss/5 p-4">
            <label
              htmlFor="confirmar-exclusao"
              className="block text-sm font-medium text-ink"
            >
              Digite <span className="font-mono">{CONFIRM_WORD}</span> para confirmar.
            </label>
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <input
                id="confirmar-exclusao"
                value={typed}
                onChange={(e) => setTyped(e.target.value)}
                autoComplete="off"
                className="rounded-md border border-hairline bg-paper px-3 py-2 font-mono text-sm text-ink"
              />
              <button
                type="button"
                onClick={() => erase.mutate()}
                disabled={typed !== CONFIRM_WORD || erase.isPending}
                className="rounded-md bg-loss px-3 py-2 text-sm font-semibold text-white transition-colors hover:bg-loss/90 disabled:opacity-50"
              >
                {erase.isPending ? "Excluindo…" : "Excluir definitivamente"}
              </button>
              <button
                type="button"
                onClick={() => {
                  setConfirming(false);
                  setTyped("");
                }}
                className="rounded-md px-3 py-2 text-sm text-muted hover:text-ink"
              >
                Cancelar
              </button>
            </div>
          </div>
        )}
      </Card>

      {contact && (
        <p className="mt-6 text-sm text-muted">
          Correção de dados, oposição ao tratamento ou qualquer outra solicitação:{" "}
          <a href={`mailto:${contact}`} className="text-navy underline underline-offset-2">
            {contact}
          </a>
          .
        </p>
      )}
    </>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="font-mono text-[11px] uppercase tracking-[0.12em] text-muted">
        {label}
      </dt>
      <dd className="mt-0.5 text-sm text-ink">{value}</dd>
    </div>
  );
}
