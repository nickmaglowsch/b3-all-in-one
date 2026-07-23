import { formatMoney } from "@/lib/format";

// `sign`: colour red/green and show an explicit + for positives — use ONLY where
// the sign carries meaning (P&L, deltas). Plain balances stay ink.
export function Money({
  value,
  sign = false,
  className = "",
}: {
  value: number;
  sign?: boolean;
  className?: string;
}) {
  const color = !sign
    ? "text-ink"
    : value > 0
      ? "text-gain"
      : value < 0
        ? "text-loss"
        : "text-muted";
  return (
    <span className={`font-mono tnum tabular-nums ${color} ${className}`}>
      {formatMoney(value, sign)}
    </span>
  );
}
