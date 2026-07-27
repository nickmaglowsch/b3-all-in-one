"use client";

import { useState } from "react";
import Link from "next/link";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api";
import type { IndexMetrics, PortfolioIndex } from "@/lib/types";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { LoadingStatus, Skeleton } from "@/components/ui/Skeleton";
import { LineSeries, type Series } from "@/components/charts/LineSeries";
import { isFiltered, useDateRange } from "@/components/charts/DateRange";
import { rebaseRows, summarize } from "@/lib/chartRange";
import { SERIES } from "@/components/charts/theme";
import { IconChart } from "@/components/icons";
import { formatDate, formatPercent } from "@/lib/format";
import { INCOME_LABEL } from "./IncomeSection";
import type { IncomeRow } from "@/lib/types";

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
  const on = (t: string) => !selected || selected.includes(t);
  const lastOne = selected?.length === 1;

  const toggle = (t: string) => {
    const next = on(t)
      ? (selected ?? universe).filter((x) => x !== t)
      : [...(selected ?? []), t];
    // Back to everything → drop the filter entirely rather than enumerate it,
    // so the chart returns to the request it started on.
    onChange(next.length === universe.length ? null : next);
  };

  return (
    <fieldset className="mb-4">
      <legend className="font-mono text-[11px] font-medium uppercase tracking-[0.14em] text-muted">
        Ativos no cálculo
      </legend>
      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        {universe.map((t) => (
          <label
            key={t}
            className={`inline-flex cursor-pointer items-center gap-1.5 rounded-[4px] border px-2 py-1 font-mono text-[11px] font-medium uppercase leading-none tracking-wide transition-colors ${
              on(t)
                ? "border-navy/40 bg-navy/[0.06] text-ink"
                : "border-hairline bg-paper text-muted"
            } ${on(t) && lastOne ? "cursor-not-allowed" : "hover:border-navy/40"}`}
          >
            <input
              type="checkbox"
              checked={on(t)}
              disabled={on(t) && lastOne}
              onChange={() => toggle(t)}
              title={
                on(t) && lastOne
                  ? "Ao menos um ativo precisa ficar no cálculo"
                  : undefined
              }
              className="h-3 w-3 cursor-pointer accent-navy disabled:cursor-not-allowed"
            />
            {t}
          </label>
        ))}
        {selected && (
          <button
            type="button"
            onClick={() => onChange(null)}
            className="rounded-md border border-hairline px-2 py-1 text-xs font-medium text-ink transition-colors hover:bg-hairline/40"
          >
            Todos
          </button>
        )}
      </div>
    </fieldset>
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
      api<PortfolioIndex>(
        `/portfolio/index${selected ? `?tickers=${selected.join(",")}` : ""}`,
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
  const income = useQuery({
    queryKey: ["income"],
    queryFn: () => api<IncomeRow[]>("/portfolio/income"),
  });

  // Above every early return, where hooks have to live. The bounds only steer
  // the native picker, so an undefined pair while loading costs nothing.
  const { range, slice, control } = useDateRange({
    min: q.data?.start,
    max: q.data?.end,
  });

  // One live region for the section, at a fixed position outside every branch.
  // A region that is unmounted is destroyed rather than updated, so nothing ever
  // announces that loading finished.
  const shell = (body: React.ReactNode) => (
    <>
      <LoadingStatus busy={q.isLoading} label="Calculando rentabilidade…" />
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
    const status = q.error instanceof ApiError ? q.error.status : 0;

    // A paywall is not an outage. Blaming the quote base for a plan boundary
    // tells the user something false about the product and leaves them with
    // nothing to do about it.
    if (status === 402) {
      return (
        <Card title={TITLE}>
          <EmptyState
            icon={<IconChart size={28} />}
            variant="offer"
            title="Acompanhar sua carteira contra o IBOV e o CDI faz parte do plano Investidor."
            hint="Rentabilidade diária ponderada pelo tempo, com queda máxima e retorno anualizado — além de IRPF, TLH e simulação."
            action={
              <Link
                href="/assinatura"
                className="rounded-md bg-navy px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-navy/90"
              >
                Ver o plano Investidor
              </Link>
            }
          />
        </Card>
      );
    }

    // 422 is the honest "nothing to chart yet" state; anything else is a fault
    // the user can retry.
    return shell(
      <Card title={TITLE}>
        <EmptyState
          variant={status === 422 ? "empty" : "error"}
          icon={<IconChart size={28} />}
          title={
            status === 422
              ? "Ainda não há histórico para calcular a rentabilidade."
              : "Não foi possível calcular a rentabilidade agora."
          }
          hint={
            status === 422
              ? "Importe seus extratos de negociação para acompanhar sua carteira contra o IBOV e o CDI."
              : "A base de cotações pode estar indisponível. Tente novamente em instantes."
          }
          action={
            status === 422 ? undefined : (
              <button
                type="button"
                onClick={() => q.refetch()}
                className="rounded-md border border-hairline px-3 py-1.5 text-sm font-medium text-ink transition-colors hover:bg-hairline/40"
              >
                Tentar de novo
              </button>
            )
          }
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
  const metrics = isFiltered(range)
    ? Object.fromEntries(
        OVERLAYS.filter((k) => k in (windowed[0] ?? {})).map((k) => [
          k,
          summarize(windowed.map((r) => r[k] as number), windowed.length),
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

  // Everything the index could not see, said out loud (spec/03: surfaced, not
  // hidden). Silence here would read as "this is your whole portfolio".
  const caveats = [
    data.coverage.clamped &&
      `O gráfico começa em ${formatDate(data.coverage.plane_start)}, depois da sua primeira operação (${formatDate(data.coverage.first_trade)}), porque a base de cotações não vai mais atrás.`,
    data.excluded.markets.length > 0 &&
      `Fora do cálculo: ${data.excluded.markets.join(", ")}.`,
    data.excluded.unpriced_tickers.length > 0 &&
      `Sem cotação na base: ${data.excluded.unpriced_tickers.join(", ")}.`,
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
      <div className="mb-5 flex flex-wrap gap-x-10 gap-y-4">
        <Metric label={subset ? "Seleção" : "Carteira"} m={metrics.portfolio} />
        <Metric label="IBOV" m={metrics.ibov} />
        <Metric label="CDI" m={metrics.cdi} />
      </div>

      {data.universe.length > 1 && (
        <AssetFilter
          universe={data.universe}
          selected={selected}
          onChange={setSelected}
        />
      )}

      {windowed.length === 0 ? (
        <p className="py-16 text-center text-sm text-muted">
          Nenhum pregão no período selecionado.
        </p>
      ) : (
        <LineSeries
          data={windowed}
          xKey="date"
          series={series}
          height={280}
          valueFormat={asReturn}
          labelFormat={formatDate}
        />
      )}

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
        </div>
      )}
    </Card>,
  );
}
