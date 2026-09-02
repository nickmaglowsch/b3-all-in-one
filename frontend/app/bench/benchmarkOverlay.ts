"use client";

// Everything the compare chart needs to draw rulers beside the runs: the
// catalogue, the levels for the window, the lines' identity (colour, dash) and
// the names of the ones it could not draw.
//
// A hook rather than four calls in the component, because the four are one
// question — "what am I comparing against, and could I get it" — and answering
// it inline is where a chart component turns into a data layer.

import type { Series } from "@/components/charts/LineSeries";
import type { Levels } from "@/lib/benchmarks";
import type { Dated } from "@/lib/chartRange";
import { type Benchmark, useBenchmarks, useBenchmarkSeries } from "./lib";

// Dashed, always. A ruler is not the same *kind* of line as a backtest, and
// colour alone would put the two in one visual class — the comparison the chart
// exists to make would then depend on remembering which hue was which.
const BENCH_DASH = "5 3";

// Benchmark colours run backwards from the end of the palette while run colours
// run forwards from the start (lib's `seriesColor`), so the two only collide
// once four runs are on the chart — and there the dash still separates them.
// Fixed to the catalogue's order rather than the selection's, so checking a
// second benchmark never recolours the first.
export const benchColor = (i: number) => `var(--series-${8 - (i % 8)})`;

export interface BenchmarkOverlay {
  catalog: Benchmark[];
  /** Raw levels, keyed by benchmark then ISO date — for `withBenchmarks`. */
  levels: Record<string, Levels>;
  /** Selected but not drawable: the plane has nothing for this window, or the
   *  engine could not be reached. Named on the picker, never dropped. */
  unavailable: string[];
  /** The lines to add to the chart — only the ones that actually have levels. */
  series: Series[];
  colorOf: (key: string) => string;
}

/**
 * `picked` benchmarks over the span `rows` covers.
 *
 * The span is the runs' whole overlap, not the visible window: the date filter
 * is a slice of what is already in hand everywhere else in the app, and
 * refetching a ruler on every keystroke in the date field would make this the
 * one thing that isn't.
 */
export function useBenchmarkOverlay(
  rows: Dated[],
  picked: string[],
): BenchmarkOverlay {
  const catalog = useBenchmarks();
  const bench = useBenchmarkSeries(
    picked,
    rows[0]?.date,
    rows[rows.length - 1]?.date,
  );

  const entries = catalog.data ?? [];
  const levels = bench.data?.series ?? {};
  const index = (key: string) => entries.findIndex((b) => b.key === key);

  return {
    catalog: entries,
    levels,
    unavailable: bench.data?.unavailable ?? [],
    series: entries
      .filter((b) => picked.includes(b.key) && b.key in levels)
      .map((b) => ({
        key: b.key,
        label: b.label,
        color: benchColor(index(b.key)),
        dash: BENCH_DASH,
      })),
    colorOf: (key) => benchColor(index(key)),
  };
}
