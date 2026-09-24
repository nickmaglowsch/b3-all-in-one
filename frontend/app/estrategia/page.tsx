"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { PageHeader } from "@/components/ui/PageHeader";
import { Card } from "@/components/ui/Card";
import { DataTable, type Column } from "@/components/ui/DataTable";
import { TickerChip } from "@/components/ui/TickerChip";
import { Money } from "@/components/ui/Money";
import { PctDelta } from "@/components/ui/PctDelta";
import { EmptyState } from "@/components/ui/EmptyState";
import { SectionNav } from "@/components/ui/SectionNav";
import { Skeleton } from "@/components/ui/Skeleton";
import { DivergingBars } from "@/components/charts/DivergingBars";
import { IconCompass } from "@/components/icons";
import { errorText } from "@/lib/api";
import { usePositions } from "@/lib/hooks";
import { REBALANCE_FREQS, compactBRL, formatDate, formatPercent } from "@/lib/format";
import type { DriftRow, Sleeve, UserStrategy } from "@/lib/types";
import {
  useDeleteUserStrategy,
  usePromoteStrategy,
  useSaveUserStrategy,
  useUserStrategies,
} from "@/lib/strategies";
import {
  CASH_REF,
  DEFAULT_REBALANCE,
  useAssetOverrides,
  useCreateBacktest,
  useRebalancePlan,
  useRebalanceSettings,
  useSaveAssetOverrides,
  useSaveRebalanceSettings,
  useSaveSleeves,
  useSleeves,
  useStrategies,
  useTarget,
  type RebalancePlan,
  type RebalanceSettings,
  type TradeLine,
} from "./lib";

const ANCHOR = "scroll-mt-28 md:scroll-mt-16";

export default function EstrategiaPage() {
  const sleeves = useSleeves();
  const hasSleeves = (sleeves.data?.length ?? 0) > 0;

  return (
    <>
      <PageHeader eyebrow="Alocação" title="Estratégia" />

      <SectionNav
        items={[
          { id: "sleeves", label: "Sleeves" },
          { id: "carteiras", label: "Minhas carteiras" },
          { id: "configuracoes", label: "Configurações" },
          ...(hasSleeves
            ? [
                { id: "alvo", label: "Alvo x atual" },
                { id: "plano", label: "Rebalanceamento" },
              ]
            : []),
        ]}
      />

      <div className="space-y-4">
        <div id="sleeves" className={ANCHOR}>
          <SleevesEditor />
        </div>
        <div id="configuracoes" className={ANCHOR}>
          <SettingsPanel />
        </div>
        {sleeves.isLoading ? (
          <Skeleton className="h-40 w-full" />
        ) : hasSleeves ? (
          <>
            <div id="alvo" className={ANCHOR}>
              <TargetPanel />
            </div>
            <div id="plano" className={ANCHOR}>
              <RebalancePanel />
            </div>
          </>
        ) : null}
      </div>
    </>
  );
}

// ── sleeves editor ──────────────────────────────────────────────────────────

interface Row {
  strategy_ref: string;
  target_pct: string; // kept as string so the input can be cleared mid-edit
}

// Engine fixed_weight spec → the editor's rows. Readback caveat (spec/06): sleeves
// are {strategy, weight(fraction)} or {asset:"CDI", weight}; map back to
// {strategy_ref, target_pct(%)} and asset:"CDI" → CASH_CDI.
function specToRows(spec: Record<string, unknown>): Row[] {
  const sleeves = Array.isArray(spec.sleeves)
    ? (spec.sleeves as Record<string, unknown>[])
    : [];
  return sleeves.map((s) => ({
    strategy_ref:
      s.asset === "CDI" ? CASH_REF : String(s.strategy ?? s.ticker ?? ""),
    target_pct: String(Math.round(Number(s.weight ?? 0) * 1e6) / 1e4),
  }));
}

