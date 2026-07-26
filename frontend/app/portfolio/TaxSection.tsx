"use client";

import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api";
import type { TaxPlan, TaxSummary } from "@/lib/types";
import { Card } from "@/components/ui/Card";
import { Money } from "@/components/ui/Money";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { TickerChip } from "@/components/ui/TickerChip";
import { IconReceipt } from "@/components/icons";
import { formatDate, formatMoney, formatPercent, formatQty } from "@/lib/format";

const TITLE = "Situação fiscal";

// The two regimes the Brazilian rules actually distinguish, in the order the
// payload uses them. They are NOT symmetric: only ações carry the R$20k monthly
// isenção, and the rates differ (15% / 20%). Rendering them as one uniform row
// of numbers would flatten the one thing this panel exists to teach.
const BUCKETS = [
  { key: "AÇÃO", label: "Ações", isento: true },
  { key: "FII", label: "FIIs", isento: false },
] as const;

/** Isenção headroom for ações: R$20k of gross sales per month, tax-free. */
function Headroom({ grossMtd, limit }: { grossMtd: number; limit: number }) {
  const used = Math.min(grossMtd / limit, 1);
  const left = Math.max(limit - grossMtd, 0);
  // Strictly greater: the engine's rule is `gross <= ISENCAO_LIMIT → isento`,
  // so exactly R$ 20.000,00 of gross sales is still tax-free. `>=` here made
  // the card announce "limite ultrapassado" on the same row where its own IR
  // line said zero.
  const over = grossMtd > limit;
  return (
    <div>
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-xs text-muted">Isenção usada no mês</span>
        <span className="font-mono tnum tabular-nums text-xs text-muted">
          {formatMoney(grossMtd)} / {formatMoney(limit)}
        </span>
      </div>
      <span className="mt-1.5 block h-2 w-full rounded-xs bg-hairline/60">
        <span
          className={`block h-full rounded-xs ${over ? "bg-loss" : "bg-ink/25"}`}
          style={{ width: `${used * 100}%` }}
        />
      </span>
      {/* The threshold is the decision on this page, so it gets a sentence, not
          just a colour — and the glyph carries it for anyone who can't see the
          bar turn red. */}
      <p className={`mt-1.5 text-xs ${over ? "text-loss" : "text-ink"}`}>
        <span aria-hidden>{over ? "▲ " : "• "}</span>
        {over
          ? "Limite ultrapassado — vendas de ações neste mês são tributáveis."
          : `Ainda cabem ${formatMoney(left)} em vendas isentas neste mês.`}
      </p>
    </div>
  );
}

function Figure({
  label,
  value,
  signed = false,
  hint,
}: {
  label: string;
  value: number;
  signed?: boolean;
  hint?: string;
}) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <span className="text-xs text-muted">
        {label}
        {hint && <span className="ml-1 text-muted/70">{hint}</span>}
      </span>
      {/* `signed` only where the sign IS the information: a realised result can
          be a gain or a loss. IR devido and prejuízo a compensar are magnitudes
          and stay ink, however large. */}
      <Money value={value} sign={signed} className="text-sm" />
    </div>
  );
}

