"use client";

// Portfolio settings (spec/05 Features 3 & 4): asset-as-CDI and rebalance
// behavior. Split from page.tsx, which sits at the module-size gate.
//
// Both endpoints *replace* the whole setting on PUT, so every write is built
// from what the server last said. Two rules keep that honest: nothing is
// clickable until the GET has landed (an unloaded set is "nothing marked", and
// one click would PUT that over every real marking), and a save writes the PUT's
// echo straight into the cache before the controls unlock — so the next click
// builds on the saved state, not on a refetch still in flight.
import { useMemo } from "react";
import { Card } from "@/components/ui/Card";
import { Skeleton } from "@/components/ui/Skeleton";
import { errorText } from "@/lib/api";
import { usePositions } from "@/lib/hooks";
import { REBALANCE_FREQS } from "@/lib/format";
import {
  useAssetOverrides,
  useRebalanceSettings,
  useSaveAssetOverrides,
  useSaveRebalanceSettings,
  type RebalanceSettings,
} from "./lib";

export function SettingsPanel() {
  return (
    <Card title="Configurações da carteira">
      <div className="space-y-6">
        <AssetCdiSection />
        <div className="border-t border-hairline pt-5">
          <RebalanceBehaviorSection />
        </div>
      </div>
    </Card>
  );
}

function LoadError({ error }: { error: unknown }) {
  return (
    <p className="text-xs text-loss" role="alert">
      {errorText(error, "Não foi possível carregar esta configuração.")} Nada foi
      alterado.
    </p>
  );
}

// (a) mark a held asset as caixa/CDI-equivalent — folds into cash, not equity.
function AssetCdiSection() {
  const positions = usePositions();
  const overrides = useAssetOverrides();
  const save = useSaveAssetOverrides();

  const cdi = useMemo(
    () =>
      new Set(
        (overrides.data ?? [])
          .filter((o) => o.treatment === "cash_cdi")
          .map((o) => o.ticker),
      ),
    [overrides.data],
  );

  const tickers = useMemo(
    () => (positions.data ?? []).map((p) => p.ticker).sort(),
    [positions.data],
  );

  const toggle = (ticker: string) => {
    const next = new Set(cdi);
    if (next.has(ticker)) next.delete(ticker);
    else next.add(ticker);
    save.mutate([...next].map((t) => ({ ticker: t, treatment: "cash_cdi" })));
  };

  const locked = !overrides.isSuccess || save.isPending;

  return (
    <div>
      <p className="text-sm font-semibold text-ink">Ativo como caixa (CDI)</p>
      <p className="mb-3 mt-0.5 text-xs text-muted">
        Marque ativos de renda fixa/caixa (ex.: LFTB11) para tratá-los como caixa: eles
        deixam de aparecer no desvio e não são vendidos como ação — passam a cumprir o
        alvo de caixa.
      </p>

      {positions.isLoading || overrides.isLoading ? (
        <Skeleton className="h-16 w-full" />
      ) : overrides.isError ? (
        <LoadError error={overrides.error} />
      ) : tickers.length === 0 ? (
        <p className="text-sm text-muted">Nenhuma posição na carteira.</p>
      ) : (
        <div className="flex flex-wrap gap-1.5">
          {tickers.map((t) => {
            const on = cdi.has(t);
            return (
              <button
                key={t}
                type="button"
                onClick={() => toggle(t)}
                disabled={locked}
                aria-pressed={on}
                title={on ? "Tratado como caixa (CDI)" : "Tratar como caixa (CDI)"}
                className={`rounded-[4px] border px-1.5 py-0.5 font-mono text-[11px] uppercase tracking-wide disabled:opacity-50 ${
                  on
                    ? "border-navy bg-navy text-white"
                    : "border-hairline bg-paper text-muted hover:bg-hairline/40"
                }`}
              >
                {t}
              </button>
            );
          })}
        </div>
      )}
      {save.error && (
        <p className="mt-2 text-xs text-loss" role="alert">
          {errorText(save.error, "Falha ao salvar.")}
        </p>
      )}
    </div>
  );
}

// (b) rebalance behavior: buy-only-laggards, sell-only-for-harvest, cadence.
function RebalanceBehaviorSection() {
  const settings = useRebalanceSettings();
  const save = useSaveRebalanceSettings();

  if (settings.isLoading) {
    return (
      <RebalanceShell>
        <Skeleton className="h-24 w-full" />
      </RebalanceShell>
    );
  }
  // No fallback to the defaults: the PUT sends all three fields, so toggling one
  // over a failed GET would quietly reset the other two.
  if (!settings.isSuccess) {
    return (
      <RebalanceShell>
        <LoadError error={settings.error} />
      </RebalanceShell>
    );
  }

  const cur: RebalanceSettings = settings.data;
  const patch = (p: Partial<RebalanceSettings>) => save.mutate({ ...cur, ...p });

  return (
    <RebalanceShell>
      <div className="space-y-3">
        <Toggle
          checked={cur.buy_only_laggards}
          disabled={save.isPending}
          onChange={(v) => patch({ buy_only_laggards: v })}
          label="Apenas aportar nos atrasados"
          hint="Sem vendas para trimar — só distribui o aporte nos ativos abaixo do alvo."
        />
        <Toggle
          checked={cur.sell_only_for_harvest}
          disabled={save.isPending}
          onChange={(v) => patch({ sell_only_for_harvest: v })}
          label="Vender só para colher prejuízo"
          hint="Mantém apenas as vendas que realizam prejuízo dedutível (tax-loss harvesting)."
        />
        <label className="flex items-center justify-between gap-3">
          <span className="text-sm text-ink">Cadência de rebalanceamento</span>
          <select
            value={cur.cadence}
            disabled={save.isPending}
            onChange={(e) => patch({ cadence: e.target.value })}
            className="rounded-md border border-hairline bg-paper px-2 py-1.5 font-mono text-sm text-ink disabled:opacity-50"
          >
            {REBALANCE_FREQS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
      </div>
      {save.error && (
        <p className="mt-2 text-xs text-loss" role="alert">
          {errorText(save.error, "Falha ao salvar.")}
        </p>
      )}
    </RebalanceShell>
  );
}

function RebalanceShell({ children }: { children: React.ReactNode }) {
  return (
    <div>
      <p className="text-sm font-semibold text-ink">Comportamento do rebalanceamento</p>
      <p className="mb-3 mt-0.5 text-xs text-muted">
        Ajusta como o plano é montado. O planejador abaixo já reflete estas opções.
      </p>
      {children}
    </div>
  );
}

function Toggle({
  checked,
  onChange,
  label,
  hint,
  disabled,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: string;
  hint: string;
  disabled?: boolean;
}) {
  return (
    <label className="flex items-start gap-2.5">
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
        className="mt-0.5 h-4 w-4 shrink-0 accent-navy disabled:opacity-50"
      />
      <span>
        <span className="block text-sm text-ink">{label}</span>
        <span className="block text-xs text-muted">{hint}</span>
      </span>
    </label>
  );
}
