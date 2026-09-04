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
import { NoData } from "./NoData";

// `right` is half the widest x tick ("04/09/2026" at 11px mono is ~66px). A tick
// is centred on its point and the last point sits at the plot's right edge, so
// with the old 12px margin the final date was cut in half on a phone — the one
// label that says how current the chart is.
const MARGIN = { top: 6, right: 34, bottom: 0, left: 4 } as const;

export interface Series {
  key: string;
  label: string;
  color?: string;
  /** SVG dash pattern. Set it for a line that is not the same *kind* of thing
   *  as the others — a benchmark beside a set of backtests. Colour alone puts
   *  the ruler in the same visual class as the runs it is there to judge. */
  dash?: string;
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
  // recharts draws bare axes for an empty array, which reads as "the data is
  // zero" rather than "there is nothing here". Reachable from every date filter
  // (an inverted range, a holiday-only span), so it is answered once here
  // instead of at each of the four call sites.
  if (data.length === 0) return <NoData height={height} />;
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
                style={
                  // The swatch has to carry the dash too, or the legend claims
                  // every line is the same kind of line.
                  s.dash
                    ? {
                        backgroundImage: `repeating-linear-gradient(to right, ${
                          s.color ?? SERIES[i % SERIES.length]
                        } 0 4px, transparent 4px 7px)`,
                      }
                    : { background: s.color ?? SERIES[i % SERIES.length] }
                }
              />
              {s.label}
            </li>
          ))}
        </ul>
      )}
      <ResponsiveContainer width="100%" height={series.length >= 2 ? "88%" : "100%"}>
        <LineChart data={data} margin={MARGIN}>
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
              strokeDasharray={s.dash}
              strokeWidth={s.dash ? 1.5 : 2}
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
