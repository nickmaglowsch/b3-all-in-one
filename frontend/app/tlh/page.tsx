"use client";

import { useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api";
import type { Position } from "@/lib/types";
import { formatMoney, formatQty } from "@/lib/format";
import { PageHeader } from "@/components/ui/PageHeader";
import { Card } from "@/components/ui/Card";
import { Money } from "@/components/ui/Money";
import { TickerChip } from "@/components/ui/TickerChip";
import { DataTable, type Column } from "@/components/ui/DataTable";
import { Skeleton } from "@/components/ui/Skeleton";
import { SectionNav } from "@/components/ui/SectionNav";
import { IconAlert } from "@/components/icons";

const ANCHOR = "scroll-mt-28 md:scroll-mt-16";

// ── API shapes (POST /tlh/plans) — mirrors taxcore/scripts/tlh_plan.py --json ──
interface Situation {
  gross_mtd: number;
  pnl_mtd: number;
  carry: number;
}
interface HarvestRow {
  ticker: string;
  qty: number;
  price: number;
  realized_loss: number;
  partial: boolean;
}
interface Bucket {
  status: string;
  gross_after: number;
  pnl_after: number;
  carry_used: number;
  target_loss: number;
  final_gross: number;
  final_pnl: number;
  ir: number;
  harvest: HarvestRow[];
  rebuys: { ticker: string; qty: number }[];
}
interface TlhPlan {
  month: string;
  isencao_limit: number;
  rates: Record<string, number>;
  situation: Record<string, Situation>;
  buckets: Record<string, Bucket>;
}

interface SellDraft {
  ticker: string;
  qty: string;
}

const CLASSES = ["AÇÃO", "FII"] as const;

export default function TlhPage() {
  const positions = useQuery({
    queryKey: ["positions"],
    queryFn: () => api<Position[]>("/portfolio/positions"),
  });
  // Situation = month-to-date state; independent of hypothetical sells, so load
  // it once with an empty plan.
  const situation = useQuery({
    queryKey: ["tlh", "situation"],
    queryFn: () =>
      api<TlhPlan>("/tlh/plans", {
        method: "POST",
        body: JSON.stringify({ sells: [] }),
      }),
  });

  const [sells, setSells] = useState<SellDraft[]>([]);
  const [exclude, setExclude] = useState<string[]>([]);

  const plan = useMutation({
    mutationFn: (body: { sells: { ticker: string; qty: number }[]; exclude?: string[] }) =>
      api<TlhPlan>("/tlh/plans", {
        method: "POST",
        body: JSON.stringify(body),
      }),
  });

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

  const situationData = plan.data?.situation ?? situation.data?.situation;
  const limit = plan.data?.isencao_limit ?? situation.data?.isencao_limit ?? 20000;

  function submit() {
    if (anyInvalid) return;
    plan.mutate({
      sells: sells.map((r) => ({ ticker: r.ticker, qty: Number(r.qty) })),
      exclude: exclude.length ? exclude : undefined,
    });
  }

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
          { id: "simular", label: "Simular vendas" },
          ...(plan.isPending || plan.data ? [{ id: "plano", label: "Plano" }] : []),
        ]}
      />

      {/* Situation panel */}
      <div id="situacao" className={`mb-6 grid gap-4 sm:grid-cols-2 ${ANCHOR}`}>
        {situation.isPending
          ? CLASSES.map((c) => <Skeleton key={c} className="h-36 w-full" />)
          : CLASSES.map((c) => (
              <SituationCard
                key={c}
                cls={c}
                data={situationData?.[c]}
                limit={c === "AÇÃO" ? limit : undefined}
              />
            ))}
      </div>

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
          <p
            className="mt-3 flex items-start gap-2 rounded-md border border-loss/30 bg-loss/[0.04] px-3 py-2 text-sm text-loss"
            role="alert"
          >
            <IconAlert size={15} className="mt-0.5 shrink-0" />
            {plan.error instanceof ApiError
              ? plan.error.message
              : "Não foi possível montar o plano."}
          </p>
        )}
      </Card>

      {/* Plan output */}
      <div id="plano" className={ANCHOR}>
        {plan.isPending ? (
          <Skeleton className="h-64 w-full" />
        ) : plan.data ? (
          <PlanResult plan={plan.data} />
        ) : null}
      </div>
    </>
  );
}

// ── Situation card ────────────────────────────────────────────────────────────
function SituationCard({
  cls,
  data,
  limit,
}: {
  cls: string;
  data?: Situation;
  limit?: number;
}) {
  const gross = data?.gross_mtd ?? 0;
  const pct = limit ? Math.min(1, gross / limit) : 0;
  const over = limit != null && gross > limit;
  return (
    <Card title={`${cls} · vendas em ${cls === "FII" ? "cotas" : "ações"}`}>
      <div className="space-y-3">
        <div>
          <div className="flex items-baseline justify-between">
            <span className="text-xs text-muted">Vendas no mês</span>
            <span className="font-mono text-sm tnum tabular-nums text-ink">
              {formatMoney(gross)}
              {limit != null && (
                <span className="text-muted"> / {formatMoney(limit)}</span>
              )}
            </span>
          </div>
          {limit != null && (
            <>
              <div className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-hairline">
                <div
                  className={`h-full rounded-full ${over ? "bg-loss" : "bg-navy"}`}
                  style={{ width: `${Math.max(pct * 100, gross > 0 ? 4 : 0)}%` }}
                />
              </div>
              <p className="mt-1 text-[11px] text-muted">
                {over
                  ? "Acima do limite — ganhos do mês são tributáveis."
                  : "Limite de isenção mensal para ações."}
              </p>
            </>
          )}
        </div>
        <Metric label="P&L do mês">
          <Money value={data?.pnl_mtd ?? 0} sign />
        </Metric>
        <Metric label="Prejuízo a compensar">
          <Money value={data?.carry ?? 0} />
        </Metric>
      </div>
    </Card>
  );
}

