"use client";

import { useMemo, useState } from "react";
import { PageHeader } from "@/components/ui/PageHeader";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { Money } from "@/components/ui/Money";
import { PctDelta } from "@/components/ui/PctDelta";
import { DataTable, type Column } from "@/components/ui/DataTable";
import { StaleHint } from "@/components/ui/StaleHint";
import { asSet, isStale } from "@/lib/staleResult";
import { LineSeries } from "@/components/charts/LineSeries";
import { isFiltered, useDateRange } from "@/components/charts/DateRange";
import { useChartMode } from "@/components/charts/ChartMode";
import { toTwrRows } from "@/lib/chartMode";
import { IconClock, IconChart } from "@/components/icons";
import { errorText } from "@/lib/api";
import {
  REBALANCE_FREQS,
  compactBRL,
  formatDate,
  formatMetricPct,
  formatRatio,
} from "@/lib/format";
import type { Strategy } from "@/lib/types";
import {
  overlayCurves,
  totalReturn,
  useRunWhatIf,
  useStrategies,
  type WhatIfResult,
} from "./lib";

export default function SimulacaoPage() {
  const strategies = useStrategies();
  const run = useRunWhatIf();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [freq, setFreq] = useState("ME");
  const [q, setQ] = useState("");

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    const all = strategies.data ?? [];
    if (!needle) return all;
    return all.filter(
      (s) =>
        s.name.toLowerCase().includes(needle) ||
        s.description.toLowerCase().includes(needle),
    );
  }, [strategies.data, q]);

  function toggle(name: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  }

  // What "Simular" would send now; the curves on screen are checked against it.
  const request = { strategy_ids: [...selected], freq };
  function simular() {
    if (selected.size === 0) return;
    run.mutate(request);
  }
  const stale = isStale(run.variables, request, (b) => ({
    ...b,
    strategy_ids: asSet(b.strategy_ids),
  }));

  const errMsg =
    run.error ? errorText(run.error, "Falha ao rodar a simulação.") : null;

  return (
    <>
      <PageHeader eyebrow="E se" title="Simulação">
        <span className="font-mono text-[11px] text-muted">
          mesma entrada · outra estratégia · após impostos
        </span>
      </PageHeader>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,380px)_minmax(0,1fr)]">
        {/* ── Controls ─────────────────────────────────────────────────── */}
        <Card title="Estratégias alternativas">
          <p className="mb-3 text-xs leading-snug text-muted">
            Reaplica seus aportes reais (compras − vendas − proventos, nas datas
            verdadeiras) em uma ou mais estratégias, do primeiro trade até hoje.
            Não é erro de rastreamento: é o mesmo dinheiro em outro veículo.
          </p>

          <input
            type="search"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Buscar estratégia…"
            className="w-full rounded-md border border-hairline bg-paper px-3 py-2 text-sm text-ink placeholder:text-muted focus:border-navy focus:outline-none"
          />

          {strategies.isLoading ? (
            <div className="mt-3 space-y-2">
              {Array.from({ length: 6 }).map((_, i) => (
                <Skeleton key={i} className="h-10 w-full" />
              ))}
            </div>
          ) : (
            <ul className="mt-3 max-h-[340px] space-y-1 overflow-y-auto pr-1">
              {filtered.map((s) => (
                <StrategyRow
                  key={s.name}
                  strategy={s}
                  checked={selected.has(s.name)}
                  onToggle={() => toggle(s.name)}
                />
              ))}
            </ul>
          )}

          <div className="mt-4 flex items-end gap-3">
            <label className="block flex-1">
              <span className="mb-1 block text-xs font-medium text-ink">
                Frequência de rebalance
              </span>
              <select
                value={freq}
                onChange={(e) => setFreq(e.target.value)}
                className="w-full rounded-md border border-hairline bg-paper px-2.5 py-2 text-sm text-ink focus:border-navy focus:outline-none"
              >
                {REBALANCE_FREQS.map((f) => (
                  <option key={f.value} value={f.value}>
                    {f.label} ({f.value})
                  </option>
                ))}
              </select>
            </label>
            <button
              type="button"
              onClick={simular}
              disabled={selected.size === 0 || run.isPending}
              className="rounded-md bg-navy px-5 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-navy/90 disabled:opacity-60"
            >
              {run.isPending ? "Simulando…" : "Simular"}
            </button>
          </div>

          <p className="mt-2 font-mono text-[11px] text-muted">
            {selected.size} selecionada{selected.size === 1 ? "" : "s"}
          </p>

          {errMsg && (
            <p className="mt-3 text-sm text-loss" role="alert">
              {errMsg}
            </p>
          )}
        </Card>

        {/* ── Results ──────────────────────────────────────────────────── */}
        <div className="space-y-4">
          {run.isPending ? (
            <PendingCard />
          ) : run.data ? (
            <>
              {stale && <StaleHint action="Simular" />}
              <Results data={run.data} />
            </>
          ) : (
            <EmptyState
              icon={<IconClock size={28} />}
              title="Selecione estratégias e clique em Simular."
              hint="Suas curvas pós-imposto aparecem sobrepostas, com a comparação lado a lado."
            />
          )}
        </div>
      </div>
    </>
  );
}

