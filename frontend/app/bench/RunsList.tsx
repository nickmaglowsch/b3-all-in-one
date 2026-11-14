"use client";

import { useRouter } from "next/navigation";
import type { BacktestRun } from "@/lib/types";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { IconChart } from "@/components/icons";
import { errorText } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { isDone, isFinished } from "@/lib/jobStatus";
import type { RowState } from "@/lib/rowConfirm";
import { useRowConfirm } from "@/lib/useRowConfirm";
import { useDeleteBacktest } from "./lib";

// Rows must be clickable → /bench/run?id=. DataTable (frozen) has no onRowClick, so
// this renders a native table in the same hairline-ruled, dense visual style.
// A leading checkbox picks runs for the comparison overlay (done runs only), and
// a trailing "Excluir" drops a single run and its artifacts.
export function RunsList({
  runs,
  loading,
  selected,
  onToggle,
  onDeleted,
}: {
  runs: BacktestRun[];
  loading: boolean;
  selected: string[];
  onToggle: (id: string) => void;
  /** So the parent can drop a deleted run from the comparison selection. */
  onDeleted: (id: string) => void;
}) {
  const router = useRouter();
  const del = useDeleteBacktest();
  const confirm = useRowConfirm(del);
  const picked = new Set(selected);

  if (loading) {
    return (
      <div className="space-y-2">
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} className="h-9 w-full" />
        ))}
      </div>
    );
  }

  if (runs.length === 0) {
    return (
      <EmptyState
        icon={<IconChart size={26} />}
        title="Nenhum backtest ainda — rode uma estratégia acima."
        hint="Cada rodada aparece aqui; clique numa linha para abrir o tear sheet."
      />
    );
  }

  const delMsg =
    del.error ? errorText(del.error, "Falha ao excluir a rodada.") : null;

  return (
    <div>
      {delMsg && (
        <p className="mb-3 text-sm text-loss" role="alert">
          {delMsg}
        </p>
      )}
      {/* rtable: below `md` these rows are stacked cards — see globals.css. Five
          columns of run metadata in a 340px box was three of them visible and the
          rest behind a sideways scroll. */}
      <div className="overflow-x-auto">
        <table className="rtable w-full border-collapse text-sm">
          <thead>
            <tr className="border-b border-hairline">
              <th className="w-9 px-3 py-2" aria-label="Comparar" />
              {["Estratégia", "Status", "Criado"].map((h, i) => (
                <th
                  key={h}
                  className={`px-3 py-2 text-[11px] font-semibold uppercase tracking-wide text-muted ${
                    i === 2 ? "text-right" : "text-left"
                  }`}
                >
                  {h}
                </th>
              ))}
              <th className="px-3 py-2" aria-label="Ações" />
            </tr>
          </thead>
          <tbody>
            {runs.map((r) => (
              <Row
                key={r.id}
                run={r}
                picked={picked.has(r.id)}
                onOpen={() => router.push(`/bench/run?id=${r.id}`)}
                onToggle={() => onToggle(r.id)}
                confirm={confirm.stateOf(r.id)}
                locked={confirm.busy}
                onConfirm={() => confirm.arm(r.id)}
                onCancel={confirm.cancel}
                onDelete={() => confirm.confirm(r.id, onDeleted)}
              />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Row({
  run,
  picked,
  onOpen,
  onToggle,
  confirm,
  locked,
  onConfirm,
  onCancel,
  onDelete,
}: {
  run: BacktestRun;
  picked: boolean;
  onOpen: () => void;
  onToggle: () => void;
  confirm: RowState;
  /** Another row's delete is in flight. */
  locked: boolean;
  onConfirm: () => void;
  onCancel: () => void;
  onDelete: () => void;
}) {
  // A run the worker still holds cannot be deleted (the API answers 409): the
  // engine is writing into its artifacts directory, and the queue quota counts
  // exactly these rows.
  const done = isDone(run.status);
  const finished = isFinished(run.status);

  return (
    <tr
      onClick={onOpen}
      className={`cursor-pointer border-b border-hairline/70 last:border-0 hover:bg-hairline/30 ${
        picked ? "bg-navy/[0.04]" : ""
      }`}
    >
      <td className="px-3 py-2.5 align-middle">
        {/* On a phone the column header is gone, so a bare checkbox floating at
            the top of the card says nothing about what ticking it does. */}
        <span className="rt-label">Comparar</span>
        <input
          type="checkbox"
          checked={picked}
          disabled={!done}
          onClick={(e) => e.stopPropagation()}
          onChange={onToggle}
          aria-label={
            done
              ? `Comparar ${run.strategy}`
              : "Só rodadas concluídas podem ser comparadas"
          }
          title={done ? "Adicionar à comparação" : "Aguardando conclusão"}
          className="h-4 w-4 cursor-pointer accent-navy disabled:cursor-not-allowed disabled:opacity-30"
        />
      </td>
      <td className="rt-title px-3 py-2.5 align-middle">
        <span className="font-medium text-ink">{run.strategy}</span>
        <span className="ml-2 font-mono text-[11px] text-muted">
          {run.id.slice(0, 8)}
        </span>
      </td>
      <td className="px-3 py-2.5 align-middle">
        <span className="rt-label">Status</span>
        <span className="rt-value">
          <StatusBadge status={run.status} />
        </span>
      </td>
      <td className="px-3 py-2.5 text-right align-middle font-mono tnum tabular-nums text-muted">
        <span className="rt-label">Criado</span>
        <span className="rt-value">{formatDateTime(run.created_at)}</span>
      </td>
      <td className="rt-full px-3 py-2.5 text-right align-middle">
        <RowActions
          strategy={run.strategy}
          finished={finished}
          confirm={confirm}
          locked={locked}
          onConfirm={onConfirm}
          onCancel={onCancel}
          onDelete={onDelete}
        />
      </td>
    </tr>
  );
}

function RowActions({
  strategy,
  finished,
  confirm,
  locked,
  onConfirm,
  onCancel,
  onDelete,
}: {
  strategy: string;
  finished: boolean;
  confirm: RowState;
  locked: boolean;
  onConfirm: () => void;
  onCancel: () => void;
  onDelete: () => void;
}) {
  // stopPropagation everywhere: the row itself navigates to the tear sheet, and
  // the click that opens a confirm must not also leave the page.
  const stop = (fn: () => void) => (e: React.MouseEvent) => {
    e.stopPropagation();
    fn();
  };

  if (confirm !== "idle") {
    return (
      <span className="flex items-center justify-end gap-1.5">
        <button
          type="button"
          onClick={stop(onDelete)}
          disabled={confirm === "busy"}
          className="rounded-md border border-loss/40 bg-paper px-2 py-1 text-xs font-medium text-loss hover:bg-loss/10 disabled:opacity-60"
        >
          Confirmar
        </button>
        <button
          type="button"
          onClick={stop(onCancel)}
          className="rounded-md border border-hairline bg-paper px-2 py-1 text-xs font-medium text-ink hover:bg-hairline/40"
        >
          Não
        </button>
      </span>
    );
  }

  return (
    <button
      type="button"
      onClick={stop(onConfirm)}
      disabled={!finished || locked}
      title={
        finished
          ? "Excluir esta rodada e seus artefatos"
          : "Rodada em andamento — aguarde terminar"
      }
      aria-label={`Excluir ${strategy}`}
      className="rounded-md border border-hairline bg-paper px-2 py-1 text-xs font-medium text-muted hover:text-loss disabled:cursor-not-allowed disabled:opacity-30 disabled:hover:text-muted"
    >
      Excluir
    </button>
  );
}
