"use client";

import Link from "next/link";
import { Card } from "@/components/ui/Card";
import { Money } from "@/components/ui/Money";
import { AllocationDonut } from "@/components/charts/AllocationDonut";
import { SERIES } from "@/components/charts/theme";
import { formatInt, formatPercent } from "@/lib/format";

export interface MarketSlice {
  name: string;
  count: number;
  value: number;
}

/** The "Visão geral" tab: where the money sits. Only offered for a book with
 *  open positions. */
export function Overview({
  markets,
  total,
  priced,
  count,
}: {
  markets: MarketSlice[];
  total: number;
  /** Positions with a mark, and all positions — the rest entered at cost. */
  priced: number;
  count: number;
}) {
  return (
    <Card title="Alocação">
      <div className="flex flex-col items-center gap-6 sm:flex-row">
        <div className="h-[200px] w-full sm:w-[220px]">
          <AllocationDonut slices={markets} total={total} />
        </div>
        <ul className="w-full space-y-2 text-sm">
          {markets.map((m, i) => (
            <li key={m.name} className="flex items-center gap-2">
              <span
                className="inline-block h-2.5 w-2.5 shrink-0 rounded-full"
                style={{ background: SERIES[i % SERIES.length] }}
              />
              <span className="text-ink">{m.name}</span>
              <span className="text-xs text-muted">{formatInt(m.count)} posições</span>
              <span className="ml-auto font-mono tnum tabular-nums text-muted">
                {formatPercent(total > 0 ? m.value / total : 0)}
              </span>
              <Money value={m.value} className="text-right sm:w-32" />
            </li>
          ))}
        </ul>
      </div>
      {/* This card totals the book on a mixed basis (marks where they exist,
          cost where they don't), so it will not sum to the Valor de mercado
          tile above. Every figure shows its provenance — including this one. */}
      {priced < count && (
        <p className="mt-4 border-t border-hairline pt-3 text-xs text-muted">
          {formatInt(count - priced)} de {formatInt(count)} posições
          entram aqui pelo custo, por não terem cotação — por isso este total
          não bate com o valor de mercado acima.
        </p>
      )}
    </Card>
  );
}

/** Where to go from the portfolio — the next step is always one of these. */
export function NextSteps() {
  return (
    <nav aria-label="Próximos passos" className="mt-6 rounded-lg border border-hairline bg-panel p-4">
      <p className="font-mono text-[11px] font-medium uppercase tracking-[0.14em] text-muted">
        A partir daqui
      </p>
      <ul className="mt-3 grid gap-2 sm:grid-cols-3">
        {[
          { href: "/irpf", label: "Declarar no IRPF", hint: "bens, rendimentos e DARF" },
          { href: "/tlh", label: "Colher prejuízo", hint: "compensar ganhos do mês" },
          { href: "/estrategia", label: "Rebalancear", hint: "desvio contra suas metas" },
        ].map((l) => (
          <li key={l.href}>
            <Link
              href={l.href}
              className="block rounded-md border border-hairline px-3 py-2 transition-colors hover:bg-hairline/40"
            >
              <span className="text-sm font-medium text-ink">{l.label}</span>
              <span className="mt-0.5 block text-xs text-muted">{l.hint}</span>
            </Link>
          </li>
        ))}
      </ul>
    </nav>
  );
}
