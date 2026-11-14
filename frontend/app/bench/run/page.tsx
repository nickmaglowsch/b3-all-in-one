"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { PageHeader } from "@/components/ui/PageHeader";
import { Card } from "@/components/ui/Card";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { Skeleton } from "@/components/ui/Skeleton";
import { Money } from "@/components/ui/Money";
import { PctDelta } from "@/components/ui/PctDelta";
import { AreaSeries } from "@/components/charts/AreaSeries";
import { isFiltered, useDateRange } from "@/components/charts/DateRange";
import {
  compactBRL,
  formatDate,
  formatMetricPct,
  formatPercent,
  formatRatio,
} from "@/lib/format";
import type { UseQueryResult } from "@tanstack/react-query";
import { EmptyState, LoadFailure, RetryButton } from "@/components/ui/EmptyState";
import type { BacktestCurves, BacktestRun } from "@/lib/types";
import { isDone, type BacktestStatus } from "@/lib/jobStatus";
import { CurveChart } from "../CurveChart";
import { curveRows, useBacktest, useCurves } from "../lib";

// The run id rides in ?id= rather than a path segment: a [id] route needs
// generateStaticParams, and run ids only exist at runtime — `output: "export"`
// can't prerender them. useSearchParams needs the Suspense boundary to build.
export default function TearSheetPage() {
  return (
    <Suspense fallback={<LoadingSkeleton />}>
      <TearSheetView />
    </Suspense>
  );
}

function TearSheetView() {
  const id = useSearchParams().get("id") ?? "";
  const run = useBacktest(id);
  const done = isDone(run.data?.status);
  const curves = useCurves(id, done);

  return (
    <>
      <PageHeader eyebrow="Tear sheet" title={run.data?.strategy ?? "Backtest"}>
        {run.data && <StatusBadge status={run.data.status} />}
        <Link
          href="/bench"
          className="rounded-md border border-hairline bg-paper px-3 py-1.5 text-xs font-medium text-ink hover:bg-hairline/40"
        >
          ← Bench
        </Link>
      </PageHeader>

      {id ? (
        <RunBody run={run} curves={curves} />
      ) : (
        <EmptyState
          title="Nenhuma rodada indicada."
          hint="Abra um backtest pela lista de rodadas do bench."
          action={
            <Link
              href="/bench"
              className="rounded-md border border-hairline px-3 py-1.5 text-sm font-medium text-ink transition-colors hover:bg-hairline/40"
            >
              Ir para o bench
            </Link>
          }
        />
      )}
    </>
  );
}

function RunBody({
  run,
  curves,
}: {
  run: UseQueryResult<BacktestRun>;
  curves: UseQueryResult<BacktestCurves>;
}) {
  // The cached run first: React Query keeps `data` when a *refetch* fails, and
  // one failed poll of a running backtest is no reason to swap its progress (or
  // its numbers) for an alarm.
  const data = run.data;
  if (!data) {
    // Not ErrorCard: a request that failed (a 402, a swept run) is not a run that
    // failed, and "Backtest falhou" would say it was.
    if (!run.error) return <LoadingSkeleton />;
    return (
      <LoadFailure
        error={run.error}
        title="Não foi possível carregar este backtest."
        offerTitle={(nome) => `Backtests vêm com o módulo ${nome}.`}
        onRetry={() => run.refetch()}
      />
    );
  }
  if (data.status === "error") {
    return <ErrorCard text={data.error ?? "O backtest falhou sem mensagem."} />;
  }
  if (!isDone(data.status)) return <PendingCard status={data.status} />;
  return <TearSheet run={data} curves={curves} />;
}

/**
 * A chart's slot: the chart once its curves are here, a skeleton while they
 * load, and — when they will not come (the artifact was swept, or lost on a
 * redeploy) — a sentence saying so, instead of a skeleton pulsing forever.
 */
function CurvesSlot({
  curves,
  height,
  render,
}: {
  curves: UseQueryResult<BacktestCurves>;
  height: string;
  render: (series: BacktestCurves["series"]) => React.ReactNode;
}) {
  if (curves.data) return render(curves.data.series);
  if (curves.isError) {
    return (
      <div
        role="alert"
        className={`flex ${height} flex-col items-center justify-center gap-3 rounded-md border border-status-error/40 px-4 text-center`}
      >
        <p className="text-sm text-muted">
          As curvas desta rodada não estão disponíveis — os números acima
          continuam valendo. Rode o backtest de novo para redesenhar os gráficos.
        </p>
        <RetryButton onClick={() => curves.refetch()} />
      </div>
    );
  }
  return <Skeleton className={`${height} w-full`} />;
}

