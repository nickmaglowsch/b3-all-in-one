"use client";

import {
  Bar,
  BarChart,
  Cell,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { CHART, TICK_STYLE } from "./theme";
import { ChartTooltip } from "./ChartTooltip";

export interface DivergingDatum {
  label: string;
  value: number;
}

// Horizontal diverging bars around zero — built for rebalance drift. Two hues
// (positive vs negative) with a neutral gray zero line. One value axis.
export function DivergingBars({
  data,
  height = 320,
  valueFormat,
}: {
  data: DivergingDatum[];
  height?: number;
  valueFormat?: (v: number) => string;
}) {
  const max = Math.max(0.0001, ...data.map((d) => Math.abs(d.value)));
  return (
    <div style={{ width: "100%", height }}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart
          data={data}
          layout="vertical"
          margin={{ top: 4, right: 16, bottom: 0, left: 8 }}
          barCategoryGap={6}
        >
          <XAxis
            type="number"
            domain={[-max, max]}
            tick={TICK_STYLE}
            tickLine={false}
            axisLine={{ stroke: CHART.axis }}
            tickFormatter={valueFormat}
          />
          <YAxis
            type="category"
            dataKey="label"
            tick={TICK_STYLE}
            tickLine={false}
            axisLine={false}
            width={72}
          />
          <ReferenceLine x={0} stroke={CHART.axis} />
          <Tooltip
            cursor={{ fill: CHART.divergeMid, fillOpacity: 0.5 }}
            content={<ChartTooltip valueFormat={valueFormat} />}
          />
          <Bar dataKey="value" name="Desvio" isAnimationActive={false}>
            {data.map((d, i) => (
              <Cell
                key={i}
                fill={d.value >= 0 ? CHART.divergePos : CHART.divergeNeg}
              />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
