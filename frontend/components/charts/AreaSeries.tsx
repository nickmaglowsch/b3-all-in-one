"use client";

import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { CHART, SERIES, TICK_STYLE } from "./theme";
import { ChartTooltip } from "./ChartTooltip";

type Row = Record<string, string | number>;

// Single filled area — built for drawdown. One value axis, thin stroke, low-alpha
// fill of the same hue.
export function AreaSeries({
  data,
  xKey,
  yKey,
  label,
  color = SERIES[0],
  height = 200,
  valueFormat,
  labelFormat,
}: {
  data: Row[];
  xKey: string;
  yKey: string;
  label: string;
  color?: string;
  height?: number;
  valueFormat?: (v: number) => string;
  labelFormat?: (v: string) => string;
}) {
  const gid = `area-${String(yKey)}`;
  return (
    <div style={{ width: "100%", height }}>
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 6, right: 12, bottom: 0, left: 4 }}>
          <defs>
            <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={color} stopOpacity={0.18} />
              <stop offset="100%" stopColor={color} stopOpacity={0.02} />
            </linearGradient>
          </defs>
          <CartesianGrid stroke={CHART.grid} vertical={false} />
          <XAxis
            dataKey={xKey}
            tick={TICK_STYLE}
            tickLine={false}
            axisLine={{ stroke: CHART.axis }}
            minTickGap={40}
            tickFormatter={labelFormat}
          />
          <YAxis
            tick={TICK_STYLE}
            tickLine={false}
            axisLine={false}
            width={56}
            tickFormatter={valueFormat}
          />
          <Tooltip
            cursor={{ stroke: CHART.axis, strokeDasharray: "3 3" }}
            content={
              <ChartTooltip valueFormat={valueFormat} labelFormat={labelFormat} />
            }
          />
          <Area
            type="monotone"
            dataKey={yKey}
            name={label}
            stroke={color}
            strokeWidth={2}
            fill={`url(#${gid})`}
            isAnimationActive={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
