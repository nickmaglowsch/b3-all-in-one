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

const TRADING_DAYS_PER_YEAR = 252;

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
 * Total return, CAGR and max drawdown of a base-100 index.
 *
 * A port of platform_app/portfolio_index.summarize — keep the two in step. It
 * lives here because the API can only summarise the window it computed, and the
 * date filter moves that window client-side; showing the full period's figures
 * over a narrowed chart would caption it with numbers it does not contain.
 *
 * `index[-1] / index[0]` is exact on a chained TWR series: the ratio between
 * any two points *is* the return between them, which is why the window can be
 * cut here rather than recomputed by the engine.
 */
export function summarize(index: number[], nDays: number): RangeMetrics {
  if (index.length === 0 || !index[0]) {
    return { total_return_pct: null, ann_return_pct: null, max_dd_pct: null };
  }
  const total = index[index.length - 1] / index[0] - 1;
  const years = nDays / TRADING_DAYS_PER_YEAR;
  return {
    total_return_pct: round(100 * total, 2),
    ann_return_pct:
      years >= 1 && 1 + total > 0
        ? round(100 * ((1 + total) ** (1 / years) - 1), 2)
        : null,
    max_dd_pct: maxDrawdownPct(index),
  };
}
