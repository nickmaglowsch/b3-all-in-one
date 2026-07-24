"use client";

import { useRouter } from "next/navigation";
import type { BacktestRun } from "@/lib/types";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { IconChart } from "@/components/icons";
import { formatDate } from "@/lib/format";

// Rows must be clickable → /bench/run?id=. DataTable (frozen) has no onRowClick, so
// this renders a native table in the same hairline-ruled, dense visual style.
// A leading checkbox picks runs for the comparison overlay (done runs only).
export function RunsList({
  runs,
  loading,
  selected,
  onToggle,
}: {
  runs: BacktestRun[];
  loading: boolean;
  selected: string[];
  onToggle: (id: string) => void;
}) {
  const router = useRouter();
  const picked = new Set(selected);

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
            <th className="w-9 px-3 py-2" aria-label="Comparar" />
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
          {runs.map((r) => {
            const done = r.status === "done";
            const isPicked = picked.has(r.id);
            return (
            <tr
              key={r.id}
              onClick={() => router.push(`/bench/run?id=${r.id}`)}
              className={`cursor-pointer border-b border-hairline/70 last:border-0 hover:bg-hairline/30 ${
                isPicked ? "bg-navy/[0.04]" : ""
              }`}
            >
              <td className="px-3 py-2.5 align-middle">
                <input
                  type="checkbox"
                  checked={isPicked}
                  disabled={!done}
                  onClick={(e) => e.stopPropagation()}
                  onChange={() => onToggle(r.id)}
                  aria-label={
                    done
                      ? `Comparar ${r.strategy}`
                      : "Só rodadas concluídas podem ser comparadas"
                  }
                  title={done ? "Adicionar à comparação" : "Aguardando conclusão"}
                  className="h-4 w-4 cursor-pointer accent-navy disabled:cursor-not-allowed disabled:opacity-30"
                />
              </td>
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
            );
          })}
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
