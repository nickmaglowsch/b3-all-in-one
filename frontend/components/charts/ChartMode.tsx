"use client";

import { useState } from "react";
import { toReturnRows, type ChartMode } from "@/lib/chartMode";
import type { Dated } from "@/lib/chartRange";
import { formatPercent } from "@/lib/format";

export type { ChartMode } from "@/lib/chartMode";

/**
 * State + control for reading a money chart either way: reais, or return from
 * the window's own first day.
 *
 * Shaped like `useDateRange` and for the same reason — the control belongs in
 * the Card's `action` slot while the chart stays where it is, and threading a
 * setter through both was the only alternative.
 *
 * Order matters at the call site: slice by date *first*, then `apply`. The
 * promise of the percent reading is that 0% is the start of the chart, and a
 * chart narrowed after the rebase would start somewhere else.
 *
 * `format` is the money formatter for the value reading; the percent one is
 * fixed, because a fraction has exactly one sensible rendering here.
 */
export function useChartMode({
  format,
  of,
}: {
  format: (v: number) => string;
  /** Names the chart, appended to the group's accessible name. Required
   *  wherever a page carries two of these — the same rule as useDateRange. */
  of?: string;
}) {
  const [mode, setMode] = useState<ChartMode>("value");
  const isReturn = mode === "return";
  return {
    mode,
    isReturn,
    /** Money rows → return rows, or through unchanged. */
    apply: <T extends Dated>(rows: T[]) => (isReturn ? toReturnRows(rows) : rows),
    valueFormat: isReturn ? formatPercent : format,
    control: <ChartModeToggle mode={mode} onChange={setMode} of={of} />,
  };
}

const LABEL: Record<ChartMode, { short: string; long: string }> = {
  value: { short: "R$", long: "em reais" },
  return: { short: "%", long: "retorno desde o início do gráfico" },
};

/**
 * Two buttons, not a select: there are exactly two readings and both fit as
 * one-character labels, so the state is legible without opening anything. The
 * accessible names carry the long form — "R$" alone is not a sentence.
 */
export function ChartModeToggle({
  mode,
  onChange,
  of,
}: {
  mode: ChartMode;
  onChange: (m: ChartMode) => void;
  of?: string;
}) {
  const named = (m: ChartMode) =>
    of ? `Ver ${LABEL[m].long} — ${of}` : `Ver ${LABEL[m].long}`;
  return (
    <div
      role="group"
      aria-label={of ? `Unidade do gráfico — ${of}` : "Unidade do gráfico"}
      className="inline-flex rounded-md border border-hairline p-0.5 text-xs"
    >
      {(["value", "return"] as ChartMode[]).map((m) => (
        <button
          key={m}
          type="button"
          onClick={() => onChange(m)}
          aria-pressed={mode === m}
          title={named(m)}
          className={`rounded px-2.5 py-1 font-mono transition-colors ${
            mode === m
              ? "bg-navy font-medium text-white"
              : "text-muted hover:text-ink"
          }`}
        >
          <span aria-hidden>{LABEL[m].short}</span>
          <span className="sr-only">{named(m)}</span>
        </button>
      ))}
    </div>
  );
}
