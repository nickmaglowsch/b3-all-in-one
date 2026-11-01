"use client";

// Nova/Editar/Clonar estratégia (rank_and_hold) — compiles form fields into the
// POST/PUT /strategies/user body (spec/06-user-strategies.md · API surface).
// The server is the source of truth: client validation is just guard-rails, the
// real errors come back as ApiError.message.
import { useMemo, useState } from "react";
import type { SignalDef, Strategy, UserStrategy } from "@/lib/types";
import { errorText } from "@/lib/api";
import { REBALANCE_FREQS } from "@/lib/format";
import { useSaveUserStrategy, useSignals, useStrategySpec } from "@/lib/strategies";
import {
  RegimeEditor,
  compileRegime,
  isEmptyRegime,
  parseRegime,
  type RegimeValue,
} from "./RegimeEditor";
import { Field, inputCls } from "./Field";
import { LegsNote, signalError, signalSource, specExtras } from "./specExtras";

type SelMode = "top_pct" | "top_n";

interface FormState {
  name: string;
  description: string;
  advanced: boolean;
  factor: string;
  expr: string;
  lookback: string;
  minAdtv: string;
  minPrice: string;
  smallcap: boolean;
  selMode: SelMode;
  topPct: string;
  topN: string;
  minNames: string;
  dedupRoots: boolean;
  weighting: "equal" | "market_cap";
  rebalance: "ME" | "QE" | "W-FRI";
}

const DEFAULTS: FormState = {
  name: "",
  description: "",
  advanced: false,
  factor: "",
  expr: "",
  lookback: "12",
  minAdtv: "1000000",
  minPrice: "1",
  smallcap: false,
  selMode: "top_pct",
  topPct: "0.1",
  topN: "20",
  minNames: "5",
  dedupRoots: false,
  weighting: "equal",
  rebalance: "ME",
};

// f_* shared-data keys are CVM fundamentals; \bf_ won't match fin_vol / has_glitch.
const isFundExpr = (s: string) => /\bf_/.test(s);

const obj = (v: unknown): Record<string, unknown> =>
  v && typeof v === "object" ? (v as Record<string, unknown>) : {};
const str = (v: unknown) => (typeof v === "string" ? v : "");
const numStr = (v: unknown, fallback = "") =>
  v == null || v === "" ? fallback : String(v);

function fromSpec(spec: Record<string, unknown>): FormState {
  const sig = obj(spec.signal);
  const sel = obj(spec.selection);
  const uni = obj(spec.universe);
  const advanced = typeof sig.expr === "string";
  return {
    name: str(spec.name),
    description: str(spec.description),
    advanced,
    factor: advanced ? "" : str(sig.factor),
    expr: advanced ? String(sig.expr) : "",
    lookback: numStr(sig.lookback, "12"),
    minAdtv: numStr(uni.min_adtv, "1000000"),
    minPrice: numStr(uni.min_price, "1"),
    smallcap: Boolean(uni.smallcap_below_median),
    selMode: sel.top_n != null ? "top_n" : "top_pct",
    topPct: numStr(sel.top_pct, "0.1"),
    topN: numStr(sel.top_n, "20"),
    minNames: numStr(sel.min_names, "5"),
    dedupRoots: Boolean(sel.dedup_roots),
    weighting: spec.weighting === "market_cap" ? "market_cap" : "equal",
    rebalance:
      spec.rebalance === "QE" || spec.rebalance === "W-FRI"
        ? spec.rebalance
        : "ME",
  };
}

// Clone a built-in: the gallery entry exposes `params`, NOT the raw spec, so we
// can only carry the exposed numeric knobs by name. Factor/universe/expr aren't
// recoverable — the user finishes the job. (Flagged in the UI note below.)
function fromClone(s: Strategy): FormState {
  const p = new Map(s.params.map((x) => [x.name, x.default]));
  const g = (k: string, fb: string) =>
    p.has(k) && p.get(k) != null ? String(p.get(k)) : fb;
  return {
    ...DEFAULTS,
    name: `${s.name} (cópia)`,
    description: s.description,
    lookback: g("lookback", "12"),
    minAdtv: g("min_adtv", "1000000"),
    minPrice: g("min_price", "1"),
    selMode: p.has("top_n") ? "top_n" : "top_pct",
    topPct: g("top_pct", "0.1"),
    topN: g("top_n", "20"),
    minNames: g("min_names", "5"),
  };
}

