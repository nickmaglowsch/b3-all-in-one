"use client";

// The plan a POST /tlh/plans returns, one card per bucket. Split from page.tsx so
// the page stays under the module-size gate (scripts/quality_gates.py); shared by
// "Simular vendas" (sells proposed) and "Reduzir IR do mês" (sales already made).
import { formatMoney, formatQty } from "@/lib/format";
import { Card } from "@/components/ui/Card";
import { Money } from "@/components/ui/Money";
import { TickerChip } from "@/components/ui/TickerChip";
import { DataTable, type Column } from "@/components/ui/DataTable";
import { IconAlert } from "@/components/icons";
import { Metric, type Bucket, type HarvestRow, type TlhPlan } from "./situation";

// ── Plan result ───────────────────────────────────────────────────────────────
function statusTone(status: string): string {
  if (status.startsWith("TLH recomendado"))
    return "border-status-done/30 bg-status-done/10 text-status-done";
  if (status.includes("sem candidatos"))
    return "border-status-processing/30 bg-status-processing/10 text-status-processing";
  return "border-hairline bg-paper text-muted";
}

export function PlanResult({
  plan,
  title = "Plano",
  empty = "Nenhuma venda classificada — verifique os ativos informados.",
}: {
  plan: TlhPlan;
  title?: string;
  /** tlh_plan skips a bucket with no proposed sells and no sales this month. */
  empty?: string;
}) {
  const classes = Object.keys(plan.buckets);
  return (
    <section className="space-y-4">
      <div className="flex items-center gap-2">
        <h2 className="font-display text-lg font-bold text-ink">
          {title} · {plan.month}
        </h2>
      </div>
      {classes.length === 0 ? (
        <p className="text-sm text-muted">{empty}</p>
      ) : (
        classes.map((cls) => (
          <BucketCard
            key={cls}
            cls={cls}
            bucket={plan.buckets[cls]}
            rate={plan.rates[cls]}
            limit={plan.isencao_limit}
          />
        ))
      )}
    </section>
  );
}

function BucketCard({
  cls,
  bucket,
  rate,
  limit,
}: {
  cls: string;
  bucket: Bucket;
  rate: number;
  limit: number;
}) {
  const harvestCols: Column<HarvestRow>[] = [
    { key: "ticker", header: "Ticker", mobile: "title", render: (r) => <TickerChip ticker={r.ticker} /> },
    {
      key: "qty",
      header: "Qtd",
      align: "right",
      render: (r) => (
        <>
          {formatQty(r.qty)}
          {r.partial && <span className="ml-1 text-[10px] text-muted">parcial</span>}
        </>
      ),
    },
    { key: "price", header: "Preço", align: "right", render: (r) => formatMoney(r.price) },
    {
      key: "loss",
      header: "Prejuízo realizado",
      align: "right",
      render: (r) => <Money value={r.realized_loss} sign />,
    },
  ];

  return (
    <Card
      title={
        <span className="flex items-center gap-2">
          {cls}
          <span
            className={`rounded-full border px-2 py-0.5 text-xs font-medium ${statusTone(
              bucket.status,
            )}`}
          >
            {bucket.status}
          </span>
        </span>
      }
      action={
        <span className="text-xs text-muted">
          Alíquota {(rate * 100).toFixed(0)}%
        </span>
      }
    >
      {/* "Nenhum TLH necessário" covers isenção, a loss month and carry that
          already absorbs the gain — the status alone cannot say which. */}
      <p className="mb-4 text-sm text-muted">{bucket.rationale}</p>

      <div className="grid grid-cols-2 gap-x-6 gap-y-2 sm:grid-cols-5">
        <Metric label="Vendas brutas">
          <span className="font-mono text-sm tnum tabular-nums text-ink">
            {formatMoney(bucket.final_gross)}
          </span>
        </Metric>
        <Metric label="P&L final">
          <Money value={bucket.final_pnl} sign />
        </Metric>
        <Metric label="Compensado">
          <span className="font-mono text-sm tnum tabular-nums text-ink">
            {formatMoney(bucket.carry_used)}
          </span>
        </Metric>
        {/* Selling at a loss consumes no carry and adds to it — without this a
            harvest looks like it did nothing. */}
        <Metric label="Prejuízo restante">
          <span className="font-mono text-sm tnum tabular-nums text-ink">
            {formatMoney(bucket.carry_after)}
          </span>
        </Metric>
        <Metric label="Imposto estimado">
          <span className="font-mono text-sm tnum tabular-nums text-ink">
            {formatMoney(bucket.ir)}
          </span>
        </Metric>
      </div>

      {cls === "AÇÃO" && bucket.final_gross <= limit && (
        <p className="mt-3 text-xs text-status-done">
          Vendas do mês ≤ {formatMoney(limit)} — ganhos isentos de IR.
        </p>
      )}

      {bucket.harvest.length > 0 ? (
        <div className="mt-5 space-y-4">
          <div>
            <p className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-muted">
              Vender hoje
            </p>
            <DataTable
              columns={harvestCols}
              rows={bucket.harvest}
              getRowKey={(r) => r.ticker}
              label="Vendas para colher prejuízo"
            />
          </div>

          <div>
            <p className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-muted">
              Recomprar em T+1
            </p>
            <div className="flex flex-wrap gap-1.5">
              {bucket.rebuys.map((r) => (
                <span
                  key={r.ticker}
                  className="inline-flex items-center gap-1.5 rounded-md border border-hairline bg-paper px-2 py-1 text-xs"
                >
                  <TickerChip ticker={r.ticker} />
                  <span className="font-mono tnum tabular-nums text-ink">
                    {formatQty(r.qty)}
                  </span>
                </span>
              ))}
            </div>
            <p className="mt-2 flex items-start gap-2 rounded-md border border-status-processing/30 bg-status-processing/10 px-3 py-2 text-xs text-status-processing">
              <IconAlert size={14} className="mt-0.5 shrink-0" />
              Recompre no próximo pregão — recomprar no mesmo dia configura day
              trade.
            </p>
          </div>
        </div>
      ) : (
        <p className="mt-4 text-sm text-muted">
          Sem colheita de prejuízo neste plano.
        </p>
      )}
    </Card>
  );
}
