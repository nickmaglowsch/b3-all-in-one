"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { PageHeader } from "@/components/ui/PageHeader";
import { Card } from "@/components/ui/Card";
import { DataTable, type Column } from "@/components/ui/DataTable";
import { TickerChip } from "@/components/ui/TickerChip";
import { Money } from "@/components/ui/Money";
import { PctDelta } from "@/components/ui/PctDelta";
import { EmptyState, LoadFailure } from "@/components/ui/EmptyState";
import { SectionNav } from "@/components/ui/SectionNav";
import { Skeleton } from "@/components/ui/Skeleton";
import { DivergingBars } from "@/components/charts/DivergingBars";
import { IconCompass } from "@/components/icons";
import { errorText } from "@/lib/api";
import { compactBRL, formatDate, formatInstantDate, formatPercent } from "@/lib/format";
import type { DriftRow, UserStrategy } from "@/lib/types";
import {
  liveToRows,
  offB3Rows,
  portfolioBody,
  rowKey,
  rowToSleeve,
  specToRows,
  type LiveSleeve,
  type SleeveRow as Row,
} from "@/lib/portfolioSleeves";
import {
  useDeleteUserStrategy,
  usePromoteStrategy,
  useSaveUserStrategy,
  useUserStrategies,
} from "@/lib/strategies";
import {
  useCreateBacktest,
  useRebalancePlan,
  useSaveSleeves,
  useSleeves,
  useStrategies,
  useTarget,
  type RebalancePlan,
  type TradeLine,
} from "./lib";
import { SettingsPanel } from "./Settings";
import { SleeveRows } from "./SleeveRows";

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
        {/* Without the saved sleeves the editor would open empty — and saving an
            empty-looking editor is how a real target gets overwritten. The
            settings behind it share the gate, so they go with it. */}
        {sleeves.isError ? (
          <Card title="Sleeves">
            <LoadFailure
              error={sleeves.error}
              icon={<IconCompass size={28} />}
              title="Não foi possível carregar seus sleeves."
              offerTitle={(nome) => `Sleeves, alvo e rebalanceamento vêm com o módulo ${nome}.`}
              onRetry={() => sleeves.refetch()}
            />
          </Card>
        ) : (
          <>
            <div id="sleeves" className={ANCHOR}>
              <SleevesEditor />
            </div>
            <div id="configuracoes" className={ANCHOR}>
              <SettingsPanel />
            </div>
          </>
        )}
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

