"use client";

import { useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { TradesPage } from "@/lib/types";
import { Card } from "@/components/ui/Card";
import { DataTable, type Column } from "@/components/ui/DataTable";
import { TickerChip } from "@/components/ui/TickerChip";
import { Money } from "@/components/ui/Money";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { IconChart } from "@/components/icons";
import { formatDate, formatInt, formatQty } from "@/lib/format";
import type { Trade } from "@/lib/types";

const LIMIT = 50;

// Side is not gain/loss — neutral pills only (Compra filled, Venda outlined for scanning).
function SideBadge({ side }: { side: string }) {
  const buy = side.toLowerCase().startsWith("compra");
  return (
    <span
      className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${
        buy ? "bg-navy/[0.06] text-navy" : "border border-hairline text-muted"
      }`}
    >
      {side}
    </span>
  );
}

const columns: Column<Trade>[] = [
  { key: "date", header: "Data", render: (t) => formatDate(t.date) },
  { key: "side", header: "Lado", render: (t) => <SideBadge side={t.side} /> },
  { key: "ticker", header: "Ativo", render: (t) => <TickerChip ticker={t.ticker} /> },
  {
    key: "market",
    header: "Mercado",
    render: (t) => <span className="text-muted">{t.market}</span>,
  },
  { key: "qty", header: "Quantidade", align: "right", render: (t) => formatQty(t.qty) },
  { key: "price", header: "Preço", align: "right", render: (t) => <Money value={t.price} /> },
  { key: "amount", header: "Valor", align: "right", render: (t) => <Money value={t.amount} /> },
];

export function TradesSection() {
  const [offset, setOffset] = useState(0);
  const q = useQuery({
    queryKey: ["trades", offset],
    queryFn: () =>
      api<TradesPage>(`/portfolio/trades?limit=${LIMIT}&offset=${offset}`),
    placeholderData: keepPreviousData,
  });

  if (q.isLoading) {
    return (
      <Card title="Negociações">
        <Skeleton className="h-64 w-full" />
      </Card>
    );
  }

  const page = q.data;
  if (!page || page.total === 0) {
    return (
      <Card title="Negociações">
        <EmptyState
          icon={<IconChart size={28} />}
          title="Nenhuma negociação importada."
          hint="Importe o extrato de negociação para ver seu histórico de compras e vendas."
        />
      </Card>
    );
  }

  const from = page.total === 0 ? 0 : offset + 1;
  const to = Math.min(offset + LIMIT, page.total);
  const canPrev = offset > 0;
  const canNext = offset + LIMIT < page.total;

  return (
    <Card
      title="Negociações"
      action={
        <span className="font-mono text-xs text-muted">
          {formatInt(from)}–{formatInt(to)} de {formatInt(page.total)}
        </span>
      }
    >
      <DataTable
        columns={columns}
        rows={page.items}
        getRowKey={(t, i) => `${t.source_file}-${offset + i}`}
      />
      <div className="mt-4 flex items-center justify-end gap-2">
        <button
          type="button"
          disabled={!canPrev}
          onClick={() => setOffset((o) => Math.max(0, o - LIMIT))}
          className="rounded-md border border-hairline px-3 py-1.5 text-sm font-medium text-ink transition-colors hover:bg-hairline/40 disabled:cursor-not-allowed disabled:opacity-40"
        >
          Anterior
        </button>
        <button
          type="button"
          disabled={!canNext}
          onClick={() => setOffset((o) => o + LIMIT)}
          className="rounded-md border border-hairline px-3 py-1.5 text-sm font-medium text-ink transition-colors hover:bg-hairline/40 disabled:cursor-not-allowed disabled:opacity-40"
        >
          Próxima
        </button>
      </div>
    </Card>
  );
}
