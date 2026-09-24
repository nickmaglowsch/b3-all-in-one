"use client";

import { useMemo, useState } from "react";
import type { Strategy, StrategyParam } from "@/lib/types";
import { errorText } from "@/lib/api";
import { REBALANCE_FREQS } from "@/lib/format";
import {
  buildRunParams,
  initialParamValues,
  inputAttrs,
  isSelect,
  paramError,
  tunableParams,
} from "@/lib/strategyParams";
import { EmptyState } from "@/components/ui/EmptyState";
import { IconChart } from "@/components/icons";
import { KindBadge } from "./StrategyGallery";
import { useCreateBacktest } from "./lib";
import { Field, inputCls } from "./Field";

// Ordered shortest → longest period (weekly first) so the list reads as a
// scale. The default stays "ME" (see useState below); this only orders them.
const FREQS = [REBALANCE_FREQS[2], REBALANCE_FREQS[0], REBALANCE_FREQS[1]];

export function RunPanel({
  strategy,
  onRan,
  onClone,
}: {
  strategy: Strategy | null;
  onRan: () => void;
  onClone?: (s: Strategy) => void;
}) {
  const [start, setStart] = useState("2023-01-01");
  const [end, setEnd] = useState("");
  const [freq, setFreq] = useState("ME");
  const [aporte, setAporte] = useState("");
  const create = useCreateBacktest();

  const knobs = useStrategyParams(strategy);

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
    create.mutate(
      {
        strategy: strategy.name,
        start: start || undefined,
        end: end || undefined,
        freq,
        params: buildRunParams(knobs.tunables, knobs.values, aporte),
      },
      { onSuccess: onRan },
    );
  }

  const errMsg =
    create.error ? errorText(create.error, "Falha ao iniciar o backtest.") : null;

  return (
    <div>
      <div className="mb-4 flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="text-sm font-semibold text-ink">{strategy.name}</h3>
          <p className="mt-1 text-xs leading-snug text-muted">{strategy.description}</p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {onClone && strategy.source !== "user" && (
            <button
              type="button"
              onClick={() => onClone(strategy)}
              className="rounded-md border border-hairline bg-paper px-2 py-1 text-xs font-medium text-navy hover:bg-hairline/40"
            >
              Clonar
            </button>
          )}
          <KindBadge kind={strategy.kind} />
        </div>
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

      {knobs.tunables.length > 0 && (
        <fieldset className="mt-4">
          <legend className="mb-2 text-xs font-medium text-muted">
            Parâmetros da estratégia
          </legend>
          <div className="grid grid-cols-2 gap-3">
            {knobs.tunables.map((p) => (
              <ParamField
                key={p.name}
                param={p}
                value={knobs.values[p.name] ?? ""}
                error={knobs.errors[p.name]}
                onChange={(v) => knobs.setValue(p.name, v)}
              />
            ))}
          </div>
        </fieldset>
      )}

      <button
        type="button"
        onClick={run}
        disabled={create.isPending || knobs.invalid}
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

/**
 * Form state for a strategy's own knobs: the fields to render, their current
 * text, and the per-field validation messages.
 *
 * Selecting another strategy swaps the whole knob set, so the values reset to
 * that strategy's defaults. Adjusting state during render (rather than in an
 * effect) keeps the panel from painting one frame of the previous strategy's
 * numbers under the new strategy's name. Period and aporte deliberately survive
 * the switch: they mean the same thing for every strategy, and re-typing them to
 * compare two strategies over one window is the tax this panel exists to avoid.
 */
function useStrategyParams(strategy: Strategy | null) {
  const tunables = useMemo(
    () => (strategy ? tunableParams(strategy) : []),
    [strategy],
  );
  const [values, setValues] = useState<Record<string, string>>({});
  const [valuesFor, setValuesFor] = useState<string | null>(null);
  if (strategy && valuesFor !== strategy.name) {
    setValuesFor(strategy.name);
    setValues(initialParamValues(tunables));
  }

  const errors: Record<string, string> = {};
  for (const p of tunables) {
    const err = paramError(p, values[p.name] ?? "");
    if (err) errors[p.name] = err;
  }

  return {
    tunables,
    values,
    errors,
    invalid: Object.keys(errors).length > 0,
    setValue: (name: string, v: string) =>
      setValues((prev) => ({ ...prev, [name]: v })),
  };
}

// One control per declared param: a select when the spec enumerates choices, a
// number input when it is numeric (min/max/step come straight off the spec, so
// the browser's own stepper respects the strategy's bounds), text otherwise.
// The placeholder is the engine default, so clearing a field reads as "default"
// rather than as an empty value the run would somehow send.
function ParamField({
  param,
  value,
  error,
  onChange,
}: {
  param: StrategyParam;
  value: string;
  error?: string;
  onChange: (v: string) => void;
}) {
  const cls = error ? `${inputCls} border-loss focus:border-loss` : inputCls;

  return (
    <Field label={param.label || param.name} hint={error ? undefined : param.name}>
      <ParamControl param={param} value={value} cls={cls} onChange={onChange} />
      {error && (
        <span className="mt-1 block text-[11px] text-loss" role="alert">
          {error}
        </span>
      )}
    </Field>
  );
}

function ParamControl({
  param,
  value,
  cls,
  onChange,
}: {
  param: StrategyParam;
  value: string;
  cls: string;
  onChange: (v: string) => void;
}) {
  if (isSelect(param)) {
    return (
      <select value={value} onChange={(e) => onChange(e.target.value)} className={cls}>
        {(param.choices ?? []).map((c) => (
          <option key={String(c)} value={String(c)}>
            {String(c)}
          </option>
        ))}
      </select>
    );
  }
  return (
    <input
      {...inputAttrs(param)}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={param.default === null ? "" : String(param.default)}
      className={cls}
    />
  );
}