function validate(f: FormState, legs: boolean): string | null {
  if (!f.name.trim()) return "Dê um nome à estratégia.";
  const sigErr = signalError(f, legs);
  if (sigErr) return sigErr;
  if (!(Number(f.minAdtv) >= 0)) return "ADTV mínimo deve ser ≥ 0.";
  if (!(Number(f.minPrice) >= 0)) return "Preço mínimo deve ser ≥ 0.";
  if (f.lookback !== "" && !(Number(f.lookback) >= 1))
    return "Lookback deve ser ≥ 1.";
  if (f.selMode === "top_pct") {
    const p = Number(f.topPct);
    if (!(p > 0 && p <= 1)) return "top_pct deve estar entre 0 e 1.";
  } else if (!(Number.isInteger(Number(f.topN)) && Number(f.topN) >= 1)) {
    return "top_n deve ser um inteiro ≥ 1.";
  }
  if (f.minNames !== "" && !(Number(f.minNames) >= 1))
    return "min_names deve ser ≥ 1.";
  return null;
}

// Public entry: clones fetch the built-in's raw spec first so the WHOLE form
// prefills (factor/expr/universe/selection/regime), not just exposed params. The
// fetch is async — we gate on the loading state, then mount the inner form once
// so its useState initializes from the resolved spec. 404 (a code-kind strategy
// has no clonable spec) → cloneSpec stays null and the inner form falls back to
// the params-only prefill with the existing note.
export function StrategyForm(props: {
  editing: UserStrategy | null;
  clone: Strategy | null;
  onClose: () => void;
  onSaved: (warnings: string[]) => void;
}) {
  const { clone } = props;
  const specQ = useStrategySpec(clone ? clone.name : null);

  if (clone && specQ.isLoading) {
    return (
      <p className="px-1 py-6 text-sm text-muted">
        Carregando spec de <span className="font-semibold text-ink">{clone.name}</span>…
      </p>
    );
  }
  const cloneSpec = clone && specQ.data ? specQ.data.spec : null;
  return <StrategyFormInner {...props} cloneSpec={cloneSpec} />;
}