function Bucket({
  plan,
  bucket,
}: {
  plan: TaxPlan;
  bucket: (typeof BUCKETS)[number];
}) {
  const s = plan.situation[bucket.key];
  // `owed` is computed by the API from `situation`, deliberately NOT read off
  // `buckets[cls].ir` — that field is the IR *after* executing the harvest plan
  // below, so it reads R$ 0,00 exactly when the real DARF is largest.
  const owed = plan.owed?.[bucket.key];
  const rate = plan.rates[bucket.key];
  if (!s) return null;

  return (
    <div className="rounded-md border border-hairline p-4">
      <div className="flex items-baseline justify-between gap-2">
        <h3 className="text-sm font-semibold text-ink">{bucket.label}</h3>
        <span className="font-mono tnum tabular-nums text-[11px] uppercase tracking-[0.14em] text-muted">
          {rate !== undefined ? `${formatPercent(rate)} IR` : "—"}
        </span>
      </div>

      <div className="mt-3 space-y-2">
        <Figure label="Resultado no mês" value={s.pnl_mtd} signed />
        <Figure label="Prejuízo a compensar" value={s.carry} />
        {owed ? (
          <Figure
            label="IR devido no mês"
            value={owed.ir}
            hint={owed.isento ? "(mês isento)" : undefined}
          />
        ) : (
          // An older cached payload predates the `owed` field. Say so rather
          // than printing a zero we cannot stand behind.
          <div className="flex items-baseline justify-between gap-3">
            <span className="text-xs text-muted">IR devido no mês</span>
            <span className="font-mono text-sm text-muted">
              — apure de novo
            </span>
          </div>
        )}
      </div>

      <div className="mt-4 border-t border-hairline pt-3">
        {bucket.isento ? (
          <Headroom grossMtd={s.gross_mtd} limit={plan.isencao_limit} />
        ) : (
          // The absence is information: someone reading two columns will assume
          // the blank one is a rendering gap unless it says otherwise.
          <div>
            <p className="text-xs text-muted">Sem isenção mensal</p>
            <p className="mt-1 text-xs text-ink">
              FIIs não têm o limite de {formatMoney(plan.isencao_limit)} — todo
              ganho é tributável.
            </p>
            <p className="mt-1 font-mono tnum tabular-nums text-xs text-muted">
              Vendas no mês {formatMoney(s.gross_mtd)}
            </p>
          </div>
        )}
      </div>
    </div>
  );
}

export function TaxSection() {
  const qc = useQueryClient();

  const q = useQuery({
    queryKey: ["tax-summary"],
    // Fires on page load, so a 402 renders inline instead of throwing a dialog
    // over a page the user has not read yet.
    queryFn: () => api<TaxSummary>("/portfolio/tax-summary", {}, { paywallDialog: false }),
    retry: false,
  });

  const refresh = useMutation({
    mutationFn: () =>
      api<TaxSummary>("/portfolio/tax-summary/refresh", { method: "POST" }, {
        paywallDialog: false,
      }),
    onSuccess: (data) => qc.setQueryData(["tax-summary"], data),
  });

  if (q.isLoading) {
    return (
      <Card title={TITLE}>
        <Skeleton className="h-48 w-full" label="Carregando situação fiscal…" />
      </Card>
    );
  }

  if (q.isError) {
    const status = q.error instanceof ApiError ? q.error.status : 0;
    if (status === 402) {
      return (
        <Card title={TITLE}>
          <EmptyState
            icon={<IconReceipt size={28} />}
            title="A apuração do mês faz parte do plano Investidor."
            hint="Isenção de R$ 20 mil usada e disponível, prejuízo a compensar, IR devido e os candidatos a colheita — apurados pelo mesmo motor que gera seu IRPF."
            action={
              <Link
                href="/assinatura"
                className="rounded-md bg-navy px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-navy/90"
              >
                Ver o plano Investidor
              </Link>
            }
          />
        </Card>
      );
    }
    return (
      <Card title={TITLE}>
        <EmptyState
          icon={<IconReceipt size={28} />}
          title="Não foi possível carregar a situação fiscal."
          hint="A conexão com a API falhou. Nada foi perdido."
          action={
            <button
              type="button"
              onClick={() => q.refetch()}
              className="rounded-md border border-hairline px-3 py-1.5 text-sm font-medium text-ink transition-colors hover:bg-hairline/40"
            >
              Tentar de novo
            </button>
          }
        />
      </Card>
    );
  }

  const data = q.data;
  const plan = data?.payload ?? null;
  const running = refresh.isPending;

  const recompute = (
    <button
      type="button"
      onClick={() => refresh.mutate()}
      disabled={running}
      className="rounded-md border border-hairline px-2.5 py-1 text-xs font-medium text-ink transition-colors hover:bg-hairline/40 disabled:cursor-not-allowed disabled:opacity-40"
    >
      {running ? "Apurando…" : "Apurar agora"}
    </button>
  );

  // Rendered in BOTH states. Living only in the never-computed branch meant a
  // failed recompute over existing numbers was completely silent: the button
  // re-enabled, the stale figures stayed, and the user believed they had just
  // re-apurado.
  const refreshError = refresh.isError && (
    <p className="mt-3 text-xs text-loss">
      <span aria-hidden>▲ </span>
      {refresh.error instanceof ApiError && refresh.error.status === 422
        ? "Sem negociações para apurar — importe um extrato de negociação primeiro."
        : refresh.error instanceof ApiError && refresh.error.status === 409
          ? "Já existe uma apuração em andamento. Aguarde alguns segundos."
          : "A apuração falhou e os números abaixo continuam os anteriores."}
    </p>
  );

  const busy = (
    <p role="status" aria-live="polite" className="sr-only">
      {running ? "Apurando situação fiscal" : ""}
    </p>
  );

  // Never computed. Not an error and not an empty book — the apuração simply has
  // not been asked for, and it costs a real engine run, so it says so.
  if (!plan) {
    return (
      <Card title={TITLE}>
        <EmptyState
          icon={<IconReceipt size={28} />}
          title="Ainda não apuramos a situação fiscal desta carteira."
          hint="A apuração roda o mesmo motor do IRPF sobre todo o seu histórico — leva alguns segundos e fica guardada."
          action={recompute}
        />
        {busy}
        <div className="text-center">{refreshError}</div>
      </Card>
    );
  }

  return (
    <Card
      title={TITLE}
      action={
        <div className="flex items-center gap-3">
          <span className="font-mono tnum tabular-nums text-xs text-muted">
            {plan.month} · apurado em {formatDate(data?.computed_at ?? null)}
          </span>
          {recompute}
        </div>
      }
    >
      {/* A tax figure computed before the newest extract is wrong, not merely
          old — and so is one from last month, since every figure here is
          month-to-date. Both are reported, with the cause named. */}
      {data?.stale && (
        <p className="mb-4 rounded-md border border-hairline bg-paper p-3 text-sm text-ink">
          <span aria-hidden>▲ </span>
          {data.stale_reason === "month"
            ? `Esta apuração é de ${plan.month} e o mês virou: a isenção reinicia e o resultado do mês volta a zero. Apure de novo para ver ${new Date().toLocaleDateString("pt-BR", { month: "2-digit", year: "numeric" })}.`
            : "Um extrato foi importado depois desta apuração, então estes números não incluem as operações novas. Apure de novo para atualizar."}
        </p>
      )}

      {refreshError}

      <div className="grid gap-3 md:grid-cols-2">
        {BUCKETS.map((b) => (
          <Bucket key={b.key} plan={plan} bucket={b} />
        ))}
      </div>

      <HarvestList plan={plan} />

      <p className="mt-4 text-xs text-muted">
        O app apura; você confere contra o informe e declara. O IR devido é o do
        mês como ele está hoje — colher os candidatos acima reduz esse valor, mas
        só depois de executado. Alíquotas de swing trade; não cobre day trade.
      </p>

      {busy}
    </Card>
  );
}

