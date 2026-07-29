"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { DarfLedger as Ledger, DarfMonth } from "@/lib/types";
import { Card } from "@/components/ui/Card";
import { Money } from "@/components/ui/Money";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { IconAlert, IconCheck, IconClock, IconReceipt } from "@/components/icons";
import { formatDate, formatPercent } from "@/lib/format";

const CLASSE_LABEL: Record<string, string> = {
  "AÇÃO": "Ações",
  FII: "FIIs",
  BDR_ETF: "BDRs e ETFs",
};

const MESES_PT = ["jan", "fev", "mar", "abr", "mai", "jun",
                  "jul", "ago", "set", "out", "nov", "dez"];

/** "2025-04" → "abr/2025". The apuração month, never the payment month. */
function formatApuracao(apuracao: string): string {
  const [y, m] = apuracao.split("-");
  return `${MESES_PT[Number(m) - 1]}/${y}`;
}

// Icon + label + colour, never colour alone — the same rule StatusBadge follows.
// Not StatusBadge itself: these are DARF states, not job states, and folding
// "atrasado" into "error" would make an unpaid tax read as a system failure.
const STATUS = {
  pago: { label: "Pago", text: "text-status-done", bg: "bg-status-done/10", Icon: IconCheck },
  pendente: { label: "A vencer", text: "text-status-pending", bg: "bg-status-pending/10", Icon: IconClock },
  atrasado: { label: "Vencido", text: "text-loss", bg: "bg-loss/10", Icon: IconAlert },
} as const;

