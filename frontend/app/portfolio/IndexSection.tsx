"use client";

import { useEffect, useRef, useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { withQuery } from "@/lib/url";
import type { IndexMetrics, PortfolioIndex } from "@/lib/types";
import { Card } from "@/components/ui/Card";
import { IndexError } from "./IndexError";
import { LoadingStatus, Skeleton } from "@/components/ui/Skeleton";
import { LineSeries, type Series } from "@/components/charts/LineSeries";
import { isFiltered, useDateRange } from "@/components/charts/DateRange";
import { rebaseRows, summarize, yearsBetween } from "@/lib/chartRange";
import { SERIES } from "@/components/charts/theme";
import { formatDate, formatPercent } from "@/lib/format";
import { useIncome } from "@/lib/hooks";
import { INCOME_LABEL } from "./IncomeSection";
import { RendaFixaTermsForm } from "./RendaFixaTermsForm";

const TITLE = "Rentabilidade vs IBOV e CDI";

// The index is base 100 and unitless — show it as a return off that base, which
// is what the user actually reads ("+23%"), not "123 pontos".
const asReturn = (v: number) => formatPercent(v / 100 - 1);

const OVERLAYS = ["portfolio", "ibov", "cdi"] as const;

/**
 * Which holdings the line is built from. `null` is the whole book — the
 * unfiltered request, so the common case shares one cache entry instead of
 * spelling out every ticker.
 *
 * The last remaining asset cannot be switched off: an empty selection has no
 * index to draw and the API answers 422, which is a correct response to a state
 * the UI should never have let the user reach.
 */
function AssetFilter({
  universe,
  selected,
  onChange,
}: {
  universe: string[];
  selected: string[] | null;
  onChange: (next: string[] | null) => void;
}) {
  const [query, setQuery] = useState("");
  const box = useRef<HTMLDetailsElement>(null);

  // `<details>` carries the open state, the disclosure semantics and the
  // keyboard activation on its own; what it does not do is close when you look
  // away, and a panel that only closes via its own trigger is not a dropdown.
  useEffect(() => {
    const close = (e: Event) => {
      const el = box.current;
      if (!el?.open) return;
      if (e.type === "keydown" && (e as KeyboardEvent).key !== "Escape") return;
      if (e.type === "pointerdown" && el.contains(e.target as Node)) return;
      el.open = false;
      // Escape should hand focus back to the trigger rather than to nothing.
      if (e.type === "keydown") el.querySelector("summary")?.focus();
    };
    document.addEventListener("pointerdown", close);
    document.addEventListener("keydown", close);
    return () => {
      document.removeEventListener("pointerdown", close);
      document.removeEventListener("keydown", close);
    };
  }, []);

  const on = (t: string) => !selected || selected.includes(t);
  const lastOne = selected?.length === 1;
  const q = query.trim().toUpperCase();
  const shown = q ? universe.filter((t) => t.includes(q)) : universe;

  const toggle = (t: string) => {
    const next = on(t)
      ? (selected ?? universe).filter((x) => x !== t)
      : [...(selected ?? []), t].sort();
    // Sorted, because the selection is a set but the query key is a string:
    // re-checking A last gives ["B","A"] where reaching the same two directly
    // gives ["A","B"], and that is two cache entries and two round trips for
    // one answer (the API sorts, so the responses are byte-identical).
    //
    // Back to everything → drop the filter entirely rather than enumerate it,
    // so the chart returns to the request it started on.
    onChange(next.length === universe.length ? null : next);
  };

  return (
    <div className="mb-4">
      <p className="font-mono text-[11px] font-medium uppercase tracking-[0.14em] text-muted">
        Ativos no cálculo
      </p>
      <details
        ref={box}
        // On close, wherever it came from — Escape, a click outside, or the
        // summary itself, which the handler above deliberately ignores. A stale
        // query is a lie about the book: reopen after searching "wege" and the
        // list shows one ticker, which reads as "this is all you hold".
        onToggle={(e) => !e.currentTarget.open && setQuery("")}
        className="relative mt-2 inline-block"
      >
        <summary
          // The marker is replaced by the count + caret below; `list-none` alone
          // leaves Safari's own triangle behind, hence the ::-webkit rule.
          className="flex w-64 cursor-pointer list-none items-center justify-between gap-2 rounded-md border border-hairline bg-paper px-3 py-1.5 text-sm text-ink transition-colors hover:border-navy/40 [&::-webkit-details-marker]:hidden"
        >
          <span>
            {selected
              ? `${selected.length} de ${universe.length} ativos`
              : `Todos os ${universe.length} ativos`}
          </span>
          <span aria-hidden className="text-muted">
            ▾
          </span>
        </summary>

        <div className="absolute left-0 z-20 mt-1 w-64 rounded-md border border-hairline bg-panel p-2 shadow-lg">
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Buscar ativo"
            aria-label="Buscar ativo na lista"
            className="w-full rounded-md border border-hairline bg-paper px-2 py-1 font-mono text-xs text-ink placeholder:text-muted focus:border-navy focus:outline-none"
          />

          <fieldset className="mt-2 max-h-56 overflow-y-auto">
            <legend className="sr-only">Ativos incluídos no cálculo</legend>
            {shown.length === 0 ? (
              <p className="px-1 py-3 text-center text-xs text-muted">
                Nenhum ativo com “{query.trim()}”.
              </p>
            ) : (
              shown.map((t) => (
                <label
                  key={t}
                  className={`flex items-center gap-2 rounded px-1 py-1 font-mono text-xs uppercase tracking-wide ${
                    on(t) && lastOne
                      ? "cursor-not-allowed text-muted"
                      : "cursor-pointer text-ink hover:bg-hairline/40"
                  }`}
                  title={
                    on(t) && lastOne
                      ? "Ao menos um ativo precisa ficar no cálculo"
                      : undefined
                  }
                >
                  <input
                    type="checkbox"
                    checked={on(t)}
                    disabled={on(t) && lastOne}
                    onChange={() => toggle(t)}
                    className="h-3.5 w-3.5 cursor-pointer accent-navy disabled:cursor-not-allowed"
                  />
                  {t}
                </label>
              ))
            )}
          </fieldset>

          {/* Only "todos" — there is deliberately no "limpar". An empty
              selection has no index to draw, so offering it would be offering
              a 422. Same reason the last checkbox above disables itself. */}
          {selected && (
            <button
              type="button"
              onClick={() => onChange(null)}
              className="mt-2 w-full rounded-md border border-hairline px-2 py-1 text-xs font-medium text-ink transition-colors hover:bg-hairline/40"
            >
              Selecionar todos
            </button>
          )}
        </div>
      </details>
    </div>
  );
}

function Metric({ label, m }: { label: string; m?: IndexMetrics }) {
  if (!m || m.total_return_pct === null) return null;
  return (
    <div>
      <p className="font-mono text-[11px] font-medium uppercase tracking-[0.14em] text-muted">
        {label}
      </p>
      <p className="mt-1 font-mono text-lg tabular-nums text-ink">
        {formatPercent(m.total_return_pct / 100)}
      </p>
      <p className="mt-0.5 text-xs text-muted">
        {m.ann_return_pct !== null
          ? `${formatPercent(m.ann_return_pct / 100)} a.a.`
          : "período < 1 ano"}
        {m.max_dd_pct !== null && ` · queda máx. ${formatPercent(m.max_dd_pct / 100)}`}
      </p>
    </div>
  );
}

export function IndexSection() {
  // null = the whole book. Only the *asset* filter goes to the API: a subset's
  // index has to be re-chained from that subset's holdings and cash, which the
  // one aggregate line in the response cannot be decomposed back into. The date
  // window is the opposite — the ratio between two points of a chained TWR is
  // exactly the return between them, so it is a slice of what is already here.
  const [selected, setSelected] = useState<string[] | null>(null);

  const q = useQuery({
    queryKey: ["portfolio-index", selected?.join(",") ?? null],
    queryFn: () =>
      // This fires on page load, so a 402 must not throw a dialog over a page the
      // user has not read yet. The offer is rendered inline below instead.
      // Encoded: renda fixa names are free text, and a raw "+" ("TESOURO
      // IPCA+ 2029") reaches the server as a space and drops the holding.
      api<PortfolioIndex>(
        withQuery("/portfolio/index", { tickers: selected?.join(",") }),
        {},
        { paywallDialog: false },
      ),
    retry: false, // 422 (no trades) and 402 (paywall) are answers, not blips
    // Toggling an asset refetches. Without this the chart unmounts to a skeleton
    // on every click, so comparing "with" against "without" means holding the
    // old shape in your head across a blank frame.
    placeholderData: keepPreviousData,
  });

  // Same key as IncomeSection and the page gate, so this is a cache read, not a
  // fourth request. Used only to decide whether the "fora do retorno" caveat
  // describes a gap this book actually has.
  const income = useIncome();

  // Above every early return, where hooks have to live. The bounds only steer
  // the native picker, so an undefined pair while loading costs nothing.
  const { range, slice, control } = useDateRange({
    min: q.data?.start,
    max: q.data?.end,
  });

  // True while a holding toggle is in flight. keepPreviousData means the query
  // reports success throughout the refetch, so isLoading is false and every
  // figure on screen belongs to the *previous* selection — this is the only
  // signal that says so.
  const stale = q.isPlaceholderData || (q.isFetching && !q.isLoading);

  // One live region for the section, at a fixed position outside every branch.
  // A region that is unmounted is destroyed rather than updated, so nothing ever
  // announces that loading finished.
  const shell = (body: React.ReactNode) => (
    <>
      <LoadingStatus
        busy={q.isLoading || stale}
        label="Calculando rentabilidade…"
      />
      {body}
    </>
  );

  if (q.isLoading) {
    return shell(
      <Card title={TITLE}>
        <Skeleton className="h-64 w-full" />
      </Card>,
    );
  }

  if (q.isError || !q.data) {
    return shell(
      <Card title={TITLE}>
        <IndexError
          error={q.error}
          onRetry={() => q.refetch()}
          onReset={selected === null ? undefined : () => setSelected(null)}
        />
      </Card>,
    );
  }

  const data = q.data;
  const hasBench = !data.excluded.benchmarks;
  const subset = data.tickers.length < data.universe.length;
  const series: Series[] = [
    { key: "portfolio", label: subset ? "Seleção" : "Carteira", color: SERIES[0] },
    ...(hasBench
      ? [
          { key: "ibov", label: "IBOV", color: SERIES[2] },
          { key: "cdi", label: "CDI", color: SERIES[1] },
        ]
      : []),
  ];

  // A narrowed window is a different question, so it gets different numbers.
  // Rebasing restarts every line at the window's own first day — otherwise the
  // chart reads "+21%" over a stretch that gained 10%, and the figures above it
  // would summarise a period nobody is looking at.
  const windowed = isFiltered(range)
    ? rebaseRows(slice(data.series))
    : data.series;
  // Annualised over the window's own calendar span, which is what the API does
  // with the full one — a session count would put a calendar year at 0.99 and
  // caption twelve months "período < 1 ano".
  const span = windowed.length
    ? yearsBetween(windowed[0].date, windowed[windowed.length - 1].date)
    : 0;
  const metrics = isFiltered(range)
    ? Object.fromEntries(
        OVERLAYS.filter((k) => k in (windowed[0] ?? {})).map((k) => [
          k,
          summarize(windowed.map((r) => r[k] as number), span),
        ]),
      )
    : data.metrics;

  // Categories the engine excludes AND this book actually holds cash in, in the
  // same pt-BR labels the Proventos card uses.
  const held = new Set(
    (income.data ?? []).filter((r) => r.total !== 0).map((r) => r.event_category),
  );
  const excludedHeld = data.excluded.categories
    .filter((c) => held.has(c))
    .map((c) => INCOME_LABEL[c] ?? c);

  // Renda fixa contracts the index could not draw. The ones missing an indexer
  // are the only ones the user can fix from here — a custody freeze or an
  // unmodelled amortização is not something a form solves.
  const blockedRf = data.excluded.renda_fixa ?? [];
  const needsIndexer = blockedRf
    .filter((r) => r.code === "sem_indexador")
    .map((r) => r.ticker);

  // Everything the index could not see, said out loud (spec/03: surfaced, not
  // hidden). Silence here would read as "this is your whole portfolio".
  // Two of these describe where the *chart* starts and ends, so a date filter
  // makes them false — the clamp still says "começa em 09/01/2024" under a
  // chart the user just moved to 2025. They are facts about the series the API
  // returned, so they stand down while a window is cutting it, and the window's
  // own bounds are on screen in the picker above.
  const wholeSpan = !isFiltered(range);
  const caveats = [
    wholeSpan &&
      data.coverage.clamped &&
      `O gráfico começa em ${formatDate(data.coverage.plane_start)}, depois da sua primeira operação (${formatDate(data.coverage.first_trade)}), porque a base de cotações não vai mais atrás.`,
    data.excluded.markets.length > 0 &&
      `Fora do cálculo: ${data.excluded.markets.join(", ")}.`,
    data.excluded.unpriced_tickers.length > 0 &&
      `Sem cotação na base: ${data.excluded.unpriced_tickers.join(", ")}.`,
    // One line per contract, with its own reason: a CDB ainda em carteira and
    // one preso numa retirada de custódia are missing for opposite reasons, and
    // only one of them is something the user can do anything about.
    ...blockedRf.map(({ ticker, reason }) => `${ticker}: ${reason}.`),
    wholeSpan &&
      data.coverage.trades_after_plane > 0 &&
      `${data.coverage.trades_after_plane} operação(ões) posterior(es) à última cotação (${formatDate(data.end)}) foram marcadas nesse preço.`,
    // EXCLUDED_CATEGORIES is a constant in the engine, so this list is never
    // empty — printing it unconditionally put permanent boilerplate inside the
    // block whose whole value is that everything in it is a real, specific gap.
    // It earns its place only when the book actually holds those categories,
    // and it says so in the same pt-BR labels the Proventos card uses.
    excludedHeld.length > 0 &&
      `Fora do retorno: ${excludedHeld.join(", ")} — aparecem em Proventos como caixa, mas não são retorno da carteira.`,
    !hasBench && "IBOV e CDI indisponíveis nesta execução.",
    // A filter the user set is still something the line does not contain, and
    // this block is where the chart says what it is missing. The chips show the
    // state, but the title still promises "sua carteira" — so it is spelled out.
    subset &&
      `Você excluiu ${data.universe.filter((t) => !data.tickers.includes(t)).join(", ")} — esta linha é uma seleção de ${data.tickers.length} de ${data.universe.length} ativos, não a carteira inteira.`,
  ].filter(Boolean) as string[];

  return shell(
    <Card title={TITLE} action={control}>
      {/* Outside the dimmed block below: the control the user is operating has
          to stay crisp and clickable while the answer to the last click loads. */}
      {data.universe.length > 1 && (
        <AssetFilter
          universe={data.universe}
          selected={selected}
          onChange={setSelected}
        />
      )}

      {/* Everything below is `data`, which under keepPreviousData is the
          *previous* selection until the refetch lands — so while it is stale the
          dropdown already names the new holdings while the line, the labels and
          the caveat still describe the old ones. Dimmed and marked busy rather
          than left to look settled; `stale` is what makes that visible, since
          the query never re-enters isLoading once it has data. */}
      <div aria-busy={stale || undefined} className={stale ? "opacity-60 transition-opacity" : undefined}>
        <div className="mb-5 flex flex-wrap gap-x-10 gap-y-4">
          <Metric label={subset ? "Seleção" : "Carteira"} m={metrics.portfolio} />
          <Metric label="IBOV" m={metrics.ibov} />
          <Metric label="CDI" m={metrics.cdi} />
        </div>

        {/* An empty window is LineSeries' own business now — it answers for all
            four charts rather than each of them growing its own branch. */}
        <LineSeries
          data={windowed}
          xKey="date"
          series={series}
          height={280}
          valueFormat={asReturn}
          labelFormat={formatDate}
        />
      </div>

      <p className="mt-4 text-sm text-muted">
        Retorno ponderado pelo tempo (TWR): aportes e retiradas mudam o saldo, não
        a linha. Proventos entram como retorno no dia em que caem.
      </p>

      {/* What the index could not see, at the weight of what it could. Reporting
          the gaps as loudly as the results is a product commitment; rendering
          them at 12px muted under the fold was the opposite of honouring it.
          Real list markers, not a typed "·" — the bullet is the list's job. */}
      {caveats.length > 0 && (
        <div className="mt-4 rounded-md border border-hairline bg-paper p-4">
          <p className="font-mono text-[11px] font-medium uppercase tracking-[0.14em] text-muted">
            O que ficou de fora
          </p>
          <ul className="mt-2 list-disc space-y-1.5 pl-5 text-sm text-ink marker:text-axis">
            {caveats.map((c) => (
              <li key={c}>{c}</li>
            ))}
          </ul>
          {/* The one gap in this block the user can close from here. Rendered
              beside the contracts it names, not on a settings page they would
              have to be told about. */}
          <RendaFixaTermsForm tickers={needsIndexer} />
        </div>
      )}
    </Card>,
  );
}
