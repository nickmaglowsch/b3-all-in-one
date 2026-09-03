"use client";

// "Limpar" in the Rodadas header: one sweep of old runs, by age and optionally
// only the failed ones. The criteria and the cutoff arithmetic live in
// lib/retention (where they are tested); this is the control that offers them.
import { useState } from "react";
import { ApiError } from "@/lib/api";
import {
  PURGE_WINDOWS,
  type PurgeChoice,
  purgeLabel,
  purgeQuery,
} from "@/lib/retention";
import { usePurgeBacktests } from "./lib";

// Takes no callback for the selection: the compare panel derives its runs from
// the listing (page.tsx), so a swept run drops out of it on the refetch without
// anyone having to be told which ids went.
export function RunsPurge() {
  const purge = usePurgeBacktests();
  const [open, setOpen] = useState(false);
  // 30 days and every finished run: the sweep someone who clicked "limpar"
  // without reading the options almost certainly meant.
  const [choice, setChoice] = useState<PurgeChoice>({
    olderThanDays: 30,
    onlyErrors: false,
  });
  const [deleted, setDeleted] = useState<number | null>(null);

  const query = purgeQuery(choice);

  if (!open) {
    return (
      <div className="flex items-center gap-3">
        {deleted !== null && (
          <span className="font-mono text-[11px] text-muted" role="status">
            {deleted === 1 ? "1 rodada excluída" : `${deleted} rodadas excluídas`}
          </span>
        )}
        <button
          type="button"
          onClick={() => {
            setDeleted(null);
            setOpen(true);
          }}
          className="rounded-md border border-hairline bg-paper px-2 py-1 text-xs font-medium text-muted hover:text-loss"
        >
          Limpar antigas
        </button>
      </div>
    );
  }

  return (
    <div className="flex flex-wrap items-center justify-end gap-2">
      <label className="flex items-center gap-1.5 text-xs text-muted">
        Mais de
        <select
          value={choice.olderThanDays ?? ""}
          onChange={(e) =>
            setChoice((c) => ({
              ...c,
              olderThanDays: e.target.value === "" ? null : Number(e.target.value),
            }))
          }
          className="rounded-md border border-hairline bg-paper px-1.5 py-1 text-xs text-ink"
        >
          {PURGE_WINDOWS.map((w) => (
            <option key={w.days} value={w.days}>
              {w.label}
            </option>
          ))}
          <option value="">qualquer idade</option>
        </select>
      </label>

      <label className="flex items-center gap-1.5 text-xs text-muted">
        <input
          type="checkbox"
          checked={choice.onlyErrors}
          onChange={(e) =>
            setChoice((c) => ({ ...c, onlyErrors: e.target.checked }))
          }
          className="h-3.5 w-3.5 accent-navy"
        />
        só as com erro
      </label>

      <button
        type="button"
        // null means the choice names no criterion at all, which the API refuses
        // — and rightly: "limpar" and "delete every backtest I have ever run"
        // are different requests.
        disabled={query === null || purge.isPending}
        onClick={() => {
          if (query === null) return;
          purge.mutate(query, {
            onSuccess: (r) => {
              setDeleted(r.deleted);
              setOpen(false);
            },
          });
        }}
        className="rounded-md border border-loss/40 bg-paper px-2 py-1 text-xs font-medium text-loss hover:bg-loss/10 disabled:cursor-not-allowed disabled:opacity-40"
      >
        {purgeLabel(choice)}
      </button>
      <button
        type="button"
        onClick={() => setOpen(false)}
        className="rounded-md border border-hairline bg-paper px-2 py-1 text-xs font-medium text-ink hover:bg-hairline/40"
      >
        Cancelar
      </button>

      {purge.error && (
        <span className="w-full text-right text-xs text-loss" role="alert">
          {purge.error instanceof ApiError
            ? purge.error.message
            : "Falha ao limpar as rodadas."}
        </span>
      )}
    </div>
  );
}
