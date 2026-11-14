"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { TradesPage } from "@/lib/types";
import { useActiveBook, useIncome, usePositions } from "@/lib/hooks";
import { PageHeader } from "@/components/ui/PageHeader";
import { Tabs, useTab } from "@/components/ui/Tabs";
import { Card } from "@/components/ui/Card";
import { Money } from "@/components/ui/Money";
import { PctDelta } from "@/components/ui/PctDelta";
import { EmptyState, RetryButton } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { IconWallet } from "@/components/icons";
import { formatDate, formatInt, formatMoney } from "@/lib/format";
import { PositionsTable } from "./PositionsTable";
import { IndexSection } from "./IndexSection";
import { TaxSection } from "./TaxSection";
import { IncomeSection } from "./IncomeSection";
import { TradesSection, TRADES_DEFAULTS } from "./TradesSection";
import { Overview } from "./Overview";

// Tab order is reading order: what the book is, what it owes this month, how it
// did, then the rows behind those answers.
const TAB_IDS = ["geral", "imposto", "rentabilidade", "posicoes", "proventos", "negociacoes"];

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
      <p className="font-mono text-[11px] font-medium uppercase tracking-[0.14em] text-muted">
        {label}
      </p>
      {/* A seven-figure BRL value is wider than any two-column tile a phone can
          offer — which is why the grid below is one column until `sm`, and why
          the type steps down here as well. "R$ 1.000.9 / 05,61" broken across
          two lines does not read as one number. */}
      <div className="mt-1.5 text-xl leading-none sm:text-2xl">{children}</div>
      {sub && <p className="mt-1.5 text-xs text-muted">{sub}</p>}
    </div>
  );
}

/** The client book's name for the tab strip — an alarm when the name is lost,
 *  nothing on the user's own book. */
function BookTag({ book }: { book: ReturnType<typeof useActiveBook> }) {
  if (book.state !== "client" && book.state !== "unknown") return null;
  const lost = book.state === "unknown";
  return (
    <span
      className={`whitespace-nowrap font-mono text-[11px] uppercase tracking-[0.14em] ${
        lost ? "text-status-error" : "text-muted"
      }`}
    >
      {lost ? "carteira de cliente — nome indisponível" : book.label}
    </span>
  );
}

/** A tab's row count, or no badge at all for none / not yet known. */
const countBadge = (n: number | undefined) => (n ? formatInt(n) : undefined);

// A value the API could not produce is an em dash, never a zero. R$ 0,00 for a
// book that has positions is the app inventing a number — the one thing it
// promises not to do.
function Unknown() {
  return <span className="font-mono text-muted">—</span>;
}

