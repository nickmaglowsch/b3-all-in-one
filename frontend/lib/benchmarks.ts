// Putting a backtest next to a ruler it did not write.
//
// The compare panel could only overlay runs on other runs, which answers "which
// of my ideas was better" and never "was either worth the trouble". The rulers —
// CDI, IPCA, IBOV, S&P 500 and bitcoin in reais — come off /benchmarks/series as
// raw daily levels on their own scales and their own calendars, and this module
// is the two steps that turn them into columns the chart can draw beside a NAV.
//
// Pure and import-free (bar a type), so `npm test` runs it through `node --test`
// directly: the alignment is the part that goes quietly wrong.

import type { Dated } from "./chartRange";

/** Raw daily levels from the API, keyed by ISO date. IBOV in points, CDI and
 *  IPCA as accrual factors, the other two as prices in reais — the scales are
 *  incomparable, which is exactly why nothing is drawn before `withBenchmarks`
 *  puts them all on the chart's own. */
export type Levels = Record<string, number>;

const day = (iso: string) => iso.slice(0, 10);

/**
 * One benchmark's levels forward-filled onto `dates`.
 *
 * Forward-filled because the calendars never line up: CDI accrues on bank
 * business days, IPCA publishes once a month, the grid is the exchange's. A day
 * the ruler did not publish is a flat day.
 *
 * Before its first published day the answer is `undefined` and not 0 — a zero
 * would draw the ruler on the floor and then leaping onto the chart, which is a
 * shape it never had. Recharts renders the gap as a line that starts later,
 * which is what actually happened.
 */
export function alignLevels(dates: string[], levels: Levels): (number | undefined)[] {
  // ISO dates sort lexicographically, and comparing strings is also what keeps a
  // date-only value from sliding across midnight in a non-UTC browser.
  const published = Object.entries(levels)
    .map(([d, v]) => [day(d), v] as const)
    .sort((a, b) => (a[0] < b[0] ? -1 : 1));

  const out: (number | undefined)[] = [];
  let i = 0;
  let last: number | undefined;
  for (const date of dates) {
    const d = day(date);
    while (i < published.length && published[i][0] <= d) last = published[i++][1];
    out.push(last);
  }
  return out;
}

/**
 * `rows` with one extra column per benchmark, scaled so each starts at `anchor`
 * on the first row it covers.
 *
 * `anchor` is the money the chart starts from — the selected runs' own starting
 * NAV. In reais that makes the ruler read as "the same capital, in this
 * instead", which is the only comparison a money axis can carry: an index in
 * points beside a NAV in reais flattens both lines against an axis neither of
 * them is on. In percent mode chartMode's `toReturnRows` rebases every column to
 * 0% anyway, so the anchor cancels and the two modes agree.
 *
 * A ruler with nothing inside the window adds no column at all, and the caller
 * reports it as unavailable — a line that silently never appears is
 * indistinguishable from one nobody selected.
 */
export function withBenchmarks<T extends Dated>(
  rows: T[],
  levels: Record<string, Levels>,
  anchor: number,
): T[] {
  const names = Object.keys(levels);
  if (names.length === 0 || !rows.length) return rows;

  const dates = rows.map((r) => r.date);
  const columns: [string, (number | undefined)[], number][] = [];
  for (const name of names) {
    const aligned = alignLevels(dates, levels[name]);
    const base = aligned.find((v) => typeof v === "number" && Number.isFinite(v));
    // No base and no anchor are the same refusal: there is no scale to draw on.
    if (base && anchor) columns.push([name, aligned, (anchor / base)]);
  }
  if (columns.length === 0) return rows;

  return rows.map((row, i) => {
    const out: Record<string, unknown> = { ...row };
    for (const [name, aligned, scale] of columns) {
      const v = aligned[i];
      if (v !== undefined) out[name] = v * scale;
    }
    return out as T;
  });
}

/** A line the API drew off its market's proxy (sp500 from SPY): the proxy, and
 *  the date it took over — null when it is the whole line. */
export interface Proxied {
  proxy: string;
  from: string | null;
}

/** The text under a benchmark in the picker: what the number is — or that it
 *  has nothing here, or that part of it is the proxy and not the series itself. */
export function benchmarkNote(note: string, missing: boolean, proxied?: Proxied): string {
  if (missing) return "sem série para este período";
  if (!proxied) return note;
  if (!proxied.from) {
    return `${note} · lido do ${proxied.proxy} no plano no período todo (a série própria está vazia)`;
  }
  const from = proxied.from.split("-").reverse().join("/");
  return `${note} · a partir de ${from}, lido do ${proxied.proxy} no plano (a série própria parou)`;
}