function SleevesEditor() {
  const sleeves = useSleeves();
  const strategies = useStrategies();
  const save = useSaveSleeves();
  const saveStrat = useSaveUserStrategy();
  const [rows, setRows] = useState<Row[]>([]);
  const [dirty, setDirty] = useState(false);
  const [syncedFrom, setSyncedFrom] = useState<LiveSleeve[] | null>(null);
  // "Salvar como estratégia" form — shares `rows` with the live-target editor.
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  const [editId, setEditId] = useState<string | undefined>();
  const [editSpec, setEditSpec] = useState<Record<string, unknown>>({});
  // The live sleeves as they stood when "Editar" borrowed the rows for a saved
  // carteira. The rows are shared, so without this a cancelled edit left the
  // carteira's mix sitting in the live editor — dirty, so never reseeded, and
  // one "Salvar" away from becoming the rebalance target.
  const [live, setLive] = useState<{ rows: Row[]; dirty: boolean } | null>(null);

  // Seed the editor from server data on first load and after each save (when not
  // mid-edit). Adjusting state during render — guarded by reference identity so it
  // runs once per new server payload — is the React-endorsed alternative to an effect.
  if (sleeves.data && sleeves.data !== syncedFrom && !dirty) {
    setSyncedFrom(sleeves.data);
    setRows(liveToRows(sleeves.data));
  }

  const { sum, sumOk, hasDup, sleevesValid } = checkRows(rows);
  // While a saved carteira is in the editor its rows are not the live target, so
  // the live "Salvar" stays off until the edit ends.
  const canSave = sleevesValid && !save.isPending && !editId;
  // A carteira backtests on the B3 plane under B3 tax rules: US and crypto
  // assets stay live-only, and the form says so rather than letting the API refuse.
  const offB3 = offB3Rows(rows);
  const canSaveStrat =
    sleevesValid && name.trim() !== "" && !saveStrat.isPending && offB3.length === 0;

  const loadStrategy = (u: UserStrategy) => {
    // Only the first "Editar" snapshots: switching between carteiras mid-edit
    // must still restore the live rows, not the previous carteira's.
    if (!editId) setLive({ rows, dirty });
    setDirty(true);
    setRows(specToRows(u.spec));
    setName(u.name);
    setDesc(typeof u.spec.description === "string" ? u.spec.description : "");
    setEditId(u.id);
    setEditSpec(u.spec);
    saveStrat.reset();
    document
      .getElementById("sleeves")
      ?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  // Leave carteira-editing mode and hand the editor back to the live sleeves:
  // the rows as they were (a pending live edit survives), or — if they were
  // clean — a reseed from the server, which may have moved since (Ativar).
  const endEdit = () => {
    setName("");
    setDesc("");
    setEditId(undefined);
    setEditSpec({});
    if (!live) return;
    if (live.dirty) {
      setRows(live.rows);
    } else {
      setDirty(false);
      setSyncedFrom(null);
    }
    setLive(null);
  };

  const onSaveStrat = () =>
    saveStrat.mutate(
      {
        id: editId,
        body: portfolioBody(name, desc, rows, editSpec),
      },
      { onSuccess: endEdit },
    );

  const edit = (patch: (rs: Row[]) => Row[]) => {
    setDirty(true);
    setRows(patch);
  };
  const setField = (i: number, patch: Partial<Row>) =>
    edit((rs) => rs.map((x, j) => (j === i ? { ...x, ...patch } : x)));

  const onSave = () =>
    save.mutate(rows.map(rowToSleeve), { onSuccess: () => setDirty(false) });

  return (
    <div className="space-y-4">
    <Card
      title={editId ? "Sleeves · editando carteira salva" : "Sleeves"}
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
          <SleeveRows
            rows={rows}
            editId={editId}
            onPick={(i, pick) => setField(i, pick)}
            onPct={(i, target_pct) => setField(i, { target_pct })}
            onRemove={(i) => edit((rs) => rs.filter((_, j) => j !== i))}
          />

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
              Cada estratégia ou ativo só pode aparecer uma vez.
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

          <StrategySaveForm
            name={name}
            setName={setName}
            desc={desc}
            setDesc={setDesc}
            editing={!!editId}
            offB3={offB3}
            saveStrat={saveStrat}
            canSave={canSaveStrat}
            onSave={onSaveStrat}
            onCancel={() => {
              endEdit();
              saveStrat.reset();
            }}
          />
        </div>
      )}
    </Card>
    <MinhasCarteiras onEdit={loadStrategy} activeEditId={editId} />
    </div>
  );
}

// What the editor's rows add up to, and whether they can be saved: summing to
// 100, each sleeve once (a ticker row is keyed by its market + ticker), none blank.
function checkRows(rows: Row[]) {
  const sum = rows.reduce((a, r) => a + (parseFloat(r.target_pct) || 0), 0);
  const keys = rows.map(rowKey);
  const hasDup = new Set(keys).size !== keys.length;
  const hasBlank = rows.some((r) => !rowKey(r) || r.target_pct.trim() === "");
  const sumOk = Math.abs(sum - 100) < 0.01;
  return { sum, sumOk, hasDup, sleevesValid: rows.length > 0 && sumOk && !hasDup && !hasBlank };
}

