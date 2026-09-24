"use client";

import { useState } from "react";
import Link from "next/link";
import { Card } from "@/components/ui/Card";
import { DataTable, type Column } from "@/components/ui/DataTable";
import { Money } from "@/components/ui/Money";
import { Skeleton } from "@/components/ui/Skeleton";
import { EmptyState } from "@/components/ui/EmptyState";
import { IconChart } from "@/components/icons";
import { formatMetricPct, formatRatio } from "@/lib/format";
import type { BacktestRun } from "@/lib/types";
import { CompareChart } from "./CompareChart";
import {
  MAX_COMPARE,
  type Metrics,
  mergeCurvesByDate,
  runLabels,
  seriesColor,
  useCurvesMany,
} from "./lib";

type View = "aftertax" | "pretax";

// One comparison-table row. `pick` reads the after-tax figure the row compares;
// `better` says which direction wins so we can highlight the leader — omit it for
// purely informational rows (tax paid isn't "better" when low simply means the
// strategy earned less, so we don't crown a winner there).
type MetricRow = {
  key: string;
  label: string;
  pick: (m: Metrics) => number | undefined;
  fmt: (v: number) => React.ReactNode;
  better?: "higher" | "lower";
};

const METRICS: MetricRow[] = [
  {
    key: "annpos",
    label: "Retorno anual pós",
    pick: (m) => m.aftertax?.["Ann. Return (%)"],
    fmt: (v) => formatMetricPct(v, true),
    better: "higher",
  },
  {
    key: "sharpe",
    label: "Sharpe",
    pick: (m) => m.aftertax?.Sharpe,
    fmt: formatRatio,
    better: "higher",
  },
  {
    // Max Drawdown (%) is stored negative → closest to zero (highest) is best.
    key: "mdd",
    label: "Max drawdown",
    pick: (m) => m.aftertax?.["Max Drawdown (%)"],
    fmt: (v) => formatMetricPct(v),
    better: "higher",
  },
  {
    key: "calmar",
    label: "Calmar",
    pick: (m) => m.aftertax?.Calmar,
    fmt: formatRatio,
    better: "higher",
  },
  {
    // Informational — no winner (low tax often just means low gains).
    key: "tax",
    label: "Imposto pago",
    pick: (m) => m.total_tax_paid,
    fmt: (v) => <Money value={v} />,
  },
];

const metricsOf = (r: BacktestRun) => (r.metrics ?? {}) as Metrics;

// Winner id for a row, or null when fewer than two runs report the figure
// (nothing to compare). Ties keep the first run — acceptable.
// ponytail: first-wins on ties; highlight all ties only if users ask.
function bestId(m: MetricRow, runs: BacktestRun[]): string | null {
  if (!m.better) return null; // informational row, no winner
  const defined = runs
    .map((r) => ({ id: r.id, v: m.pick(metricsOf(r)) }))
    .filter((x): x is { id: string; v: number } => Number.isFinite(x.v));
  if (defined.length < 2) return null;
  const win = defined.reduce((a, b) =>
    m.better === "higher" ? (b.v > a.v ? b : a) : b.v < a.v ? b : a,
  );
  return win.id;
}

