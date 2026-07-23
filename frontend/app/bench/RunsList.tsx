"use client";

import { useRouter } from "next/navigation";
import type { BacktestRun } from "@/lib/types";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { IconChart } from "@/components/icons";
import { formatDate } from "@/lib/format";

// Rows must be clickable → /bench/[id]. DataTable (frozen) has no onRowClick, so
// this renders a native table in the same hairline-ruled, dense visual style.
export function RunsList({
  runs,
  loading,
}: {
  runs: BacktestRun[];
  loading: boolean;
}) {
  const router = useRouter();

  if (loading) {
    return (
      <div className="space-y-2">
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} className="h-9 w-full" />
        ))}
      </div>
    );
  }

  if (runs.length === 0) {
    return (
      <EmptyState
        icon={<IconChart size={26} />}
        title="Nenhum backtest ainda — rode uma estratégia acima."
        hint="Cada rodada aparece aqui; clique numa linha para abrir o tear sheet."
      />
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-sm">
        <thead>
          <tr className="border-b border-hairline">
            {["Estratégia", "Status", "Criado"].map((h, i) => (
              <th
                key={h}
                className={`px-3 py-2 text-[11px] font-semibold uppercase tracking-wide text-muted ${
                  i === 2 ? "text-right" : "text-left"
                }`}
              >
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {runs.map((r) => (
            <tr
              key={r.id}
              onClick={() => router.push(`/bench/${r.id}`)}
              className="cursor-pointer border-b border-hairline/70 last:border-0 hover:bg-hairline/30"
            >
              <td className="px-3 py-2.5 align-middle">
                <span className="font-medium text-ink">{r.strategy}</span>
                <span className="ml-2 font-mono text-[11px] text-muted">
                  {r.id.slice(0, 8)}
                </span>
              </td>
              <td className="px-3 py-2.5 align-middle">
                <StatusBadge status={r.status} />
              </td>
              <td className="px-3 py-2.5 text-right align-middle font-mono tnum tabular-nums text-muted">
                {formatDateTime(r.created_at)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function formatDateTime(iso: string | null): string {
  if (!iso) return "—";
  const d = formatDate(iso);
  const t = iso.slice(11, 16);
  return t ? `${d} ${t}` : d;
}
