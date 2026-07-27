"use client";

// Bench-local data hooks + formatters. Study note: metrics %-fields (e.g.
// "Ann. Return (%)": 8.9) are already percent *numbers*, not fractions —
// formatPct here appends "%" directly; the chart drawdown series IS a fraction
// so it uses lib/format's formatPercent instead.
import {
  useMutation,
  useQueries,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { Dated } from "@/lib/chartRange";
import type { BacktestCurves, BacktestRun, Strategy } from "@/lib/types";

export function useStrategies() {
  return useQuery({
    queryKey: ["strategies"],
    queryFn: () => api<Strategy[]>("/strategies"),
    staleTime: 60 * 60_000, // gallery only changes on image rebuild
  });
}

const busy = (s: string) => s === "pending" || s === "running";

export function useBacktests() {
  return useQuery({
    queryKey: ["backtests"],
    queryFn: () => api<BacktestRun[]>("/backtests"),
    // Poll while any run is in flight, otherwise rest.
    refetchInterval: (q) =>
      (q.state.data ?? []).some((r) => busy(r.status)) ? 2500 : false,
  });
}

export function useBacktest(id: string) {
  return useQuery({
    queryKey: ["backtest", id],
    queryFn: () => api<BacktestRun>(`/backtests/${id}`),
    refetchInterval: (q) => (q.state.data && busy(q.state.data.status) ? 2000 : false),
  });
}

export function useCurves(id: string, enabled: boolean) {
  return useQuery({
    queryKey: ["curves", id],
    queryFn: () => api<BacktestCurves>(`/backtests/${id}/curves`),
    enabled,
    staleTime: 60 * 60_000, // a finished run's curves never change
  });
}

// Parallel curve fetch for the comparison overlay. Shares the ["curves", id]
// cache with useCurves, so opening a tear sheet after comparing is a cache hit.
export function useCurvesMany(ids: string[]) {
  return useQueries({
    queries: ids.map((id) => ({
      queryKey: ["curves", id],
      queryFn: () => api<BacktestCurves>(`/backtests/${id}/curves`),
      staleTime: 60 * 60_000,
    })),
  });
}

export interface BacktestBody {
  strategy: string;
  params?: Record<string, unknown>;
  start?: string;
  end?: string;
  freq?: string;
}

export function useCreateBacktest() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: BacktestBody) =>
      api<BacktestRun>("/backtests", { method: "POST", body: JSON.stringify(body) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["backtests"] }),
  });
}

// ── formatters ────────────────────────────────────────────────────────────────

export const kindLabel = (k: Strategy["kind"]) => (k === "spec" ? "espec" : "código");

// Metric %-number (8.9 → "8,9%"). Signed prefixes an explicit +.
export function formatMetricPct(n: number, signed = false): string {
  const s = n.toLocaleString("pt-BR", { minimumFractionDigits: 1, maximumFractionDigits: 2 });
  return `${signed && n > 0 ? "+" : ""}${s}%`;
}

export const formatRatio = (n: number) =>
  n.toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

// Compact BRL for chart axes / bar labels (NAV runs into 7 figures).
export function compactBRL(v: number): string {
  const a = Math.abs(v);
  const sign = v < 0 ? "-" : "";
  if (a >= 1e6) return `${sign}R$ ${(a / 1e6).toLocaleString("pt-BR", { maximumFractionDigits: 1 })} mi`;
  if (a >= 1e3) return `${sign}R$ ${(a / 1e3).toLocaleString("pt-BR", { maximumFractionDigits: 0 })} mil`;
  return `${sign}R$ ${a.toLocaleString("pt-BR", { maximumFractionDigits: 0 })}`;
}

// Column-oriented curves → row-oriented for recharts, keeping only wanted keys.
// `date` is narrowed to string (the engine emits ISO dates) so the rows satisfy
// the charts' `Dated` shape and can be windowed by lib/chartRange.
export function curveRows(
  series: Record<string, (string | number)[]>,
  keys: string[],
): (Dated & Record<string, string | number>)[] {
  const n = series.date?.length ?? 0;
  const rows: (Dated & Record<string, string | number>)[] = [];
  for (let i = 0; i < n; i++) {
    const row = { date: String(series.date[i]) } as Dated &
      Record<string, string | number>;
    for (const k of keys) if (series[k]) row[k] = series[k][i];
    rows.push(row);
  }
  return rows;
}

// ── multi-run comparison ──────────────────────────────────────────────────────

// Max series in one overlay; more than this and the chart is unreadable + the
// palette (8 colours) starts repeating.
export const MAX_COMPARE = 6;

// Metric shape the tear sheet + comparison both read out of run.metrics.
export type Metrics = {
  pretax?: Record<string, number>;
  aftertax?: Record<string, number>;
  total_tax_paid?: number;
};

// Palette colour for the Nth overlaid run — kept identical between the chart
// line and the table swatch so a colour maps to exactly one run everywhere.
export const seriesColor = (i: number) => `var(--series-${(i % 8) + 1})`;

// Same-strategy runs need disambiguating; append a short id only when the
// strategy name repeats in the selection, otherwise keep it clean.
export function runLabels(runs: BacktestRun[]): Record<string, string> {
  const counts = new Map<string, number>();
  for (const r of runs) counts.set(r.strategy, (counts.get(r.strategy) ?? 0) + 1);
  const out: Record<string, string> = {};
  for (const r of runs) {
    out[r.id] =
      (counts.get(r.strategy) ?? 0) > 1
        ? `${r.strategy} · ${r.id.slice(0, 6)}`
        : r.strategy;
  }
  return out;
}

// Align N runs' curves on the date axis: one row per date present in ANY run,
// one column per run id. Recharts breaks a line at missing dates — fine, runs on
// the same period/freq share a grid; differing grids just render honest gaps.
export function mergeCurvesByDate(
  inputs: { id: string; series: Record<string, (string | number)[]> }[],
  key: string,
): (Dated & Record<string, string | number>)[] {
  const byDate = new Map<string, Dated & Record<string, string | number>>();
  for (const { id, series } of inputs) {
    const dates = series.date ?? [];
    const vals = series[key] ?? [];
    for (let i = 0; i < dates.length; i++) {
      const d = String(dates[i]);
      let row = byDate.get(d);
      if (!row) {
        row = { date: d };
        byDate.set(d, row);
      }
      if (vals[i] != null) row[id] = vals[i];
    }
  }
  // ISO dates sort lexicographically.
  return [...byDate.values()].sort((a, b) => (a.date < b.date ? -1 : 1));
}
