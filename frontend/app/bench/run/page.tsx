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
import { LineSeries } from "@/components/charts/LineSeries";
import { AreaSeries } from "@/components/charts/AreaSeries";
import { formatDate, formatPercent } from "@/lib/format";
import { ApiError } from "@/lib/api";
import type { BacktestRun } from "@/lib/types";
import {
  compactBRL,
  curveRows,
  formatMetricPct,
  formatRatio,
  useBacktest,
  useCurves,
} from "../lib";

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
  const done = run.data?.status === "done";
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

      {run.isLoading ? (
        <LoadingSkeleton />
      ) : run.error ? (
        <ErrorCard
          text={
            run.error instanceof ApiError
              ? run.error.message
              : "Falha ao carregar o backtest."
          }
        />
      ) : run.data?.status === "error" ? (
        <ErrorCard text={run.data.error ?? "O backtest falhou sem mensagem."} />
      ) : !done ? (
        <PendingCard status={run.data?.status ?? "pending"} />
      ) : (
        <TearSheet
          run={run.data!}
          curves={curves.data?.series ?? null}
          curvesLoading={curves.isLoading}
        />
      )}
    </>
  );
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
  curves,
  curvesLoading,
}: {
  run: BacktestRun;
  curves: Record<string, (string | number)[]> | null;
  curvesLoading: boolean;
}) {
  const m = (run.metrics ?? {}) as Metrics;
  const pre = m.pretax ?? {};
  const aft = m.aftertax ?? {};

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

      {/* Equity curve — pré vs pós is the whole point, so it's the centerpiece */}
      <Card
        title="Patrimônio: pré vs pós-imposto"
        action={
          <span className="font-mono text-[11px] text-muted">
            o diferencial: quanto o imposto corrói o retorno
          </span>
        }
      >
        {curvesLoading || !curves ? (
          <Skeleton className="h-[300px] w-full" />
        ) : (
          <LineSeries
            data={curveRows(curves, ["pretax", "aftertax"])}
            xKey="date"
            height={320}
            series={[
              { key: "pretax", label: "Pré-imposto", color: "var(--series-1)" },
              { key: "aftertax", label: "Pós-imposto", color: "var(--series-2)" },
            ]}
            valueFormat={compactBRL}
            labelFormat={formatDate}
          />
        )}
      </Card>

      {/* Drawdown + secondary tiles */}
      <div className="grid gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <Card title="Drawdown">
          {curvesLoading || !curves ? (
            <Skeleton className="h-[200px] w-full" />
          ) : (
            <AreaSeries
              data={curveRows(curves, ["drawdown"])}
              xKey="date"
              yKey="drawdown"
              label="Drawdown"
              color="var(--loss)"
              height={220}
              valueFormat={(v) => formatPercent(v)}
              labelFormat={formatDate}
            />
          )}
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

function PendingCard({ status }: { status: string }) {
  return (
    <Card>
      <div className="flex flex-col items-center justify-center gap-3 py-12 text-center">
        <StatusBadge status={status as BacktestRun["status"]} />
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
