// The simulação page's small presentational pieces, kept apart so page.tsx stays
// the page: its state, the strategy picker and the results.
import { Card } from "@/components/ui/Card";
import { Skeleton } from "@/components/ui/Skeleton";

export function StatTile({
  label,
  value,
  sub,
}: {
  label: string;
  value: React.ReactNode;
  sub?: string;
}) {
  return (
    <div className="rounded-lg border border-hairline bg-panel p-4">
      <p className="font-mono text-[10px] font-medium uppercase tracking-[0.12em] text-muted">
        {label}
      </p>
      <p className="mt-1.5 text-2xl font-extrabold leading-none tracking-tight text-ink">
        {value}
      </p>
      {sub && <p className="mt-1.5 font-mono text-[11px] text-muted">{sub}</p>}
    </div>
  );
}

export function PendingCard() {
  return (
    <Card>
      <div className="flex flex-col items-center justify-center gap-3 py-12 text-center">
        <p className="text-sm text-muted">
          Reaplicando seus aportes na(s) estratégia(s) — isso roda o motor de
          backtest e pode levar alguns segundos.
        </p>
        <div className="grid w-full max-w-md grid-cols-3 gap-2">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-16" />
          ))}
        </div>
        <Skeleton className="h-[240px] w-full" />
      </div>
    </Card>
  );
}
