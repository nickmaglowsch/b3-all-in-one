"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { Position } from "@/lib/types";
import { PageHeader } from "@/components/ui/PageHeader";
import { Card } from "@/components/ui/Card";
import { Money } from "@/components/ui/Money";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { IconWallet } from "@/components/icons";
import { formatInt } from "@/lib/format";
import { PositionsTable } from "./PositionsTable";
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

  const positions = q.data ?? [];
  const totalCost = positions.reduce((s, p) => s + p.total_cost, 0);

  // Honest split by the market the API actually reports (Mercado à Vista, Renda
  // Fixa, …). FIIs aren't a distinct market in the data — they ride under Mercado
  // à Vista — so grouping by ticker would misclassify ETFs like IVVB11.
  const byMarket = new Map<string, { count: number; cost: number }>();
  for (const p of positions) {
    const m = byMarket.get(p.market) ?? { count: 0, cost: 0 };
    m.count += 1;
    m.cost += p.total_cost;
    byMarket.set(p.market, m);
  }
  const markets = [...byMarket.entries()].sort((a, b) => b[1].cost - a[1].cost);

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
            <Stat label="Total investido">
              <Money value={totalCost} className="text-2xl" />
            </Stat>
            <Stat label="Posições">
              <span className="font-display font-extrabold tabular-nums text-ink">
                {formatInt(positions.length)}
              </span>
            </Stat>
            {markets.map(([name, m]) => (
              <Stat key={name} label={name} sub={`${formatInt(m.count)} posições`}>
                <Money value={m.cost} className="text-2xl" />
              </Stat>
            ))}
          </div>

          <Card title="Posições">
            <PositionsTable positions={positions} />
          </Card>

          <IncomeSection />
          <TradesSection />
        </div>
      )}
    </>
  );
}
