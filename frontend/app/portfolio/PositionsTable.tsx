"use client";

import { useMemo, useState } from "react";
import { DataTable, type Column } from "@/components/ui/DataTable";
import { TickerChip } from "@/components/ui/TickerChip";
import { Money } from "@/components/ui/Money";
import { formatQty } from "@/lib/format";
import type { Position } from "@/lib/types";

type SortKey = "ticker" | "market" | "qty" | "avg_cost" | "total_cost";
type Dir = "asc" | "desc";

// Text sorts asc first, numerics desc first (biggest position on top is the useful default).
const DEFAULT_DIR: Record<SortKey, Dir> = {
  ticker: "asc",
  market: "asc",
  qty: "desc",
  avg_cost: "desc",
  total_cost: "desc",
};

function SortHeader({
  label,
  active,
  dir,
  align,
  onClick,
}: {
  label: string;
  active: boolean;
  dir: Dir;
  align: "left" | "right";
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={`Ordenar por ${label}${
        active ? (dir === "asc" ? " (crescente)" : " (decrescente)") : ""
      }`}
      className={`inline-flex items-center gap-1 uppercase tracking-wide transition-colors hover:text-ink ${
        active ? "text-ink" : ""
      } ${align === "right" ? "flex-row-reverse" : ""}`}
    >
      {label}
      <span aria-hidden className="text-[0.85em] leading-none">
        {active ? (dir === "asc" ? "▲" : "▼") : "↕"}
      </span>
    </button>
  );
}

export function PositionsTable({ positions }: { positions: Position[] }) {
  const [sort, setSort] = useState<{ key: SortKey; dir: Dir }>({
    key: "total_cost",
    dir: "desc",
  });

  const rows = useMemo(() => {
    const { key, dir } = sort;
    const sorted = [...positions].sort((a, b) => {
      const av = a[key];
      const bv = b[key];
      const cmp =
        typeof av === "number" && typeof bv === "number"
          ? av - bv
          : String(av).localeCompare(String(bv), "pt-BR");
      return dir === "asc" ? cmp : -cmp;
    });
    return sorted;
  }, [positions, sort]);

  function toggle(key: SortKey) {
    setSort((s) =>
      s.key === key
        ? { key, dir: s.dir === "asc" ? "desc" : "asc" }
        : { key, dir: DEFAULT_DIR[key] },
    );
  }

  const head = (key: SortKey, label: string, align: "left" | "right") => (
    <SortHeader
      label={label}
      active={sort.key === key}
      dir={sort.dir}
      align={align}
      onClick={() => toggle(key)}
    />
  );

  const columns: Column<Position>[] = [
    {
      key: "ticker",
      header: head("ticker", "Ativo", "left"),
      render: (p) => <TickerChip ticker={p.ticker} />,
    },
    {
      key: "market",
      header: head("market", "Mercado", "left"),
      render: (p) => <span className="text-muted">{p.market}</span>,
    },
    {
      key: "qty",
      header: head("qty", "Quantidade", "right"),
      align: "right",
      render: (p) => formatQty(p.qty),
    },
    {
      key: "avg_cost",
      header: head("avg_cost", "Preço médio", "right"),
      align: "right",
      render: (p) => <Money value={p.avg_cost} />,
    },
    {
      key: "total_cost",
      header: head("total_cost", "Custo total", "right"),
      align: "right",
      render: (p) => <Money value={p.total_cost} />,
    },
  ];

  return (
    <DataTable columns={columns} rows={rows} getRowKey={(p) => p.ticker} />
  );
}
