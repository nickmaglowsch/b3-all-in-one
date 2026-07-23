"use client";

// Bench-local data hooks + formatters. Study note: metrics %-fields (e.g.
// "Ann. Return (%)": 8.9) are already percent *numbers*, not fractions —
// formatPct here appends "%" directly; the chart drawdown series IS a fraction
// so it uses lib/format's formatPercent instead.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
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
export function curveRows(
  series: Record<string, (string | number)[]>,
  keys: string[],
): Record<string, string | number>[] {
  const n = series.date?.length ?? 0;
  const rows: Record<string, string | number>[] = [];
  for (let i = 0; i < n; i++) {
    const row: Record<string, string | number> = { date: series.date[i] };
    for (const k of keys) if (series[k]) row[k] = series[k][i];
    rows.push(row);
  }
  return rows;
}