// ── numbers ───────────────────────────────────────────────────────────────────

type Metrics = {
  pretax?: Record<string, number>;
  aftertax?: Record<string, number>;
  total_tax_paid?: number;
  final_aftertax_nav?: number;
  final_pretax_nav?: number;
  initial_capital?: number;
  start?: string;
  end?: string;
  freq?: string;
  periods?: number;
};

function TearSheet({
  run,
  curves: curvesQuery,
}: {
  run: BacktestRun;
  curves: UseQueryResult<BacktestCurves>;
}) {
  const curves = curvesQuery.data?.series ?? null;
  const m = (run.metrics ?? {}) as Metrics;
  const pre = m.pretax ?? {};
  const aft = m.aftertax ?? {};

  // Per chart, not per page: the tiles above are the engine's figures for the
  // whole run and cannot follow a window (Sharpe and Calmar are not recoverable
  // from an equity curve alone). A control sitting on the card it filters says
  // what it covers; one at the top of the page would claim the tiles too.
  const equity = useDateRange({ min: m.start, max: m.end, of: "patrimônio" });
  const drawdown = useDateRange({ min: m.start, max: m.end, of: "drawdown" });
  const windowed = isFiltered(equity.range) || isFiltered(drawdown.range);

  const preAnn = pre["Ann. Return (%)"] ?? 0;
  const aftAnn = aft["Ann. Return (%)"] ?? 0;
  const taxDrag = (aftAnn - preAnn) / 100; // fraction, for PctDelta (pós vs pré)

  const avgTurnover =
    curves?.turnover && curves.turnover.length
      ? (curves.turnover as number[]).reduce((a, b) => a + b, 0) /
        curves.turnover.length
      : null;

  return (
    <div className="space-y-4">
      {/* Run meta */}
      <p className="font-mono text-[11px] text-muted">
        {m.start && m.end ? `${formatDate(m.start)} → ${formatDate(m.end)}` : "—"}
        {m.freq ? ` · ${m.freq}` : ""}
        {m.periods != null ? ` · ${m.periods} períodos` : ""}
        {m.initial_capital != null ? ` · capital ${compactBRL(m.initial_capital)}` : ""}
      </p>

      {/* Primary metric tiles */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        <MetricTile
          label="Retorno anual pré"
          value={formatMetricPct(preAnn, true)}
          tone={preAnn >= 0 ? "gain" : "loss"}
        />
        <MetricTile
          label="Retorno anual pós"
          value={formatMetricPct(aftAnn, true)}
          tone={aftAnn >= 0 ? "gain" : "loss"}
          highlight
          sub={
            <span className="inline-flex items-center gap-1 text-[11px]">
              <PctDelta value={taxDrag} /> <span className="text-muted">vs pré</span>
            </span>
          }
        />
        <MetricTile label="Sharpe" value={formatRatio(pre.Sharpe ?? 0)} sub={
          <span className="text-[11px] text-muted">pós {formatRatio(aft.Sharpe ?? 0)}</span>
        } />
        <MetricTile
          label="Max drawdown"
          value={formatMetricPct(pre["Max Drawdown (%)"] ?? 0)}
          tone="loss"
        />
        <MetricTile label="Calmar" value={formatRatio(pre.Calmar ?? 0)} sub={
          <span className="text-[11px] text-muted">pós {formatRatio(aft.Calmar ?? 0)}</span>
        } />
      </div>

      {/* The tiles are the engine's figures for the whole run and cannot follow
          a window — Sharpe and Calmar are not recoverable from an equity curve
          alone. Narrow the drawdown chart to a calm stretch and the -40% tile
          sits inches above a chart bottoming out at -12%; placing the control on
          the card it filters is not the same as saying so. */}
      {windowed && (
        <p className="font-mono text-[11px] text-muted">
          os números acima são da rodada inteira, não do período escolhido nos
          gráficos
        </p>
      )}

      {/* Equity curve — pré vs pós is the whole point, so it's the centerpiece.
          Reais answer "how much would I have"; the percent reading answers "how
          did this do", which is the only one that survives a different starting
          capital — and the only axis on which a ruler like the CDI can be drawn
          beside the run at all. Both live in CurveChart, shared with the
          comparison overlay. */}
      <Card
        title="Patrimônio: pré vs pós-imposto"
        action={
          <span className="font-mono text-[11px] text-muted">
            o diferencial: quanto o imposto corrói o retorno
          </span>
        }
      >
        <CurvesSlot
          curves={curvesQuery}
          height="h-[300px]"
          render={(series) => (
            <CurveChart
              rows={curveRows(series, ["pretax", "aftertax"])}
              period={equity}
              of="patrimônio"
              series={[
                { key: "pretax", label: "Pré-imposto", color: "var(--series-1)" },
                { key: "aftertax", label: "Pós-imposto", color: "var(--series-2)" },
              ]}
            />
          )}
        />
      </Card>

      {/* Drawdown + secondary tiles */}
      <div className="grid gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <Card title="Drawdown" action={drawdown.control}>
          <CurvesSlot
            curves={curvesQuery}
            height="h-[200px]"
            render={(series) => (
              <AreaSeries
                data={drawdown.slice(curveRows(series, ["drawdown"]))}
                xKey="date"
                yKey="drawdown"
                label="Drawdown"
                color="var(--loss)"
                height={220}
                valueFormat={(v) => formatPercent(v)}
                labelFormat={formatDate}
              />
            )}
          />
        </Card>

        <div className="grid content-start gap-3">
          <MetricTile
            label="Imposto pago"
            value={<Money value={m.total_tax_paid ?? 0} />}
          />
          <MetricTile
            label="Giro médio / período"
            value={avgTurnover == null ? "—" : formatPercent(avgTurnover)}
          />
          <MetricTile
            label="Patrimônio final pós"
            value={<Money value={m.final_aftertax_nav ?? 0} />}
            sub={
              <span className="text-[11px] text-muted">
                pré {compactBRL(m.final_pretax_nav ?? 0)}
              </span>
            }
          />
        </div>
      </div>
    </div>
  );
}

// ── tiles + states ──────────────────────────────────────────────────────────

function MetricTile({
  label,
  value,
  sub,
  tone = "ink",
  highlight = false,
}: {
  label: string;
  value: React.ReactNode;
  sub?: React.ReactNode;
  tone?: "ink" | "gain" | "loss";
  highlight?: boolean;
}) {
  const color =
    tone === "gain" ? "text-gain" : tone === "loss" ? "text-loss" : "text-ink";
  return (
    <div
      className={`rounded-lg border p-4 ${
        highlight ? "border-navy/40 bg-navy/[0.03]" : "border-hairline bg-panel"
      }`}
    >
      <p className="font-mono text-[11px] font-medium uppercase tracking-[0.14em] text-muted">
        {label}
      </p>
      {/* Mono + tnum: these tiles stack into a column on narrow viewports, and a
          headline figure that shifts width between runs is a broken readout. */}
      <p className={`mt-1.5 font-mono text-2xl font-semibold leading-none tnum tabular-nums ${color}`}>
        {value}
      </p>
      {sub && <div className="mt-1.5">{sub}</div>}
    </div>
  );
}

function ErrorCard({ text }: { text: string }) {
  return (
    <Card title="Backtest falhou">
      <p className="mb-2 text-sm text-loss">A rodada não pôde ser concluída.</p>
      <pre className="max-h-80 overflow-auto whitespace-pre-wrap rounded-md border border-hairline bg-paper p-3 font-mono text-[11px] leading-relaxed text-ink">
        {text}
      </pre>
    </Card>
  );
}

function PendingCard({ status }: { status: BacktestStatus }) {
  return (
    <Card>
      <div className="flex flex-col items-center justify-center gap-3 py-12 text-center">
        <StatusBadge status={status} />
        <p className="text-sm text-muted">
          Rodando a simulação — as métricas e curvas aparecem ao concluir.
        </p>
        <div className="grid w-full max-w-md grid-cols-3 gap-2">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-16" />
          ))}
        </div>
      </div>
    </Card>
  );
}

function LoadingSkeleton() {
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        {Array.from({ length: 5 }).map((_, i) => (
          <Skeleton key={i} className="h-24" />
        ))}
      </div>
      <Skeleton className="h-[300px] w-full" />
    </div>
  );
}
