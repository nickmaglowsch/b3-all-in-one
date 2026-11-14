// Chart chrome pulled from the token set (CSS vars resolve inside SVG fills/strokes).
export const SERIES = [
  "var(--series-1)",
  "var(--series-2)",
  "var(--series-3)",
  "var(--series-4)",
  "var(--series-5)",
  "var(--series-6)",
  "var(--series-7)",
  "var(--series-8)",
];

export const CHART = {
  grid: "var(--hairline)",
  axis: "var(--axis)", // dataviz baseline/axis (themed)
  tick: "var(--muted)",
  surface: "var(--panel)",
  divergePos: "var(--diverge-pos)",
  divergeNeg: "var(--diverge-neg)",
  divergeMid: "var(--diverge-mid)",
};

export const TICK_STYLE = {
  fill: CHART.tick,
  fontSize: 11,
  fontFamily: "var(--font-plex-mono)",
} as const;

/** Recharts word-wraps an axis tick to the axis width, so "R$ 180 mil" broke
 *  into "R$ 180" over "mil" — two lines per tick, stacked into the next one.
 *  Non-breaking spaces keep a label one label. */
export const oneLine = (label: string) => label.replace(/ /g, "\u00a0");
