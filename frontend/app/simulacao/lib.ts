"use client";

// Simulação-local hooks, types and formatters for the what-if replay
// (POST /whatif). Kept local per feature; shares only the typed `api` wrapper
// and the Strategy gallery type from lib/.
import { useMutation, useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { Strategy } from "@/lib/types";

export interface CurvePoint {
  date: string;
  aftertax: number;
}

export interface WhatIfScenario {
  strategy: string;
  final_value: number;
  ann_return: number | null; // %-number, e.g. -8.15
  sharpe: number | null;
  max_dd: number | null; // %-number, non-positive
  curve: CurvePoint[];
}

export interface WhatIfResult {
  as_of: string;
  total_invested: number;
  actual_value: number;
  scenarios: WhatIfScenario[];
}

export interface WhatIfBody {
  strategy_ids: string[];
  freq?: string;
}

export function useStrategies() {
  return useQuery({
    queryKey: ["strategies"],
    queryFn: () => api<Strategy[]>("/strategies"),
    staleTime: 60 * 60_000, // gallery only changes on image rebuild
  });
}

export function useRunWhatIf() {
  return useMutation({
    mutationFn: (body: WhatIfBody) =>
      api<WhatIfResult>("/whatif", { method: "POST", body: JSON.stringify(body) }),
  });
}

// ── formatters + shaping ──────────────────────────────────────────────────────

export const FREQS = [
  { value: "ME", label: "Mensal" },
  { value: "QE", label: "Trimestral" },
  { value: "W-FRI", label: "Semanal" },
];

// Metric %-number (8.9 → "8,9%"). Signed prefixes an explicit +.
export function formatMetricPct(n: number | null, signed = false): string {
  if (n == null) return "—";
  const s = n.toLocaleString("pt-BR", { minimumFractionDigits: 1, maximumFractionDigits: 2 });
  return `${signed && n > 0 ? "+" : ""}${s}%`;
}

export const formatRatio = (n: number | null) =>
  n == null ? "—" : n.toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

// Compact BRL for chart axes (NAV runs into 6-7 figures).
export function compactBRL(v: number): string {
  const a = Math.abs(v);
  const sign = v < 0 ? "-" : "";
  if (a >= 1e6) return `${sign}R$ ${(a / 1e6).toLocaleString("pt-BR", { maximumFractionDigits: 1 })} mi`;
  if (a >= 1e3) return `${sign}R$ ${(a / 1e3).toLocaleString("pt-BR", { maximumFractionDigits: 0 })} mil`;
  return `${sign}R$ ${a.toLocaleString("pt-BR", { maximumFractionDigits: 0 })}`;
}

// Total return of a scenario vs the money put in (fraction, for PctDelta).
export const totalReturn = (finalValue: number, invested: number) =>
  invested > 0 ? finalValue / invested - 1 : 0;

// Overlay several scenario curves into one row set keyed by date, with one
// column per strategy. Curves share the same rebalance grid (same flows/freq),
// so rows align; a later-warming strategy just leaves earlier cells undefined.
export function overlayCurves(scenarios: WhatIfScenario[]): {
  rows: Record<string, string | number>[];
  series: { key: string; label: string }[];
} {
  const byDate = new Map<string, Record<string, string | number>>();
  for (const sc of scenarios) {
    for (const p of sc.curve) {
      const row = byDate.get(p.date) ?? { date: p.date };
      row[sc.strategy] = p.aftertax;
      byDate.set(p.date, row);
    }
  }
  const rows = [...byDate.values()].sort((a, b) =>
    String(a.date) < String(b.date) ? -1 : 1,
  );
  return { rows, series: scenarios.map((sc) => ({ key: sc.strategy, label: sc.strategy })) };
}
