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
import { NoData } from "./NoData";

// `right` is half the widest x tick ("04/09/2026" at 11px mono is ~66px). A tick
// is centred on its point and the last point sits at the plot's right edge, so
// with the old 12px margin the final date was cut in half on a phone — the one
// label that says how current the chart is.
const MARGIN = { top: 6, right: 34, bottom: 0, left: 4 } as const;

type Row = Record<string, string | number>;

// Single filled area — built for drawdown. One value axis, thin stroke, low-alpha
// fill of the same hue.
export interface AreaSeriesProps {
  data: Row[];
  xKey: string;
  yKey: string;
  label: string;
  color?: string;
  height?: number;
  valueFormat?: (v: number) => string;
  labelFormat?: (v: string) => string;
}

export function AreaSeries({
  data,
  xKey,
  yKey,
  label,
  color = SERIES[0],
  height = 200,
  valueFormat,
  labelFormat,
}: AreaSeriesProps) {
  // See LineSeries.impl: bare axes read as "zero", not as "empty window".
  if (data.length === 0) return <NoData height={height} />;
  const gid = `area-${String(yKey)}`;
  return (
    <div style={{ width: "100%", height }}>
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={MARGIN}>
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
