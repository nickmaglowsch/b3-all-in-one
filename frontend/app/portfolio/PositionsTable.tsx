"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { DataTable, type Column } from "@/components/ui/DataTable";
import { TickerChip } from "@/components/ui/TickerChip";
import { Money } from "@/components/ui/Money";
import { PctDelta } from "@/components/ui/PctDelta";
import { Sparkline } from "@/components/charts/Sparkline";
import { formatInt, formatQty } from "@/lib/format";
import type { Position } from "@/lib/types";

type SortKey =
  | "ticker"
  | "market"
  | "qty"
  | "avg_cost"
  | "last_price"
  | "total_cost"
  | "market_value"
  | "pnl";
type Dir = "asc" | "desc";

const SORT_KEYS: SortKey[] = [
  "ticker",
  "market",
  "qty",
  "avg_cost",
  "last_price",
  "total_cost",
  "market_value",
  "pnl",
];

// Text sorts asc first, numerics desc first (biggest position on top is the useful default).
const DEFAULT_DIR: Record<SortKey, Dir> = {
  ticker: "asc",
  market: "asc",
  qty: "desc",
  avg_cost: "desc",
  last_price: "desc",
  total_cost: "desc",
  market_value: "desc",
  pnl: "desc",
};

const isSortKey = (v: string | null): v is SortKey =>
  !!v && (SORT_KEYS as string[]).includes(v);

