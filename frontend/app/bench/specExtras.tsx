// Spec fields the bench form has no control for, kept from the source spec so an
// edit or full clone round-trips instead of silently dropping them: range/shift/
// warmup_pad, an AI-authored spec's market/min_hold/sticky, and the MCP-only
// legs/vars/weight_expr/max_weight/rebalance_months/sectors.

type Obj = Record<string, unknown>;

const obj = (v: unknown): Obj => (v && typeof v === "object" ? (v as Obj) : {});
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
    universe: pick(obj(s.universe), ["sectors"]),
    top: {
      ...pick(s, ["warmup_pad", "market", "weight_expr", "max_weight", "rebalance_months"]),
      // weight_expr is only valid with weighting "expr", which the select lacks.
      ...(s.weighting === "expr" ? { weighting: "expr" } : {}),
    },
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
