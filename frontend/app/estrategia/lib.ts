"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { Position, Sleeve, Target } from "@/lib/types";
// Reused across the two feature dirs I own — same queryKey dedupes the fetch.
export { useStrategies, compactBRL } from "../bench/lib";

export const CASH_REF = "CASH_CDI";

export function useSleeves() {
  return useQuery({
    queryKey: ["sleeves"],
    queryFn: () => api<Sleeve[]>("/portfolio/sleeves"),
  });
}

export function useSaveSleeves() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: Sleeve[]) =>
      api<Sleeve[]>("/portfolio/sleeves", {
        method: "PUT",
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["sleeves"] });
      qc.invalidateQueries({ queryKey: ["target"] });
    },
  });
}

// target 409s when no sleeves are set — surface that to the UI, don't retry it.
export function useTarget() {
  return useQuery({
    queryKey: ["target"],
    queryFn: () => api<Target>("/portfolio/target"),
    retry: false,
  });
}

export function usePositions() {
  return useQuery({
    queryKey: ["positions"],
    queryFn: () => api<Position[]>("/portfolio/positions"),
  });
}

// ── rebalance plan (untyped in lib/types — modeled here) ─────────────────────

export interface TradeLine {
  ticker: string;
  qty: number;
  est_price: number;
  est_brl: number;
}

export interface TaxSell {
  ticker: string;
  qty: number;
  avg_cost: number;
  price: number;
  sell_value: number;
  pnl: number;
  cls: string;
}

export interface HarvestLine {
  ticker: string;
  qty: number;
  price: number;
  realized_loss: number;
  partial: boolean;
}

export interface TaxBucket {
  status: string;
  gross_after: number;
  pnl_after: number;
  carry_used: number;
  target_loss: number;
  final_gross: number;
  final_pnl: number;
  ir: number;
  harvest: HarvestLine[];
  rebuys: { ticker: string; qty: number }[];
}

export interface TaxSituation {
  gross_mtd: number;
  pnl_mtd: number;
  carry: number;
}

export interface RebalanceTax {
  month: string;
  generated: string;
  isencao_limit: number;
  rates: Record<string, number>;
  situation: Record<string, TaxSituation>;
  sells: TaxSell[];
  buckets: Record<string, TaxBucket>;
}

export interface RebalancePlan {
  as_of: string;
  nav: number;
  new_cash: number;
  trades: { sells: TradeLine[]; buys: TradeLine[] };
  tax: RebalanceTax;
  notes: string[];
}

export interface RebalanceBody {
  new_cash?: number;
  exclude?: string[];
  pins?: string[];
}

export function useRebalancePlan() {
  return useMutation({
    mutationFn: (body: RebalanceBody) =>
      api<RebalancePlan>("/portfolio/rebalance-plans", {
        method: "POST",
        body: JSON.stringify(body),
      }),
  });
}