function SleevesEditor() {
  const sleeves = useSleeves();
  const strategies = useStrategies();
  const userStrats = useUserStrategies();
  const save = useSaveSleeves();
  const saveStrat = useSaveUserStrategy();
  const [rows, setRows] = useState<Row[]>([]);
  const [dirty, setDirty] = useState(false);
  const [syncedFrom, setSyncedFrom] = useState<Sleeve[] | null>(null);
  // "Salvar como estratégia" form — shares `rows` with the live-target editor.
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  const [editId, setEditId] = useState<string | undefined>();

  // Seed the editor from server data on first load and after each save (when not
  // mid-edit). Adjusting state during render — guarded by reference identity so it
  // runs once per new server payload — is the React-endorsed alternative to an effect.
  if (sleeves.data && sleeves.data !== syncedFrom && !dirty) {
    setSyncedFrom(sleeves.data);
    setRows(
      sleeves.data.map((s) => ({
        strategy_ref: s.strategy_ref,
        target_pct: String(s.target_pct),
      })),
    );
  }

  // A user fixed_weight portfolio can't be a sleeve of another (cycle guard —
  // server 422s); drop those names from the picker.
  const fixedWeightNames = useMemo(
    () =>
      new Set(
        (userStrats.data ?? [])
          .filter((u) => u.kind === "fixed_weight")
          .map((u) => u.name),
      ),
    [userStrats.data],
  );

  // Split the merged gallery: platform built-ins vs the tenant's own rule
  // strategies (source:"user", minus fixed_weight portfolios) — grouped in the
  // picker via <optgroup>.
  const { builtinRefs, userRefs } = useMemo(() => {
    const b: string[] = [];
    const u: string[] = [];
    for (const s of strategies.data ?? []) {
      if (fixedWeightNames.has(s.name)) continue;
      (s.source === "user" ? u : b).push(s.name);
    }
    return { builtinRefs: b, userRefs: u };
  }, [strategies.data, fixedWeightNames]);

  // Strategies that read CVM fundamentals — flag them in the picker so the choice
  // is informed (native <option> can't hold a badge, so the label carries a marker).
  const needsFunds = useMemo(
    () =>
      new Set(
        (strategies.data ?? [])
          .filter((s) => s.needs_fundamentals)
          .map((s) => s.name),
      ),
    [strategies.data],
  );

  const optGroup = (label: string, names: string[]) => (
    <optgroup label={label}>
      {names.map((n) => (
        <option key={n} value={n}>
          {needsFunds.has(n) ? `${n} · precisa de fundamentos` : n}
        </option>
      ))}
    </optgroup>
  );

  const sum = rows.reduce((a, r) => a + (parseFloat(r.target_pct) || 0), 0);
  const refs = rows.map((r) => r.strategy_ref);
  const hasDup = new Set(refs).size !== refs.length;
  const hasBlank = rows.some((r) => !r.strategy_ref || r.target_pct.trim() === "");
  const sumOk = Math.abs(sum - 100) < 0.01;
  const sleevesValid = rows.length > 0 && sumOk && !hasDup && !hasBlank;
  const canSave = sleevesValid && !save.isPending;
  const canSaveStrat = sleevesValid && name.trim() !== "" && !saveStrat.isPending;

  const loadStrategy = (u: UserStrategy) => {
    setDirty(true);
    setRows(specToRows(u.spec));
    setName(u.name);
    setDesc(typeof u.spec.description === "string" ? u.spec.description : "");
    setEditId(u.id);
    saveStrat.reset();
    document
      .getElementById("sleeves")
      ?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  const onSaveStrat = () =>
    saveStrat.mutate(
      {
        id: editId,
        body: {
          kind: "fixed_weight",
          name: name.trim(),
          description: desc.trim() || undefined,
          sleeves: rows.map((r) => ({
            strategy_ref: r.strategy_ref,
            target_pct: parseFloat(r.target_pct),
          })),
        },
      },
      {
        onSuccess: () => {
          setName("");
          setDesc("");
          setEditId(undefined);
        },
      },
    );

  const edit = (patch: (rs: Row[]) => Row[]) => {
    setDirty(true);
    setRows(patch);
  };
  const setField = (i: number, patch: Partial<Row>) =>
    edit((rs) => rs.map((x, j) => (j === i ? { ...x, ...patch } : x)));

  const onSave = () => {
    const body: Sleeve[] = rows.map((r) => ({
      strategy_ref: r.strategy_ref,
      target_pct: parseFloat(r.target_pct),
    }));
    save.mutate(body, { onSuccess: () => setDirty(false) });
  };

  return (
    <div className="space-y-4">
    <Card
      title="Sleeves"
      action={
        <span
          className={`font-mono text-[11px] ${sumOk ? "text-muted" : "text-loss"}`}
        >
          soma {sum.toLocaleString("pt-BR", { maximumFractionDigits: 2 })}% / 100%
        </span>
      }
    >
      {strategies.isLoading ? (
        <Skeleton className="h-24 w-full" />
      ) : (
        <div className="space-y-2">
          {rows.map((r, i) => (
            <div key={i} className="flex items-center gap-2">
              <select
                value={r.strategy_ref}
                onChange={(e) => setField(i, { strategy_ref: e.target.value })}
                className="min-w-0 flex-1 rounded-md border border-hairline bg-paper px-2 py-1.5 font-mono text-sm text-ink"
              >
                <option value="">selecione…</option>
                <option value={CASH_REF}>Caixa (CDI)</option>
                {optGroup("Estratégias da plataforma", builtinRefs)}
                {userRefs.length > 0 && optGroup("Minhas estratégias de regra", userRefs)}
              </select>
              <div className="flex items-center gap-1">
                <input
                  type="number"
                  min={0}
                  max={100}
                  step={1}
                  value={r.target_pct}
                  onChange={(e) => setField(i, { target_pct: e.target.value })}
                  className="w-20 rounded-md border border-hairline bg-paper px-2 py-1.5 text-right font-mono text-sm tnum text-ink"
                />
                <span className="font-mono text-sm text-muted">%</span>
              </div>
              <button
                type="button"
                onClick={() => edit((rs) => rs.filter((_, j) => j !== i))}
                aria-label="Remover sleeve"
                className="rounded-md border border-hairline px-2 py-1.5 text-sm text-muted hover:bg-hairline/40"
              >
                ✕
              </button>
            </div>
          ))}

          <div className="flex items-center justify-between pt-1">
            <button
              type="button"
              onClick={() =>
                edit((rs) => [...rs, { strategy_ref: "", target_pct: "" }])
              }
              className="rounded-md border border-hairline bg-paper px-3 py-1.5 text-xs font-medium text-ink hover:bg-hairline/40"
            >
              + Adicionar sleeve
            </button>
            <button
              type="button"
              onClick={onSave}
              disabled={!canSave}
              className="rounded-md bg-navy px-4 py-1.5 text-xs font-semibold text-white disabled:cursor-not-allowed disabled:opacity-40"
            >
              {save.isPending ? "Salvando…" : "Salvar"}
            </button>
          </div>

          {hasDup && (
            <p className="text-xs text-loss" role="alert">
              Cada estratégia só pode aparecer uma vez.
            </p>
          )}
          {save.error && (
            <p className="text-xs text-loss" role="alert">
              {errorText(save.error, "Falha ao salvar os sleeves.")}
            </p>
          )}
          {save.isSuccess && !dirty && (
            <p className="text-xs text-gain">Sleeves salvos.</p>
          )}
          <Warnings warnings={save.data?.warnings} />

          <div className="space-y-2 border-t border-hairline pt-3">
            <p className="text-xs font-semibold uppercase tracking-wide text-muted">
              {editId ? "Editar carteira" : "Salvar como estratégia"}
            </p>
            <div className="flex flex-wrap items-center gap-2">
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Nome da carteira"
                className="min-w-[10rem] flex-1 rounded-md border border-hairline bg-paper px-2 py-1.5 text-sm text-ink"
              />
              <input
                value={desc}
                onChange={(e) => setDesc(e.target.value)}
                placeholder="Descrição (opcional)"
                className="min-w-[10rem] flex-1 rounded-md border border-hairline bg-paper px-2 py-1.5 text-sm text-ink"
              />
              <button
                type="button"
                onClick={onSaveStrat}
                disabled={!canSaveStrat}
                className="rounded-md bg-navy px-4 py-1.5 text-xs font-semibold text-white disabled:cursor-not-allowed disabled:opacity-40"
              >
                {saveStrat.isPending
                  ? "Salvando…"
                  : editId
                    ? "Atualizar"
                    : "Salvar como estratégia"}
              </button>
              {editId && (
                <button
                  type="button"
                  onClick={() => {
                    setEditId(undefined);
                    setName("");
                    setDesc("");
                    saveStrat.reset();
                  }}
                  className="rounded-md border border-hairline px-3 py-1.5 text-xs text-muted hover:bg-hairline/40"
                >
                  Cancelar
                </button>
              )}
            </div>
            <p className="text-[11px] text-muted">
              Salva a alocação atual como carteira nomeada — dá para backtestar e
              ativar em “Minhas carteiras”.
            </p>
            {saveStrat.error && (
              <p className="text-xs text-loss" role="alert">
                {errorText(saveStrat.error, "Falha ao salvar a estratégia.")}
              </p>
            )}
            {saveStrat.isSuccess && (
              <p className="text-xs text-gain">Carteira salva.</p>
            )}
            <Warnings warnings={saveStrat.data?.warnings} />
          </div>
        </div>
      )}
    </Card>
    <MinhasCarteiras onEdit={loadStrategy} activeEditId={editId} />
    </div>
  );
}

// Soft warnings the API echoes on a save (e.g. needs-funds).
function Warnings({ warnings }: { warnings?: string[] }) {
  return warnings?.map((w, i) => (
    <p key={i} className="text-xs text-series-4" role="status">
      {w}
    </p>
  ));
}

// ── saved portfolio strategies ───────────────────────────────────────────────

function MinhasCarteiras({
  onEdit,
  activeEditId,
}: {
  onEdit: (u: UserStrategy) => void;
  activeEditId?: string;
}) {
  const userStrats = useUserStrategies();
  const promote = usePromoteStrategy();
  const del = useDeleteUserStrategy();
  const backtest = useCreateBacktest();

  const portfolios = (userStrats.data ?? []).filter(
    (u) => u.kind === "fixed_weight",
  );

  const onDelete = (u: UserStrategy) => {
    if (!window.confirm(`Excluir a carteira “${u.name}”?`)) return;
    del.mutate(u.id);
  };

  return (
    <Card id="carteiras" className={ANCHOR} title="Minhas carteiras">
      {userStrats.isLoading ? (
        <Skeleton className="h-20 w-full" />
      ) : portfolios.length === 0 ? (
        <p className="text-sm text-muted">
          Nenhuma carteira salva. Monte os sleeves acima e use “Salvar como
          estratégia”.
        </p>
      ) : (
        <ul className="space-y-2">
          {portfolios.map((u) => {
            const nSleeves = Array.isArray(u.spec.sleeves)
              ? (u.spec.sleeves as unknown[]).length
              : 0;
            const ranId =
              backtest.data && backtest.variables?.strategy === u.name
                ? backtest.data.id
                : null;
            return (
              <li
                key={u.id}
                className={`rounded-lg border bg-paper p-3 ${
                  u.id === activeEditId ? "border-navy" : "border-hairline"
                }`}
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-semibold text-ink">
                      {u.name}
                    </p>
                    <p className="font-mono text-[11px] text-muted">
                      {nSleeves} sleeve(s) · atualizada {formatDate(u.updated_at)}
                    </p>
                  </div>
                  <div className="flex flex-wrap items-center gap-1.5">
                    <button
                      type="button"
                      onClick={() => promote.mutate(u.id)}
                      disabled={promote.isPending && promote.variables === u.id}
                      className="rounded-md bg-navy px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-40"
                    >
                      {promote.isPending && promote.variables === u.id
                        ? "Ativando…"
                        : "Ativar"}
                    </button>
                    <button
                      type="button"
                      onClick={() => backtest.mutate({ strategy: u.name })}
                      disabled={
                        backtest.isPending &&
                        backtest.variables?.strategy === u.name
                      }
                      className="rounded-md border border-hairline px-3 py-1.5 text-xs font-medium text-ink hover:bg-hairline/40 disabled:opacity-40"
                    >
                      {backtest.isPending &&
                      backtest.variables?.strategy === u.name
                        ? "Enviando…"
                        : "Backtest"}
                    </button>
                    <button
                      type="button"
                      onClick={() => onEdit(u)}
                      className="rounded-md border border-hairline px-3 py-1.5 text-xs font-medium text-ink hover:bg-hairline/40"
                    >
                      Editar
                    </button>
                    <button
                      type="button"
                      onClick={() => onDelete(u)}
                      disabled={del.isPending && del.variables === u.id}
                      className="rounded-md border border-hairline px-3 py-1.5 text-xs font-medium text-loss hover:bg-hairline/40 disabled:opacity-40"
                    >
                      Excluir
                    </button>
                  </div>
                </div>

                {ranId && (
                  <p className="mt-2 text-xs text-gain">
                    Backtest enviado —{" "}
                    <Link href={`/bench/run?id=${ranId}`} className="underline">
                      ver resultado
                    </Link>
                    .
                  </p>
                )}
                {promote.error && promote.variables === u.id && (
                  <p className="mt-2 text-xs text-loss" role="alert">
                    {errorText(promote.error, "Falha ao ativar.")}
                  </p>
                )}
                {promote.isSuccess && promote.variables === u.id && (
                  <p className="mt-2 text-xs text-gain">
                    Carteira ativada — alvo atualizado.
                  </p>
                )}
                {del.error && del.variables === u.id && (
                  <p className="mt-2 text-xs text-loss" role="alert">
                    {errorText(del.error, "Falha ao excluir.")}
                  </p>
                )}
                {backtest.error && backtest.variables?.strategy === u.name && (
                  <p className="mt-2 text-xs text-loss" role="alert">
                    {errorText(backtest.error, "Falha ao enviar o backtest.")}
                  </p>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}

// ── portfolio settings (spec/05 Features 3 & 4) ──────────────────────────────

function SettingsPanel() {
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

  return (
    <div>
      <p className="text-sm font-semibold text-ink">Ativo como caixa (CDI)</p>
      <p className="mb-3 mt-0.5 text-xs text-muted">
        Marque ativos de renda fixa/caixa (ex.: LFTB11) para tratá-los como caixa: eles
        deixam de aparecer no desvio e não são vendidos como ação — passam a cumprir o
        alvo de caixa.
      </p>

      {positions.isLoading ? (
        <Skeleton className="h-16 w-full" />
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
                disabled={save.isPending}
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
  const cur: RebalanceSettings = settings.data ?? DEFAULT_REBALANCE;

  const patch = (p: Partial<RebalanceSettings>) => save.mutate({ ...cur, ...p });

  return (
    <div>
      <p className="text-sm font-semibold text-ink">Comportamento do rebalanceamento</p>
      <p className="mb-3 mt-0.5 text-xs text-muted">
        Ajusta como o plano é montado. O planejador abaixo já reflete estas opções.
      </p>

      {settings.isLoading ? (
        <Skeleton className="h-24 w-full" />
      ) : (
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
      )}
      {save.error && (
        <p className="mt-2 text-xs text-loss" role="alert">
          {errorText(save.error, "Falha ao salvar.")}
        </p>
      )}
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

// ── target / drift ──────────────────────────────────────────────────────────

const DRIFT_TOP = 12;

function TargetPanel() {
  const target = useTarget();

  if (target.isLoading) return <Skeleton className="h-72 w-full" />;

  // 409 = sleeves exist but target can't be built (shouldn't happen here, but be honest).
  if (target.error) {
    const msg = errorText(target.error, "Falha ao calcular o alvo.");
    return (
      <Card title="Alvo x atual">
        <EmptyState
          icon={<IconCompass size={28} />}
          title="Não foi possível montar a carteira-alvo."
          hint={msg}
        />
      </Card>
    );
  }

  const t = target.data!;
  const sorted = [...t.drift].sort(
    (a, b) => Math.abs(b.drift_brl) - Math.abs(a.drift_brl),
  );
  const top = sorted.slice(0, DRIFT_TOP);
  const rest = sorted.slice(DRIFT_TOP);
  const restSum = rest.reduce((a, r) => a + r.drift_brl, 0);
  const bars = [
    ...top.map((d) => ({ label: d.ticker, value: d.drift_brl })),
    ...(rest.length ? [{ label: "outros", value: restSum }] : []),
  ];

  const columns: Column<DriftRow>[] = [
    { key: "ticker", header: "Ativo", mobile: "title", render: (r) => <TickerChip ticker={r.ticker} /> },
    {
      key: "target_w",
      header: "Peso alvo",
      align: "right",
      render: (r) => formatPercent(r.target_w),
    },
    {
      key: "current_w",
      header: "Peso atual",
      align: "right",
      render: (r) => formatPercent(r.current_w),
    },
    {
      key: "drift_w",
      header: "Desvio",
      align: "right",
      render: (r) => <PctDelta value={r.drift_w} />,
    },
    {
      key: "drift_brl",
      header: "Desvio (R$)",
      align: "right",
      render: (r) => <Money value={r.drift_brl} sign />,
    },
  ];

  return (
    <Card
      title="Alvo x atual"
      action={
        <span className="font-mono text-[11px] text-muted">
          {formatDate(t.as_of)} · patrimônio {compactBRL(t.nav)} · caixa-alvo{" "}
          {formatPercent(t.cash_weight_target)}
        </span>
      }
    >
      <div className="space-y-4">
        <div>
          <p className="mb-2 text-xs text-muted">
            Desvio por ativo (positivo = comprar para atingir o alvo)
          </p>
          <DivergingBars data={bars} valueFormat={compactBRL} height={Math.max(200, bars.length * 26)} />
        </div>
        <DataTable columns={columns} rows={sorted} getRowKey={(r) => r.ticker} label="Desvio por ativo" />
      </div>
    </Card>
  );
}

// ── rebalance planner ───────────────────────────────────────────────────────

function RebalancePanel() {
  const target = useTarget();
  const plan = useRebalancePlan();
  const [newCash, setNewCash] = useState("");
  const [exclude, setExclude] = useState<string[]>([]);

  const tickers = useMemo(
    () => (target.data ? target.data.drift.map((d) => d.ticker) : []),
    [target.data],
  );

  const toggle = (t: string) =>
    setExclude((xs) => (xs.includes(t) ? xs.filter((x) => x !== t) : [...xs, t]));

  const onPlan = () =>
    plan.mutate({
      new_cash: parseFloat(newCash) || 0,
      exclude: exclude.length ? exclude : undefined,
    });

  return (
    <Card title="Plano de rebalanceamento">
      <div className="space-y-4">
        <div className="flex flex-wrap items-end gap-4">
          <label className="text-sm">
            <span className="mb-1 block text-xs text-muted">Aporte novo (R$)</span>
            <input
              type="number"
              min={0}
              step={100}
              value={newCash}
              onChange={(e) => setNewCash(e.target.value)}
              placeholder="0"
              className="w-40 rounded-md border border-hairline bg-paper px-2 py-1.5 text-right font-mono text-sm tnum text-ink"
            />
          </label>
          <button
            type="button"
            onClick={onPlan}
            disabled={plan.isPending}
            className="rounded-md bg-navy px-4 py-1.5 text-xs font-semibold text-white disabled:opacity-40"
          >
            {plan.isPending ? "Gerando…" : "Gerar plano"}
          </button>
        </div>

        {tickers.length > 0 && (
          <div>
            <p className="mb-1.5 text-xs text-muted">Excluir do plano (mantém a posição atual)</p>
            <div className="flex flex-wrap gap-1.5">
              {tickers.map((t) => {
                const on = exclude.includes(t);
                return (
                  <button
                    key={t}
                    type="button"
                    onClick={() => toggle(t)}
                    className={`rounded-[4px] border px-1.5 py-0.5 font-mono text-[11px] uppercase tracking-wide ${
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
          </div>
        )}

        {plan.error && (
          <p className="text-sm text-loss" role="alert">
            {errorText(plan.error, "Falha ao gerar o plano.")}
          </p>
        )}

        {plan.data && <PlanResult plan={plan.data} />}

        <p className="border-t border-hairline pt-3 text-[11px] text-muted">
          Plano informativo — nada é executado. Envie as ordens na sua corretora.
        </p>
      </div>
    </Card>
  );
}

function PlanResult({ plan }: { plan: RebalancePlan }) {
  const tradeCols: Column<TradeLine>[] = [
    { key: "ticker", header: "Ativo", mobile: "title", render: (r) => <TickerChip ticker={r.ticker} /> },
    { key: "qty", header: "Qtd", align: "right", render: (r) => r.qty.toLocaleString("pt-BR") },
    { key: "est_price", header: "Preço est.", align: "right", render: (r) => <Money value={r.est_price} /> },
    { key: "est_brl", header: "Valor", align: "right", render: (r) => <Money value={r.est_brl} /> },
  ];

  const { sells, buys } = plan.trades;
  const buckets = Object.entries(plan.tax?.buckets ?? {});
  const isencao = plan.tax?.isencao_limit ?? 20000;

  return (
    <div className="space-y-4">
      <p className="font-mono text-[11px] text-muted">
        {formatDate(plan.as_of)} · patrimônio {compactBRL(plan.nav)}
        {plan.new_cash ? ` · aporte ${compactBRL(plan.new_cash)}` : ""}
      </p>

      {plan.notes?.length > 0 && (
        <div className="space-y-1.5">
          {plan.notes.map((n, i) => (
            <p
              key={i}
              className="rounded-md border border-hairline bg-paper px-3 py-2 text-sm text-ink"
            >
              {n}
            </p>
          ))}
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        {(
          [
            ["Vender", sells, "Vendas sugeridas", "Nenhuma venda necessária."],
            ["Comprar", buys, "Compras sugeridas", "Nenhuma compra necessária."],
          ] as const
        ).map(([title, rows, label, none]) => (
          <div key={title}>
            <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">
              {title} ({rows.length})
            </p>
            {rows.length ? (
              <DataTable columns={tradeCols} rows={rows} getRowKey={(r) => r.ticker} label={label} />
            ) : (
              <p className="text-sm text-muted">{none}</p>
            )}
          </div>
        ))}
      </div>

      {buckets.length > 0 && (
        <div>
          <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">
            Imposto por classe
          </p>
          <div className="grid gap-3 sm:grid-cols-2">
            {buckets.map(([cls, b]) => (
              <div key={cls} className="rounded-lg border border-hairline bg-panel p-3">
                <div className="flex items-center justify-between">
                  <span className="font-mono text-sm font-semibold text-ink">{cls}</span>
                  <span className="text-xs text-muted">{b.status}</span>
                </div>
                <dl className="mt-2 space-y-1 text-sm">
                  <Line label="Vendas do mês" value={<Money value={b.gross_after} />} />
                  <Line
                    label={`Isenção (${compactBRL(isencao)})`}
                    value={
                      <span className={b.gross_after > isencao ? "text-loss" : "text-gain"}>
                        {b.gross_after > isencao ? "ultrapassada" : "dentro"}
                      </span>
                    }
                  />
                  <Line label="Imposto estimado" value={<Money value={b.ir} />} />
                  {b.harvest?.length > 0 && (
                    <Line
                      label="Harvest sugerido"
                      value={`${b.harvest.length} ativo(s)`}
                    />
                  )}
                </dl>
                {b.harvest?.length > 0 && (
                  <p className="mt-2 border-t border-hairline pt-2 text-[11px] text-muted">
                    Recompre no próximo pregão — recomprar no mesmo dia configura day trade.
                  </p>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function Line({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <dt className="text-muted">{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}