function StrategyFormInner({
  editing,
  clone,
  cloneSpec,
  onClose,
  onSaved,
}: {
  editing: UserStrategy | null;
  clone: Strategy | null;
  cloneSpec: Record<string, unknown> | null;
  onClose: () => void;
  onSaved: (warnings: string[]) => void;
}) {
  const signals = useSignals();
  const save = useSaveUserStrategy();

  // Source of the prefill: an edited row, a full-spec clone, params-only clone,
  // or nothing (new). A full-spec clone reuses fromSpec but takes a fresh name.
  const srcSpec = editing?.spec ?? cloneSpec ?? null;
  const [f, setF] = useState<FormState>(() =>
    editing
      ? fromSpec(editing.spec)
      : cloneSpec
        ? { ...fromSpec(cloneSpec), name: `${clone!.name} (cópia)` }
        : clone
          ? fromClone(clone)
          : DEFAULTS,
  );
  // Fields the form doesn't expose, carried through a save (specExtras.ts).
  const [preserved] = useState(() => ({
    ...specExtras(srcSpec),
    fundLocked: Boolean(srcSpec?.needs_fundamentals ?? clone?.needs_fundamentals),
  }));
  // Regime overlay: parsed from the source spec (edit/full clone), else empty.
  // Compiled back into body.regime only when non-empty (see submit).
  const [regime, setRegime] = useState<RegimeValue>(() => parseRegime(srcSpec?.regime));
  const [regimeOpen, setRegimeOpen] = useState(() => !isEmptyRegime(parseRegime(srcSpec?.regime)));
  const [tried, setTried] = useState(false);

  const set = <K extends keyof FormState>(k: K, v: FormState[K]) =>
    setF((s) => ({ ...s, [k]: v }));

  // `?? []` is a fresh array on every render, so a useMemo keyed on it never
  // hits. Memoise the fallback itself and the ones below actually memoise.
  const sigList: SignalDef[] = useMemo(() => signals.data ?? [], [signals.data]);
  // Keep an edited strategy's own factor selectable even if it left the catalog.
  const factorOptions = useMemo(() => {
    const names = sigList.map((s) => s.name);
    return f.factor && !names.includes(f.factor)
      ? [f.factor, ...names]
      : names;
  }, [sigList, f.factor]);

  const factorExpr = sigList.find((s) => s.name === f.factor)?.expr ?? "";
  const needsFund = f.advanced
    ? isFundExpr(f.expr)
    : isFundExpr(factorExpr) || preserved.fundLocked;

  const legs = preserved.legs;
  const error = validate(f, legs);
  const apiMsg = save.error ? errorText(save.error, "Falha ao salvar a estratégia.") : null;

  function submit() {
    setTried(true);
    if (error) return;
    const signal = signalSource(f, legs);
    if (f.lookback !== "") signal.lookback = Number(f.lookback);
    Object.assign(signal, preserved.signal);

    const selection: Record<string, unknown> = { ...preserved.selection };
    if (f.selMode === "top_pct") selection.top_pct = Number(f.topPct);
    else selection.top_n = Number(f.topN);
    if (f.minNames !== "") selection.min_names = Number(f.minNames);
    selection.dedup_roots = needsFund || f.dedupRoots;

    const body: Record<string, unknown> = {
      kind: "rank_and_hold",
      name: f.name.trim(),
      description: f.description.trim() || undefined,
      rebalance: f.rebalance,
      needs_fundamentals: needsFund || undefined,
      weighting: f.weighting,
      universe: {
        min_adtv: Number(f.minAdtv),
        min_price: Number(f.minPrice),
        smallcap_below_median: f.smallcap || undefined,
        ...preserved.universe,
      },
      signal,
      selection,
      ...preserved.top,
    };
    if (!isEmptyRegime(regime)) body.regime = compileRegime(regime);

    save.mutate(
      { id: editing?.id, body },
      { onSuccess: (r) => onSaved(r.warnings ?? []) },
    );
  }

  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Nome" hint="obrigatório">
          <input
            value={f.name}
            onChange={(e) => set("name", e.target.value)}
            placeholder="Meu momentum"
            className={inputCls}
          />
        </Field>
        <Field label="Descrição" hint="opcional">
          <input
            value={f.description}
            onChange={(e) => set("description", e.target.value)}
            placeholder="Top-decil momentum 12m, mensal"
            className={inputCls}
          />
        </Field>
      </div>

      {clone &&
        (cloneSpec ? (
          <p className="rounded-md border border-hairline bg-paper px-3 py-2 text-xs text-muted">
            Clonado de <span className="font-semibold text-ink">{clone.name}</span> — spec
            completa copiada (fator, universo, seleção, regime). Renomeie e ajuste à
            vontade.
          </p>
        ) : (
          <p className="rounded-md border border-hairline bg-paper px-3 py-2 text-xs text-muted">
            Clonado de <span className="font-semibold text-ink">{clone.name}</span> — só os
            parâmetros expostos foram copiados. Confira o fator e o universo.
          </p>
        ))}

      {/* ── Fator ─────────────────────────────────────────────────────────── */}
      <fieldset className="rounded-md border border-hairline p-3">
        <legend className="flex items-center gap-2 px-1 text-xs font-semibold text-ink">
          Fator
          <button
            type="button"
            onClick={() => set("advanced", !f.advanced)}
            className="rounded-md border border-hairline bg-paper px-2 py-0.5 text-[11px] font-medium text-navy hover:bg-hairline/40"
          >
            {f.advanced ? "Usar catálogo" : "Modo avançado"}
          </button>
        </legend>

        <LegsNote show={legs} />

        {f.advanced ? (
          <div className="space-y-2">
            <textarea
              value={f.expr}
              onChange={(e) => set("expr", e.target.value)}
              rows={3}
              spellCheck={false}
              placeholder="roll_sum(shift(log_ret, 1), lookback)"
              className={`${inputCls} font-mono`}
            />
            <ExprCheatSheet />
          </div>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Sinal">
              <select
                value={f.factor}
                onChange={(e) => set("factor", e.target.value)}
                className={inputCls}
              >
                <option value="" disabled>
                  {signals.isLoading ? "carregando…" : "Escolha um fator"}
                </option>
                {factorOptions.map((n) => (
                  <option key={n} value={n}>
                    {n}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Lookback" hint="períodos">
              <input
                type="number"
                min={1}
                value={f.lookback}
                onChange={(e) => set("lookback", e.target.value)}
                className={inputCls}
              />
            </Field>
          </div>
        )}
        {f.advanced && (
          <Field label="Lookback" hint="períodos" className="mt-3">
            <input
              type="number"
              min={1}
              value={f.lookback}
              onChange={(e) => set("lookback", e.target.value)}
              className={`${inputCls} sm:w-40`}
            />
          </Field>
        )}
      </fieldset>

      {needsFund && (
        <p className="rounded-md border border-status-processing/40 bg-status-processing/10 px-3 py-2 text-xs text-ink">
          Fator de fundamentos: precisa de dados CVM ingeridos e usa dedup de classes
          (PETR3/PETR4) automaticamente. O primeiro backtest avisa se faltar dado.
        </p>
      )}

      {/* ── Universo ──────────────────────────────────────────────────────── */}
      <fieldset className="rounded-md border border-hairline p-3">
        <legend className="px-1 text-xs font-semibold text-ink">Universo</legend>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="ADTV mínimo" hint="R$/dia">
            <input
              type="number"
              min={0}
              step={100000}
              value={f.minAdtv}
              onChange={(e) => set("minAdtv", e.target.value)}
              className={inputCls}
            />
          </Field>
          <Field label="Preço mínimo" hint="R$">
            <input
              type="number"
              min={0}
              step={0.5}
              value={f.minPrice}
              onChange={(e) => set("minPrice", e.target.value)}
              className={inputCls}
            />
          </Field>
        </div>
        <Toggle
          checked={f.smallcap}
          onChange={(v) => set("smallcap", v)}
          label="Apenas small caps (abaixo da mediana de ADTV)"
        />
      </fieldset>

      {/* ── Seleção ───────────────────────────────────────────────────────── */}
      <fieldset className="rounded-md border border-hairline p-3">
        <legend className="px-1 text-xs font-semibold text-ink">Seleção</legend>
        <div className="mb-3 inline-flex rounded-md border border-hairline p-0.5 text-xs">
          {(["top_pct", "top_n"] as SelMode[]).map((m) => (
            <button
              key={m}
              type="button"
              onClick={() => set("selMode", m)}
              className={`rounded px-2.5 py-1 font-medium ${
                f.selMode === m ? "bg-navy text-white" : "text-muted hover:text-ink"
              }`}
            >
              {m === "top_pct" ? "Top %" : "Top N"}
            </button>
          ))}
        </div>

        {f.selMode === "top_pct" ? (
          <Field label={`Top ${Math.round(Number(f.topPct || 0) * 100)}% do universo`}>
            <input
              type="range"
              min={0.01}
              max={0.5}
              step={0.01}
              value={f.topPct}
              onChange={(e) => set("topPct", e.target.value)}
              className="w-full accent-navy"
            />
          </Field>
        ) : (
          <Field label="Top N nomes">
            <input
              type="number"
              min={1}
              step={1}
              value={f.topN}
              onChange={(e) => set("topN", e.target.value)}
              className={inputCls}
            />
          </Field>
        )}

        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          <Field label="Mín. de nomes" hint="p/ investir">
            <input
              type="number"
              min={1}
              step={1}
              value={f.minNames}
              onChange={(e) => set("minNames", e.target.value)}
              className={inputCls}
            />
          </Field>
        </div>
        <Toggle
          checked={needsFund || f.dedupRoots}
          disabled={needsFund}
          onChange={(v) => set("dedupRoots", v)}
          label={
            needsFund
              ? "Dedup de classes (obrigatório p/ fundamentos)"
              : "Dedup de classes (colapsa PETR3/PETR4)"
          }
        />
      </fieldset>

      {/* ── Pesos & rebalance ─────────────────────────────────────────────── */}
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Ponderação">
          <select
            value={f.weighting}
            onChange={(e) => set("weighting", e.target.value as FormState["weighting"])}
            className={inputCls}
          >
            <option value="equal">Igual</option>
            <option value="market_cap">Por tamanho (ADTV)</option>
          </select>
        </Field>
        <Field label="Rebalance">
          <select
            value={f.rebalance}
            onChange={(e) => set("rebalance", e.target.value as FormState["rebalance"])}
            className={inputCls}
          >
            {REBALANCE_FREQS.map((r) => (
              <option key={r.value} value={r.value}>
                {r.label} ({r.value})
              </option>
            ))}
          </select>
        </Field>
      </div>

      {/* ── Overlay de regime (avançado) ──────────────────────────────────── */}
      <fieldset className="rounded-md border border-hairline p-3">
        <legend className="flex items-center gap-2 px-1 text-xs font-semibold text-ink">
          Overlay de regime
          <span className="font-mono text-[11px] text-muted">avançado</span>
          <button
            type="button"
            onClick={() => setRegimeOpen((o) => !o)}
            className="rounded-md border border-hairline bg-paper px-2 py-0.5 text-[11px] font-medium text-navy hover:bg-hairline/40"
          >
            {regimeOpen ? "Ocultar" : isEmptyRegime(regime) ? "Configurar" : "Editar"}
          </button>
        </legend>
        {regimeOpen ? (
          <RegimeEditor value={regime} onChange={setRegime} />
        ) : (
          <p className="px-1 text-xs text-muted">
            {isEmptyRegime(regime)
              ? "Opcional — gate/escala o book por regime macro, parkeando em CDI quando “off”."
              : `${regime.inputs.length} entrada(s) e ${regime.rules.length} regra(s) configuradas.`}
          </p>
        )}
      </fieldset>

      {tried && error && (
        <p className="text-sm text-loss" role="alert">
          {error}
        </p>
      )}
      {apiMsg && (
        <p className="text-sm text-loss" role="alert">
          {apiMsg}
        </p>
      )}

      <div className="flex gap-2">
        <button
          type="button"
          onClick={submit}
          disabled={save.isPending}
          className="rounded-md bg-navy px-4 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-navy/90 disabled:opacity-60"
        >
          {save.isPending
            ? "Salvando…"
            : editing
              ? "Salvar alterações"
              : "Criar estratégia"}
        </button>
        <button
          type="button"
          onClick={onClose}
          className="rounded-md border border-hairline bg-paper px-4 py-2.5 text-sm font-medium text-ink hover:bg-hairline/40"
        >
          Cancelar
        </button>
      </div>
    </div>
  );
}

// The DSL vocabulary users may call in an expr — pulled from
// engine/docs/config_strategies.md (not invented). Unknown function names only
// fail at the first backtest (Phase 1's check_syntax gates structure, not name
// resolution), so we don't validate the expr client-side beyond non-empty — the
// backtest error surfaces in the run panel.
function ExprCheatSheet() {
  const rows: [string, string][] = [
    ["frames", "ret log_ret adj_close adtv vol_60d vol_20d mf_composite f_*"],
    ["escalares", "lookback + qualquer chave numérica no bloco signal"],
    ["séries", "shift roll_sum/mean/std/min/max pct_change diff ewm_mean"],
    ["elemento", "log sign abs clip"],
    ["seção", "rank zscore demean"],
    ["máscara", "mask_glitch where(x, cond) nan_add at_rebalance"],
    ["operadores", "+ - * / **  > < >= <= == !=  & |  (parentize: (a>0) & (b<1))"],
  ];
  return (
    <div className="rounded-md border border-hairline bg-paper p-2.5 text-[11px] leading-relaxed text-muted">
      <p className="mb-1 font-semibold text-ink">
        Vocabulário — maior = melhor, sem execução de código
      </p>
      {rows.map(([k, v]) => (
        <p key={k} className="font-mono">
          <span className="text-navy">{k}:</span> {v}
        </p>
      ))}
    </div>
  );
}

function Toggle({
  checked,
  onChange,
  label,
  disabled,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: string;
  disabled?: boolean;
}) {
  return (
    <label className="mt-3 flex items-center gap-2 text-xs text-ink">
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
        className="h-4 w-4 accent-navy disabled:opacity-50"
      />
      {label}
    </label>
  );
}
