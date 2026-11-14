"use client";

// Bench-local data hooks + shaping. Study note: metrics %-fields (e.g.
// "Ann. Return (%)": 8.9) are already percent *numbers*, not fractions — they go
// through lib/format's formatMetricPct; the chart drawdown series IS a fraction
// so it uses formatPercent instead.
import {
  useMutation,
  useQueries,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { api } from "@/lib/api";
import { isBusy } from "@/lib/jobStatus";
import { withQuery } from "@/lib/url";
import type { Dated } from "@/lib/chartRange";
import type { Levels, Proxied } from "@/lib/benchmarks";
import type { BacktestCurves, BacktestRun, Strategy } from "@/lib/types";

export function useStrategies() {
  return useQuery({
    queryKey: ["strategies"],
    queryFn: () => api<Strategy[]>("/strategies"),
    staleTime: 60 * 60_000, // gallery only changes on image rebuild
  });
}

export function useBacktests() {
  return useQuery({
    queryKey: ["backtests"],
    // On load, so a 402 renders the inline offer instead of a modal.
    queryFn: () => api<BacktestRun[]>("/backtests", {}, { paywallDialog: false }),
    // Poll while any run is in flight, otherwise rest.
    refetchInterval: (q) =>
      (q.state.data ?? []).some((r) => isBusy(r.status)) ? 2500 : false,
  });
}

export function useBacktest(id: string) {
  return useQuery({
    queryKey: ["backtest", id],
    queryFn: () =>
      api<BacktestRun>(`/backtests/${encodeURIComponent(id)}`, {}, { paywallDialog: false }),
    // No id, no request: "/backtests/" is the *list*, which answers 200 with an
    // array — a run with no status, rendered as "Rodando a simulação…" forever.
    enabled: id !== "",
    refetchInterval: (q) => (isBusy(q.state.data?.status) ? 2000 : false),
  });
}

export function useCurves(id: string, enabled: boolean) {
  return useQuery({
    queryKey: ["curves", id],
    queryFn: () =>
      api<BacktestCurves>(
        `/backtests/${encodeURIComponent(id)}/curves`,
        {},
        { paywallDialog: false },
      ),
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
      queryFn: () =>
        api<BacktestCurves>(`/backtests/${id}/curves`, {}, { paywallDialog: false }),
      staleTime: 60 * 60_000,
    })),
  });
}

// ── benchmarks ────────────────────────────────────────────────────────────────

/** One ruler the bench can draw a run against. `key` is the engine's own name
 *  (scripts/bench_series.BENCHMARKS) and travels unchanged through the API. */
export interface Benchmark {
  key: string;
  label: string;
  note: string;
}

export interface BenchmarkSeries {
  start: string;
  end: string;
  series: Record<string, Levels>;
  /** Requested but not drawable — the plane has nothing for this window, or the
   *  engine could not be reached. Named rather than dropped: a line that
   *  silently never appears reads as one nobody selected. */
  unavailable: string[];
  /** Lines drawn (in part) off their market's proxy on the plane. */
  proxied?: Record<string, Proxied>;
}

export function useBenchmarks() {
  return useQuery({
    queryKey: ["benchmarks"],
    queryFn: () => api<Benchmark[]>("/benchmarks", {}, { paywallDialog: false }),
    staleTime: 60 * 60_000, // a catalogue in code, not data
  });
}

/**
 * Levels for `keys` over [start, end]. Idle until something is selected and the
 * window is known — the runs' own curves decide it, and asking before they land
 * would fetch one window and draw another.
 */
export function useBenchmarkSeries(keys: string[], start?: string, end?: string) {
  const sorted = [...keys].sort();
  return useQuery({
    // Sorted, because the selection is a set but the key is a string: picking
    // CDI then IBOV and IBOV then CDI are one question and must be one cache
    // entry (the API answers in the catalogue's order either way).
    queryKey: ["benchmark-series", sorted.join(","), start, end],
    queryFn: () =>
      api<BenchmarkSeries>(
        withQuery("/benchmarks/series", { start, end, keys: sorted.join(",") }),
      ),
    enabled: sorted.length > 0 && Boolean(start && end),
    staleTime: 60 * 60_000, // the plane moves once a day
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

export function useDeleteBacktest() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api<void>(`/backtests/${id}`, { method: "DELETE" }),
    onSuccess: (_void, id) => {
      // Evicted, not invalidated: there is nothing to refetch for a run that no
      // longer exists, and a stale ["backtest", id] entry would render a tear
      // sheet for it until the 404 came back.
      qc.removeQueries({ queryKey: ["backtest", id] });
      qc.removeQueries({ queryKey: ["curves", id] });
      qc.invalidateQueries({ queryKey: ["backtests"] });
    },
  });
}

/** Sweep old runs. Takes the query string lib/retention builds — which is null
 *  for a choice with no criteria, so this is never called with one. */
export function usePurgeBacktests() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (query: string) =>
      api<{ deleted: number }>(`/backtests?${query}`, { method: "DELETE" }),
    onSuccess: () => {
      // The answer is a count, not a list of ids, so every cached run and curve
      // is suspect. Dropping the two families is cheaper than being wrong about
      // which survived; the listing refetches immediately.
      qc.removeQueries({ queryKey: ["backtest"] });
      qc.removeQueries({ queryKey: ["curves"] });
      qc.invalidateQueries({ queryKey: ["backtests"] });
    },
  });
}

// ── formatters ────────────────────────────────────────────────────────────────

export const kindLabel = (k: Strategy["kind"]) => (k === "spec" ? "espec" : "código");

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
