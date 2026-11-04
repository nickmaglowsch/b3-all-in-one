// Spec fields the bench form has no control for, kept from the source spec so an
// edit or full clone round-trips instead of silently dropping them: range/shift/
// warmup_pad, an AI-authored spec's market/min_hold/sticky, and the MCP-only
// legs/vars/weight_expr/exposure_expr/max_weight/rebalance_months/sectors/tickers.

type Obj = Record<string, unknown>;

export const obj = (v: unknown): Obj => (v && typeof v === "object" ? (v as Obj) : {});
const pick = (o: Obj, keys: string[]) =>
  Object.fromEntries(keys.filter((k) => o[k] != null).map((k) => [k, o[k]]));

export interface SpecExtras {
  signal: Obj;
  selection: Obj;
  universe: Obj;
  top: Obj;
  // A multi-leg signal: no factor/expr of its own, so the form's picker is moot.
  legs: boolean;
}

export function specExtras(spec: Obj | null): SpecExtras {
  const s = spec ?? {};
  const signal = pick(obj(s.signal), ["range", "shift", "apply_glitch", "vars", "legs", "coverage"]);
  return {
    signal,
    selection: pick(obj(s.selection), ["min_hold", "sticky"]),
    universe: pick(obj(s.universe), ["sectors", "tickers"]),
    top: pick(s, ["warmup_pad", "market", "weight_expr", "exposure_expr", "max_weight",
                  "rebalance_months"]),
    legs: Array.isArray(signal.legs),
  };
}

interface SignalFields {
  advanced: boolean;
  expr: string;
  factor: string;
}

export function signalError(f: SignalFields, legs: boolean): string | null {
  if (legs) return null;
  if (f.advanced) return f.expr.trim() ? null : "A expressão não pode ficar vazia.";
  return f.factor ? null : "Escolha um fator.";
}

export function signalSource(f: SignalFields, legs: boolean): Obj {
  if (legs) return {};
  return f.advanced ? { expr: f.expr.trim() } : { factor: f.factor };
}

export function LegsNote({ show }: { show: boolean }) {
  if (!show) return null;
  return (
    <p className="mb-2 text-xs text-ink/70">
      Sinal multi-perna (legs) criado pelo MCP: é mantido como está ao salvar. O fator
      abaixo é ignorado.
    </p>
  );
}

export type Weighting = "equal" | "market_cap" | "expr";

// `adtv` is the same scheme the select calls market_cap; `expr` needs the
// spec's weight_expr, which is why the select only offers it when there is one.
export function weightingOf(w: unknown): Weighting {
  if (w === "expr") return "expr";
  return w === "market_cap" || w === "adtv" ? "market_cap" : "equal";
}

// The preserved top-level fields for a save: weight_expr only rides along while
// the select still says "expr" — switched off, it would be refused.
export function topFor(top: Obj, weighting: Weighting): Obj {
  if (weighting === "expr") return top;
  return Object.fromEntries(Object.entries(top).filter(([k]) => k !== "weight_expr"));
}

export function ExprWeightingOption({ show }: { show: boolean }) {
  return show ? <option value="expr">Por expressão (weight_expr do MCP)</option> : null;
}
