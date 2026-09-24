"use client";

// Simulação-local hooks, types and shaping for the what-if replay
// (POST /whatif). Formatters live in lib/format.
import { useMutation } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { Dated } from "@/lib/chartRange";
// Same queryKey as the bench gallery, so the fetch is shared.
export { useStrategies } from "../bench/lib";

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

export function useRunWhatIf() {
  return useMutation({
    mutationFn: (body: WhatIfBody) =>
      api<WhatIfResult>("/whatif", { method: "POST", body: JSON.stringify(body) }),
  });
}

// ── formatters + shaping ──────────────────────────────────────────────────────

// Total return of a scenario vs the money put in (fraction, for PctDelta).
export const totalReturn = (finalValue: number, invested: number) =>
  invested > 0 ? finalValue / invested - 1 : 0;

// Overlay several scenario curves into one row set keyed by date, with one
// column per strategy. Curves share the same rebalance grid (same flows/freq),
// so rows align; a later-warming strategy just leaves earlier cells undefined.
export function overlayCurves(scenarios: WhatIfScenario[]): {
  rows: (Dated & Record<string, string | number>)[];
  series: { key: string; label: string }[];
} {
  const byDate = new Map<string, Dated & Record<string, string | number>>();
  for (const sc of scenarios) {
    for (const p of sc.curve) {
      const row = byDate.get(p.date) ?? { date: p.date };
      row[sc.strategy] = p.aftertax;
      byDate.set(p.date, row);
    }
  }
  const rows = [...byDate.values()].sort((a, b) => (a.date < b.date ? -1 : 1));
  return { rows, series: scenarios.map((sc) => ({ key: sc.strategy, label: sc.strategy })) };
}
