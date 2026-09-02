"use client";

import type { Benchmark } from "./lib";

/**
 * Which rulers to overlay on the comparison chart.
 *
 * Checkboxes rather than the `<details>` dropdown the portfolio's asset filter
 * uses: there are five of them, forever (the catalogue is code), and a list that
 * fits on one line has nothing to gain from being hidden behind a trigger. The
 * note under each label is the point — a fund proxy, an inflation index and a
 * coin are three different kinds of number, and a chart that stacks them without
 * saying so invites exactly the comparison this product exists to make honest.
 */
export function BenchmarkPicker({
  catalog,
  selected,
  colorOf,
  unavailable,
  onToggle,
}: {
  catalog: Benchmark[];
  selected: string[];
  /** The colour the chart draws this ruler in — the same swatch, so a line and
   *  a checkbox can never disagree about which benchmark is which. */
  colorOf: (key: string) => string;
  /** Selected, requested, and not drawable. */
  unavailable: string[];
  onToggle: (key: string) => void;
}) {
  // Nothing to offer yet — the catalogue is still in flight, or this account
  // cannot reach it. An empty ruled box would read as "no benchmarks exist".
  if (catalog.length === 0) return null;
  return (
    <fieldset className="rounded-md border border-hairline bg-paper p-3">
      <legend className="px-1 font-mono text-[11px] font-medium uppercase tracking-[0.14em] text-muted">
        Comparar com
      </legend>
      <div className="flex flex-wrap gap-x-5 gap-y-2">
        {catalog.map((b) => {
          const on = selected.includes(b.key);
          const missing = on && unavailable.includes(b.key);
          return (
            <label
              key={b.key}
              className="flex max-w-[19rem] cursor-pointer items-start gap-2 text-xs"
              title={b.note}
            >
              <input
                type="checkbox"
                checked={on}
                onChange={() => onToggle(b.key)}
                className="mt-0.5 h-3.5 w-3.5 shrink-0 cursor-pointer accent-navy"
              />
              <span className="min-w-0">
                <span className="flex items-center gap-1.5 font-medium text-ink">
                  {on && (
                    <span
                      aria-hidden
                      className="inline-block h-0.5 w-3.5 shrink-0"
                      style={{
                        backgroundImage: `repeating-linear-gradient(to right, ${colorOf(
                          b.key,
                        )} 0 4px, transparent 4px 7px)`,
                      }}
                    />
                  )}
                  {b.label}
                </span>
                {/* Not a tooltip only: what the number is has to be readable
                    without hovering, on a touch screen included. */}
                <span className="mt-0.5 block leading-snug text-muted">
                  {missing ? "sem série para este período" : b.note}
                </span>
              </span>
            </label>
          );
        })}
      </div>
    </fieldset>
  );
}
