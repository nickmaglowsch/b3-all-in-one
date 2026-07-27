"use client";

// See LineSeries.tsx — recharts is loaded on demand, not in the route's initial
// chunk. Consumers import this exactly as before.
import dynamic from "next/dynamic";
import { ChartSkeleton } from "./ChartSkeleton";
import type { AreaSeriesProps } from "./AreaSeries.impl";

const Impl = dynamic(() => import("./AreaSeries.impl").then((m) => m.AreaSeries), {
  ssr: false,
  loading: () => <ChartSkeleton height={200} />,
});

export function AreaSeries(props: AreaSeriesProps) {
  return <Impl {...props} />;
}
