import { formatPercent } from "@/lib/format";

// `value` is a fraction: 0.012 → ▲ 1,2%. The glyph carries direction (not colour
// alone). Zero renders neutral with a dash.
export function PctDelta({
  value,
  className = "",
}: {
  value: number;
  className?: string;
}) {
  const up = value > 0;
  const down = value < 0;
  const color = up ? "text-gain" : down ? "text-loss" : "text-muted";
  const glyph = up ? "▲" : down ? "▼" : "–";
  return (
    <span
      className={`inline-flex items-center gap-1 font-mono tnum tabular-nums ${color} ${className}`}
    >
      <span aria-hidden className="text-[0.7em]">
        {glyph}
      </span>
      {formatPercent(Math.abs(value))}
    </span>
  );
}
