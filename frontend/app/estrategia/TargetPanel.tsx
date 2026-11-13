"use client";

// "Alvo x atual": the combined target against the priced book. Split from
// page.tsx, which sits at the module-size gate.
//
// The B3 book is compared with its own share of the target; a sleeve held off
// B3 (US, crypto) has no known position, so it is listed after the B3 rows with
// its weight in the whole target and no drift (platform rebalance_plan.py).
import { Card } from "@/components/ui/Card";
import { DataTable, type Column } from "@/components/ui/DataTable";
import { TickerChip } from "@/components/ui/TickerChip";
import { Money } from "@/components/ui/Money";
import { PctDelta } from "@/components/ui/PctDelta";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { DivergingBars } from "@/components/charts/DivergingBars";
import { IconCompass } from "@/components/icons";
import { errorText } from "@/lib/api";
import { compactBRL, formatDate, formatPercent } from "@/lib/format";
import type { DriftRow } from "@/lib/types";
import { useTarget } from "./lib";

const DRIFT_TOP = 12;

export function TargetPanel() {
  const target = useTarget();

  if (target.isLoading) return <Skeleton className="h-72 w-full" />;

  // 409 = sleeves exist but target can't be built (shouldn't happen here, but be honest).
  if (target.error) {
    const msg = errorText(target.error, "Falha ao calcular o alvo.");
    return (
      <Card title="Alvo x atual">
        <EmptyState
          icon={<IconCompass size={28} />}
          title="Não foi possível montar a carteira-alvo."
          hint={msg}
        />
      </Card>
    );
  }

  const t = target.data!;
  // The B3 book's rows drift; a sleeve held off B3 has no known position, so it
  // is listed after them, with no bar.
  const sorted = t.drift
    .filter((d) => !d.outside_b3)
    .sort((a, b) => Math.abs(b.drift_brl ?? 0) - Math.abs(a.drift_brl ?? 0));
  const outside = t.drift.filter((d) => d.outside_b3);
  const top = sorted.slice(0, DRIFT_TOP);
  const rest = sorted.slice(DRIFT_TOP);
  const restSum = rest.reduce((a, r) => a + (r.drift_brl ?? 0), 0);
  const bars = [
    ...top.map((d) => ({ label: d.ticker, value: d.drift_brl ?? 0 })),
    ...(rest.length ? [{ label: "outros", value: restSum }] : []),
  ];

  const columns: Column<DriftRow>[] = [
    { key: "ticker", header: "Ativo", mobile: "title", render: (r) => <TickerChip ticker={r.ticker} /> },
    {
      key: "target_w",
      header: "Peso alvo",
      align: "right",
      // Two frames, said: a B3 row is % of the B3 book, an off-B3 one % of it all.
      render: (r) =>
        r.outside_b3 ? `${formatPercent(r.target_w)} do alvo total` : formatPercent(r.target_w),
    },
    {
      key: "current_w",
      header: "Peso atual",
      align: "right",
      render: (r) => (r.current_w === null ? "fora da B3" : formatPercent(r.current_w)),
    },
    {
      key: "drift_w",
      header: "Desvio",
      align: "right",
      render: (r) => (r.drift_w === null ? "—" : <PctDelta value={r.drift_w} />),
    },
    {
      key: "drift_brl",
      header: "Desvio (R$)",
      align: "right",
      render: (r) => (r.drift_brl === null ? "—" : <Money value={r.drift_brl} sign />),
    },
  ];

  return (
    <Card
      title="Alvo x atual"
      action={
        <span className="font-mono text-[11px] text-muted">
          {formatDate(t.as_of)} · patrimônio {compactBRL(t.nav)} · caixa-alvo{" "}
          {formatPercent(t.cash_weight_target)}
        </span>
      }
    >
      <div className="space-y-4">
        <Warnings warnings={t.warnings} />
        <div>
          <p className="mb-2 text-xs text-muted">
            Desvio por ativo (positivo = comprar para atingir o alvo)
          </p>
          <DivergingBars data={bars} valueFormat={compactBRL} height={Math.max(200, bars.length * 26)} />
        </div>
        <DataTable columns={columns} rows={[...sorted, ...outside]} getRowKey={(r) => r.ticker} label="Desvio por ativo" />
        {outside.length > 0 && (
          <p className="text-[11px] text-muted">
            Peso alvo e atual dos ativos da B3: % da carteira da B3, comparada com a parte
            dela no alvo. Dos ativos fora da B3: % do alvo total — a posição deles não vem dos
            seus extratos.
          </p>
        )}
      </div>
    </Card>
  );
}

// Soft warnings the API echoes on a save (e.g. needs-funds).
export function Warnings({ warnings }: { warnings?: string[] }) {
  return warnings?.map((w, i) => (
    <p key={i} className="text-xs text-series-4" role="status">
      {w}
    </p>
  ));
}