function StrategyRow({
  strategy,
  checked,
  onToggle,
}: {
  strategy: Strategy;
  checked: boolean;
  onToggle: () => void;
}) {
  return (
    <li>
      <label
        className={`flex cursor-pointer items-start gap-2.5 rounded-md border px-3 py-2 transition-colors ${
          checked
            ? "border-navy bg-navy/[0.05]"
            : "border-hairline bg-panel hover:border-navy/40"
        }`}
      >
        <input
          type="checkbox"
          checked={checked}
          onChange={onToggle}
          className="mt-0.5 h-4 w-4 shrink-0 accent-navy"
        />
        <span className="min-w-0">
          <span className="flex items-center gap-2">
            <span className="text-sm font-semibold text-ink">{strategy.name}</span>
            {strategy.needs_fundamentals && (
              <span className="font-mono text-[10px] uppercase tracking-wide text-muted">
                fundamentos
              </span>
            )}
          </span>
          <span className="mt-0.5 line-clamp-1 block text-xs text-muted">
            {strategy.description}
          </span>
        </span>
      </label>
    </li>
  );
}

// ── Result rendering ──────────────────────────────────────────────────────────

interface TableRow {
  label: string;
  finalValue: number;
  ret: number;
  annReturn: number | null;
  sharpe: number | null;
  maxDd: number | null;
  real: boolean;
}