export function ComparePanel({
  runs,
  onClear,
  onRemove,
}: {
  runs: BacktestRun[]; // selected + done, in pick order
  onClear: () => void;
  onRemove: (id: string) => void;
}) {
  const [view, setView] = useState<View>("aftertax");

  const capped = runs.slice(0, MAX_COMPARE);
  const overflow = runs.length - capped.length;
  // Hooks stay unconditional: fetch curves for whatever is capped, then branch.
  // Everything the overlay itself needs — the window, the unit, the rulers —
  // lives in CompareChart: it is state the metrics table below can never follow,
  // since those figures are the engine's over each run's whole period.
  const curveQueries = useCurvesMany(capped.map((r) => r.id));

  if (runs.length < 2) {
    return (
      <Card title="Comparar rodadas" action={<ClearButton onClear={onClear} />}>
        <EmptyState
          icon={<IconChart size={26} />}
          title="Selecione ao menos 2 rodadas concluídas para comparar."
          hint="1 rodada marcada — marque outra na lista acima para sobrepor as curvas."
        />
      </Card>
    );
  }

  const labels = runLabels(capped);
  const colorById = Object.fromEntries(
    capped.map((r, i) => [r.id, seriesColor(i)]),
  );

  const loading = curveQueries.some((q) => q.isLoading);
  const withCurves = capped
    .map((r, i) => ({ run: r, series: curveQueries[i].data?.series }))
    .filter((x): x is { run: BacktestRun; series: Record<string, (string | number)[]> } =>
      Boolean(x.series),
    );
  const failed = capped.filter((_, i) => curveQueries[i].isError);

  const chartData = mergeCurvesByDate(
    withCurves.map((x) => ({ id: x.run.id, series: x.series })),
    view,
  );

  const chartSeries = withCurves.map((x) => ({
    key: x.run.id,
    label: labels[x.run.id],
    color: colorById[x.run.id],
  }));

  const bestByMetric = Object.fromEntries(
    METRICS.map((m) => [m.key, bestId(m, capped)]),
  );

  const columns: Column<MetricRow>[] = [
    {
      key: "metric",
      header: "Métrica",
      align: "left",
      // The card's headline: which metric the run figures under it are for.
      mobile: "title",
      render: (m) => <span className="font-medium text-ink">{m.label}</span>,
    },
    ...capped.map<Column<MetricRow>>((r) => ({
      key: r.id,
      align: "right",
      // The header is a chip with a colour swatch and a remove button; the card
      // layout needs the run's name as plain text to label the line with.
      label: labels[r.id],
      header: (
        <RunHeader
          run={r}
          label={labels[r.id]}
          color={colorById[r.id]}
          onRemove={() => onRemove(r.id)}
        />
      ),
      render: (m) => {
        const v = m.pick(metricsOf(r));
        if (!Number.isFinite(v)) return <span className="text-muted">—</span>;
        const isBest = bestByMetric[m.key] === r.id;
        return (
          <span
            className={
              isBest
                ? // Weight + a ruled chip, not gain green: "melhor" is a ranking,
                  // not a signed value, and green here would misread as a profit.
                  "inline-flex items-center gap-1 rounded border border-hairline bg-hairline/40 px-1.5 py-0.5 font-semibold text-ink"
                : undefined
            }
          >
            {isBest && (
              <>
                <span aria-hidden className="text-[0.7em] text-muted">
                  ●
                </span>
                <span className="sr-only">melhor: </span>
              </>
            )}
            {m.fmt(v as number)}
          </span>
        );
      },
    })),
  ];

  return (
    <Card
      title={`Comparar rodadas · ${capped.length}`}
      action={
        <div className="flex items-center gap-3">
          <Toggle view={view} onChange={setView} />
          <ClearButton onClear={onClear} />
        </div>
      }
    >
      {overflow > 0 && (
        <p className="mb-3 rounded-md border border-hairline bg-paper px-3 py-2 text-xs text-muted">
          Mostrando as primeiras {MAX_COMPARE} de {runs.length} rodadas — o
          gráfico fica ilegível com mais linhas.
        </p>
      )}

      {/* Overlay */}
      <div className="mb-4">
        {loading ? (
          <Skeleton className="h-[320px] w-full" />
        ) : chartSeries.length === 0 ? (
          <p className="py-16 text-center text-sm text-loss">
            Não foi possível carregar as curvas das rodadas selecionadas.
          </p>
        ) : (
          <CompareChart
            rows={chartData}
            runSeries={chartSeries}
            failedLabels={failed.map((r) => labels[r.id])}
          />
        )}
      </div>

      {/* Metrics table (after-tax) */}
      <DataTable columns={columns} rows={METRICS} getRowKey={(m) => m.key} label="Métricas comparadas" />
      {/* Said out loud rather than left to be inferred: a narrowed chart above an
          unchanged table is exactly the kind of mismatch a reader assumes away.
          Unconditional now, because the chart owns the window — and because a
          benchmark drawn up there has no row down here at all. */}
      <p className="mt-2 font-mono text-[11px] text-muted">
        ● melhor por linha · métricas pós-imposto, sobre a rodada inteira
      </p>
    </Card>
  );
}

function RunHeader({
  run,
  label,
  color,
  onRemove,
}: {
  run: BacktestRun;
  label: string;
  color: string;
  onRemove: () => void;
}) {
  return (
    <div className="flex items-center justify-end gap-1.5">
      <span
        aria-hidden
        className="inline-block h-2 w-2 shrink-0 rounded-full"
        style={{ background: color }}
      />
      <Link
        href={`/bench/run?id=${run.id}`}
        title={`${label} · ${run.id.slice(0, 8)} — abrir tear sheet`}
        className="max-w-[10rem] truncate font-semibold text-ink hover:underline"
      >
        {label}
      </Link>
      <button
        type="button"
        onClick={onRemove}
        aria-label={`Remover ${label} da comparação`}
        title="Remover"
        className="leading-none text-muted hover:text-loss"
      >
        ×
      </button>
    </div>
  );
}

function Toggle({
  view,
  onChange,
}: {
  view: View;
  onChange: (v: View) => void;
}) {
  return (
    <div className="inline-flex rounded-md border border-hairline p-0.5 text-xs">
      {(["aftertax", "pretax"] as View[]).map((v) => (
        <button
          key={v}
          type="button"
          onClick={() => onChange(v)}
          aria-pressed={view === v}
          className={`rounded px-2.5 py-1 transition-colors ${
            view === v
              ? "bg-navy font-medium text-white"
              : "text-muted hover:text-ink"
          }`}
        >
          {v === "aftertax" ? "Pós-imposto" : "Pré-imposto"}
        </button>
      ))}
    </div>
  );
}

function ClearButton({ onClear }: { onClear: () => void }) {
  return (
    <button
      type="button"
      onClick={onClear}
      className="rounded-md border border-hairline bg-paper px-2.5 py-1 text-xs font-medium text-ink hover:bg-hairline/40"
    >
      Limpar
    </button>
  );
}
