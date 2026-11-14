"use client";

// Simulação-local hooks, types and shaping for the what-if replay
// (POST /whatif queues it, GET /whatif/{id} answers it). Formatters live in lib/format.
import { useMutation } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api";
import { pollJob } from "@/lib/pollJob";
import type { Dated } from "@/lib/chartRange";
import type { Flows } from "@/lib/chartMode";
// Same queryKey as the bench gallery, so the fetch is shared.
export { useStrategies } from "../bench/lib";

export interface CurvePoint {
  date: string;
  aftertax: number;
  /** Aporte the engine deployed on this row (routes_whatif._read_curve). */
  contribution?: number;
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

/** A queued replay, as GET /whatif/{id} returns it. */
export interface WhatIfRun {
  id: string;
  status: "pending" | "running" | "done" | "error";
  error: string | null;
  result: WhatIfResult | null;
}

const POLL_MS = 2000;
// Four full backtests on a busy worker is minutes, not hours; past this the
// page says so rather than spinning forever.
const GIVE_UP_MS = 15 * 60_000;

/** Queue the replay, then poll it until the worker is done. Still one mutation
 *  to the page: pending while the job runs, `data` the result, `error` its failure. */
export async function runWhatIf(body: WhatIfBody): Promise<WhatIfResult> {
  const run = await pollJob<WhatIfRun>(
    () => api<WhatIfRun>("/whatif", { method: "POST", body: JSON.stringify(body) }),
    (id) => api<WhatIfRun>(`/whatif/${id}`),
    {
      everyMs: POLL_MS,
      giveUpMs: GIVE_UP_MS,
      timedOut: () =>
        new ApiError(504, "A simulação está demorando mais que o normal — tente de novo em alguns minutos."),
    },
  );
  if (run.status === "error" || !run.result) {
    throw new ApiError(500, run.error ?? "Falha ao rodar a simulação.");
  }
  return run.result;
}

export function useRunWhatIf() {
  return useMutation({ mutationFn: (body: WhatIfBody) => runWhatIf(body) });
}

// ── formatters + shaping ──────────────────────────────────────────────────────

// Total return of a scenario vs the money put in (fraction, for PctDelta).
export const totalReturn = (finalValue: number, invested: number) =>
  invested > 0 ? finalValue / invested - 1 : 0;

// Overlay several scenario curves into one row set keyed by date, with one
// column per strategy. Curves share the same rebalance grid (same flows/freq),
// so rows align; a later-warming strategy just leaves earlier cells undefined.
//
// `flows` is each column's deployed aportes by date — what the "%" reading needs
// to tell a deposit from a gain (lib/chartMode.toTwrRows).
export function overlayCurves(scenarios: WhatIfScenario[]): {
  rows: (Dated & Record<string, string | number>)[];
  series: { key: string; label: string }[];
  flows: Flows;
} {
  const byDate = new Map<string, Dated & Record<string, string | number>>();
  const flows: Flows = {};
  for (const sc of scenarios) {
    const mine: Record<string, number> = (flows[sc.strategy] = {});
    for (const p of sc.curve) {
      const row = byDate.get(p.date) ?? { date: p.date };
      row[sc.strategy] = p.aftertax;
      byDate.set(p.date, row);
      if (p.contribution) mine[p.date] = p.contribution;
    }
  }
  const rows = [...byDate.values()].sort((a, b) => (a.date < b.date ? -1 : 1));
  return {
    rows,
    series: scenarios.map((sc) => ({ key: sc.strategy, label: sc.strategy })),
    flows,
  };
}
