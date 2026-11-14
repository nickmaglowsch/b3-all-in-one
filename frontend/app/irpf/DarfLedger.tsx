"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { DarfLedger as LedgerData, DarfMonth } from "@/lib/types";
import { Card } from "@/components/ui/Card";
import { Money } from "@/components/ui/Money";
import { EmptyState, LoadFailure } from "@/components/ui/EmptyState";
import { useYearChoice, YearSelect } from "./YearSelect";
import { Skeleton } from "@/components/ui/Skeleton";
import { IconAlert, IconCheck, IconClock, IconReceipt } from "@/components/icons";
import { formatDate } from "@/lib/format";

const CLASSE_LABEL: Record<string, string> = {
  "AÇÃO": "Ações",
  FII: "FIIs",
  BDR_ETF: "BDRs e ETFs",
  DAYTRADE: "Day trade",
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
  acumulado: { label: "Abaixo de R$ 10 — vai para o mês seguinte", text: "text-muted", bg: "bg-hairline/40", Icon: IconClock },
  compensado: { label: "Coberto pelo IRRF", text: "text-status-done", bg: "bg-status-done/10", Icon: IconCheck },
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

/** The row's identity in the ledger: one DARF 6015 per apuração month. */
function keyOf(m: { apuracao: string }) {
  return m.apuracao;
}

/** "Ações + FIIs" — the lines this month's one DARF pays. */
function classesOf(m: DarfMonth) {
  return m.classes.map((c) => CLASSE_LABEL[c.classe] ?? c.classe).join(" + ") || "—";
}

const TITLE = "DARF — apuração mensal";

// Its own year, not the report's ano-base: the report's natural year is the one
// being declared (last year), the ledger's is the one whose DARFs are coming due.
export function DarfLedger() {
  const [year, setChosen] = useYearChoice((atual) => atual);
  return (
    <Card
      title={TITLE}
      action={
        <YearSelect
          id="darf-year"
          label="Ano da apuração"
          optionLabel={(y) => `Apuração ${y}`}
          value={year}
          onChange={setChosen}
        />
      }
    >
      {/* keyed: an open payment form belongs to the year it was opened in */}
      {year === null ? <Skeleton className="h-32 w-full" /> : <Ledger key={year} year={year} />}
    </Card>
  );
}

function Ledger({ year }: { year: number }) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState<string | null>(null);

  const ledger = useQuery({
    queryKey: ["darf", year],
    // On load: a 402 is rendered inline below, not thrown up as a modal.
    queryFn: () => api<LedgerData>(`/irpf/darf?year=${year}`, {}, { paywallDialog: false }),
  });

  // The API replaces the whole year, so every write sends the surviving rows
  // plus (or minus) the one being edited. A year holds a handful of DARFs, so
  // this is cheaper than tracking row ids client-side.
  //
  // Which makes ledger.data the base of the *next* write, so it must not be
  // stale when that write is built. The invalidate is returned, which keeps the
  // mutation pending until the refetched ledger has landed (the apuração is a
  // taxcore run, seconds long); before that, a second "Remover" would rebuild
  // the year from the old snapshot and bring the first payment back.
  const save = useMutation({
    mutationFn: (rows: object[]) =>
      api(`/irpf/darf?year=${year}`, { method: "PUT", body: JSON.stringify(rows) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["darf", year] }),
  });
  // A background refetch is just as stale a base as an unfinished save.
  const busy = save.isPending || ledger.isFetching;

  // Opening another month's form, or cancelling, starts over: the last save's
  // error was about that one. Never reset a pending save — that would drop
  // `busy` while the write is still in flight.
  const open = (key: string | null) => {
    if (save.isError) save.reset();
    setEditing(key);
  };
  // A saved payment closes its own form. A "Remover" elsewhere used to close
  // whichever form happened to be open.
  const savePayment = (key: string, row: object) =>
    save.mutate([...others(key), row], {
      onSuccess: () => setEditing((e) => (e === key ? null : e)),
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

  if (ledger.isPending) return <Skeleton className="h-32 w-full" />;

  if (ledger.isError) {
    return (
      <LoadFailure
        error={ledger.error}
        icon={<IconReceipt size={28} />}
        title="Não foi possível apurar os DARFs deste ano."
        offerTitle={(nome) => `O controle de DARF vem com o módulo ${nome}.`}
        onRetry={() => ledger.refetch()}
      />
    );
  }

  const { meses, total_devido, total_pago, atrasados } = ledger.data;

  if (meses.length === 0) {
    return (
      <EmptyState
        icon={<IconReceipt size={28} />}
        title={`Nenhum DARF apurado em ${year}.`}
        hint="Meses sem imposto a pagar não geram DARF — vendas de ações até R$ 20.000 no mês são isentas, e um mês no prejuízo não é tributado."
      />
    );
  }

  return (
    <>
      <p className="mb-3 text-xs text-muted">
        Código {ledger.data.codigo} · devido{" "}
        <Money value={total_devido} className="text-xs" /> · pago{" "}
        <Money value={total_pago} className="text-xs" />
      </p>
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

      {/* rtable: below `md` each month becomes a card — see globals.css. Eight
          columns of apuração in a 340px box put the IR devido, the vencimento and
          the "registrar pagamento" button all off-screen. */}
      <div className="overflow-x-auto">
        <table className="rtable w-full text-sm">
          <thead>
            <tr className="border-b border-hairline text-left text-xs text-muted">
              <th className="py-2 pr-3 font-medium">Apuração</th>
              <th className="py-2 pr-3 font-medium">Classes</th>
              <th className="py-2 pr-3 text-right font-medium">IR apurado</th>
              <th className="py-2 pr-3 text-right font-medium">IRRF / acumulado</th>
              <th className="py-2 pr-3 text-right font-medium">DARF</th>
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
                  <td className="rt-title py-2 pr-3 whitespace-nowrap font-medium">
                    {formatApuracao(m.apuracao)}
                  </td>
                  <td className="py-2 pr-3 whitespace-nowrap text-muted">
                    <span className="rt-label">Classes</span>
                    <span className="rt-value">{classesOf(m)}</span>
                  </td>
                  <td className="py-2 pr-3 text-right">
                    <span className="rt-label">IR apurado</span>
                    <span className="rt-value"><Money value={m.imposto} /></span>
                  </td>
                  <td className="py-2 pr-3 text-right text-muted">
                    <span className="rt-label">IRRF / acumulado</span>
                    <span className="rt-value">
                      <Money value={m.acumulado_anterior - m.irrf_deduzido} />
                    </span>
                  </td>
                  <td className="py-2 pr-3 text-right">
                    <span className="rt-label">DARF</span>
                    <span className="rt-value"><Money value={m.ir_devido} /></span>
                  </td>
                  <td className="py-2 pr-3 whitespace-nowrap text-muted">
                    <span className="rt-label">Vencimento</span>
                    <span className="rt-value">{formatDate(m.vencimento)}</span>
                  </td>
                  <td className="py-2 pr-3">
                    <span className="rt-label">Situação</span>
                    <span className="rt-value"><StatusChip status={m.status} /></span>
                  </td>
                  <td className="rt-full py-2 text-right whitespace-nowrap">
                    {m.pagamento ? (
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() => save.mutate(others(key))}
                        aria-label={`Remover pagamento do DARF de ${formatApuracao(m.apuracao)}`}
                        className="text-xs text-muted underline underline-offset-2 hover:text-ink disabled:opacity-60"
                      >
                        Remover
                      </button>
                    ) : (
                      <button
                        type="button"
                        onClick={() => open(editing === key ? null : key)}
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
            pending={busy}
            onCancel={() => open(null)}
            onSave={(row) => savePayment(key, row)}
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
        Um DARF (código 6015) por mês soma todas as classes. Abaixo de R$ 10,00 não
        se emite DARF: o valor passa para o mês seguinte. O IRRF (0,005% das vendas,
        1% do ganho em day trade) é estimado a partir das vendas.
      </p>
      <p className="mt-2 text-xs text-muted">
        A apuração é recalculada do seu histórico a cada consulta — só o pagamento
        fica guardado. Emissão do DARF com código de barras continua sendo no
        e-CAC/Sicalc.
      </p>
    </>
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
          classe: "TOTAL",
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
