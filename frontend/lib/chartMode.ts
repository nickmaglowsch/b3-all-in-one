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

/** Per series, per date: the cash that entered that series on that row. */
export type Flows = Record<string, Record<string, number>>;

/**
 * Like `toReturnRows`, for curves that take deposits: the time-weighted return
 * from each series' first positive value in `rows`.
 *
 * A replay of real aportes is patrimônio, and patrimônio grows with every
 * deposit — R$10k followed by R$90k more in a flat market is "+900%" to
 * `toReturnRows`. Here each row's gain is measured net of the cash that landed
 * on it, `(V_t − C_t) / V_{t−1}`, and chained: the engine's own
 * `metrics.value_to_ret`, so the line agrees with its annual-return figure.
 *
 * The base is the first *positive* value, not the first one: a replay can open
 * on rows of zero before its first deposit is deployed, and there is no return
 * on nothing. A row whose predecessor is not positive carries the level over.
 */
export function toTwrRows<T extends Dated>(rows: T[], flows: Flows): T[] {
  const level = new Map<string, number>();
  const prev = new Map<string, number>();
  return rows.map((row) => {
    const out: Record<string, unknown> = { ...row };
    for (const key of numericKeys([row])) {
      const v = row[key as keyof T] as number;
      const before = prev.get(key);
      if (before === undefined) {
        if (v > 0) {
          level.set(key, 1);
          prev.set(key, v);
          out[key] = 0;
        } else {
          delete out[key];
        }
        continue;
      }
      const cash = flows[key]?.[row.date] ?? 0;
      const lv = before > 0 ? level.get(key)! * ((v - cash) / before) : level.get(key)!;
      level.set(key, lv);
      prev.set(key, v);
      out[key] = round(lv - 1);
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
