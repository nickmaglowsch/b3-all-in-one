"use client";

import { useMemo, useState } from "react";
import { PageHeader } from "@/components/ui/PageHeader";
import { Card } from "@/components/ui/Card";
import { DataTable, type Column } from "@/components/ui/DataTable";
import { TickerChip } from "@/components/ui/TickerChip";
import { Money } from "@/components/ui/Money";
import { PctDelta } from "@/components/ui/PctDelta";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { DivergingBars } from "@/components/charts/DivergingBars";
import { IconCompass } from "@/components/icons";
import { ApiError } from "@/lib/api";
import { formatDate, formatPercent } from "@/lib/format";
import type { DriftRow, Sleeve } from "@/lib/types";
import {
  CASH_REF,
  compactBRL,
  useRebalancePlan,
  useSaveSleeves,
  useSleeves,
  useStrategies,
  useTarget,
  type RebalancePlan,
  type TradeLine,
} from "./lib";

export default function EstrategiaPage() {
  const sleeves = useSleeves();
  const hasSleeves = (sleeves.data?.length ?? 0) > 0;

  return (
    <>
      <PageHeader eyebrow="Alocação" title="Estratégia">
        <span className="font-mono text-[11px] text-muted">
          sleeves · alvo · rebalanceamento
        </span>
      </PageHeader>

      <div className="space-y-4">
        <SleevesEditor />
        {sleeves.isLoading ? (
          <Skeleton className="h-40 w-full" />
        ) : hasSleeves ? (
          <>
            <TargetPanel />
            <RebalancePanel />
          </>
        ) : null}
      </div>
    </>
  );
}

// ── sleeves editor ──────────────────────────────────────────────────────────

interface Row {
  strategy_ref: string;
  target_pct: string; // kept as string so the input can be cleared mid-edit
}