// "Salvar como estratégia" / "Editar carteira": names the editor's rows as a
// saved fixed_weight carteira. Cancelar and a successful Atualizar both end the
// edit, which hands the rows back to the live sleeves (SleevesEditor.endEdit).
function StrategySaveForm({
  name,
  setName,
  desc,
  setDesc,
  editing,
  offB3,
  saveStrat,
  canSave,
  onSave,
  onCancel,
}: {
  name: string;
  setName: (v: string) => void;
  desc: string;
  setDesc: (v: string) => void;
  editing: boolean;
  offB3: string[];
  saveStrat: ReturnType<typeof useSaveUserStrategy>;
  canSave: boolean;
  onSave: () => void;
  onCancel: () => void;
}) {
  const label = editing ? "Atualizar" : "Salvar como estratégia";
  return (
    <div className="space-y-2 border-t border-hairline pt-3">
      <p className="text-xs font-semibold uppercase tracking-wide text-muted">
        {editing ? "Editar carteira" : "Salvar como estratégia"}
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
          onClick={onSave}
          disabled={!canSave}
          className="rounded-md bg-navy px-4 py-1.5 text-xs font-semibold text-white disabled:cursor-not-allowed disabled:opacity-40"
        >
          {saveStrat.isPending ? "Salvando…" : label}
        </button>
        {editing && (
          <button
            type="button"
            onClick={onCancel}
            className="rounded-md border border-hairline px-3 py-1.5 text-xs text-muted hover:bg-hairline/40"
          >
            Cancelar
          </button>
        )}
      </div>
      <p className="text-[11px] text-muted">
        {editing
          ? "Editando uma carteira salva — os sleeves ativos não mudam. Cancelar ou Atualizar devolve o editor a eles."
          : "Salva a alocação atual como carteira nomeada — dá para backtestar e ativar em “Minhas carteiras”."}
      </p>
      {offB3.length > 0 && (
        <p className="text-xs text-series-4" role="status">
          {offB3.join(", ")}: ativos dos EUA e cripto ficam só nos sleeves ativos — uma
          carteira salva roda no plano da B3 e apura o imposto pelas regras da B3.
        </p>
      )}
      {saveStrat.error && (
        <p className="text-xs text-loss" role="alert">
          {errorText(saveStrat.error, "Falha ao salvar a estratégia.")}
        </p>
      )}
      {saveStrat.isSuccess && <p className="text-xs text-gain">Carteira salva.</p>}
      <Warnings warnings={saveStrat.data?.warnings} />
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
      ) : userStrats.isError ? (
        <LoadFailure
          error={userStrats.error}
          title="Não foi possível carregar suas carteiras salvas."
          offerTitle={(nome) => `Carteiras salvas vêm com o módulo ${nome}.`}
          onRetry={() => userStrats.refetch()}
        />
      ) : portfolios.length === 0 ? (
        <p className="text-sm text-muted">
          Nenhuma carteira salva. Monte os sleeves acima e use “Salvar como
          estratégia”.
        </p>
      ) : (
        <ul className="space-y-2">
          {portfolios.map((u) => (
            <CarteiraItem
              key={u.id}
              u={u}
              active={u.id === activeEditId}
              status={carteiraStatus(u, promote, del, backtest)}
              onPromote={() => promote.mutate(u.id)}
              onBacktest={() => backtest.mutate({ strategy: u.name })}
              onEdit={() => onEdit(u)}
              onDelete={() => onDelete(u)}
            />
          ))}
        </ul>
      )}
    </Card>
  );
}

// What this list's mutations say about carteira `u`. One hook instance serves
// every row, so its state belongs only to the row it was last called for.
interface CarteiraStatus {
  promoting: boolean;
  promoted: boolean;
  promoteError: unknown;
  deleting: boolean;
  deleteError: unknown;
  sending: boolean;
  backtestError: unknown;
  ranId: string | null;
}

function carteiraStatus(
  u: UserStrategy,
  promote: ReturnType<typeof usePromoteStrategy>,
  del: ReturnType<typeof useDeleteUserStrategy>,
  backtest: ReturnType<typeof useCreateBacktest>,
): CarteiraStatus {
  const promoted = promote.variables === u.id;
  const deleted = del.variables === u.id;
  return {
    promoting: promote.isPending && promoted,
    promoted: promote.isSuccess && promoted,
    promoteError: promoted ? promote.error : null,
    deleting: del.isPending && deleted,
    deleteError: deleted ? del.error : null,
    ...backtestStatus(u, backtest),
  };
}

