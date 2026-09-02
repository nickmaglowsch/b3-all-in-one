// Money charts, read two ways. Every equity curve in the app plots reais, which
// answers "how much do I have" and is the wrong axis for "how did this do":
// two runs that started from different capital are incomparable in reais, and a
// benchmark is not denominated in a portfolio's money at all.
//
// So the same rows get a second reading — return from the window's own start —
// and the toggle that picks between them is one piece of shared state per chart.
// The transform is here, pure and import-free, because `npm test` runs this file
// through `node --test` directly and the arithmetic is the part worth pinning.
//
// Companion to chartRange's `rebaseRows`, deliberately not the same function:
// that one restarts a base-100 *index* at 100 for a portfolio line that is
// already unitless, this one turns a money curve into a fraction and has to
// answer for series that begin mid-window, which an overlay of runs always has.

import type { Dated } from "./chartRange";

/** What a money chart is currently plotting. */
export type ChartMode = "value" | "return";

// Six places on a fraction is a hundredth of a basis point — past anything an
// axis or a tooltip renders, and enough to keep 121000/100000 from arriving as
// 0.20999999999999996.
const round = (v: number, places = 6) => Number(v.toFixed(places));

const firstFinite = (rows: Record<string, unknown>[], key: string): number | null => {
  for (const row of rows) {
    const v = row[key];
    if (typeof v === "number" && Number.isFinite(v)) return v;
  }
  return null;
};

const numericKeys = (rows: Record<string, unknown>[]): string[] => {
  const keys = new Set<string>();
  for (const row of rows) {
    for (const [k, v] of Object.entries(row)) {
      if (typeof v === "number" && Number.isFinite(v)) keys.add(k);
    }
  }
  return [...keys];
};

/**
 * Restate every numeric series as a fraction of return from its own first value
 * in `rows` — 0 at the start of the chart, 0.21 for +21%.
 *
 * Per series, not per row, and that is the whole subtlety. An overlay of runs
 * (or of a run against a benchmark) is one date axis with holes in front of
 * whatever started later; basing those on the first *row* would divide by a
 * value they do not have. Each line starts at its own 0%, which is the only
 * honest reading — and the caption has to say the lines start on different days,
 * because the chart no longer shows it.
 *
 * A series whose base is 0 is dropped rather than divided by: Infinity blanks
 * the chart and 0 would claim the line was flat. Returns new rows — the toggle
 * has to be reversible, so the caller's money rows are never touched.
 */
export function toReturnRows<T extends Dated>(rows: T[]): T[] {
  const bases = new Map<string, number>();
  for (const key of numericKeys(rows)) {
    const base = firstFinite(rows, key);
    if (base) bases.set(key, base);
  }
  return rows.map((row) => {
    const out: Record<string, unknown> = { ...row };
    for (const key of numericKeys([row])) {
      const base = bases.get(key);
      const v = out[key] as number;
      if (base === undefined) delete out[key];
      else out[key] = round(v / base - 1);
    }
    return out as T;
  });
}

/**
 * Total return of one column over the rows it actually appears in, as a
 * fraction — or null when the column is not on the chart at all.
 *
 * null rather than 0: a benchmark the plane could not serve and a benchmark
 * that went nowhere are different answers, and the caption prints them
 * differently.
 */
export function columnReturn<T extends Dated>(rows: T[], key: string): number | null {
  const first = firstFinite(rows, key);
  const last = firstFinite([...rows].reverse(), key);
  if (!first || last === null) return null;
  return round(last / first - 1);
}