function Results({ data }: { data: WhatIfResult }) {
  const { rows: curveRows, series, flows } = useMemo(
    () => overlayCurves(data.scenarios),
    [data.scenarios],
  );
  // The chart's window only. The "Comparação" table below is the engine's
  // figures over the whole replay and does not follow it — said so in its note.
  const period = useDateRange({ min: curveRows[0]?.date, max: data.as_of });
  // Same aportes everywhere, so the question is "which veículo did more with the
  // same money": a percentage, sliced then rebased (0% = left edge on screen), and
  // time-weighted — value/first − 1 called R$10k plus R$90k of aportes "+900%".
  const unit = useChartMode({
    format: compactBRL, of: "patrimônio", toReturn: (rows) => toTwrRows(rows, flows),
  });
  const windowed = unit.apply(period.slice(curveRows));

  const tableRows: TableRow[] = [
    {
      label: "Carteira real",
      finalValue: data.actual_value,
      ret: totalReturn(data.actual_value, data.total_invested),
      annReturn: null,
      sharpe: null,
      maxDd: null,
      real: true,
    },
    ...data.scenarios.map((sc) => ({
      label: sc.strategy,
      finalValue: sc.final_value,
      ret: totalReturn(sc.final_value, data.total_invested),
      annReturn: sc.ann_return,
      sharpe: sc.sharpe,
      maxDd: sc.max_dd,
      real: false,
    })),
  ];

  const columns: Column<TableRow>[] = [
    {
      key: "label",
      header: "Cenário",
      mobile: "title",
      render: (r) => (
        <span className={r.real ? "font-semibold text-ink" : "text-ink"}>
          {r.label}
        </span>
      ),
    },
    {
      key: "finalValue",
      header: "Valor final",
      align: "right",
      render: (r) => <Money value={r.finalValue} />,
    },
    {
      key: "ret",
      header: "Retorno",
      align: "right",
      render: (r) => <PctDelta value={r.ret} />,
    },
    {
      key: "annReturn",
      header: "Retorno a.a.",
      align: "right",
      render: (r) => formatMetricPct(r.annReturn, true),
    },
    {
      key: "sharpe",
      header: "Sharpe",
      align: "right",
      render: (r) => formatRatio(r.sharpe),
    },
    {
      key: "maxDd",
      header: "Máx. queda",
      align: "right",
      render: (r) => formatMetricPct(r.maxDd),
    },
  ];

  return (
    <>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        <StatTile label="Aportado" value={<Money value={data.total_invested} />} />
        <StatTile label="Valor atual real" value={<Money value={data.actual_value} />} />
        <StatTile
          label="Estratégias"
          value={<span className="font-display">{data.scenarios.length}</span>}
          sub={`em ${formatDate(data.as_of)}`}
        />
      </div>

      <Card
        title="Patrimônio pós-imposto por estratégia"
        action={
          <div className="flex flex-wrap items-center gap-3">
            <span className="font-mono text-[11px] text-muted">
              mesmos aportes, veículos diferentes
            </span>
            {unit.control}
            {period.control}
          </div>
        }
      >
        {curveRows.length < 2 ? (
          <EmptyState
            icon={<IconChart size={26} />}
            title="Sem curva suficiente para exibir."
            hint="A janela de preços não cobre os aportes desta estratégia."
          />
        ) : (
          <LineSeries
            data={windowed}
            xKey="date"
            series={series}
            height={320}
            valueFormat={unit.valueFormat}
            labelFormat={formatDate}
          />
        )}
      </Card>

      <Card title="Comparação">
        <DataTable columns={columns} rows={tableRows} getRowKey={(r) => r.label} label="Resultado da simulação" />
        <p className="mt-3 text-xs leading-snug text-muted">
          &quot;Retorno&quot; é o valor final sobre o total aportado (não anualizado).
          No gráfico em %, o retorno é ponderado pelo tempo: aporte não conta como
          ganho. Comparação após impostos; a carteira real usa o valor de mercado das
          posições atuais. Aproximação: cada aporte é aplicado no rebalance
          seguinte.
          {isFiltered(period.range) &&
            " Estes números são da simulação inteira, não do período escolhido no gráfico acima."}
        </p>
      </Card>
    </>
  );
}

function StatTile({
  label,
  value,
  sub,
}: {
  label: string;
  value: React.ReactNode;
  sub?: string;
}) {
  return (
    <div className="rounded-lg border border-hairline bg-panel p-4">
      <p className="font-mono text-[10px] font-medium uppercase tracking-[0.12em] text-muted">
        {label}
      </p>
      <p className="mt-1.5 text-2xl font-extrabold leading-none tracking-tight text-ink">
        {value}
      </p>
      {sub && <p className="mt-1.5 font-mono text-[11px] text-muted">{sub}</p>}
    </div>
  );
}

function PendingCard() {
  return (
    <Card>
      <div className="flex flex-col items-center justify-center gap-3 py-12 text-center">
        <p className="text-sm text-muted">
          Reaplicando seus aportes na(s) estratégia(s) — isso roda o motor de
          backtest e pode levar alguns segundos.
        </p>
        <div className="grid w-full max-w-md grid-cols-3 gap-2">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-16" />
          ))}
        </div>
        <Skeleton className="h-[240px] w-full" />
      </div>
    </Card>
  );
}