function SleevesEditor() {
  const sleeves = useSleeves();
  const strategies = useStrategies();
  const save = useSaveSleeves();
  const [rows, setRows] = useState<Row[]>([]);
  const [dirty, setDirty] = useState(false);
  const [syncedFrom, setSyncedFrom] = useState<Sleeve[] | null>(null);

  // Seed the editor from server data on first load and after each save (when not
  // mid-edit). Adjusting state during render — guarded by reference identity so it
  // runs once per new server payload — is the React-endorsed alternative to an effect.
  if (sleeves.data && sleeves.data !== syncedFrom && !dirty) {
    setSyncedFrom(sleeves.data);
    setRows(
      sleeves.data.map((s) => ({
        strategy_ref: s.strategy_ref,
        target_pct: String(s.target_pct),
      })),
    );
  }

  const refOptions = useMemo(() => {
    const names = (strategies.data ?? []).map((s) => s.name);
    return [CASH_REF, ...names];
  }, [strategies.data]);

  // Strategies that read CVM fundamentals — flag them in the picker so the choice
  // is informed (native <option> can't hold a badge, so the label carries a marker).
  const needsFunds = useMemo(
    () =>
      new Set(
        (strategies.data ?? [])
          .filter((s) => s.needs_fundamentals)
          .map((s) => s.name),
      ),
    [strategies.data],
  );

  const sum = rows.reduce((a, r) => a + (parseFloat(r.target_pct) || 0), 0);
  const refs = rows.map((r) => r.strategy_ref);
  const hasDup = new Set(refs).size !== refs.length;
  const hasBlank = rows.some((r) => !r.strategy_ref || r.target_pct.trim() === "");
  const sumOk = Math.abs(sum - 100) < 0.01;
  const canSave = rows.length > 0 && sumOk && !hasDup && !hasBlank && !save.isPending;

  const edit = (patch: (rs: Row[]) => Row[]) => {
    setDirty(true);
    setRows(patch);
  };

  const onSave = () => {
    const body: Sleeve[] = rows.map((r) => ({
      strategy_ref: r.strategy_ref,
      target_pct: parseFloat(r.target_pct),
    }));
    save.mutate(body, { onSuccess: () => setDirty(false) });
  };

  return (
    <Card
      title="Sleeves"
      action={
        <span
          className={`font-mono text-[11px] ${sumOk ? "text-muted" : "text-loss"}`}
        >
          soma {sum.toLocaleString("pt-BR", { maximumFractionDigits: 2 })}% / 100%
        </span>
      }
    >
      {strategies.isLoading ? (
        <Skeleton className="h-24 w-full" />
      ) : (
        <div className="space-y-2">
          {rows.map((r, i) => (
            <div key={i} className="flex items-center gap-2">
              <select
                value={r.strategy_ref}
                onChange={(e) =>
                  edit((rs) =>
                    rs.map((x, j) =>
                      j === i ? { ...x, strategy_ref: e.target.value } : x,
                    ),
                  )
                }
                className="min-w-0 flex-1 rounded-md border border-hairline bg-paper px-2 py-1.5 font-mono text-sm text-ink"
              >
                <option value="">selecione…</option>
                {refOptions.map((name) => (
                  <option key={name} value={name}>
                    {name === CASH_REF
                      ? "Caixa (CDI)"
                      : needsFunds.has(name)
                        ? `${name} · precisa de fundamentos`
                        : name}
                  </option>
                ))}
              </select>
              <div className="flex items-center gap-1">
                <input
                  type="number"
                  min={0}
                  max={100}
                  step={1}
                  value={r.target_pct}
                  onChange={(e) =>
                    edit((rs) =>
                      rs.map((x, j) =>
                        j === i ? { ...x, target_pct: e.target.value } : x,
                      ),
                    )
                  }
                  className="w-20 rounded-md border border-hairline bg-paper px-2 py-1.5 text-right font-mono text-sm tnum text-ink"
                />
                <span className="font-mono text-sm text-muted">%</span>
              </div>
              <button
                type="button"
                onClick={() => edit((rs) => rs.filter((_, j) => j !== i))}
                aria-label="Remover sleeve"
                className="rounded-md border border-hairline px-2 py-1.5 text-sm text-muted hover:bg-hairline/40"
              >
                ✕
              </button>
            </div>
          ))}

          <div className="flex items-center justify-between pt-1">
            <button
              type="button"
              onClick={() =>
                edit((rs) => [...rs, { strategy_ref: "", target_pct: "" }])
              }
              className="rounded-md border border-hairline bg-paper px-3 py-1.5 text-xs font-medium text-ink hover:bg-hairline/40"
            >
              + Adicionar sleeve
            </button>
            <button
              type="button"
              onClick={onSave}
              disabled={!canSave}
              className="rounded-md bg-navy px-4 py-1.5 text-xs font-semibold text-white disabled:cursor-not-allowed disabled:opacity-40"
            >
              {save.isPending ? "Salvando…" : "Salvar"}
            </button>
          </div>

          {hasDup && (
            <p className="text-xs text-loss" role="alert">
              Cada estratégia só pode aparecer uma vez.
            </p>
          )}
          {save.error && (
            <p className="text-xs text-loss" role="alert">
              {save.error instanceof ApiError
                ? save.error.message
                : "Falha ao salvar os sleeves."}
            </p>
          )}
          {save.isSuccess && !dirty && (
            <p className="text-xs text-gain">Sleeves salvos.</p>
          )}
          {save.data?.warnings?.map((w, i) => (
            <p key={i} className="text-xs text-series-4" role="status">
              {w}
            </p>
          ))}
        </div>
      )}
    </Card>
  );
}

// ── target / drift ──────────────────────────────────────────────────────────

const DRIFT_TOP = 12;