function backtestStatus(
  u: UserStrategy,
  backtest: ReturnType<typeof useCreateBacktest>,
): Pick<CarteiraStatus, "sending" | "backtestError" | "ranId"> {
  const ran = backtest.variables?.strategy === u.name;
  return {
    sending: backtest.isPending && ran,
    backtestError: ran ? backtest.error : null,
    ranId: ran ? (backtest.data?.id ?? null) : null,
  };
}

function CarteiraItem({
  u,
  active,
  status,
  onPromote,
  onBacktest,
  onEdit,
  onDelete,
}: {
  u: UserStrategy;
  active: boolean;
  status: CarteiraStatus;
  onPromote: () => void;
  onBacktest: () => void;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const nSleeves = Array.isArray(u.spec.sleeves) ? (u.spec.sleeves as unknown[]).length : 0;
  return (
    <li className={`rounded-lg border bg-paper p-3 ${active ? "border-navy" : "border-hairline"}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold text-ink">{u.name}</p>
          <p className="font-mono text-[11px] text-muted">
            {nSleeves} sleeve(s) · atualizada {formatInstantDate(u.updated_at)}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <button
            type="button"
            onClick={onPromote}
            disabled={status.promoting}
            className="rounded-md bg-navy px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-40"
          >
            {status.promoting ? "Ativando…" : "Ativar"}
          </button>
          <button
            type="button"
            onClick={onBacktest}
            disabled={status.sending}
            className="rounded-md border border-hairline px-3 py-1.5 text-xs font-medium text-ink hover:bg-hairline/40 disabled:opacity-40"
          >
            {status.sending ? "Enviando…" : "Backtest"}
          </button>
          <button
            type="button"
            onClick={onEdit}
            className="rounded-md border border-hairline px-3 py-1.5 text-xs font-medium text-ink hover:bg-hairline/40"
          >
            Editar
          </button>
          <button
            type="button"
            onClick={onDelete}
            disabled={status.deleting}
            className="rounded-md border border-hairline px-3 py-1.5 text-xs font-medium text-loss hover:bg-hairline/40 disabled:opacity-40"
          >
            Excluir
          </button>
        </div>
      </div>
      <CarteiraMessages status={status} />
    </li>
  );
}

function CarteiraMessages({ status }: { status: CarteiraStatus }) {
  const fail = (error: unknown, fallback: string) =>
    !!error && (
      <p className="mt-2 text-xs text-loss" role="alert">
        {errorText(error, fallback)}
      </p>
    );
  return (
    <>
      {status.ranId && (
        <p className="mt-2 text-xs text-gain">
          Backtest enviado —{" "}
          <Link href={`/bench/run?id=${status.ranId}`} className="underline">
            ver resultado
          </Link>
          .
        </p>
      )}
      {fail(status.promoteError, "Falha ao ativar.")}
      {status.promoted && (
        <p className="mt-2 text-xs text-gain">Carteira ativada — alvo atualizado.</p>
      )}
      {fail(status.deleteError, "Falha ao excluir.")}
      {fail(status.backtestError, "Falha ao enviar o backtest.")}
    </>
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
        <Warnings warnings={t.warnings} />
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
    // A crypto quantity is a fraction of a coin (8 decimals); every other is whole.
    { key: "qty", header: "Qtd", align: "right", render: (r) => r.qty.toLocaleString("pt-BR", { maximumFractionDigits: 8 }) },
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
                  {/* Ações only: FIIs pay on every gain, so "dentro" in green
                      would promise them an isenção they do not have. */}
                  {cls === "AÇÃO" && (
                    <IsencaoLine gross={b.gross_after} limit={isencao} />
                  )}
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

function IsencaoLine({ gross, limit }: { gross: number; limit: number }) {
  const over = gross > limit;
  return (
    <Line
      label={`Isenção (${compactBRL(limit)})`}
      value={
        <span className={over ? "text-loss" : "text-gain"}>
          {over ? "ultrapassada" : "dentro"}
        </span>
      }
    />
  );
}
