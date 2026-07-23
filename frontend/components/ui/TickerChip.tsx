export function TickerChip({
  ticker,
  className = "",
}: {
  ticker: string;
  className?: string;
}) {
  return (
    <span
      className={`inline-flex items-center rounded-[4px] border border-hairline bg-paper px-1.5 py-0.5 font-mono text-[11px] font-medium uppercase leading-none tracking-wide text-ink ${className}`}
    >
      {ticker}
    </span>
  );
}