function TargetPanel() {
  const target = useTarget();

  if (target.isLoading) return <Skeleton className="h-72 w-full" />;

  // 409 = sleeves exist but target can't be built (shouldn't happen here, but be honest).
  if (target.error) {
    const msg =
      target.error instanceof ApiError
        ? target.error.message
        : "Falha ao calcular o alvo.";
    return (
      <Card title="Alvo x atual">
        <EmptyState
          icon={<IconCompass size={28} />}
          title="Não foi possível montar a carteira-alvo."
          hint={msg}
        />
      </Card>
    );
  }

  const t = target.data!;
  const sorted = [...t.drift].sort(
    (a, b) => Math.abs(b.drift_brl) - Math.abs(a.drift_brl),
  );
  const top = sorted.slice(0, DRIFT_TOP);
  const rest = sorted.slice(DRIFT_TOP);
  const restSum = rest.reduce((a, r) => a + r.drift_brl, 0);
  const bars = [
    ...top.map((d) => ({ label: d.ticker, value: d.drift_brl })),
    ...(rest.length ? [{ label: "outros", value: restSum }] : []),
  ];

  const columns: Column<DriftRow>[] = [
    {
      key: "ticker",
      header: "Ativo",
      render: (r) => <TickerChip ticker={r.ticker} />,
    },
    {
      key: "target_w",
      header: "Peso alvo",
      align: "right",
      render: (r) => formatPercent(r.target_w),
    },
    {
      key: "current_w",
      header: "Peso atual",
      align: "right",
      render: (r) => formatPercent(r.current_w),
    },
    {
      key: "drift_w",
      header: "Desvio",
      align: "right",
      render: (r) => <PctDelta value={r.drift_w} />,
    },
    {
      key: "drift_brl",
      header: "Desvio (R$)",
      align: "right",
      render: (r) => <Money value={r.drift_brl} sign />,
    },
  ];

  return (
    <Card
      title="Alvo x atual"
      action={
        <span className="font-mono text-[11px] text-muted">
          {formatDate(t.as_of)} · patrimônio {compactBRL(t.nav)} · caixa-alvo{" "}
          {formatPercent(t.cash_weight_target)}
        </span>
      }
    >
      <div className="space-y-4">
        <div>
          <p className="mb-2 text-xs text-muted">
            Desvio por ativo (positivo = comprar para atingir o alvo)
          </p>
          <DivergingBars data={bars} valueFormat={compactBRL} height={Math.max(200, bars.length * 26)} />
        </div>
        <DataTable columns={columns} rows={sorted} getRowKey={(r) => r.ticker} />
      </div>
    </Card>
  );
}

// ── rebalance planner ───────────────────────────────────────────────────────

function RebalancePanel() {
  const target = useTarget();
  const plan = useRebalancePlan();
  const [newCash, setNewCash] = useState("");
  const [exclude, setExclude] = useState<string[]>([]);

  const tickers = useMemo(
    () => (target.data ? target.data.drift.map((d) => d.ticker) : []),
    [target.data],
  );

  const toggle = (t: string) =>
    setExclude((xs) => (xs.includes(t) ? xs.filter((x) => x !== t) : [...xs, t]));

  const onPlan = () =>
    plan.mutate({
      new_cash: parseFloat(newCash) || 0,
      exclude: exclude.length ? exclude : undefined,
    });

  return (
    <Card title="Plano de rebalanceamento">
      <div className="space-y-4">
        <div className="flex flex-wrap items-end gap-4">
          <label className="text-sm">
            <span className="mb-1 block text-xs text-muted">Aporte novo (R$)</span>
            <input
              type="number"
              min={0}
              step={100}
              value={newCash}
              onChange={(e) => setNewCash(e.target.value)}
              placeholder="0"
              className="w-40 rounded-md border border-hairline bg-paper px-2 py-1.5 text-right font-mono text-sm tnum text-ink"
            />
          </label>
          <button
            type="button"
            onClick={onPlan}
            disabled={plan.isPending}
            className="rounded-md bg-navy px-4 py-1.5 text-xs font-semibold text-white disabled:opacity-40"
          >
            {plan.isPending ? "Gerando…" : "Gerar plano"}
          </button>
        </div>

        {tickers.length > 0 && (
          <div>
            <p className="mb-1.5 text-xs text-muted">Excluir do plano (mantém a posição atual)</p>
            <div className="flex flex-wrap gap-1.5">
              {tickers.map((t) => {
                const on = exclude.includes(t);
                return (
                  <button
                    key={t}
                    type="button"
                    onClick={() => toggle(t)}
                    className={`rounded-[4px] border px-1.5 py-0.5 font-mono text-[11px] uppercase tracking-wide ${
                      on
                        ? "border-navy bg-navy text-white"
                        : "border-hairline bg-paper text-muted hover:bg-hairline/40"
                    }`}
                  >
                    {t}
                  </button>
                );
              })}
            </div>
          </div>
        )}

        {plan.error && (
          <p className="text-sm text-loss" role="alert">
            {plan.error instanceof ApiError
              ? plan.error.message
              : "Falha ao gerar o plano."}
          </p>
        )}

        {plan.data && <PlanResult plan={plan.data} />}

        <p className="border-t border-hairline pt-3 text-[11px] text-muted">
          Plano informativo — nada é executado. Envie as ordens na sua corretora.
        </p>
      </div>
    </Card>
  );
}

