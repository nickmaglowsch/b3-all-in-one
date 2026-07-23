import { formatPercent } from "@/lib/format";

// ponytail: inline SVG, not recharts — one ResponsiveContainer per row across
// 50+ rows is a resize-observer storm. A sparkline is a viewBox and a stroke.
// Ceiling: no tooltip/hover, no dates. Reach for a real chart if that's wanted.
export function Sparkline({
  values,
  width = 72,
  height = 20,
}: {
  values?: number[];
  width?: number;
  height?: number;
}) {
  if (!values || values.length < 2) return <span className="text-muted">—</span>;

  const min = Math.min(...values);
  const span = Math.max(...values) - min;
  const pad = 1; // keeps the stroke inside the viewBox at the extremes
  const points = values
    .map((v, i) => {
      const x = (i / (values.length - 1)) * width;
      // flat series → straight line down the middle instead of a divide by zero
      const y = span ? pad + (1 - (v - min) / span) * (height - 2 * pad) : height / 2;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");

  const first = values[0];
  const last = values[values.length - 1];
  const up = last >= first;
  const change = first ? (last - first) / first : 0;

  return (
    <svg
      role="img"
      aria-label={`Tendência de 6 meses: ${up ? "alta" : "baixa"} ${formatPercent(
        Math.abs(change),
      )}`}
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      className="inline-block align-middle"
    >
      <polyline
        points={points}
        fill="none"
        stroke={up ? "var(--gain)" : "var(--loss)"}
        strokeWidth={1.5}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
