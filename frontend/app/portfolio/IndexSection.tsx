"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api";
import type { IndexMetrics, PortfolioIndex } from "@/lib/types";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { LineSeries, type Series } from "@/components/charts/LineSeries";
import { SERIES } from "@/components/charts/theme";
import { IconChart } from "@/components/icons";
import { formatDate, formatPercent } from "@/lib/format";

const TITLE = "Rentabilidade vs IBOV e CDI";

// The index is base 100 and unitless — show it as a return off that base, which
// is what the user actually reads ("+23%"), not "123 pontos".
const asReturn = (v: number) => formatPercent(v / 100 - 1);

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
  const q = useQuery({
    queryKey: ["portfolio-index"],
    queryFn: () =>
      // This fires on page load, so a 402 must not throw a dialog over a page the
      // user has not read yet. The offer is rendered inline below instead.
      api<PortfolioIndex>("/portfolio/index", {}, { paywallDialog: false }),
    retry: false, // 422 (no trades) and 402 (paywall) are answers, not blips
  });

  if (q.isLoading) {
    return (
      <Card title={TITLE}>
        <Skeleton className="h-64 w-full" label="Calculando rentabilidade…" />
      </Card>
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
    return (
      <Card title={TITLE}>
        <EmptyState
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
      </Card>
    );
  }

  const data = q.data;
  const hasBench = !data.excluded.benchmarks;
  const series: Series[] = [
    { key: "portfolio", label: "Carteira", color: SERIES[0] },
    ...(hasBench
      ? [
          { key: "ibov", label: "IBOV", color: SERIES[2] },
          { key: "cdi", label: "CDI", color: SERIES[1] },
        ]
      : []),
  ];

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
    data.excluded.categories.length > 0 &&
      `Categorias de caixa fora do retorno: ${data.excluded.categories.join(", ")} — aparecem em Proventos, mas não são retorno da carteira.`,
    !hasBench && "IBOV e CDI indisponíveis nesta execução.",
  ].filter(Boolean) as string[];

  return (
    <Card title={TITLE}>
      <div className="mb-5 flex flex-wrap gap-x-10 gap-y-4">
        <Metric label="Carteira" m={data.metrics.portfolio} />
        <Metric label="IBOV" m={data.metrics.ibov} />
        <Metric label="CDI" m={data.metrics.cdi} />
      </div>

      <LineSeries
        data={data.series}
        xKey="date"
        series={series}
        height={280}
        valueFormat={asReturn}
        labelFormat={formatDate}
      />

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
    </Card>
  );
}
