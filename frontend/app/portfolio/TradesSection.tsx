"use client";

import { useEffect, useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { TradesPage } from "@/lib/types";
import { Card } from "@/components/ui/Card";
import { Pager } from "@/components/ui/Pager";
import { DataTable, type Column } from "@/components/ui/DataTable";
import { SortControl } from "@/components/ui/SortControl";
import { SortHeader, type Dir } from "@/components/ui/SortHeader";
import { TickerChip } from "@/components/ui/TickerChip";
import { Money } from "@/components/ui/Money";
import { EmptyState, RetryButton } from "@/components/ui/EmptyState";
import { LoadingStatus, Skeleton } from "@/components/ui/Skeleton";
import { IconChart } from "@/components/icons";
import { formatDate, formatQty } from "@/lib/format";
import type { Trade } from "@/lib/types";

const PAGE_SIZES = [20, 50, 100];
const TITLE = "Negociações";

/** First-page query shape. The portfolio page reuses this so its "does this book
 *  have any trades" gate and this table share one request. */
export const TRADES_DEFAULTS = { limit: 20, sort: "date", dir: "desc" } as const;

type SortKey = "date" | "side" | "ticker" | "market" | "broker" | "qty" | "price" | "amount";

// One spelling of each column name, read three ways: the desktop `<th>`, the
// label on the card layout's line, and the phone's sort picker.
const COLUMN_LABEL: Record<SortKey, string> = {
  date: "Data",
  side: "Lado",
  ticker: "Ativo",
  market: "Mercado",
  broker: "Corretora",
  qty: "Quantidade",
  price: "Preço",
  amount: "Valor",
};

const SORT_OPTIONS = (Object.keys(COLUMN_LABEL) as SortKey[]).map((key) => ({
  key,
  label: COLUMN_LABEL[key],
}));

// Money and dates read newest/largest first; names read A→Z.
const defaultDir = (key: SortKey): Dir =>
  key === "date" || key === "qty" || key === "price" || key === "amount"
    ? "desc"
    : "asc";

// Side is not gain/loss — neutral pills only (Compra filled, Venda outlined for
// scanning). Ink, not navy: the accent marks where you are and what you can do,
// and a badge on every one of 250 rows is neither. The fill/outline contrast
// carries the scan just as well without spending the accent on data.
function SideBadge({ side }: { side: string }) {
  const buy = side.toLowerCase().startsWith("compra");
  return (
    <span
      className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${
        buy ? "bg-ink/[0.07] text-ink" : "border border-hairline text-muted"
      }`}
    >
      {side}
    </span>
  );
}

export function TradesSection() {
  const [limit, setLimit] = useState<number>(TRADES_DEFAULTS.limit);
  const [offset, setOffset] = useState(0);
  const [sort, setSort] = useState<{ key: SortKey; dir: Dir }>({
    key: TRADES_DEFAULTS.sort,
    dir: TRADES_DEFAULTS.dir,
  });
  // Server-side: this table is paginated, so filtering what is already on screen
  // would search page 1 and present it as the whole book. Debounced so typing a
  // ticker is one request, not six.
  const [filterInput, setFilterInput] = useState("");
  const [filter, setFilter] = useState("");
  // Only when the filter really changes: this also ran on mount, and the
  // setOffset(0) inside threw away a page turned in the first 300ms — which,
  // now that the table mounts when its tab is opened, is a real click.
  useEffect(() => {
    const next = filterInput.trim();
    if (next === filter) return;
    const t = setTimeout(() => {
      setFilter(next);
      setOffset(0);
    }, 300);
    return () => clearTimeout(t);
  }, [filterInput, filter]);

  // Sorting is server-side: ordering only the rows already fetched answers
  // "largest trade on this page", which is not what a column header promises.
  const q = useQuery({
    queryKey: filter
      ? ["trades", limit, offset, sort.key, sort.dir, filter]
      : ["trades", limit, offset, sort.key, sort.dir],
    queryFn: () =>
      api<TradesPage>(
        `/portfolio/trades?limit=${limit}&offset=${offset}&sort=${sort.key}&dir=${sort.dir}` +
        (filter ? `&q=${encodeURIComponent(filter)}` : ""),
      ),
    placeholderData: keepPreviousData,
  });

  // One live region for the section, at a fixed position outside every branch.
  // A region that is unmounted is destroyed rather than updated, so nothing ever
  // announces that loading finished.
  const shell = (body: React.ReactNode) => (
    <>
      <LoadingStatus busy={q.isLoading} label="Carregando negociações…" />
      {body}
    </>
  );

  const page = q.data;
  const rows = page?.items ?? [];

  const filterField = (
    <label className="flex items-center gap-2 text-xs text-muted">
      <span className="sr-only">Filtrar negociações</span>
      <input
        type="search"
        value={filterInput}
        onChange={(e) => setFilterInput(e.target.value)}
        placeholder="Ativo ou corretora"
        aria-label="Filtrar negociações por ativo ou corretora"
        className="w-32 rounded-md border border-hairline bg-paper px-3 py-1 font-mono text-xs sm:w-44 text-ink placeholder:text-muted focus:border-navy focus:outline-none"
      />
    </label>
  );

  if (q.isLoading) {
    return shell(
      <Card title={TITLE}>
        <Skeleton className="h-64 w-full" />
      </Card>,
    );
  }

  if (q.isError) {
    return shell(
      <Card title={TITLE}>
        <EmptyState
          icon={<IconChart size={28} />}
          variant="error"
          title="Não foi possível carregar as negociações."
          hint="A conexão com a API falhou. Seu histórico continua importado."
          action={
            <RetryButton onClick={() => q.refetch()} />
          }
        />
      </Card>,
    );
  }

  if (!page || page.total === 0) {
    // A filter that matches nothing is not an empty book. Saying "importe o
    // extrato" to someone who has 3.117 trades and typed a typo would be the
    // page misreading its own state.
    return shell(
      <Card title={TITLE} action={filterField}>
        <EmptyState
          icon={<IconChart size={28} />}
          title={
            filter
              ? `Nenhuma negociação encontrada para "${filter}".`
              : "Nenhuma negociação importada."
          }
          hint={
            filter
              ? "Busque por um ativo (PETR4) ou por uma corretora."
              : "Importe o extrato de negociação para ver seu histórico de compras e vendas."
          }
        />
      </Card>,
    );
  }

  // Re-sorting reorders the whole book, so the page the user was on no longer
  // means anything — go back to the first page rather than landing them in the
  // middle of a new ordering.
  function toggle(key: SortKey) {
    setOffset(0);
    setSort((s) =>
      s.key === key
        ? { key, dir: s.dir === "asc" ? "desc" : "asc" }
        : { key, dir: defaultDir(key) },
    );
  }

  // Picking a field from the phone's list is not a toggle: re-picking the field
  // already selected must not reverse it, the way tapping its header would.
  function pick(key: SortKey) {
    if (key === sort.key) return;
    setOffset(0);
    setSort({ key, dir: defaultDir(key) });
  }

  const head = (key: SortKey, align: "left" | "right") => {
    const active = sort.key === key;
    const label = COLUMN_LABEL[key];
    return {
      label,
      header: (
        <SortHeader
          label={label}
          active={active}
          dir={sort.dir}
          fallback={defaultDir(key)}
          align={align}
          onClick={() => toggle(key)}
        />
      ),
      sortable: true as const,
      sort: active ? sort.dir : undefined,
    };
  };

  const columns: Column<Trade>[] = [
    {
      key: "date",
      ...head("date", "left"),
      // A stacked column of dd/mm/aaaa is a column of numbers, whatever its
      // alignment — proportional figures make the days jitter against each other.
      numeric: true,
      render: (t) => formatDate(t.date),
    },
    { key: "side", ...head("side", "left"), render: (t) => <SideBadge side={t.side} /> },
    {
      key: "ticker",
      ...head("ticker", "left"),
      // The card's headline: which trade this is, before its figures.
      mobile: "title",
      render: (t) => <TickerChip ticker={t.ticker} />,
    },
    {
      key: "market",
      ...head("market", "left"),
      render: (t) => <span className="text-muted">{t.market}</span>,
    },
    {
      // Users hold accounts across institutions and consolidating them is the
      // job — the API has said which corretora all along.
      key: "broker",
      ...head("broker", "left"),
      render: (t) => <span className="text-muted">{t.broker}</span>,
    },
    { key: "qty", ...head("qty", "right"), align: "right", render: (t) => formatQty(t.qty) },
    { key: "price", ...head("price", "right"), align: "right", render: (t) => <Money value={t.price} /> },
    { key: "amount", ...head("amount", "right"), align: "right", render: (t) => <Money value={t.amount} /> },
  ];

  const from = offset + 1;
  const to = Math.min(offset + limit, page.total);
  const pageNo = Math.floor(offset / limit) + 1;
  const pages = Math.max(1, Math.ceil(page.total / limit));

  return shell(
    <Card
      title={TITLE}
      action={
        <div className="flex flex-wrap items-center justify-end gap-3">
        {filterField}
        {/* The card layout has no column headers to click, so the sort moves
            here on a phone. Same server-side query, same reset to page 1. */}
        <SortControl
          options={SORT_OPTIONS}
          value={sort.key}
          dir={sort.dir}
          onPick={pick}
          onToggleDir={() => toggle(sort.key)}
        />
        <label className="flex items-center gap-2 text-xs text-muted">
          Por página
          <select
            value={limit}
            onChange={(e) => {
              setLimit(Number(e.target.value));
              setOffset(0);
            }}
            className="rounded-md border border-hairline bg-paper px-2 py-1 font-mono text-xs text-ink focus:border-navy focus:outline-none"
          >
            {PAGE_SIZES.map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </label>
        </div>
      }
    >
      <DataTable
        columns={columns}
        rows={rows}
        getRowKey={(t, i) => `${t.source_file}-${offset + i}`}
        label="Histórico de negociações"
      />
      <Pager
        page={pageNo}
        pages={pages}
        from={from}
        to={to}
        total={page.total}
        onPage={(p) => setOffset((p - 1) * limit)}
        className="mt-4"
      />
    </Card>,
  );
}
