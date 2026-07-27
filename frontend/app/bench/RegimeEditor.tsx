"use client";

// Regime-overlay builder for a rank_and_hold spec's `regime` block
// (engine/docs/config_strategies.md · "Regime overlay"). A controlled sub-form:
// StrategyForm owns the compile-to-body and prefill and just hands us
// value + onChange. The pure parseRegime/compileRegime/isEmptyRegime helpers
// live here (next to the shape they serialize) so StrategyForm stays the
// orchestrator without knowing the regime wire format.

// Common macro flags in shared_data (documented list). ibov_vol_pctrank is a
// 0..1 percentile → usually paired with `gt`; the rest read as booleans.
export const REGIME_FLAGS = [
  "is_easing",
  "above_ma200",
  "ibov_above",
  "ibov_calm",
  "ibov_uptrend",
  "ibov_vol_pctrank",
] as const;

export interface RegimeInput {
  name: string;
  flag: string;
  offset: 0 | 1;
  gt: string; // "" = no threshold (boolean read)
}

export type RuleKind = "cond" | "vote" | "default";
export type RuleAction = "invest" | "park";

export interface RegimeRule {
  kind: RuleKind;
  conds: Record<string, boolean>; // cond: inputName -> required truth; absent = ignore
  minTrue: string; // vote: N-of-M
  action: RuleAction;
  topPct: string; // invest override; "" = fall back to selection.top_pct
}

export interface RegimeValue {
  addIbov: boolean;
  parkWhenTooFew: boolean;
  inputs: RegimeInput[];
  rules: RegimeRule[];
}

const obj = (v: unknown): Record<string, unknown> =>
  v && typeof v === "object" ? (v as Record<string, unknown>) : {};

// Non-empty = worth sending. StrategyForm omits the `regime` key when this is
// false so we never POST an empty overlay.
export const isEmptyRegime = (v: RegimeValue) =>
  v.inputs.length === 0 && v.rules.length === 0;

// Mirrors config_strategy.py's top_pct-suppression: if every rule overrides
// top_pct (or parks / is the default), selection.top_pct is a dead knob.
export const topPctInert = (v: RegimeValue) =>
  v.rules.length > 0 &&
  v.rules.every(
    (r) => r.action === "park" || r.kind === "default" || r.topPct.trim() !== "",
  );

const RESERVED = new Set(["top_pct", "park", "default", "min_true"]);

export function parseRegime(raw: unknown): RegimeValue {
  const r = obj(raw);
  const inputs: RegimeInput[] = Object.entries(obj(r.inputs)).map(([name, v]) => {
    const ic = obj(v);
    return {
      name,
      flag: typeof ic.flag === "string" ? ic.flag : REGIME_FLAGS[0],
      offset: Number(ic.offset) === 0 ? 0 : 1,
      gt: ic.gt == null ? "" : String(ic.gt),
    };
  });
  const rulesRaw = Array.isArray(r.rules) ? r.rules : [];
  const rules: RegimeRule[] = rulesRaw.map((rr) => {
    const rule = obj(rr);
    const kind: RuleKind = rule.default
      ? "default"
      : "min_true" in rule
        ? "vote"
        : "cond";
    const conds: Record<string, boolean> = {};
    if (kind === "cond")
      for (const [k, v] of Object.entries(rule))
        if (!RESERVED.has(k)) conds[k] = Boolean(v);
    return {
      kind,
      conds,
      minTrue: rule.min_true == null ? "2" : String(rule.min_true),
      action: rule.park ? "park" : "invest",
      topPct: rule.top_pct == null ? "" : String(rule.top_pct),
    };
  });
  return {
    addIbov: r.add_ibov !== false, // default true
    parkWhenTooFew: r.park_when_too_few !== false,
    inputs,
    rules,
  };
}

export function compileRegime(v: RegimeValue): Record<string, unknown> {
  const names = new Set(v.inputs.map((i) => i.name.trim()).filter(Boolean));
  const inputs: Record<string, unknown> = {};
  for (const inp of v.inputs) {
    const nm = inp.name.trim();
    if (!nm) continue;
    const one: Record<string, unknown> = { flag: inp.flag, offset: inp.offset };
    if (inp.gt.trim() !== "") one.gt = Number(inp.gt);
    inputs[nm] = one;
  }
  const rules = v.rules.map((rule) => {
    const out: Record<string, unknown> = {};
    if (rule.kind === "default") out.default = true;
    else if (rule.kind === "vote") out.min_true = Number(rule.minTrue);
    else
      for (const [k, val] of Object.entries(rule.conds))
        if (names.has(k)) out[k] = val; // prune stale refs to removed inputs
    if (rule.action === "park") out.park = true;
    else if (rule.topPct.trim() !== "") out.top_pct = Number(rule.topPct);
    return out;
  });
  return {
    add_ibov: v.addIbov,
    park_when_too_few: v.parkWhenTooFew,
    inputs,
    rules,
  };
}

// ── UI ────────────────────────────────────────────────────────────────────────