/** Losing positions worth harvesting. Read-only by design: the moment this grows
 *  a quantity input it has become the TLH planner, and the two will drift. */
function HarvestList({ plan }: { plan: TaxPlan }) {
  const rows = BUCKETS.flatMap((b) =>
    (plan.buckets[b.key]?.harvest ?? []).map((h) => ({ ...h, cls: b.label })),
  );
  if (rows.length === 0) return null;

  return (
    <div className="mt-4 border-t border-hairline pt-4">
      <p className="font-mono text-[11px] font-medium uppercase tracking-[0.14em] text-muted">
        Candidatos a colheita
      </p>
      <ul className="mt-2 space-y-1.5">
        {rows.map((h) => (
          <li key={`${h.cls}-${h.ticker}`} className="flex items-center gap-2 text-sm">
            <TickerChip ticker={h.ticker} />
            <span className="font-mono tnum tabular-nums text-xs text-muted">
              {formatQty(h.qty)}
            </span>
            {h.partial && <span className="text-xs text-muted">parcial</span>}
            {/* realized_loss already arrives negative from the engine; passing
                it through unchanged means a sign inversion upstream would show
                up here instead of being silently absorbed by Math.abs. */}
            <Money value={h.realized_loss} sign className="ml-auto text-sm" />
          </li>
        ))}
      </ul>
      <Link
        href="/tlh"
        className="mt-3 inline-block rounded-md border border-hairline px-2.5 py-1 text-xs font-medium text-navy transition-colors hover:bg-hairline/40"
      >
        Montar plano de colheita
      </Link>
    </div>
  );
}