function Metric({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-baseline justify-between">
      <span className="text-xs text-muted">{label}</span>
      <span className="text-sm">{children}</span>
    </div>
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

// ── Plan result ───────────────────────────────────────────────────────────────
function statusTone(status: string): string {
  if (status.startsWith("TLH recomendado"))
    return "border-status-done/30 bg-status-done/10 text-status-done";
  if (status.includes("sem candidatos"))
    return "border-status-processing/30 bg-status-processing/10 text-status-processing";
  return "border-hairline bg-paper text-muted";
}

function PlanResult({ plan }: { plan: TlhPlan }) {
  const classes = Object.keys(plan.buckets);
  return (
    <section className="space-y-4">
      <div className="flex items-center gap-2">
        <h2 className="font-display text-lg font-bold text-ink">
          Plano · {plan.month}
        </h2>
      </div>
      {classes.length === 0 ? (
        <p className="text-sm text-muted">
          Nenhuma venda classificada — verifique os ativos informados.
        </p>
      ) : (
        classes.map((cls) => (
          <BucketCard
            key={cls}
            cls={cls}
            bucket={plan.buckets[cls]}
            rate={plan.rates[cls]}
            limit={plan.isencao_limit}
          />
        ))
      )}
    </section>
  );
}

function BucketCard({
  cls,
  bucket,
  rate,
  limit,
}: {
  cls: string;
  bucket: Bucket;
  rate: number;
  limit: number;
}) {
  const harvestCols: Column<HarvestRow>[] = [
    { key: "ticker", header: "Ticker", render: (r) => <TickerChip ticker={r.ticker} /> },
    {
      key: "qty",
      header: "Qtd",
      align: "right",
      render: (r) => (
        <>
          {formatQty(r.qty)}
          {r.partial && <span className="ml-1 text-[10px] text-muted">parcial</span>}
        </>
      ),
    },
    { key: "price", header: "Preço", align: "right", render: (r) => formatMoney(r.price) },
    {
      key: "loss",
      header: "Prejuízo realizado",
      align: "right",
      render: (r) => <Money value={r.realized_loss} sign />,
    },
  ];

  return (
    <Card
      title={
        <span className="flex items-center gap-2">
          {cls}
          <span
            className={`rounded-full border px-2 py-0.5 text-xs font-medium ${statusTone(
              bucket.status,
            )}`}
          >
            {bucket.status}
          </span>
        </span>
      }
      action={
        <span className="text-xs text-muted">
          Alíquota {(rate * 100).toFixed(0)}%
        </span>
      }
    >
      <div className="grid grid-cols-2 gap-x-6 gap-y-2 sm:grid-cols-4">
        <Metric label="Vendas brutas">
          <span className="font-mono text-sm tnum tabular-nums text-ink">
            {formatMoney(bucket.final_gross)}
          </span>
        </Metric>
        <Metric label="P&L final">
          <Money value={bucket.final_pnl} sign />
        </Metric>
        <Metric label="Compensado">
          <span className="font-mono text-sm tnum tabular-nums text-ink">
            {formatMoney(bucket.carry_used)}
          </span>
        </Metric>
        <Metric label="Imposto estimado">
          <span className="font-mono text-sm tnum tabular-nums text-ink">
            {formatMoney(bucket.ir)}
          </span>
        </Metric>
      </div>

      {cls === "AÇÃO" && bucket.final_gross <= limit && (
        <p className="mt-3 text-xs text-status-done">
          Vendas do mês ≤ {formatMoney(limit)} — ganhos isentos de IR.
        </p>
      )}

      {bucket.harvest.length > 0 ? (
        <div className="mt-5 space-y-4">
          <div>
            <p className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-muted">
              Vender hoje
            </p>
            <DataTable
              columns={harvestCols}
              rows={bucket.harvest}
              getRowKey={(r) => r.ticker}
              label="Vendas para colher prejuízo"
            />
          </div>

          <div>
            <p className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-muted">
              Recomprar em T+1
            </p>
            <div className="flex flex-wrap gap-1.5">
              {bucket.rebuys.map((r) => (
                <span
                  key={r.ticker}
                  className="inline-flex items-center gap-1.5 rounded-md border border-hairline bg-paper px-2 py-1 text-xs"
                >
                  <TickerChip ticker={r.ticker} />
                  <span className="font-mono tnum tabular-nums text-ink">
                    {formatQty(r.qty)}
                  </span>
                </span>
              ))}
            </div>
            <p className="mt-2 flex items-start gap-2 rounded-md border border-status-processing/30 bg-status-processing/10 px-3 py-2 text-xs text-status-processing">
              <IconAlert size={14} className="mt-0.5 shrink-0" />
              Recompre no próximo pregão — recomprar no mesmo dia configura day
              trade.
            </p>
          </div>
        </div>
      ) : (
        <p className="mt-4 text-sm text-muted">
          Sem colheita de prejuízo neste plano.
        </p>
      )}
    </Card>
  );
}
