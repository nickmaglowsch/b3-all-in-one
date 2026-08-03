"use client";

import { lazyChart } from "./lazy";
import type { AreaSeriesProps } from "./AreaSeries.impl";

export const AreaSeries = lazyChart<AreaSeriesProps>(
  () => import("./AreaSeries.impl").then((m) => m.AreaSeries),
  200,
);