export default function PortfolioPage() {
  const book = useActiveBook();
  // Never label a client's money "Carteira". Unknown says so out loud; pending
  // says nothing at all, rather than flashing an alarm on every cold load.
  const eyebrow =
    book.state === "client"
      ? book.label
      : book.state === "unknown"
        ? "Carteira de cliente — nome indisponível"
        : book.state === "pending"
          ? undefined
          : "Carteira";

  const [tab, setTab] = useTab(TAB_IDS);
  const q = usePositions();

  // One request for the whole book — never one per row.
  const history = useQuery({
    queryKey: ["price-history"],
    queryFn: () => api<Record<string, number[]>>("/portfolio/price-history"),
  });

  // Positions, proventos and negociações are three independent outputs of one
  // import. A movimentação-only extract yields proventos and no positions; a
  // fully liquidated book yields years of trades and no positions — the ordinary
  // state of an IRPF user who sold out. Gating the page on positions alone told
  // both of them nothing was ever imported, and hid the data they do have.
  // Same key as IncomeSection, so React Query serves one request for both.
  const income = useIncome();
  // Deliberately the SAME key and URL as TradesSection's first page, so the gate
  // and the table share one request instead of hitting /portfolio/trades twice.
  // Keep these in sync with TRADES_DEFAULTS if that component's defaults change.
  const tradeProbe = useQuery({
    queryKey: ["trades", TRADES_DEFAULTS.limit, 0, TRADES_DEFAULTS.sort, TRADES_DEFAULTS.dir],
    queryFn: () =>
      api<TradesPage>(
        `/portfolio/trades?limit=${TRADES_DEFAULTS.limit}&offset=0&sort=${TRADES_DEFAULTS.sort}&dir=${TRADES_DEFAULTS.dir}`,
      ),
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
  // The price engine can fail wholesale (routes.py returns every position
  // unpriced). Then market value is unknown, not zero, and the tiles say so.
  const nothingPriced = positions.length > 0 && priced.length === 0;

  // Renda fixa carries no live quote: its mark is the per-unit price B3 printed
  // on the monthly posição report, so those rows are dated and the rest are
  // "latest close". Both totals sit in the same tile — the caption says which is
  // which rather than letting a month-old number pass for today's.
  const dated = priced.filter((p) => p.price_date);
  const oldestMark = dated
    .map((p) => p.price_date as string)
    .sort()[0];

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

  const hasPositions = positions.length > 0;
  const hasAnyData =
    hasPositions ||
    (income.data?.length ?? 0) > 0 ||
    (tradeProbe.data?.total ?? 0) > 0;

  // The income and trades probes exist for ONE question: "is this book really
  // empty, or did it just sell everything / import movimentação only?" So they
  // are consulted only when positions came back empty. Blocking the page on them
  // otherwise meant one slow or failing proventos call blanked a portfolio that
  // had already loaded — the same over-claiming this gate was written to stop,
  // pointed the other way.
  const needsEmptinessProof = !q.isLoading && !q.isError && !hasPositions;
  const stillCounting =
    q.isLoading ||
    (needsEmptinessProof && (income.isLoading || tradeProbe.isLoading));
  // A probe that failed cannot prove emptiness either: `?? 0` on a rejected
  // query would coalesce an outage into "you imported nothing".
  const anyFailed =
    q.isError || (needsEmptinessProof && (income.isError || tradeProbe.isError));

  return (
    <>
      <PageHeader eyebrow={eyebrow} title="Portfólio" />

      {stillCounting ? (
        <div className="space-y-6">
          <Skeleton className="h-24 w-full" label="Carregando sua carteira…" />
          <Skeleton className="h-64 w-full" />
          <Skeleton className="h-96 w-full" />
        </div>
      ) : anyFailed ? (
        // Distinct from the empty state on purpose. Falling through to "nenhum
        // extrato importado" tells someone with a full book that their data is
        // gone, and tells an advisor a client's book is empty when it is not.
        <EmptyState
          icon={<IconWallet size={28} />}
          variant="error"
          title="Não foi possível carregar sua carteira."
          hint="A conexão com a API falhou. Nada foi perdido — seus extratos continuam importados."
          action={
            <RetryButton
              onClick={() => {
                if (q.isError) q.refetch();
                if (income.isError) income.refetch();
                if (tradeProbe.isError) tradeProbe.refetch();
              }}
            />
          }
        />
      ) : !hasAnyData ? (
        <EmptyState
          icon={<IconWallet size={28} />}
          title="Nenhum extrato importado — envie seus arquivos da B3."
          hint="Depois de importar, suas posições, negociações e proventos aparecem aqui."
          action={
            <Link
              href="/imports"
              className="rounded-md bg-navy px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-navy/90"
            >
              Importar extrato
            </Link>
          }
        />
      ) : (
        <>
            {hasPositions && (
            <section
              aria-label="Resumo da carteira"
              className="mb-6"
            >
              <div className="grid gap-3 sm:grid-cols-2 md:grid-cols-4">
                <Stat
                  label="Valor de mercado"
                  sub={
                    nothingPriced
                      ? "cotações indisponíveis — mostrando apenas custo"
                      : priced.length < positions.length
                        ? `${formatInt(priced.length)} de ${formatInt(positions.length)} posições precificadas`
                        : "última cotação disponível"
                  }
                >
                  {nothingPriced ? <Unknown /> : <Money value={marketValue} className="text-xl sm:text-2xl" />}
                </Stat>
                {/* "Never invent a number" has a mirror: never withhold a known
                    one. Cost does not depend on prices, so the price plane being
                    down is exactly when this tile matters most — blanking it
                    while restating the figure in a 12px caption below was the
                    tile failing in the one scenario it exists for. */}
                <Stat
                  label={nothingPriced ? "Custo total" : "Custo (precificado)"}
                  sub={
                    nothingPriced
                      ? "custo de todas as posições"
                      : `custo total da carteira ${formatMoney(totalCost)}`
                  }
                >
                  <Money
                    value={nothingPriced ? totalCost : pricedCost}
                    className="text-xl sm:text-2xl"
                  />
                </Stat>
                <Stat
                  label="Retorno simples"
                  sub="sobre o custo das posições precificadas"
                >
                  {nothingPriced ? (
                    <Unknown />
                  ) : (
                    <div className="flex flex-wrap items-baseline gap-2">
                      <Money value={pnl} sign className="text-xl sm:text-2xl" />
                      <PctDelta value={pnlPct} className="text-sm" />
                    </div>
                  )}
                </Stat>
                <Stat label="Posições" sub={`${formatInt(markets.length)} mercados`}>
                  {/* Mono + tnum like every other figure on the row: a headline
                      number that changes typeface between tiles reads as a
                      different kind of quantity, which it is not. */}
                  <span className="font-mono tnum tabular-nums text-ink">
                    {formatInt(positions.length)}
                  </span>
                </Stat>
              </div>

              {dated.length > 0 && (
                <p className="mt-3 text-xs text-muted">
                  {formatInt(dated.length)} posições de renda fixa entram pelo
                  preço que a B3 publicou no relatório de posição, corrigido até
                  a data ao lado de cada uma pelo indexador que você cadastrou —
                  sem indexador, fica no preço do relatório
                  {oldestMark ? ` (o mais antigo é de ${formatDate(oldestMark)})` : ""}.
                  Não há cotação diária para esse papel.
                </p>
              )}

              <p className="mt-3 text-xs text-muted">
                O retorno simples compara o valor de mercado com o custo de hoje. A
                curva em{" "}
                <button
                  type="button"
                  onClick={() => setTab("rentabilidade")}
                  className="text-navy hover:underline"
                >
                  Rentabilidade
                </button>{" "}
                usa TWR e responde outra pergunta — as duas não batem, e não
                deveriam.
              </p>
            </section>
            )}


          <Tabs
            label="Seções do portfólio"
            active={tab}
            onChange={setTab}
            // The client's name rides the sticky strip, so it survives the whole
            // page and lands in a screenshot of any tab — the promise the
            // empresarial module is sold on.
            trailing={<BookTag book={book} />}
            tabs={[
              {
                id: "geral",
                label: "Visão geral",
                content: (
                  <Overview
                    markets={hasPositions ? markets : []}
                    total={allocTotal}
                    priced={priced.length}
                    count={positions.length}
                  />
                ),
              },
              // Second, deliberately: the isenção headroom changes whether you
              // should sell anything this month, so it reads before the
              // positions. Never gated on hasPositions — a book liquidated this
              // month has no positions and the largest DARF it will ever owe.
              { id: "imposto", label: "Situação fiscal", content: <TaxSection /> },
              { id: "rentabilidade", label: "Rentabilidade", content: <IndexSection /> },
              {
                id: "posicoes",
                label: "Posições",
                badge: countBadge(positions.length),
                content: (
                  <Card
                    title="Posições"
                    action={
                      hasPositions ? (
                        <Link
                          href="/tlh"
                          className="rounded-md border border-hairline px-2.5 py-1 text-xs font-medium text-navy transition-colors hover:bg-hairline/40"
                        >
                          Colher prejuízo (TLH)
                        </Link>
                      ) : undefined
                    }
                  >
                    {hasPositions ? (
                      <PositionsTable
                        positions={positions}
                        history={history.data}
                        historyFailed={history.isError}
                        historyLoading={history.isLoading}
                      />
                    ) : (
                      // Zero positions with data elsewhere is a real, ordinary state:
                      // the book was liquidated, or only a movimentação extract was
                      // imported. It is not "nothing was imported".
                      <EmptyState
                        icon={<IconWallet size={28} />}
                        title="Nenhuma posição em aberto."
                        hint="Você vendeu tudo, ou importou apenas o extrato de movimentação. Seus proventos e negociações seguem nas abas ao lado."
                      />
                    )}
                  </Card>
                ),
              },
              { id: "proventos", label: "Proventos", content: <IncomeSection /> },
              {
                id: "negociacoes",
                label: "Negociações",
                badge: countBadge(tradeProbe.data?.total),
                content: <TradesSection />,
              },
            ]}
          />
        </>
      )}
    </>
  );
}
