"use client";

// /portfolio leads with the positions table and the tax panel; the allocation
// donut sits below both, so it has no business blocking either.
import { lazyChart } from "./lazy";
import type { AllocationDonutProps } from "./AllocationDonut.impl";

export type { AllocationSlice } from "./AllocationDonut.impl";

export const AllocationDonut = lazyChart<AllocationDonutProps>(
  () => import("./AllocationDonut.impl").then((m) => m.AllocationDonut),
  200,
);