function StatusChip({ status }: { status: DarfMonth["status"] }) {
  const s = STATUS[status];
  const { Icon } = s;
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium ${s.text} ${s.bg}`}>
      <Icon size={13} />
      {s.label}
    </span>
  );
}

/** The row's identity in the ledger: one DARF per apuração month per class. */
function keyOf(m: { apuracao: string; classe: string }) {
  return `${m.apuracao}·${m.classe}`;
}

export function DarfLedger({ year }: { year: number }) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState<string | null>(null);

  const ledger = useQuery({
    queryKey: ["darf", year],
    queryFn: () => api<Ledger>(`/irpf/darf?year=${year}`),
  });

  // The API replaces the whole year, so every write sends the surviving rows
  // plus (or minus) the one being edited. A year holds a handful of DARFs, so
  // this is cheaper than tracking row ids client-side.
  const save = useMutation({
    mutationFn: (rows: object[]) =>
      api(`/irpf/darf?year=${year}`, { method: "PUT", body: JSON.stringify(rows) }),
    onSuccess: () => {
      setEditing(null);
      qc.invalidateQueries({ queryKey: ["darf", year] });
    },
  });

  function others(exclude: string) {
    return (ledger.data?.meses ?? [])
      .filter((m) => m.pagamento && keyOf(m) !== exclude)
      .map((m) => ({
        apuracao: m.apuracao,
        classe: m.classe,
        valor_principal: m.pagamento!.valor_principal,
        data_pagamento: m.pagamento!.data_pagamento,
        obs: m.pagamento!.obs,
      }));
  }

  if (ledger.isPending) {
    return (
      <Card title="DARF — apuração mensal">
        <Skeleton className="h-32 w-full" />
      </Card>
    );
  }

  if (ledger.isError) {
    return (
      <Card title="DARF — apuração mensal">
        <EmptyState
          variant="error"
          title="Não foi possível apurar os DARFs deste ano."
          hint={ledger.error instanceof Error ? ledger.error.message : undefined}
        />
      </Card>
    );
  }

  const { meses, total_devido, total_pago, atrasados } = ledger.data;

  if (meses.length === 0) {
    return (
      <Card title="DARF — apuração mensal">
        <EmptyState
          icon={<IconReceipt size={28} />}
          title={`Nenhum DARF apurado em ${year}.`}
          hint="Meses sem imposto a pagar não geram DARF — vendas de ações até R$ 20.000 no mês são isentas, e um mês no prejuízo não é tributado."
        />
      </Card>
    );
  }

  return (
    <Card
      title="DARF — apuração mensal"
      action={
        <span className="text-xs text-muted">
          Código {ledger.data.codigo} · devido{" "}
          <Money value={total_devido} className="text-xs" /> · pago{" "}
          <Money value={total_pago} className="text-xs" />
        </span>
      }
    >
      {atrasados > 0 && (
        <p className="mb-4 flex items-start gap-2 rounded-md border border-loss/30 bg-loss/[0.04] p-3 text-sm text-loss" role="alert">
          <IconAlert size={16} />
          <span>
            {atrasados === 1
              ? "1 mês com imposto apurado passou do vencimento sem pagamento registrado."
              : `${atrasados} meses com imposto apurado passaram do vencimento sem pagamento registrado.`}{" "}
            Se você já pagou, registre abaixo — o alerta some.
          </span>
        </p>
      )}

      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-hairline text-left text-xs text-muted">
              <th className="py-2 pr-3 font-medium">Apuração</th>
              <th className="py-2 pr-3 font-medium">Classe</th>
              <th className="py-2 pr-3 text-right font-medium">Base</th>
              <th className="py-2 pr-3 text-right font-medium">Alíquota</th>
              <th className="py-2 pr-3 text-right font-medium">IR devido</th>
              <th className="py-2 pr-3 font-medium">Vencimento</th>
              <th className="py-2 pr-3 font-medium">Situação</th>
              <th className="py-2 font-medium" />
            </tr>
          </thead>
          <tbody>
            {meses.map((m) => {
              const key = keyOf(m);
              return (
                <tr key={key} className="border-b border-hairline/60 align-middle">
                  <td className="py-2 pr-3 whitespace-nowrap">{formatApuracao(m.apuracao)}</td>
                  <td className="py-2 pr-3 whitespace-nowrap text-muted">
                    {CLASSE_LABEL[m.classe] ?? m.classe}
                  </td>
                  <td className="py-2 pr-3 text-right">
                    <Money value={m.base} />
                  </td>
                  <td className="py-2 pr-3 text-right font-mono tnum tabular-nums text-muted">
                    {formatPercent(m.aliquota)}
                  </td>
                  <td className="py-2 pr-3 text-right">
                    <Money value={m.ir_devido} />
                  </td>
                  <td className="py-2 pr-3 whitespace-nowrap text-muted">
                    {formatDate(m.vencimento)}
                  </td>
                  <td className="py-2 pr-3">
                    <StatusChip status={m.status} />
                  </td>
                  <td className="py-2 text-right whitespace-nowrap">
                    {m.pagamento ? (
                      <button
                        type="button"
                        disabled={save.isPending}
                        onClick={() => save.mutate(others(key))}
                        className="text-xs text-muted underline underline-offset-2 hover:text-ink disabled:opacity-60"
                      >
                        Remover
                      </button>
                    ) : (
                      <button
                        type="button"
                        onClick={() => setEditing(editing === key ? null : key)}
                        className="rounded-md border border-hairline px-2.5 py-1 text-xs font-medium text-muted hover:bg-hairline/40 hover:text-ink"
                      >
                        Registrar pagamento
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* The form lives outside the table so it can lay out freely instead of
          fighting a colspan row on a phone. */}
      {meses.map((m) => {
        const key = keyOf(m);
        if (editing !== key) return null;
        return (
          <PaymentForm
            key={key}
            mes={m}
            pending={save.isPending}
            onCancel={() => setEditing(null)}
            onSave={(row) => save.mutate([...others(key), row])}
          />
        );
      })}

      {save.isError && (
        <p className="mt-3 text-sm text-loss" role="alert">
          Não foi possível salvar:{" "}
          {save.error instanceof Error ? save.error.message : "erro inesperado."}
        </p>
      )}

      <p className="mt-4 text-xs text-muted">
        A apuração é recalculada do seu histórico a cada consulta — só o pagamento
        fica guardado. Emissão do DARF com código de barras continua sendo no
        e-CAC/Sicalc.
      </p>
    </Card>
  );
}

function PaymentForm({
  mes,
  pending,
  onCancel,
  onSave,
}: {
  mes: DarfMonth;
  pending: boolean;
  onCancel: () => void;
  onSave: (row: object) => void;
}) {
  // Pre-filled with what the engine computed and the day it was due: the common
  // case is "paid it, on time, for the amount shown", and typing it again is the
  // step where a wrong number gets in.
  const [valor, setValor] = useState(String(mes.ir_devido.toFixed(2)));
  const [data, setData] = useState(mes.vencimento);
  const [obs, setObs] = useState("");

  return (
    <form
      className="mt-4 flex flex-wrap items-end gap-3 rounded-md border border-hairline bg-hairline/20 p-3"
      onSubmit={(e) => {
        e.preventDefault();
        onSave({
          apuracao: mes.apuracao,
          classe: mes.classe,
          valor_principal: Number(valor),
          data_pagamento: data || null,
          obs: obs.trim() || null,
        });
      }}
    >
      <label className="text-xs text-muted">
        <span className="mb-1 block">Valor principal</span>
        <input
          type="number"
          step="0.01"
          min="0"
          required
          value={valor}
          onChange={(e) => setValor(e.target.value)}
          className="w-36 rounded-md border border-hairline bg-panel px-2 py-1.5 font-mono tnum text-sm text-ink"
        />
      </label>
      <label className="text-xs text-muted">
        <span className="mb-1 block">Data do pagamento</span>
        <input
          type="date"
          value={data}
          onChange={(e) => setData(e.target.value)}
          className="rounded-md border border-hairline bg-panel px-2 py-1.5 text-sm text-ink"
        />
      </label>
      <label className="min-w-40 flex-1 text-xs text-muted">
        <span className="mb-1 block">Observação (opcional)</span>
        <input
          type="text"
          value={obs}
          onChange={(e) => setObs(e.target.value)}
          className="w-full rounded-md border border-hairline bg-panel px-2 py-1.5 text-sm text-ink"
        />
      </label>
      <button
        type="submit"
        disabled={pending}
        className="rounded-md bg-navy px-3 py-1.5 text-sm font-semibold text-white hover:bg-navy/90 disabled:opacity-60"
      >
        {pending ? "Salvando…" : "Salvar"}
      </button>
      <button
        type="button"
        onClick={onCancel}
        className="px-2 py-1.5 text-sm text-muted hover:text-ink"
      >
        Cancelar
      </button>
    </form>
  );
}
