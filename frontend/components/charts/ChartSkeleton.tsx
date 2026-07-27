"use client";

/** Placeholder held while a chart's recharts bundle loads.
 *
 * Sized from the same `height` the chart will take, so the surrounding layout
 * does not shift when it swaps in — a chart that pops into place and pushes the
 * rest of the page down is worse than a slightly later chart.
 */
export function ChartSkeleton({ height }: { height: number }) {
  return (
    <div
      style={{ width: "100%", height }}
      className="animate-pulse rounded-md bg-ink/5"
      aria-hidden
    />
  );
}
