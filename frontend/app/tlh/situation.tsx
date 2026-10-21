"use client";

// TLH's API shapes and the month-to-date situation panel. Split from page.tsx so
// the page stays under the module-size gate (scripts/quality_gates.py).
import type { UseQueryResult } from "@tanstack/react-query";
import { formatMoney } from "@/lib/format";
import { Card } from "@/components/ui/Card";
import { Money } from "@/components/ui/Money";
import { Skeleton } from "@/components/ui/Skeleton";
import { LoadFailure } from "@/components/ui/EmptyState";

// ── API shapes (POST /tlh/plans) — mirrors taxcore/scripts/tlh_plan.py --json ──
export interface Situation {
  gross_mtd: number;
  pnl_mtd: number;
  carry: number;
}
export interface HarvestRow {
  ticker: string;
  qty: number;
  price: number;
  realized_loss: number;
  partial: boolean;
}
export interface Bucket {
  status: string;
  rationale: string;
  gross_after: number;
  pnl_after: number;
  carry_used: number;
  carry_after: number;
  target_loss: number;
  final_gross: number;
  final_pnl: number;
  ir: number;
  harvest: HarvestRow[];
  rebuys: { ticker: string; qty: number }[];
}
export interface TlhPlan {
  month: string;
  isencao_limit: number;
  rates: Record<string, number>;
  /** Always both classes — tlh_plan.py builds it over ("AÇÃO", "FII"). */
  situation: Record<string, Situation>;
  buckets: Record<string, Bucket>;
}

const CLASSES = ["AÇÃO", "FII"] as const;

// ── Situation panel ───────────────────────────────────────────────────────────
// A plan carries the same month-to-date situation, so once one exists it is the
// fresher read. Without either there is nothing to show — and "R$ 0,00 vendidos,
// nada a compensar" in its place would be a false statement about the month.
export function SituationPanel({
  situation,
  plan,
}: {
  situation: UseQueryResult<TlhPlan>;
  plan?: TlhPlan;
}) {
  const shown = plan ?? situation.data;
  if (!shown && situation.isPending) {
    return CLASSES.map((c) => <Skeleton key={c} className="h-36 w-full" />);
  }
  if (!shown) {
    return (
      <div className="sm:col-span-2">
        <LoadFailure
          error={situation.error}
          title="Não foi possível apurar a situação do mês."
          offerTitle={(nome) => `A colheita de prejuízo vem com o módulo ${nome}.`}
          onRetry={() => situation.refetch()}
        />
      </div>
    );
  }
  return CLASSES.map((c) => (
    <SituationCard
      key={c}
      cls={c}
      data={shown.situation[c]}
      limit={c === "AÇÃO" ? shown.isencao_limit : undefined}
    />
  ));
}

function SituationCard({
  cls,
  data,
  limit,
}: {
  cls: string;
  data: Situation;
  limit?: number;
}) {
  const gross = data.gross_mtd;
  const pct = limit ? Math.min(1, gross / limit) : 0;
  const over = limit != null && gross > limit;
  return (
    <Card title={`${cls} · vendas em ${cls === "FII" ? "cotas" : "ações"}`}>
      <div className="space-y-3">
        <div>
          <div className="flex items-baseline justify-between">
            <span className="text-xs text-muted">Vendas no mês</span>
            <span className="font-mono text-sm tnum tabular-nums text-ink">
              {formatMoney(gross)}
              {limit != null && (
                <span className="text-muted"> / {formatMoney(limit)}</span>
              )}
            </span>
          </div>
          {limit != null && (
            <>
              <div className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-hairline">
                <div
                  className={`h-full rounded-full ${over ? "bg-loss" : "bg-navy"}`}
                  style={{ width: `${Math.max(pct * 100, gross > 0 ? 4 : 0)}%` }}
                />
              </div>
              <p className="mt-1 text-[11px] text-muted">
                {over
                  ? "Acima do limite — ganhos do mês são tributáveis."
                  : "Limite de isenção mensal para ações."}
              </p>
            </>
          )}
        </div>
        <Metric label="P&L do mês">
          <Money value={data.pnl_mtd} sign />
        </Metric>
        <Metric label="Prejuízo a compensar">
          <Money value={data.carry} />
        </Metric>
      </div>
    </Card>
  );
}

export function Metric({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-baseline justify-between">
      <span className="text-xs text-muted">{label}</span>
      <span className="text-sm">{children}</span>
    </div>
  );
}
