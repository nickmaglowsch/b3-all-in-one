"use client";

import { useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api, errorText } from "@/lib/api";
import type { Position } from "@/lib/types";
import { formatQty } from "@/lib/format";
import { usePositions } from "@/lib/hooks";
import { PageHeader } from "@/components/ui/PageHeader";
import { Card } from "@/components/ui/Card";
import { TickerChip } from "@/components/ui/TickerChip";
import { Skeleton } from "@/components/ui/Skeleton";
import { SectionNav } from "@/components/ui/SectionNav";
import { IconAlert } from "@/components/icons";
import { StaleHint } from "@/components/ui/StaleHint";
import { asSet, isStale } from "@/lib/staleResult";
import { SituationPanel, type TlhPlan } from "./situation";
import { PlanResult } from "./plan";

const ANCHOR = "scroll-mt-28 md:scroll-mt-16";

interface SellDraft {
  ticker: string;
  qty: string;
}

interface PlanRequest {
  sells: { ticker: string; qty: number }[];
  exclude?: string[];
}

function postPlan(body: PlanRequest) {
  return api<TlhPlan>("/tlh/plans", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

// What the button would send now — also what the plan on screen is compared to,
// so an edited sell or exclusion flags that plan as answering older inputs.
const planRequest = (sells: SellDraft[], exclude: string[]): PlanRequest => ({
  sells: sells.map((r) => ({ ticker: r.ticker, qty: Number(r.qty) })),
  exclude: exclude.length ? exclude : undefined,
});
const planKey = (b: PlanRequest) => ({ ...b, exclude: asSet(b.exclude) });

export default function TlhPage() {
  const positions = usePositions();
  // Situation = month-to-date state; independent of hypothetical sells, so load
  // it once with an empty plan. On load, so a 402 is an inline offer, not a modal.
  const situation = useQuery({
    queryKey: ["tlh", "situation"],
    queryFn: () =>
      api<TlhPlan>(
        "/tlh/plans",
        { method: "POST", body: JSON.stringify({ sells: [] }) },
        { paywallDialog: false },
      ),
  });

  const [sells, setSells] = useState<SellDraft[]>([]);
  const [exclude, setExclude] = useState<string[]>([]);

  const plan = useMutation({ mutationFn: postPlan });

  // Same reason as StrategyForm's sigList: a fresh `[]` each render defeats the
  // memo that depends on it.
  const held = useMemo(() => positions.data ?? [], [positions.data]);
  const heldQty = useMemo(
    () => Object.fromEntries(held.map((p) => [p.ticker, p.qty])),
    [held],
  );

  function rowError(r: SellDraft): string | null {
    if (!r.ticker) return "Escolha um ativo.";
    const q = Number(r.qty);
    if (!r.qty || Number.isNaN(q) || q <= 0) return "Quantidade inválida.";
    const max = heldQty[r.ticker];
    if (max != null && q > max)
      return `Máximo ${formatQty(max)} em carteira.`;
    return null;
  }
  const anyInvalid = sells.length === 0 || sells.some((r) => rowError(r));

  function submit() {
    if (anyInvalid) return;
    plan.mutate(planRequest(sells, exclude));
  }
  const planStale = isStale(plan.variables, planRequest(sells, exclude), planKey);

  return (
    <>
      <PageHeader eyebrow="Colheita de prejuízo" title="TLH" />

      <p className="mb-4 flex items-start gap-2 text-xs text-muted">
        <span aria-hidden>ⓘ</span>
        Plano informativo — execute por sua conta na sua corretora.
      </p>

      <SectionNav
        items={[
          { id: "situacao", label: "Situação do mês" },
          { id: "reduzir", label: "Reduzir IR" },
          { id: "simular", label: "Simular vendas" },
          ...(plan.isPending || plan.data ? [{ id: "plano", label: "Plano" }] : []),
        ]}
      />

      {/* Situation panel */}
      <div id="situacao" className={`mb-6 grid gap-4 sm:grid-cols-2 ${ANCHOR}`}>
        <SituationPanel situation={situation} plan={plan.data} />
      </div>

      {/* After the fact: sales already made this month */}
      <ReduceSection held={held} exclude={exclude} setExclude={setExclude} />

      {/* Sells builder */}
      <Card id="simular" title="Simular vendas" className={`mb-6 ${ANCHOR}`}>
        <SellsBuilder
          held={held}
          loading={positions.isPending}
          sells={sells}
          setSells={setSells}
          rowError={rowError}
          heldQty={heldQty}
        />

        <ExcludePicker held={held} exclude={exclude} setExclude={setExclude} />

        <div className="mt-4 flex items-center gap-3">
          <button
            type="button"
            onClick={submit}
            disabled={anyInvalid || plan.isPending}
            className="rounded-md bg-navy px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-navy/90 disabled:opacity-60"
          >
            {plan.isPending ? "Planejando…" : "Planejar"}
          </button>
          {sells.length === 0 && (
            <span className="text-xs text-muted">
              Adicione ao menos uma venda para simular.
            </span>
          )}
        </div>

        {plan.isError && (
          <PlanError error={plan.error} fallback="Não foi possível montar o plano." />
        )}
      </Card>

      {/* Plan output */}
      <div id="plano" className={ANCHOR}>
        <PlanSlot pending={plan.isPending} plan={plan.data} stale={planStale} action="Planejar" />
      </div>
    </>
  );
}

// A plan's slot: a skeleton while it is computed, then the plan — flagged when
// the inputs above have changed since it was asked for.
function PlanSlot({
  pending,
  plan,
  stale,
  action,
  ...result
}: {
  pending: boolean;
  plan?: TlhPlan;
  stale: boolean;
  action: string;
  title?: string;
  empty?: string;
}) {
  if (pending) return <Skeleton className="h-64 w-full" />;
  if (!plan) return null;
  return (
    <>
      {stale && <StaleHint action={action} />}
      <PlanResult plan={plan} {...result} />
    </>
  );
}

function PlanError({ error, fallback }: { error: unknown; fallback: string }) {
  return (
    <p
      className="mt-3 flex items-start gap-2 rounded-md border border-loss/30 bg-loss/[0.04] px-3 py-2 text-sm text-loss"
      role="alert"
    >
      <IconAlert size={15} className="mt-0.5 shrink-0" />
      {errorText(error, fallback)}
    </p>
  );
}

// ── Reduce the month's IR ─────────────────────────────────────────────────────
// The after-the-fact plan: no sells proposed, so tlh_plan harvests against the
// gain already realised this month. Run on click rather than read off
// `situation`, so it prices at today's quotes and honours the exclusions.
function ReduceSection({
  held,
  exclude,
  setExclude,
}: {
  held: Position[];
  exclude: string[];
  setExclude: React.Dispatch<React.SetStateAction<string[]>>;
}) {
  const reduce = useMutation({ mutationFn: postPlan });
  const shown = reduce.isPending || reduce.data;
  const request = planRequest([], exclude);
  const stale = isStale(reduce.variables, request, planKey);
  return (
    <>
      <Card id="reduzir" title="Reduzir IR do mês" className={`mb-6 ${ANCHOR}`}>
        <p className="text-sm text-muted">
          Já vendeu com lucro neste mês? Veja quais posições em prejuízo vender
          hoje para compensar o ganho já realizado — e o que recomprar no
          próximo pregão.
        </p>

        <ExcludePicker held={held} exclude={exclude} setExclude={setExclude} />

        <div className="mt-4 flex items-center gap-3">
          <button
            type="button"
            onClick={() => reduce.mutate(request)}
            disabled={reduce.isPending}
            className="rounded-md bg-navy px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-navy/90 disabled:opacity-60"
          >
            {reduce.isPending ? "Calculando…" : "Ver operações"}
          </button>
        </div>

        {reduce.isError && (
          <PlanError error={reduce.error} fallback="Não foi possível calcular as operações." />
        )}
      </Card>

      <div id="reduzir-plano" className={`${ANCHOR} ${shown ? "mb-6" : ""}`}>
        <PlanSlot
          pending={reduce.isPending}
          plan={reduce.data}
          stale={stale}
          action="Ver operações"
          title="Reduzir IR"
          empty="Nenhuma venda realizada neste mês — não há lucro a compensar."
        />
      </div>
    </>
  );
}

// ── Sells builder ─────────────────────────────────────────────────────────────
function SellsBuilder({
  held,
  loading,
  sells,
  setSells,
  rowError,
  heldQty,
}: {
  held: Position[];
  loading: boolean;
  sells: SellDraft[];
  setSells: React.Dispatch<React.SetStateAction<SellDraft[]>>;
  rowError: (r: SellDraft) => string | null;
  heldQty: Record<string, number>;
}) {
  function update(i: number, patch: Partial<SellDraft>) {
    setSells((rows) => rows.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  }
  function addRow() {
    const used = new Set(sells.map((s) => s.ticker));
    const next = held.find((p) => !used.has(p.ticker))?.ticker ?? held[0]?.ticker ?? "";
    setSells((rows) => [...rows, { ticker: next, qty: "" }]);
  }
  function removeRow(i: number) {
    setSells((rows) => rows.filter((_, j) => j !== i));
  }

  if (loading) return <Skeleton className="h-10 w-full" />;

  return (
    <div className="space-y-2">
      {sells.map((r, i) => {
        const err = rowError(r);
        const max = heldQty[r.ticker];
        return (
          <div key={i} className="flex items-start gap-2">
            <select
              value={r.ticker}
              onChange={(e) => update(i, { ticker: e.target.value })}
              className="w-40 rounded-md border border-hairline bg-panel px-2 py-1.5 font-mono text-sm text-ink"
            >
              {held.map((p) => (
                <option key={p.ticker} value={p.ticker}>
                  {p.ticker}
                </option>
              ))}
            </select>
            <div className="flex-1">
              <input
                type="number"
                inputMode="decimal"
                min={0}
                max={max}
                step="any"
                value={r.qty}
                onChange={(e) => update(i, { qty: e.target.value })}
                placeholder={max != null ? `qtd (máx ${formatQty(max)})` : "qtd"}
                className={`w-full rounded-md border bg-panel px-2 py-1.5 font-mono text-sm text-ink tnum tabular-nums ${
                  err ? "border-loss" : "border-hairline"
                }`}
              />
              {err && <p className="mt-1 text-[11px] text-loss">{err}</p>}
            </div>
            <button
              type="button"
              onClick={() => removeRow(i)}
              aria-label="Remover venda"
              className="rounded-md border border-hairline px-2.5 py-1.5 text-sm text-muted hover:text-loss"
            >
              ×
            </button>
          </div>
        );
      })}
      <button
        type="button"
        onClick={addRow}
        disabled={held.length === 0}
        className="rounded-md border border-hairline px-3 py-1.5 text-sm font-medium text-ink transition-colors hover:bg-hairline/40 disabled:opacity-60"
      >
        + Adicionar venda
      </button>
    </div>
  );
}

// ── Exclude picker ────────────────────────────────────────────────────────────
function ExcludePicker({
  held,
  exclude,
  setExclude,
}: {
  held: Position[];
  exclude: string[];
  setExclude: React.Dispatch<React.SetStateAction<string[]>>;
}) {
  if (held.length === 0) return null;
  function toggle(t: string) {
    setExclude((xs) => (xs.includes(t) ? xs.filter((x) => x !== t) : [...xs, t]));
  }
  return (
    <details className="mt-4">
      <summary className="cursor-pointer text-xs font-medium text-muted hover:text-ink">
        Excluir candidatos da colheita{exclude.length ? ` (${exclude.length})` : ""}
      </summary>
      <p className="mt-2 text-[11px] text-muted">
        Ativos marcados não serão vendidos para realizar prejuízo.
      </p>
      <div className="mt-2 flex flex-wrap gap-1.5">
        {held.map((p) => {
          const on = exclude.includes(p.ticker);
          return (
            <button key={p.ticker} type="button" onClick={() => toggle(p.ticker)}>
              <TickerChip
                ticker={p.ticker}
                className={
                  on
                    ? "border-loss/40 bg-loss/10 text-loss line-through"
                    : "hover:border-muted"
                }
              />
            </button>
          );
        })}
      </div>
    </details>
  );
}
