"use client";

import { useMemo, useState } from "react";
import { PageHeader } from "@/components/ui/PageHeader";
import { Card } from "@/components/ui/Card";
import { ApiError } from "@/lib/api";
import { StrategyGallery } from "./StrategyGallery";
import { RunPanel } from "./RunPanel";
import { RunsList } from "./RunsList";
import { ComparePanel } from "./ComparePanel";
import { useBacktests, useStrategies } from "./lib";

export default function BenchPage() {
  const strategies = useStrategies();
  const backtests = useBacktests();
  const [selected, setSelected] = useState<string | null>(null);
  const [compareIds, setCompareIds] = useState<string[]>([]);

  const selectedStrategy = useMemo(
    () => strategies.data?.find((s) => s.name === selected) ?? null,
    [strategies.data, selected],
  );

  // Selected runs in pick order (colours track this), pruned to still-done runs.
  const compareRuns = useMemo(() => {
    const byId = new Map((backtests.data ?? []).map((r) => [r.id, r]));
    return compareIds
      .map((id) => byId.get(id))
      .filter((r): r is NonNullable<typeof r> => r?.status === "done");
  }, [backtests.data, compareIds]);

  const toggleCompare = (id: string) =>
    setCompareIds((ids) =>
      ids.includes(id) ? ids.filter((x) => x !== id) : [...ids, id],
    );

  return (
    <>
      <PageHeader
        eyebrow="Backtests"
        title="Bench"
      >
        <span className="font-mono text-[11px] text-muted">
          pré vs pós-imposto
        </span>
      </PageHeader>

      {strategies.error && (
        <p className="mb-4 text-sm text-loss" role="alert">
          {strategies.error instanceof ApiError
            ? strategies.error.message
            : "Falha ao carregar estratégias."}
        </p>
      )}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <Card title="Galeria de estratégias">
          <StrategyGallery
            strategies={strategies.data ?? []}
            loading={strategies.isLoading}
            selected={selected}
            onSelect={setSelected}
          />
        </Card>

        <Card title="Rodar backtest">
          <RunPanel strategy={selectedStrategy} onRan={() => {}} />
        </Card>
      </div>

      <div className="mt-4">
        <Card
          title="Rodadas"
          action={
            compareIds.length > 0 ? (
              <span className="font-mono text-[11px] text-muted">
                {compareIds.length} p/ comparar
              </span>
            ) : (
              <span className="font-mono text-[11px] text-muted">
                marque 2+ p/ comparar
              </span>
            )
          }
        >
          <RunsList
            runs={backtests.data ?? []}
            loading={backtests.isLoading}
            selected={compareIds}
            onToggle={toggleCompare}
          />
        </Card>
      </div>

      {compareIds.length > 0 && (
        <div className="mt-4">
          <ComparePanel
            runs={compareRuns}
            onClear={() => setCompareIds([])}
            onRemove={toggleCompare}
          />
        </div>
      )}
    </>
  );
}
