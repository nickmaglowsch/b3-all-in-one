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
import { Tabs, useTab } from "@/components/ui/Tabs";
import { useBacktests, useStrategies } from "./lib";

const TAB_IDS = ["rodar", "rodadas", "minhas", "ia"];

export default function BenchPage() {
  const strategies = useStrategies();
  const backtests = useBacktests();
  const [selected, setSelected] = useState<string | null>(null);
  const [compareIds, setCompareIds] = useState<string[]>([]);
  const [form, setForm] = useState<FormMode>(CLOSED);
  const [tab, setTab] = useTab(TAB_IDS);

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

      <Tabs
        label="Seções do Bench"
        active={tab}
        onChange={setTab}
        tabs={[
          {
            id: "rodar",
            label: "Rodar",
            content: (
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
                    // A queued run's next stop is the list it lands in.
                    onRan={() => setTab("rodadas")}
                    onClone={(s) => {
                      setForm((m) => withClone(m, s));
                      setTab("minhas");
                    }}
                    cloneBlocked={cloneBlocked(form)}
                  />
                </Card>
              </div>
            ),
          },
          {
            id: "rodadas",
            label: "Rodadas",
            badge: compareRuns.length ? `${compareRuns.length} p/ comparar` : undefined,
            content: (
              <div className="space-y-4">
                <Card
                  title="Rodadas"
                  action={
                    <div className="flex items-center gap-3">
                      {/* Counted off the live runs, not the picked ids: a run
                          deleted or swept while picked is gone from the overlay
                          already. */}
                      <span className="font-mono text-[11px] text-muted">
                        {compareRuns.length > 0
                          ? `${compareRuns.length} p/ comparar`
                          : "marque 2+ p/ comparar"}
                      </span>
                      <RunsPurge />
                    </div>
                  }
                >
                  {/* A failed list is not an empty one: "Nenhum backtest ainda"
                      over a 402 or an outage tells the user their runs are gone. */}
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

                {compareRuns.length > 0 && (
                  <ComparePanel
                    runs={compareRuns}
                    onClear={() => setCompareIds([])}
                    onRemove={toggleCompare}
                  />
                )}
              </div>
            ),
          },
          {
            id: "minhas",
            label: "Minhas estratégias",
            content: (
              <UserStrategies
                onBacktest={(name) => {
                  setSelected(name);
                  setTab("rodar");
                }}
                mode={form}
                setMode={setForm}
              />
            ),
          },
          { id: "ia", label: "Conectar IA", content: <McpConnect /> },
        ]}
      />
    </>
  );
}
