import { formatPercent, fractionShown } from "@/lib/format";

// `value` is a fraction: 0.012 → ▲ 1,2%. The glyph carries direction for sighted
// users; the printed sign carries it for everyone else. Do not wrap the number in
// Math.abs to "avoid duplicating" the glyph — the glyph is aria-hidden, so that
// leaves assistive tech announcing a loss as a gain. Zero renders neutral.
export function PctDelta({
  value,
  className = "",
}: {
  value: number;
  className?: string;
}) {
  const shown = fractionShown(value); // the glyph follows the printed digits
  const up = shown > 0;
  const down = shown < 0;
  const color = up ? "text-gain" : down ? "text-loss" : "text-muted";
  const glyph = up ? "▲" : down ? "▼" : "–";
  return (
    <span
      className={`inline-flex items-center gap-1 font-mono tnum tabular-nums ${color} ${className}`}
    >
      <span aria-hidden className="text-[0.7em]">
        {glyph}
      </span>
      {formatPercent(value)}
    </span>
  );
}