function SortHeader({
  label,
  active,
  dir,
  nextDir,
  align,
  onClick,
}: {
  label: string;
  active: boolean;
  dir: Dir;
  nextDir: Dir;
  align: "left" | "right";
  onClick: () => void;
}) {
  // The label names what the click will DO; the current state is on the <th> as
  // aria-sort, which is where a screen reader expects it. Saying both in one
  // string is how "Ativo (crescente)" ends up ambiguous about whether it
  // describes the order now or the order after clicking.
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={`Ordenar por ${label}, ${nextDir === "asc" ? "crescente" : "decrescente"}`}
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

export function PositionsTable({
  positions,
  history = {},
  historyFailed = false,
  historyLoading = false,
}: {
  positions: Position[];
  history?: Record<string, number[]>;
  historyFailed?: boolean;
  historyLoading?: boolean;
}) {
  // Sort lives in the URL: a sorted view is a view worth sending to someone, and
  // losing it on reload is the difference between a report and a toy.
  const [sort, setSort] = useState<{ key: SortKey; dir: Dir }>(() => {
    if (typeof window === "undefined") return { key: "total_cost", dir: "desc" };
    const p = new URLSearchParams(window.location.search);
    const key = p.get("ordem");
    const dir = p.get("dir");
    return {
      key: isSortKey(key) ? key : "total_cost",
      dir: dir === "asc" || dir === "desc" ? dir : "desc",
    };
  });

  // Only once the user actually sorts. Writing the default on mount put
  // `?ordem=total_cost&dir=desc` into every shared Portfólio link, claiming two
  // very generic param names app-wide for state nobody chose.
  const touched = useRef(false);
  useEffect(() => {
    if (!touched.current) return;
    const url = new URL(window.location.href);
    url.searchParams.set("ordem", sort.key);
    url.searchParams.set("dir", sort.dir);
    window.history.replaceState(null, "", url);
  }, [sort]);

  // Every row is already in memory, so filtering is a substring match and not a
  // round trip. "What's my position in PETR4" is the most common question this
  // table answers, and eight sort headers never answered it.
  const [filter, setFilter] = useState("");

  const rows = useMemo(() => {
    const needle = filter.trim().toUpperCase();
    const matched = needle
      ? positions.filter(
          (p) =>
            p.ticker.toUpperCase().includes(needle) ||
            p.market.toUpperCase().includes(needle),
        )
      : positions;
    const { key, dir } = sort;
    const sorted = [...matched].sort((a, b) => {
      const av = a[key];
      const bv = b[key];
      // Unpriced rows (Renda Fixa, delisted) sink to the bottom either way —
      // flipping the sort shouldn't float a column of dashes to the top.
      if (av === null || bv === null) {
        if (av === bv) return 0;
        return av === null ? 1 : -1;
      }
      const cmp =
        typeof av === "number" && typeof bv === "number"
          ? av - bv
          : String(av).localeCompare(String(bv), "pt-BR");
      return dir === "asc" ? cmp : -cmp;
    });
    return sorted;
  }, [positions, sort, filter]);

  function toggle(key: SortKey) {
    touched.current = true;
    setSort((s) =>
      s.key === key
        ? { key, dir: s.dir === "asc" ? "desc" : "asc" }
        : { key, dir: DEFAULT_DIR[key] },
    );
  }

  const head = (key: SortKey, label: string, align: "left" | "right") => {
    const active = sort.key === key;
    return {
      header: (
        <SortHeader
          label={label}
          active={active}
          dir={sort.dir}
          nextDir={
            active ? (sort.dir === "asc" ? "desc" : "asc") : DEFAULT_DIR[key]
          }
          align={align}
          onClick={() => toggle(key)}
        />
      ),
      sortable: true as const,
      sort: active ? sort.dir : undefined,
    };
  };

  const columns: Column<Position>[] = [
    {
      key: "ticker",
      ...head("ticker", "Ativo", "left"),
      render: (p) => <TickerChip ticker={p.ticker} />,
    },
    {
      key: "market",
      ...head("market", "Mercado", "left"),
      render: (p) => <span className="text-muted">{p.market}</span>,
    },
    {
      key: "qty",
      ...head("qty", "Quantidade", "right"),
      align: "right",
      render: (p) => formatQty(p.qty),
    },
    {
      key: "avg_cost",
      ...head("avg_cost", "Preço médio", "right"),
      align: "right",
      render: (p) => <Money value={p.avg_cost} />,
    },
    {
      // The mark behind every market value on this page. A number whose source
      // is invisible is a number without provenance.
      key: "last_price",
      ...head("last_price", "Última cotação", "right"),
      align: "right",
      render: (p) =>
        p.last_price === null ? (
          <span className="text-muted">—</span>
        ) : (
          <Money value={p.last_price} />
        ),
    },
    {
      key: "total_cost",
      ...head("total_cost", "Custo total", "right"),
      align: "right",
      render: (p) => <Money value={p.total_cost} />,
    },
    {
      key: "market_value",
      ...head("market_value", "Valor de mercado", "right"),
      align: "right",
      render: (p) =>
        p.market_value === null ? (
          <span className="text-muted">—</span>
        ) : (
          <Money value={p.market_value} />
        ),
    },
    {
      key: "pnl",
      ...head("pnl", "Rentabilidade", "right"),
      align: "right",
      render: (p) =>
        p.pnl === null ? (
          <span className="text-muted">—</span>
        ) : (
          <span className="inline-flex items-baseline justify-end gap-2">
            <Money value={p.pnl} sign />
            {p.pnl_pct !== null && (
              <PctDelta value={p.pnl_pct} className="text-xs" />
            )}
          </span>
        ),
    },
    {
      // Not sortable — "shape" has no total order worth a header click.
      key: "trend",
      header: "Tendência 6m",
      align: "right",
      numeric: false,
      render: (p) =>
        // Three distinct answers, three distinct renderings. An em dash for all
        // of them would make "still fetching" look identical to "this ticker has
        // no history" and to "the request failed".
        historyFailed ? (
          <span className="text-muted" title="Histórico de preços indisponível">
            —
          </span>
        ) : historyLoading ? (
          <span
            className="ml-auto block h-3 w-[72px] animate-pulse rounded bg-hairline/70 motion-reduce:animate-none"
            aria-hidden
          />
        ) : (
          <Sparkline values={history[p.ticker]} />
        ),
    },
  ];

  return (
    <div aria-busy={historyLoading || undefined}>
      <div className="mb-3 flex items-center justify-between gap-3">
        <label className="flex items-center gap-2 text-xs text-muted">
          <span className="sr-only sm:not-sr-only">Filtrar</span>
          <input
            type="search"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Ativo ou mercado"
            aria-label="Filtrar posições por ativo ou mercado"
            className="w-36 rounded-md border border-hairline bg-paper px-3 py-1.5 font-mono text-xs sm:w-48 text-ink placeholder:text-muted focus:border-navy focus:outline-none"
          />
        </label>
        <p role="status" aria-live="polite" className="font-mono text-xs text-muted">
          {filter.trim()
            ? `${formatInt(rows.length)} de ${formatInt(positions.length)} posições`
            : `${formatInt(positions.length)} posições`}
        </p>
      </div>
      {historyFailed && (
        <p className="mb-3 text-xs text-muted">
          Não foi possível carregar o histórico de preços — a coluna Tendência
          está vazia. Os demais números não dependem dela.
        </p>
      )}
      <DataTable
        columns={columns}
        rows={rows}
        getRowKey={(p) => p.ticker}
        label="Posições da carteira"
      />
    </div>
  );
}
