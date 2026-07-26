"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { IncomeRow } from "@/lib/types";
import { Card } from "@/components/ui/Card";
import { DataTable, type Column } from "@/components/ui/DataTable";
import { Money } from "@/components/ui/Money";
import { EmptyState } from "@/components/ui/EmptyState";
import { LoadingStatus, Skeleton } from "@/components/ui/Skeleton";
import { IconReceipt } from "@/components/icons";
import { formatMoney } from "@/lib/format";

// event_category is the only breakdown the API exposes (income lumps dividendos +
// JCP + rendimentos). Friendly pt-BR labels; unknown categories fall back capitalised.
export const INCOME_LABEL: Record<string, string> = {
  income: "Proventos",
  premium: "Prêmios",
  reembolso: "Reembolsos",
  amortization: "Amortizações",
  other: "Outros",
  qty_event: "Eventos de qtd.",
};
const label = (c: string) => INCOME_LABEL[c] ?? c.charAt(0).toUpperCase() + c.slice(1);

type MatrixRow = {
  key: string;
  year: number | null; // null → totals row
  cells: Record<string, number>;
  total: number;
  bold?: boolean;
};

// A computed zero is zero, not missing. The em dash is reserved for values the
// platform could not produce; printing it here would make a year with genuinely
// no JCP look like a year it failed to read.
function Cell({ value, bold }: { value: number; bold?: boolean }) {
  return (
    <Money
      value={value}
      className={`${value === 0 ? "text-muted" : ""} ${bold ? "font-semibold" : ""}`}
    />
  );
}

export function IncomeSection() {
  const q = useQuery({
    queryKey: ["income"],
    queryFn: () => api<IncomeRow[]>("/portfolio/income"),
  });

  // One live region for the section, at a fixed position outside every branch.
  // A region that is unmounted is destroyed rather than updated, so nothing ever
  // announces that loading finished.
  const shell = (body: React.ReactNode) => (
    <>
      <LoadingStatus busy={q.isLoading} label="Carregando proventos…" />
      {body}
    </>
  );

  if (q.isLoading) {
    return shell(
      <Card title="Proventos e eventos de caixa">
        <Skeleton className="h-40 w-full" />
      </Card>,
    );
  }

  if (q.isError) {
    return shell(
      <Card title="Proventos e eventos de caixa">
        <EmptyState
          icon={<IconReceipt size={28} />}
          variant="error"
          title="Não foi possível carregar os proventos."
          hint="A conexão com a API falhou. Seus lançamentos continuam importados."
          action={
            <button
              type="button"
              onClick={() => q.refetch()}
              className="rounded-md border border-hairline px-3 py-1.5 text-sm font-medium text-ink transition-colors hover:bg-hairline/40"
            >
              Tentar de novo
            </button>
          }
        />
      </Card>,
    );
  }

  const data = q.data ?? [];
  if (data.length === 0) {
    return shell(
      <Card title="Proventos e eventos de caixa">
        <EmptyState
          icon={<IconReceipt size={28} />}
          title="Nenhum provento registrado ainda."
          hint="Importe o extrato de movimentação para ver dividendos, JCP e rendimentos por ano."
        />
      </Card>,
    );
  }

  const years = [...new Set(data.map((r) => r.year))].sort((a, b) => a - b);
  const grand: Record<string, number> = {};
  for (const r of data) grand[r.event_category] = (grand[r.event_category] ?? 0) + r.total;

  // Drop categories with no cash across every year (qty_event is always 0 — non-cash
  // corporate actions), ordered by size so the biggest provento leads.
  const cats = Object.keys(grand)
    .filter((c) => grand[c] !== 0)
    .sort((a, b) => Math.abs(grand[b]) - Math.abs(grand[a]));

  const cell = (year: number, cat: string) =>
    data.find((r) => r.year === year && r.event_category === cat)?.total ?? 0;

  const rows: MatrixRow[] = years.map((y) => {
    const cells = Object.fromEntries(cats.map((c) => [c, cell(y, c)]));
    return { key: String(y), year: y, cells, total: Object.values(cells).reduce((s, v) => s + v, 0) };
  });
  rows.push({
    key: "total",
    year: null,
    cells: Object.fromEntries(cats.map((c) => [c, grand[c]])),
    total: cats.reduce((s, c) => s + grand[c], 0),
    bold: true,
  });

  const columns: Column<MatrixRow>[] = [
    {
      key: "year",
      header: "Ano",
      // Years stack in a column and must not jitter, whatever the alignment.
      numeric: true,
      render: (r) => (
        <span className={r.bold ? "font-semibold" : ""}>{r.year ?? "Total"}</span>
      ),
    },
    ...cats.map<Column<MatrixRow>>((c) => ({
      key: c,
      header: label(c),
      align: "right",
      render: (r) => <Cell value={r.cells[c]} bold={r.bold} />,
    })),
    {
      key: "total",
      header: "Total",
      align: "right",
      // Semibold only on the totals row. Bolding the whole column meant weight
      // no longer marked the one number people came for.
      render: (r) => <Money value={r.total} className={r.bold ? "font-semibold" : ""} />,
    },
  ];

  const maxAbs = Math.max(...cats.map((c) => Math.abs(grand[c])), 1);

  return shell(
    <Card title="Proventos e eventos de caixa">
      {/* Per-category totals with inline proportion bars (tokens, no chart
          component). One ink bar, not eight palette entries: the row is already
          labelled and the length already carries the magnitude, so colour here
          would encode nothing. Ink, not navy — a magnitude is data, and the
          accent is reserved for where you are and what you can do. */}
      <ul className="mb-5 space-y-2.5">
        {cats.map((c) => (
          <li key={c} className="grid grid-cols-[9rem_1fr_auto] items-center gap-3">
            <span className="truncate text-sm text-ink">{label(c)}</span>
            <span className="h-2 w-full rounded-xs bg-hairline/60">
              <span
                className="block h-full rounded-xs bg-ink/25"
                style={{ width: `${(Math.abs(grand[c]) / maxAbs) * 100}%` }}
              />
            </span>
            <span className="text-right font-mono text-sm tabular-nums text-ink">
              {formatMoney(grand[c])}
            </span>
          </li>
        ))}
      </ul>

      <div className="border-t border-hairline pt-3">
        <DataTable
          columns={columns}
          rows={rows}
          getRowKey={(r) => r.key}
          label="Proventos por ano e categoria"
          footRowKey="total"
        />
      </div>

      {/* Premios and reembolsos are real cash here and deliberately outside the
          rentabilidade curve. Two cards with different scopes on one page need
          the reconciliation said out loud, not left for the user to notice. */}
      {cats.some((c) => c === "premium" || c === "reembolso") && (
        <p className="mt-3 text-xs text-muted">
          Prêmios e reembolsos entram neste quadro como caixa, mas ficam fora da
          curva de rentabilidade — não são retorno da carteira.
        </p>
      )}
    </Card>,
  );
}
