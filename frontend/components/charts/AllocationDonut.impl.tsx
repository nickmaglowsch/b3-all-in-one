"use client";

import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts";
import { SERIES } from "./theme";
import { ChartTooltip } from "./ChartTooltip";
import { formatMoney, formatPercent } from "@/lib/format";

export interface AllocationSlice {
  name: string;
  value: number;
}

export interface AllocationDonutProps {
  slices: AllocationSlice[];
  total: number;
}

// Allocation by market. Lifted out of app/portfolio/page.tsx unchanged so the
// page stops importing recharts directly — see AllocationDonut.tsx.
export function AllocationDonut({ slices, total }: AllocationDonutProps) {
  return (
    <ResponsiveContainer width="100%" height="100%">
      <PieChart
        role="img"
        aria-label={`Alocação por mercado: ${slices
          .map(
            (m) =>
              `${m.name} ${formatPercent(total > 0 ? m.value / total : 0)}`,
          )
          .join(", ")}`}
      >
        <Pie
          data={slices}
          dataKey="value"
          nameKey="name"
          innerRadius="58%"
          outerRadius="88%"
          paddingAngle={1}
          stroke="var(--panel)"
          isAnimationActive={false}
        >
          {slices.map((m, i) => (
            <Cell key={m.name} fill={SERIES[i % SERIES.length]} />
          ))}
        </Pie>
        <Tooltip content={<ChartTooltip valueFormat={(v) => formatMoney(v)} />} />
      </PieChart>
    </ResponsiveContainer>
  );
}
