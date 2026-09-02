"use client";

import type { Series } from "@/components/charts/LineSeries";
import { isFiltered, useDateRange } from "@/components/charts/DateRange";
import { CurveChart, type Row } from "./CurveChart";

/**
 * The overlay half of the comparison.
 *
 * It owns the date window because that is the one control the card below has to
 * answer for: the metrics table is the engine's figures over each run's whole
 * period and cannot follow a narrowed chart, so the mismatch has to be said out
 * loud where the narrowing happened. Everything else — the unit, the rulers —
 * is CurveChart's, shared with the tear sheet.
 */
export function CompareChart({
  rows,
  runSeries,
  failedLabels,
}: {
  /** One row per date, one column per selected run. */
  rows: Row[];
  runSeries: Series[];
  /** Runs whose curves could not be fetched — named under the chart. */
  failedLabels: string[];
}) {
  const period = useDateRange({ of: "comparação" });
  return (
    <CurveChart rows={rows} series={runSeries} period={period} of="comparação" height={340}>
      {failedLabels.length > 0 && (
        <p className="mt-2 text-xs text-muted">
          Sem curva para {failedLabels.join(", ")} — as métricas seguem na tabela.
        </p>
      )}
      {isFiltered(period.range) && (
        <p className="mt-2 text-xs text-muted">
          A tabela abaixo é da rodada inteira, não deste período.
        </p>
      )}
    </CurveChart>
  );
}
