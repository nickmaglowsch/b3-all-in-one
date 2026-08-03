"use client";

import { lazyChart } from "./lazy";
import type { DivergingBarsProps } from "./DivergingBars.impl";

export type { DivergingDatum } from "./DivergingBars.impl";

export const DivergingBars = lazyChart<DivergingBarsProps>(
  () => import("./DivergingBars.impl").then((m) => m.DivergingBars),
  320,
);
