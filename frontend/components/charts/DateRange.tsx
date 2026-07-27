"use client";

import { useState } from "react";
import { sliceByDate, type Dated } from "@/lib/chartRange";

/** Empty string on either side means "unbounded". */
export type Range = { from: string; to: string };

export const FULL_RANGE: Range = { from: "", to: "" };

export const isFiltered = (r: Range) => Boolean(r.from || r.to);

/**
 * State + control for windowing a dated chart.
 *
 * Returns the control so a caller can drop it into a Card's `action` slot while
 * the chart itself stays where it is — the two live in different parts of the
 * tree but share one piece of state, and threading a setter through both was
 * the only alternative.
 */
export function useDateRange(bounds?: {
  min?: string;
  max?: string;
  /**
   * Names the chart being filtered, appended to each field's accessible name.
   * Required wherever a page carries two of these — otherwise a screen reader
   * listing the form controls hears "Início do período" twice with nothing to
   * tell the equity curve from the drawdown.
   */
  of?: string;
}) {
  const [range, setRange] = useState<Range>(FULL_RANGE);
  return {
    range,
    /** Narrow `rows` to the chosen window. */
    slice: <T extends Dated>(rows: T[]) => sliceByDate(rows, range.from, range.to),
    control: <DateRange value={range} onChange={setRange} {...bounds} />,
  };
}

const field =
  "rounded-md border border-hairline bg-paper px-2 py-1 font-mono text-xs " +
  "text-ink focus:border-navy focus:outline-none";

/**
 * Two native date inputs. `<input type="date">` is the platform's own picker:
 * localised, keyboard-navigable and screen-reader-labelled for free, which no
 * hand-rolled calendar in a 450KB dependency would have matched.
 *
 * `min`/`max` steer the picker towards the data's own span; they are a courtesy
 * rather than a guarantee, since the field is still typeable — the slicing
 * downstream is what actually has to hold, and it does for any pair of values.
 */
export function DateRange({
  value,
  onChange,
  min,
  max,
  of,
}: {
  value: Range;
  onChange: (r: Range) => void;
  min?: string;
  max?: string;
  of?: string;
}) {
  const named = (side: string) => (of ? `${side} do período — ${of}` : `${side} do período`);
  return (
    <div className="flex flex-wrap items-center gap-2">
      <input
        type="date"
        value={value.from}
        min={min}
        max={value.to || max}
        onChange={(e) => onChange({ ...value, from: e.target.value })}
        aria-label={named("Início")}
        className={field}
      />
      <span aria-hidden className="text-xs text-muted">
        até
      </span>
      <input
        type="date"
        value={value.to}
        min={value.from || min}
        max={max}
        onChange={(e) => onChange({ ...value, to: e.target.value })}
        aria-label={named("Fim")}
        className={field}
      />
      {/* Rendered only when there is something to clear: a permanently visible
          reset reads as a control rather than as an escape hatch. */}
      {isFiltered(value) && (
        <button
          type="button"
          onClick={() => onChange(FULL_RANGE)}
          // Visible text stays short; the accessible name carries the chart, so
          // two resets on one page are distinguishable out of context.
          aria-label={of ? `Todo o período — ${of}` : undefined}
          className="rounded-md border border-hairline px-2 py-1 text-xs font-medium text-ink transition-colors hover:bg-hairline/40"
        >
          Todo o período
        </button>
      )}
    </div>
  );
}