function PlanResult({ plan }: { plan: RebalancePlan }) {
  const tradeCols: Column<TradeLine>[] = [
    { key: "ticker", header: "Ativo", render: (r) => <TickerChip ticker={r.ticker} /> },
    { key: "qty", header: "Qtd", align: "right", render: (r) => r.qty.toLocaleString("pt-BR") },
    { key: "est_price", header: "Preço est.", align: "right", render: (r) => <Money value={r.est_price} /> },
    { key: "est_brl", header: "Valor", align: "right", render: (r) => <Money value={r.est_brl} /> },
  ];

  const { sells, buys } = plan.trades;
  const buckets = Object.entries(plan.tax?.buckets ?? {});
  const isencao = plan.tax?.isencao_limit ?? 20000;

  return (
    <div className="space-y-4">
      <p className="font-mono text-[11px] text-muted">
        {formatDate(plan.as_of)} · patrimônio {compactBRL(plan.nav)}
        {plan.new_cash ? ` · aporte ${compactBRL(plan.new_cash)}` : ""}
      </p>

      {plan.notes?.length > 0 && (
        <div className="space-y-1.5">
          {plan.notes.map((n, i) => (
            <p
              key={i}
              className="rounded-md border border-hairline bg-paper px-3 py-2 text-sm text-ink"
            >
              {n}
            </p>
          ))}
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        <div>
          <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">
            Vender ({sells.length})
          </p>
          {sells.length ? (
            <DataTable columns={tradeCols} rows={sells} getRowKey={(r) => r.ticker} />
          ) : (
            <p className="text-sm text-muted">Nenhuma venda necessária.</p>
          )}
        </div>
        <div>
          <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">
            Comprar ({buys.length})
          </p>
          {buys.length ? (
            <DataTable columns={tradeCols} rows={buys} getRowKey={(r) => r.ticker} />
          ) : (
            <p className="text-sm text-muted">Nenhuma compra necessária.</p>
          )}
        </div>
      </div>

      {buckets.length > 0 && (
        <div>
          <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">
            Imposto por classe
          </p>
          <div className="grid gap-3 sm:grid-cols-2">
            {buckets.map(([cls, b]) => (
              <div key={cls} className="rounded-lg border border-hairline bg-panel p-3">
                <div className="flex items-center justify-between">
                  <span className="font-mono text-sm font-semibold text-ink">{cls}</span>
                  <span className="text-xs text-muted">{b.status}</span>
                </div>
                <dl className="mt-2 space-y-1 text-sm">
                  <Line label="Vendas do mês" value={<Money value={b.gross_after} />} />
                  <Line
                    label={`Isenção (${compactBRL(isencao)})`}
                    value={
                      <span className={b.gross_after > isencao ? "text-loss" : "text-gain"}>
                        {b.gross_after > isencao ? "ultrapassada" : "dentro"}
                      </span>
                    }
                  />
                  <Line label="Imposto estimado" value={<Money value={b.ir} />} />
                  {b.harvest?.length > 0 && (
                    <Line
                      label="Harvest sugerido"
                      value={`${b.harvest.length} ativo(s)`}
                    />
                  )}
                </dl>
                {b.harvest?.length > 0 && (
                  <p className="mt-2 border-t border-hairline pt-2 text-[11px] text-muted">
                    Recompre no próximo pregão — recomprar no mesmo dia configura day trade.
                  </p>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function Line({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <dt className="text-muted">{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}
