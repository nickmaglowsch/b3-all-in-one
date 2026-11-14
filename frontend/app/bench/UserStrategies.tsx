"use client";

// "Minhas estratégias" (/bench): lists the tenant's rule strategies + hosts the
// Nova/Editar/Clonar form. A saved strategy is just another gallery entry, so
// "Backtest" only selects it in the run panel above — the existing RunPanel path
// runs it unchanged.
import { useState } from "react";
import { errorText } from "@/lib/api";
import { useDeleteUserStrategy, useUserStrategies } from "@/lib/strategies";
import { CLOSED, cloneOf, editingOf, type FormMode } from "@/lib/strategyFormMode";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { StrategyForm } from "./StrategyForm";
import { OriginBadge, RuleBadge } from "./StrategyGallery";

// The form's mode is BenchPage's (lib/strategyFormMode.ts): Clonar lives in a
// sibling, and one value is what keeps "editing X" and "cloned from Y" from both
// being true.
export function UserStrategies({
  onBacktest,
  mode,
  setMode,
}: {
  onBacktest: (name: string) => void;
  mode: FormMode;
  setMode: (m: FormMode) => void;
}) {
  const { data, isLoading } = useUserStrategies();
  const del = useDeleteUserStrategy();
  const rows = (data ?? []).filter((s) => s.kind === "rank_and_hold");

  const [confirmId, setConfirmId] = useState<string | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);

  const open = (m: FormMode) => {
    setWarnings([]);
    setMode(m);
  };

  const delMsg = del.error ? errorText(del.error, "Falha ao excluir.") : null;

  if (mode.kind !== "closed") {
    const editing = editingOf(mode);
    const clone = cloneOf(mode);
    return (
      <Card title={editing ? "Editar estratégia" : "Nova estratégia"}>
        <StrategyForm
          key={editing?.id ?? clone?.name ?? "new"}
          editing={editing}
          clone={clone}
          onClose={() => setMode(CLOSED)}
          onSaved={(w) => {
            setWarnings(w);
            setMode(CLOSED);
          }}
        />
      </Card>
    );
  }

  return (
    <Card
      title="Minhas estratégias"
      action={
        <button
          type="button"
          onClick={() => open({ kind: "new" })}
          className="rounded-md bg-navy px-3 py-1.5 text-xs font-semibold text-white hover:bg-navy/90"
        >
          Nova estratégia
        </button>
      }
    >
      {warnings.length > 0 && (
        <div className="mb-3 rounded-md border border-status-processing/40 bg-status-processing/10 px-3 py-2 text-xs text-ink">
          {warnings.map((w, i) => (
            <p key={i}>{w}</p>
          ))}
        </div>
      )}
      {delMsg && (
        <p className="mb-3 text-sm text-loss" role="alert">
          {delMsg}
        </p>
      )}

      {isLoading ? (
        <div className="space-y-2">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-14 w-full" />
          ))}
        </div>
      ) : rows.length === 0 ? (
        <EmptyState
          title="Nenhuma estratégia própria ainda."
          hint="Crie uma regra de ranqueamento ou clone uma da galeria."
        />
      ) : (
        <ul className="space-y-1.5">
          {rows.map((s) => (
            <li
              key={s.id}
              className="rounded-md border border-hairline bg-panel px-3 py-2.5"
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-semibold text-ink">{s.name}</span>
                    <RuleBadge />
                    <OriginBadge origin={s.origin} />
                  </div>
                  {typeof s.spec.description === "string" && (
                    <p className="mt-0.5 line-clamp-2 text-xs leading-snug text-muted">
                      {s.spec.description}
                    </p>
                  )}
                </div>
                <div className="flex shrink-0 items-center gap-1.5">
                  <RowBtn onClick={() => onBacktest(s.name)}>Backtest</RowBtn>
                  <RowBtn onClick={() => open({ kind: "edit", s })}>Editar</RowBtn>
                  {confirmId === s.id ? (
                    <>
                      <button
                        type="button"
                        onClick={() =>
                          del.mutate(s.id, { onSettled: () => setConfirmId(null) })
                        }
                        disabled={del.isPending}
                        className="rounded-md border border-loss/40 bg-paper px-2 py-1 text-xs font-medium text-loss hover:bg-loss/10 disabled:opacity-60"
                      >
                        Confirmar
                      </button>
                      <RowBtn onClick={() => setConfirmId(null)}>Não</RowBtn>
                    </>
                  ) : (
                    <button
                      type="button"
                      onClick={() => setConfirmId(s.id)}
                      className="rounded-md border border-hairline bg-paper px-2 py-1 text-xs font-medium text-muted hover:text-loss"
                    >
                      Excluir
                    </button>
                  )}
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function RowBtn({
  onClick,
  children,
}: {
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="rounded-md border border-hairline bg-paper px-2 py-1 text-xs font-medium text-ink hover:bg-hairline/40"
    >
      {children}
    </button>
  );
}
