"use client";

// See LineSeries.tsx — recharts is loaded on demand, not in the route's initial
// chunk. /portfolio leads with the positions table and the tax panel; the
// allocation donut sits below both, so it has no business blocking either.
import dynamic from "next/dynamic";
import { ChartSkeleton } from "./ChartSkeleton";
import type { AllocationDonutProps } from "./AllocationDonut.impl";

export type { AllocationSlice } from "./AllocationDonut.impl";

const Impl = dynamic(
  () => import("./AllocationDonut.impl").then((m) => m.AllocationDonut),
  { ssr: false, loading: () => <ChartSkeleton height={200} /> },
);

export function AllocationDonut(props: AllocationDonutProps) {
  return <Impl {...props} />;
}
