"use client";

import { useState } from "react";
import { LineSeries, type Series } from "@/components/charts/LineSeries";
import type { useDateRange } from "@/components/charts/DateRange";
import { useChartMode } from "@/components/charts/ChartMode";
import { columnReturn } from "@/lib/chartMode";
import { withBenchmarks } from "@/lib/benchmarks";
import type { Dated } from "@/lib/chartRange";
import { compactBRL, formatDate, formatPercent } from "@/lib/format";
import { BenchmarkPicker } from "./BenchmarkPicker";
import { useBenchmarkOverlay } from "./benchmarkOverlay";

export type Row = Dated & Record<string, string | number>;

/**
 * Every money curve the bench draws: the tear sheet's pré/pós pair and the
 * comparison overlay are the same chart asked two questions, and both wanted the
 * same three controls — the window, the unit, and what to measure against.
 *
 * The window is the caller's: on the tear sheet it is shared with the drawdown
 * chart and with a caveat about the metric tiles, neither of which this component
 * can see. Everything else is local, because it is local — a benchmark picked
 * here changes no figure anywhere else on the page.
 */
export function CurveChart({
  rows,
  series: runSeries,
  period,
  of,
  height = 320,
  children,
}: {
  /** One row per date, one column per line. Money, in reais. */
  rows: Row[];
  series: Series[];
  /** The date window, owned by the caller — see above. */
  period: ReturnType<typeof useDateRange>;
  /** Names this chart in the controls' accessible names. */
  of: string;
  height?: number;
  /** Notes rendered under the chart, below the per-line returns. */
  children?: React.ReactNode;
}) {
  const [picked, setPicked] = useState<string[]>([]);
  const unit = useChartMode({ format: compactBRL, of });
  const bench = useBenchmarkOverlay(rows, picked);

  const windowed = period.slice(rows);
  // The money the chart starts from — what a ruler is scaled to, so "IBOV" reads
  // as the same capital in IBOV instead. In percent mode every column rebases to
  // 0% and the anchor cancels, so the two readings agree.
  const overlaid = withBenchmarks(windowed, bench.levels, firstValue(windowed, runSeries));
  const lines = [...runSeries, ...bench.series];

  const toggle = (key: string) =>
    setPicked((prev) =>
      prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key],
    );

  return (
    <>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <span className="font-mono text-[11px] uppercase tracking-[0.14em] text-muted">
          Período do gráfico
        </span>
        <div className="flex flex-wrap items-center gap-3">
          {unit.control}
          {period.control}
        </div>
      </div>

      <div className="mb-3">
        <BenchmarkPicker
          catalog={bench.catalog}
          selected={picked}
          colorOf={bench.colorOf}
          unavailable={bench.unavailable}
          onToggle={toggle}
        />
      </div>

      <LineSeries
        // Sliced, then overlaid, then rebased — in that order. The percent
        // reading promises 0% at the left edge of what is on screen, and a ruler
        // scaled to a window it is not being drawn over would start elsewhere.
        data={unit.apply(overlaid)}
        xKey="date"
        height={height}
        series={lines}
        valueFormat={unit.valueFormat}
        labelFormat={formatDate}
      />

      <WindowReturns rows={overlaid} series={lines} />
      {children}
    </>
  );
}

/**
 * Total return of every line over the window on screen.
 *
 * The chart shows the shape and the tiles (or the comparison table) show the
 * engine's figures for whole runs; neither answers "what did each of these do
 * over the stretch I am looking at", and a benchmark has no tile and no row
 * anywhere else to say it in. Computed off the money rows, before the percent
 * rebase, so the figure is the same in both readings.
 */
function WindowReturns({ rows, series }: { rows: Row[]; series: Series[] }) {
  const shown = series.flatMap((s) => {
    const ret = columnReturn(rows, s.key);
    // A line with no rows in the window has no return to report — and no chip,
    // rather than a "0%" that would read as "it went nowhere".
    return ret === null ? [] : [{ label: s.label, color: s.color, ret }];
  });
  if (shown.length === 0) return null;
  return (
    <ul className="mt-3 flex flex-wrap gap-x-4 gap-y-1 font-mono text-[11px]">
      {shown.map((x) => (
        <li key={x.label} className="flex items-center gap-1.5 text-muted">
          <span aria-hidden style={{ color: x.color }}>
            ●
          </span>
          {x.label}
          <span className={x.ret >= 0 ? "text-gain" : "text-loss"}>
            {formatPercent(x.ret)}
          </span>
        </li>
      ))}
    </ul>
  );
}

/** The first value any run has in the window — the chart's own starting money. */
function firstValue(rows: Row[], series: Series[]): number {
  for (const row of rows) {
    for (const s of series) {
      const v = row[s.key];
      if (typeof v === "number" && Number.isFinite(v)) return v;
    }
  }
  return 0;
}
