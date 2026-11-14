"use client";

import { useMemo, useState } from "react";
import { PageHeader } from "@/components/ui/PageHeader";
import { Card } from "@/components/ui/Card";
import { LoadFailure } from "@/components/ui/EmptyState";
import { errorText } from "@/lib/api";
import { isDone } from "@/lib/jobStatus";
import { CLOSED, cloneBlocked, withClone, type FormMode } from "@/lib/strategyFormMode";
import { StrategyGallery } from "./StrategyGallery";
import { RunPanel } from "./RunPanel";
import { RunsList } from "./RunsList";
import { RunsPurge } from "./RunsPurge";
import { ComparePanel } from "./ComparePanel";
import { UserStrategies } from "./UserStrategies";
import { McpConnect } from "./McpConnect";
import { useBacktests, useStrategies } from "./lib";

export default function BenchPage() {
  const strategies = useStrategies();
  const backtests = useBacktests();
  const [selected, setSelected] = useState<string | null>(null);
  const [compareIds, setCompareIds] = useState<string[]>([]);
  const [form, setForm] = useState<FormMode>(CLOSED);

  const selectedStrategy = useMemo(
    () => strategies.data?.find((s) => s.name === selected) ?? null,
    [strategies.data, selected],
  );

  // Selected runs in pick order (colours track this), pruned to still-done runs.
  const compareRuns = useMemo(() => {
    const byId = new Map((backtests.data ?? []).map((r) => [r.id, r]));
    return compareIds
      .map((id) => byId.get(id))
      .filter((r): r is NonNullable<typeof r> => isDone(r?.status));
  }, [backtests.data, compareIds]);

  const toggleCompare = (id: string) =>
    setCompareIds((ids) =>
      ids.includes(id) ? ids.filter((x) => x !== id) : [...ids, id],
    );

  const dropCompare = (id: string) =>
    setCompareIds((ids) => ids.filter((x) => x !== id));

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
          {errorText(strategies.error, "Falha ao carregar estratégias.")}
        </p>
      )}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <Card title="Galeria de estratégias">
          <StrategyGallery
            strategies={strategies.data ?? []}
            loading={strategies.isLoading}
            selected={selected}
            onSelect={setSelected}
          />
        </Card>

        <Card title="Rodar backtest">
          <RunPanel
            strategy={selectedStrategy}
            onRan={() => {}}
            onClone={(s) => setForm((m) => withClone(m, s))}
            cloneBlocked={cloneBlocked(form)}
          />
        </Card>
      </div>

      <div className="mt-4">
        <UserStrategies
          onBacktest={setSelected}
          mode={form}
          setMode={setForm}
        />
      </div>

      <div className="mt-4">
        <McpConnect />
      </div>

      <div className="mt-4">
        <Card
          title="Rodadas"
          action={
            <div className="flex items-center gap-3">
              {/* Counted off the live runs, not the picked ids: a run deleted or
                  swept while picked is gone from the overlay already, and a
                  header that still counted it would be describing a ghost. */}
              <span className="font-mono text-[11px] text-muted">
                {compareRuns.length > 0
                  ? `${compareRuns.length} p/ comparar`
                  : "marque 2+ p/ comparar"}
              </span>
              <RunsPurge />
            </div>
          }
        >
          {/* A failed list is not an empty one: "Nenhum backtest ainda" over a
              402 or an outage tells the user their runs are gone. */}
          {backtests.isError ? (
            <LoadFailure
              error={backtests.error}
              title="Não foi possível carregar suas rodadas."
              offerTitle={(nome) => `Backtests vêm com o módulo ${nome}.`}
              onRetry={() => backtests.refetch()}
            />
          ) : (
            <RunsList
              runs={backtests.data ?? []}
              loading={backtests.isLoading}
              selected={compareIds}
              onToggle={toggleCompare}
              onDeleted={dropCompare}
            />
          )}
        </Card>
      </div>

      {compareRuns.length > 0 && (
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
