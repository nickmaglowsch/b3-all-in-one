"use client";

import type { TooltipContentProps } from "recharts";

// Shared crosshair tooltip: panel surface, hairline ring, mono tabular values.
// Recharts injects active/payload/label via cloneElement, so they're optional
// here — the formatters are what callers actually pass.
export function ChartTooltip({
  active,
  payload,
  label,
  labelFormat,
  valueFormat,
}: Partial<TooltipContentProps<number, string>> & {
  labelFormat?: (v: string) => string;
  valueFormat?: (v: number) => string;
}) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-md border border-hairline bg-panel px-3 py-2 text-xs shadow-sm">
      {label != null && (
        <p className="mb-1 font-mono text-[11px] text-muted">
          {labelFormat ? labelFormat(String(label)) : String(label)}
        </p>
      )}
      <ul className="space-y-0.5">
        {payload.map((p, i) => (
          <li key={i} className="flex items-center gap-2">
            <span
              className="inline-block h-2 w-2 rounded-full"
              style={{ background: p.color }}
            />
            <span className="text-muted">{p.name}</span>
            <span className="ml-auto font-mono tnum tabular-nums text-ink">
              {typeof p.value === "number" && valueFormat
                ? valueFormat(p.value)
                : String(p.value)}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
