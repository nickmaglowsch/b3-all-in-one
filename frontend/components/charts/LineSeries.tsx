"use client";

// recharts is ~450KB and every route that draws anything pulled it into its
// initial chunk. Loaded on demand instead, so the page shell — table, figures,
// controls — parses and paints without waiting for the charting library.
//
// The bytes still ship; they just stop blocking first render. Consumers import
// this exactly as before.
import dynamic from "next/dynamic";
import { ChartSkeleton } from "./ChartSkeleton";
import type { LineSeriesProps } from "./LineSeries.impl";

export type { Series } from "./LineSeries.impl";

const Impl = dynamic(() => import("./LineSeries.impl").then((m) => m.LineSeries), {
  ssr: false,
  loading: () => <ChartSkeleton height={260} />,
});

export function LineSeries(props: LineSeriesProps) {
  return <Impl {...props} />;
}
