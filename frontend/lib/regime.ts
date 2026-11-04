// A rank_and_hold spec's `regime` block, as the bench form edits it
// (engine/docs/config_strategies.md · "Regime overlay"): the wire format, and the
// pure parse/compile between it and the form's state. Kept out of
// app/bench/RegimeEditor.tsx so `npm test` (lib/**/*.test.ts) covers the part
// that can silently lose a field on a round trip.

// Common macro flags in shared_data (documented list). The bench_* names are the
// market-neutral aliases (IBOV on B3, the S&P on US, BTC on crypto).
// Only one-value-per-date series: a per-name frame (above_ma200) never reads true
// as an input, and engine validate_spec refuses it (regimes.REGIME_FLAGS).
export const REGIME_FLAGS = [
  "is_easing",
  "ibov_above",
  "ibov_calm",
  "ibov_uptrend",
  "ibov_vol_pctrank",
  "bench_above",
  "bench_calm",
  "bench_uptrend",
  "bench_vol_pctrank",
  "bench_drawdown",
] as const;

// Flags that are numbers, not booleans: the engine's validate_spec refuses an
// input on one of these without a `gt` threshold (read bare, a drawdown is
// "true" on every day but a new high).
export const NUMERIC_FLAGS: ReadonlySet<string> = new Set([
  "ibov_vol_pctrank",
  "bench_vol_pctrank",
  "ibov_vol_monthly",
  "bench_vol_monthly",
  "bench_drawdown",
]);

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
  exposure: string; // invest: fraction of the book, rest in CDI; "" = all of it
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

// A rule's action keys — everything else on a cond rule is an input it matches.
// `exposure` must be here: read as a condition it would be pruned on compile and
// an AI-made partial book would come back from the form fully invested.
const RESERVED = new Set(["top_pct", "exposure", "park", "default", "min_true"]);

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
      exposure: rule.exposure == null ? "" : String(rule.exposure),
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
    else {
      if (rule.topPct.trim() !== "") out.top_pct = Number(rule.topPct);
      if (rule.exposure.trim() !== "") out.exposure = Number(rule.exposure);
    }
    return out;
  });
  return {
    add_ibov: v.addIbov,
    park_when_too_few: v.parkWhenTooFew,
    inputs,
    rules,
  };
}
