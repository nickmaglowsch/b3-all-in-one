"use client";

import { useState } from "react";
import type { Strategy } from "@/lib/types";
import { ApiError } from "@/lib/api";
import { EmptyState } from "@/components/ui/EmptyState";
import { IconChart } from "@/components/icons";
import { KindBadge } from "./StrategyGallery";
import { useCreateBacktest } from "./lib";

const FREQS = [
  { value: "ME", label: "Mensal" },
  { value: "QE", label: "Trimestral" },
  { value: "W-FRI", label: "Semanal" },
];

export function RunPanel({
  strategy,
  onRan,
}: {
  strategy: Strategy | null;
  onRan: () => void;
}) {
  const [start, setStart] = useState("2023-01-01");
  const [end, setEnd] = useState("");
  const [freq, setFreq] = useState("ME");
  const [aporte, setAporte] = useState("");
  const create = useCreateBacktest();

  if (!strategy) {
    return (
      <EmptyState
        icon={<IconChart size={26} />}
        title="Selecione uma estratégia na galeria."
        hint="Ajuste período e frequência, depois rode o backtest."
      />
    );
  }

  function run() {
    if (!strategy) return;
    const contribution = Number(aporte);
    create.mutate(
      {
        strategy: strategy.name,
        start: start || undefined,
        end: end || undefined,
        freq,
        params: aporte && contribution > 0 ? { contribution } : undefined,
      },
      { onSuccess: onRan },
    );
  }

  const errMsg =
    create.error instanceof ApiError
      ? create.error.message
      : create.error
        ? "Falha ao iniciar o backtest."
        : null;

  return (
    <div>
      <div className="mb-4 flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="text-sm font-semibold text-ink">{strategy.name}</h3>
          <p className="mt-1 text-xs leading-snug text-muted">{strategy.description}</p>
        </div>
        <KindBadge kind={strategy.kind} />
      </div>

      <div className="grid grid-cols-2 gap-3">
        <Field label="Início">
          <input
            type="date"
            value={start}
            onChange={(e) => setStart(e.target.value)}
            className={inputCls}
          />
        </Field>
        <Field label="Fim" hint="vazio = mais recente">
          <input
            type="date"
            value={end}
            onChange={(e) => setEnd(e.target.value)}
            className={inputCls}
          />
        </Field>
        <Field label="Frequência">
          <select
            value={freq}
            onChange={(e) => setFreq(e.target.value)}
            className={inputCls}
          >
            {FREQS.map((f) => (
              <option key={f.value} value={f.value}>
                {f.label} ({f.value})
              </option>
            ))}
          </select>
        </Field>
        <Field label="Aporte mensal" hint="opcional (R$)">
          <input
            type="number"
            min={0}
            step={100}
            inputMode="decimal"
            value={aporte}
            onChange={(e) => setAporte(e.target.value)}
            placeholder="0"
            className={inputCls}
          />
        </Field>
      </div>

      <button
        type="button"
        onClick={run}
        disabled={create.isPending}
        className="mt-4 w-full rounded-md bg-navy px-4 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-navy/90 disabled:opacity-60"
      >
        {create.isPending ? "Enviando…" : "Rodar backtest"}
      </button>

      {errMsg && (
        <p className="mt-3 text-sm text-loss" role="alert">
          {errMsg}
        </p>
      )}
    </div>
  );
}

const inputCls =
  "w-full rounded-md border border-hairline bg-paper px-2.5 py-2 text-sm text-ink focus:border-navy focus:outline-none";

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <span className="mb-1 flex items-baseline gap-1.5">
        <span className="text-xs font-medium text-ink">{label}</span>
        {hint && <span className="font-mono text-[10px] text-muted">{hint}</span>}
      </span>
      {children}
    </label>
  );
}
