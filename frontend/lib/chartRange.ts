// Windowing for the time-series charts. Every one of them already receives its
// whole history from the API, so a date range is a slice of what is in hand —
// no refetch, no endpoint parameter, one helper for all four charts.
//
// Deliberately import-free: `npm test` runs this file through `node --test`
// directly, and the pure math is the part worth pinning.

/** Any row a dated chart draws: an ISO `date` plus its series values. */
export type Dated = { date: string };

const day = (iso: string) => iso.slice(0, 10);

const round = (v: number, places: number) => Number(v.toFixed(places));

/**
 * Rows inside [from, to], both bounds inclusive and either one optionally open
 * (empty string = unbounded).
 *
 * ISO dates sort lexicographically, so this compares strings rather than
 * parsing Dates — which is also what keeps a date-only value from sliding
 * across midnight into the previous day in a non-UTC browser.
 */
export function sliceByDate<T extends Dated>(
  rows: T[],
  from: string,
  to: string,
): T[] {
  if (!from && !to) return rows;
  return rows.filter(
    (r) => (!from || day(r.date) >= from) && (!to || day(r.date) <= to),
  );
}

/**
 * Restart every numeric series at 100 on the window's first row.
 *
 * Only for base-100 index rows. A narrowed window whose values still count from
 * the book's inception answers the wrong question: the user asked what happened
 * *in* this window, and reading "+21%" off a stretch that gained 10% is simply
 * false. Money curves (patrimônio in BRL) must NOT go through here — their axis
 * means reais.
 *
 * A series missing from the first row has no base to divide by, so it is left
 * alone rather than turned into Infinity.
 */
export function rebaseRows<T extends Dated>(rows: T[]): T[] {
  const first = rows[0];
  if (!first) return rows;
  const base = new Map<string, number>();
  for (const [k, v] of Object.entries(first)) {
    if (typeof v === "number" && v) base.set(k, v);
  }
  if (base.size === 0) return rows;
  return rows.map((row) => {
    const out: Record<string, unknown> = { ...row };
    for (const [k, b] of base) {
      const v = out[k];
      if (typeof v === "number") out[k] = round((100 * v) / b, 4);
    }
    return out as T;
  });
}

export interface RangeMetrics {
  total_return_pct: number | null;
  /** null under a year — annualising a short run reports a number nobody
   *  should act on. Same rule as the API. */
  ann_return_pct: number | null;
  max_dd_pct: number | null;
}

/**
 * Elapsed calendar years between two ISO dates.
 *
 * Not sessions/252. That proxy was good enough while the only window was the
 * whole book, but a date picker hands the user windows chosen on the calendar,
 * and B3 runs ~248-251 sessions a year — so calendar 2025 counts 250 sessions,
 * lands at 0.992 "years", and a full twelve months gets captioned "período < 1
 * ano". 365.25 absorbs the leap day.
 */
export function yearsBetween(from: string, to: string): number {
  const ms = Date.parse(`${to.slice(0, 10)}T00:00:00Z`) -
    Date.parse(`${from.slice(0, 10)}T00:00:00Z`);
  return Number.isNaN(ms) ? 0 : ms / (365.25 * 24 * 3600 * 1000);
}

// Shortest window `summarize` will annualise — platform_app/portfolio_index.py
// MIN_ANNUALISE_YEARS, and the note there explains the 0.99. Short version: a
// calendar year picked off this filter is 01-01 → 12-31, i.e. 364 elapsed days.
const MIN_ANNUALISE_YEARS = 0.99;

/** Worst peak-to-trough fall, as a non-positive percentage. */
function maxDrawdownPct(values: number[]): number {
  let peak = 0;
  let worst = 0;
  for (const v of values) {
    peak = Math.max(peak, v);
    if (peak > 0) worst = Math.min(worst, v / peak - 1);
  }
  return round(100 * worst, 2);
}

/**
 * Total return, CAGR and max drawdown of a base-100 index over `years` of
 * elapsed calendar time (see yearsBetween — the callers pass the window's own
 * span, never a session count).
 *
 * A port of platform_app/portfolio_index.summarize — keep the two in step; the
 * test below pins them against the same numbers. It lives here because the API
 * can only summarise the window it computed, and the date filter moves that
 * window client-side; showing the full period's figures over a narrowed chart
 * would caption it with numbers it does not contain.
 *
 * `index[-1] / index[0]` is exact on a chained TWR series: the ratio between
 * any two points *is* the return between them, which is why the window can be
 * cut here rather than recomputed by the engine.
 */
export function summarize(index: number[], years: number): RangeMetrics {
  if (index.length === 0 || !index[0]) {
    return { total_return_pct: null, ann_return_pct: null, max_dd_pct: null };
  }
  const total = index[index.length - 1] / index[0] - 1;
  return {
    total_return_pct: round(100 * total, 2),
    ann_return_pct:
      years >= MIN_ANNUALISE_YEARS && 1 + total > 0
        ? round(100 * ((1 + total) ** (1 / years) - 1), 2)
        : null,
    max_dd_pct: maxDrawdownPct(index),
  };
}
