"use client";

import { useMemo, useState } from "react";
import { PageHeader } from "@/components/ui/PageHeader";
import { Card } from "@/components/ui/Card";
import { ApiError } from "@/lib/api";
import { StrategyGallery } from "./StrategyGallery";
import { RunPanel } from "./RunPanel";
import { RunsList } from "./RunsList";
import { useBacktests, useStrategies } from "./lib";

export default function BenchPage() {
  const strategies = useStrategies();
  const backtests = useBacktests();
  const [selected, setSelected] = useState<string | null>(null);

  const selectedStrategy = useMemo(
    () => strategies.data?.find((s) => s.name === selected) ?? null,
    [strategies.data, selected],
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
        <Card title="Rodadas">
          <RunsList
            runs={backtests.data ?? []}
            loading={backtests.isLoading}
          />
        </Card>
      </div>
    </>
  );
}
