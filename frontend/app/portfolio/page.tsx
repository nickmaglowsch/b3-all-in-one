"use client";

import { useQuery } from "@tanstack/react-query";
import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts";
import { api } from "@/lib/api";
import type { Position } from "@/lib/types";
import { PageHeader } from "@/components/ui/PageHeader";
import { Card } from "@/components/ui/Card";
import { Money } from "@/components/ui/Money";
import { PctDelta } from "@/components/ui/PctDelta";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { ChartTooltip } from "@/components/charts/ChartTooltip";
import { SERIES } from "@/components/charts/theme";
import { IconWallet } from "@/components/icons";
import { formatInt, formatMoney, formatPercent } from "@/lib/format";
import { PositionsTable } from "./PositionsTable";
import { IndexSection } from "./IndexSection";
import { IncomeSection } from "./IncomeSection";
import { TradesSection } from "./TradesSection";

function Stat({
  label,
  children,
  sub,
}: {
  label: string;
  children: React.ReactNode;
  sub?: string;
}) {
  return (
    <div className="rounded-lg border border-hairline bg-panel p-4">
      <p className="font-mono text-[11px] font-medium uppercase tracking-wide text-muted">
        {label}
      </p>
      <div className="mt-1.5 text-2xl leading-none">{children}</div>
      {sub && <p className="mt-1.5 text-xs text-muted">{sub}</p>}
    </div>
  );
}

export default function PortfolioPage() {
  const q = useQuery({
    queryKey: ["positions"],
    queryFn: () => api<Position[]>("/portfolio/positions"),
  });

  // One request for the whole book — never one per row.
  const history = useQuery({
    queryKey: ["price-history"],
    queryFn: () => api<Record<string, number[]>>("/portfolio/price-history"),
  });

  const positions = q.data ?? [];
  const totalCost = positions.reduce((s, p) => s + p.total_cost, 0);

  // P/L compares like with like: only positions the API could price count, so an
  // unpriced Renda Fixa row doesn't drag the return toward zero.
  const priced = positions.filter((p) => p.market_value !== null);
  const marketValue = priced.reduce((s, p) => s + (p.market_value ?? 0), 0);
  const pricedCost = priced.reduce((s, p) => s + p.total_cost, 0);
  const pnl = marketValue - pricedCost;
  const pnlPct = pricedCost > 0 ? pnl / pricedCost : 0;

  // Honest split by the market the API actually reports (Mercado à Vista, Renda
  // Fixa, …). FIIs aren't a distinct market in the data — they ride under Mercado
  // à Vista — so grouping by ticker would misclassify ETFs like IVVB11.
  const byMarket = new Map<string, { count: number; value: number }>();
  for (const p of positions) {
    const m = byMarket.get(p.market) ?? { count: 0, value: 0 };
    m.count += 1;
    m.value += p.market_value ?? p.total_cost; // unpriced falls back to cost
    byMarket.set(p.market, m);
  }
  const markets = [...byMarket.entries()]
    .map(([name, m]) => ({ name, ...m }))
    .sort((a, b) => b.value - a.value);
  const allocTotal = markets.reduce((s, m) => s + m.value, 0);

  return (
    <>
      <PageHeader eyebrow="Carteira" title="Portfólio" />

      {q.isLoading ? (
        <div className="space-y-6">
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-96 w-full" />
        </div>
      ) : positions.length === 0 ? (
        <EmptyState
          icon={<IconWallet size={28} />}
          title="Nenhum extrato importado — envie seus arquivos da B3."
          hint="Depois de importar, suas posições, negociações e proventos aparecem aqui."
        />
      ) : (
        <div className="space-y-6">
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <Stat
              label="Valor de mercado"
              sub={
                priced.length < positions.length
                  ? `${formatInt(priced.length)} de ${formatInt(positions.length)} posições precificadas`
                  : "última cotação disponível"
              }
            >
              <Money value={marketValue} className="text-2xl" />
            </Stat>
            <Stat label="Total investido" sub="custo das posições precificadas">
              <Money value={pricedCost} className="text-2xl" />
            </Stat>
            <Stat label="Rentabilidade" sub={`custo total ${formatMoney(totalCost)}`}>
              <div className="flex flex-wrap items-baseline gap-2">
                <Money value={pnl} sign className="text-2xl" />
                <PctDelta value={pnlPct} className="text-sm" />
              </div>
            </Stat>
            <Stat label="Posições">
              <span className="font-display font-extrabold tabular-nums text-ink">
                {formatInt(positions.length)}
              </span>
            </Stat>
          </div>

          <Card title="Alocação">
            <div className="flex flex-col items-center gap-6 sm:flex-row">
              <div className="h-[200px] w-full sm:w-[220px]">
                <ResponsiveContainer width="100%" height="100%">
                  <PieChart>
                    <Pie
                      data={markets}
                      dataKey="value"
                      nameKey="name"
                      innerRadius="58%"
                      outerRadius="88%"
                      paddingAngle={1}
                      stroke="var(--panel)"
                      isAnimationActive={false}
                    >
                      {markets.map((m, i) => (
                        <Cell key={m.name} fill={SERIES[i % SERIES.length]} />
                      ))}
                    </Pie>
                    <Tooltip
                      content={<ChartTooltip valueFormat={(v) => formatMoney(v)} />}
                    />
                  </PieChart>
                </ResponsiveContainer>
              </div>
              <ul className="w-full space-y-2 text-sm">
                {markets.map((m, i) => (
                  <li key={m.name} className="flex items-center gap-2">
                    <span
                      className="inline-block h-2.5 w-2.5 shrink-0 rounded-full"
                      style={{ background: SERIES[i % SERIES.length] }}
                    />
                    <span className="text-ink">{m.name}</span>
                    <span className="text-xs text-muted">
                      {formatInt(m.count)} posições
                    </span>
                    <span className="ml-auto font-mono tnum tabular-nums text-muted">
                      {formatPercent(allocTotal > 0 ? m.value / allocTotal : 0)}
                    </span>
                    <Money value={m.value} className="w-32 text-right" />
                  </li>
                ))}
              </ul>
            </div>
          </Card>

          <IndexSection />

          <Card title="Posições">
            <PositionsTable positions={positions} history={history.data} />
          </Card>

          <IncomeSection />
          <TradesSection />
        </div>
      )}
    </>
  );
}
