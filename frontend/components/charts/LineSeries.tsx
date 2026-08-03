"use client";

import { lazyChart } from "./lazy";
import type { LineSeriesProps } from "./LineSeries.impl";

export type { Series } from "./LineSeries.impl";

export const LineSeries = lazyChart<LineSeriesProps>(
  () => import("./LineSeries.impl").then((m) => m.LineSeries),
  260,
);
