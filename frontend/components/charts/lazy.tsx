"use client";

// recharts is ~450KB and every route that draws anything pulled it into its
// initial chunk. Charts are loaded on demand instead, so the page shell — table,
// figures, controls — parses and paints without waiting for the charting library.
// The bytes still ship; they just stop blocking first render.
//
// Each chart keeps a thin `X.tsx` in front of its `X.impl.tsx` so consumers import
// it exactly as before. Those wrappers were four copies of the call below, which
// is the only part that was ever the same.
import dynamic from "next/dynamic";
import type { ComponentType } from "react";

import { ChartSkeleton } from "./ChartSkeleton";

/** `height` is the chart's own height: the placeholder reserves it so the layout
 *  does not shift when the real chart swaps in. */
export function lazyChart<P extends object>(
  load: () => Promise<ComponentType<P>>,
  height: number,
) {
  return dynamic(load, {
    ssr: false,
    loading: () => <ChartSkeleton height={height} />,
  });
}
