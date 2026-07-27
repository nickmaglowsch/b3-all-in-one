"use client";

import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { CHART, SERIES, TICK_STYLE } from "./theme";
import { ChartTooltip } from "./ChartTooltip";

export interface Series {
  key: string;
  label: string;
  color?: string;
}

// `undefined` is allowed: a series need not cover every row (an overlay the API
// could not fetch is simply absent from each point, and recharts skips it).
type Row = Record<string, string | number | undefined>;

// Multi-series line. 2px strokes, recessive hairline grid, crosshair tooltip,
// legend for ≥2 series. One value axis — never a second/right axis.
export interface LineSeriesProps {
  data: Row[];
  xKey: string;
  series: Series[];
  height?: number;
  valueFormat?: (v: number) => string;
  labelFormat?: (v: string) => string;
}

export function LineSeries({
  data,
  xKey,
  series,
  height = 260,
  valueFormat,
  labelFormat,
}: LineSeriesProps) {
  return (
    <div style={{ width: "100%", height }}>
      {series.length >= 2 && (
        <ul className="mb-2 flex flex-wrap gap-x-4 gap-y-1">
          {series.map((s, i) => (
            <li
              key={s.key}
              className="flex items-center gap-1.5 text-xs text-muted"
            >
              <span
                className="inline-block h-0.5 w-4 rounded-full"
                style={{ background: s.color ?? SERIES[i % SERIES.length] }}
              />
              {s.label}
            </li>
          ))}
        </ul>
      )}
      <ResponsiveContainer width="100%" height={series.length >= 2 ? "88%" : "100%"}>
        <LineChart data={data} margin={{ top: 6, right: 12, bottom: 0, left: 4 }}>
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
              <ChartTooltip
                valueFormat={valueFormat}
                labelFormat={labelFormat}
              />
            }
          />
          {series.map((s, i) => (
            <Line
              key={s.key}
              type="monotone"
              dataKey={s.key}
              name={s.label}
              stroke={s.color ?? SERIES[i % SERIES.length]}
              strokeWidth={2}
              dot={false}
              activeDot={{ r: 3 }}
              isAnimationActive={false}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
