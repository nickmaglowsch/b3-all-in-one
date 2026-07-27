"use client";

// See LineSeries.tsx — recharts is loaded on demand, not in the route's initial
// chunk. Consumers import this exactly as before.
import dynamic from "next/dynamic";
import { ChartSkeleton } from "./ChartSkeleton";
import type { DivergingBarsProps } from "./DivergingBars.impl";

export type { DivergingDatum } from "./DivergingBars.impl";

const Impl = dynamic(
  () => import("./DivergingBars.impl").then((m) => m.DivergingBars),
  { ssr: false, loading: () => <ChartSkeleton height={320} /> },
);

export function DivergingBars(props: DivergingBarsProps) {
  return <Impl {...props} />;
}