const smallCls =
  "rounded-md border border-hairline bg-paper px-2 py-1.5 text-xs text-ink focus:border-navy focus:outline-none";

export function RegimeEditor({
  value,
  onChange,
}: {
  value: RegimeValue;
  onChange: (v: RegimeValue) => void;
}) {
  const set = (patch: Partial<RegimeValue>) => onChange({ ...value, ...patch });

  const addInput = () =>
    set({
      inputs: [
        ...value.inputs,
        {
          name: `sinal_${value.inputs.length + 1}`,
          flag: REGIME_FLAGS[0],
          offset: 0,
          gt: "",
        },
      ],
    });
  const setInput = (i: number, patch: Partial<RegimeInput>) =>
    set({ inputs: value.inputs.map((x, j) => (j === i ? { ...x, ...patch } : x)) });
  const rmInput = (i: number) =>
    set({ inputs: value.inputs.filter((_, j) => j !== i) });

  const addRule = () =>
    set({
      rules: [
        ...value.rules,
        { kind: "cond", conds: {}, minTrue: "2", action: "invest", topPct: "" },
      ],
    });
  const setRule = (i: number, patch: Partial<RegimeRule>) =>
    set({ rules: value.rules.map((x, j) => (j === i ? { ...x, ...patch } : x)) });
  const rmRule = (i: number) =>
    set({ rules: value.rules.filter((_, j) => j !== i) });

  const setCond = (i: number, name: string, v: "any" | "true" | "false") => {
    const conds = { ...value.rules[i].conds };
    if (v === "any") delete conds[name];
    else conds[name] = v === "true";
    setRule(i, { conds });
  };

  return (
    <div className="space-y-4">
      <p className="text-xs leading-snug text-muted">
        Gate/escala o book por um regime macro, parkeando em CDI quando “off”.
        Cada <span className="font-mono">entrada</span> lê uma flag do{" "}
        <span className="font-mono">shared_data</span>; as{" "}
        <span className="font-mono">regras</span> são avaliadas de cima para
        baixo — a primeira que casar vence.
      </p>

      <label className="flex items-center gap-2 text-xs text-ink">
        <input
          type="checkbox"
          checked={value.addIbov}
          onChange={(e) => set({ addIbov: e.target.checked })}
          className="h-4 w-4 accent-navy"
        />
        Expor IBOV como ativo disponível (add_ibov)
      </label>
      <label className="flex items-center gap-2 text-xs text-ink">
        <input
          type="checkbox"
          checked={value.parkWhenTooFew}
          onChange={(e) => set({ parkWhenTooFew: e.target.checked })}
          className="h-4 w-4 accent-navy"
        />
        Parkear em CDI quando o universo filtrado &lt; mín. de nomes
        (park_when_too_few)
      </label>

      {/* ── Entradas ─────────────────────────────────────────────────────── */}
      <div className="rounded-md border border-hairline p-3">
        <div className="mb-2 flex items-center justify-between">
          <span className="text-xs font-semibold text-ink">Entradas</span>
          <button
            type="button"
            onClick={addInput}
            className="rounded-md border border-hairline bg-paper px-2 py-0.5 text-[11px] font-medium text-navy hover:bg-hairline/40"
          >
            + Entrada
          </button>
        </div>
        {value.inputs.length === 0 ? (
          <p className="text-xs text-muted">
            Nenhuma entrada — adicione uma flag para condicionar as regras.
          </p>
        ) : (
          <ul className="space-y-2">
            {value.inputs.map((inp, i) => (
              <li
                key={i}
                className="grid grid-cols-2 gap-2 rounded-md border border-hairline bg-panel p-2 sm:grid-cols-[1fr_1fr_auto_auto_auto] sm:items-end"
              >
                <Labeled label="Nome">
                  <input
                    value={inp.name}
                    onChange={(e) => setInput(i, { name: e.target.value })}
                    placeholder="easing"
                    className={`${smallCls} w-full`}
                  />
                </Labeled>
                <Labeled label="Flag">
                  <select
                    value={inp.flag}
                    onChange={(e) => setInput(i, { flag: e.target.value })}
                    className={`${smallCls} w-full`}
                  >
                    {REGIME_FLAGS.map((fl) => (
                      <option key={fl} value={fl}>
                        {fl}
                      </option>
                    ))}
                  </select>
                </Labeled>
                <Labeled label="Offset">
                  <select
                    value={inp.offset}
                    onChange={(e) =>
                      setInput(i, { offset: Number(e.target.value) === 0 ? 0 : 1 })
                    }
                    className={`${smallCls} w-16`}
                  >
                    <option value={0}>0 (i)</option>
                    <option value={1}>1 (i-1)</option>
                  </select>
                </Labeled>
                <Labeled label="gt" hint="opcional">
                  <input
                    type="number"
                    step="any"
                    value={inp.gt}
                    onChange={(e) => setInput(i, { gt: e.target.value })}
                    placeholder="—"
                    className={`${smallCls} w-20`}
                  />
                </Labeled>
                <button
                  type="button"
                  onClick={() => rmInput(i)}
                  className="justify-self-start rounded-md border border-hairline bg-paper px-2 py-1.5 text-xs font-medium text-muted hover:text-loss"
                >
                  Remover
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* ── Regras ───────────────────────────────────────────────────────── */}
      <div className="rounded-md border border-hairline p-3">
        <div className="mb-2 flex items-center justify-between">
          <span className="text-xs font-semibold text-ink">
            Regras <span className="font-normal text-muted">(ordem = avaliação)</span>
          </span>
          <button
            type="button"
            onClick={addRule}
            className="rounded-md border border-hairline bg-paper px-2 py-0.5 text-[11px] font-medium text-navy hover:bg-hairline/40"
          >
            + Regra
          </button>
        </div>
        {value.rules.length === 0 ? (
          <p className="text-xs text-muted">
            Sem regras — normalmente uma regra de investir + uma padrão
            (default) que parkeia em CDI.
          </p>
        ) : (
          <ul className="space-y-2">
            {value.rules.map((rule, i) => (
              <li
                key={i}
                className="space-y-2 rounded-md border border-hairline bg-panel p-2.5"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-mono text-[11px] text-muted">#{i + 1}</span>
                  <select
                    value={rule.kind}
                    onChange={(e) =>
                      setRule(i, { kind: e.target.value as RuleKind })
                    }
                    className={smallCls}
                  >
                    <option value="cond">Condição</option>
                    <option value="vote">Voto (min_true)</option>
                    <option value="default">Padrão (casa sempre)</option>
                  </select>
                  <select
                    value={rule.action}
                    onChange={(e) =>
                      setRule(i, { action: e.target.value as RuleAction })
                    }
                    className={smallCls}
                  >
                    <option value="invest">Investir</option>
                    <option value="park">Parkear (100% CDI)</option>
                  </select>
                  {rule.action === "invest" && (
                    <label className="flex items-center gap-1.5 text-[11px] text-muted">
                      top_pct
                      <input
                        type="number"
                        step="any"
                        min={0}
                        max={1}
                        value={rule.topPct}
                        onChange={(e) => setRule(i, { topPct: e.target.value })}
                        placeholder="seleção"
                        className={`${smallCls} w-24`}
                      />
                    </label>
                  )}
                  <button
                    type="button"
                    onClick={() => rmRule(i)}
                    className="ml-auto rounded-md border border-hairline bg-paper px-2 py-1 text-xs font-medium text-muted hover:text-loss"
                  >
                    Remover
                  </button>
                </div>

                {rule.kind === "cond" &&
                  (value.inputs.length === 0 ? (
                    <p className="text-[11px] text-muted">
                      Defina entradas acima para condicionar esta regra (sem
                      condições = casa sempre).
                    </p>
                  ) : (
                    <div className="flex flex-wrap gap-2">
                      {value.inputs.map((inp) => {
                        const nm = inp.name.trim();
                        const cur =
                          nm in rule.conds
                            ? rule.conds[nm]
                              ? "true"
                              : "false"
                            : "any";
                        return (
                          <label
                            key={inp.name}
                            className="flex items-center gap-1.5 text-[11px] text-ink"
                          >
                            <span className="font-mono text-muted">
                              {inp.name || "?"}
                            </span>
                            <select
                              value={cur}
                              onChange={(e) =>
                                setCond(
                                  i,
                                  nm,
                                  e.target.value as "any" | "true" | "false",
                                )
                              }
                              className={smallCls}
                            >
                              <option value="any">— (ignora)</option>
                              <option value="true">verdadeiro</option>
                              <option value="false">falso</option>
                            </select>
                          </label>
                        );
                      })}
                    </div>
                  ))}

                {rule.kind === "vote" && (
                  <label className="flex items-center gap-1.5 text-[11px] text-ink">
                    <span className="text-muted">
                      Investe quando ≥ N entradas forem verdadeiras
                    </span>
                    <input
                      type="number"
                      min={1}
                      step={1}
                      value={rule.minTrue}
                      onChange={(e) => setRule(i, { minTrue: e.target.value })}
                      className={`${smallCls} w-16`}
                    />
                  </label>
                )}

                {rule.kind === "default" && (
                  <p className="text-[11px] text-muted">
                    Casa sempre — costuma ser a última regra (default), parkeando
                    em CDI.
                  </p>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>

      {topPctInert(value) && (
        <p className="rounded-md border border-hairline bg-paper px-3 py-2 text-[11px] text-muted">
          Todas as regras definem <span className="font-mono">top_pct</span> (ou
          parkeiam / são padrão): o controle de seleção <span className="font-mono">top_pct</span> fica
          inativo — o overlay decide a fatia.
        </p>
      )}
    </div>
  );
}

function Labeled({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <span className="mb-1 flex items-baseline gap-1">
        <span className="text-[11px] font-medium text-ink">{label}</span>
        {hint && <span className="font-mono text-[11px] text-muted">{hint}</span>}
      </span>
      {children}
    </label>
  );
}
