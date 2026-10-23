// A saved portfolio's sleeves ↔ the /estrategia sleeve editor's rows.
//
// The editor has one picker per row and it stores a strategy_ref. A saved
// portfolio can also hold a listed instrument directly — {ticker: "IVVB11.SA"},
// the sleeve form the built-in blends use and the one an AI saves over MCP
// (spec/06). Those ride the same row field under a prefix, so "Editar" →
// "Atualizar" sends them back as tickers instead of as a strategy called
// "IVVB11.SA", which the save would refuse. Adding a new ticker sleeve stays an
// API/MCP thing; the editor only keeps the ones a carteira already has.

export const CASH_REF = "CASH_CDI";
export const TICKER_PREFIX = "ticker:";

export interface SleeveRow {
  strategy_ref: string;
  target_pct: string; // kept as string so the input can be cleared mid-edit
}

export type SleeveBody =
  | { strategy_ref: string; target_pct: number }
  | { ticker: string; target_pct: number };

// Engine fixed_weight spec → rows. Readback caveat (spec/06): sleeves are
// {strategy, weight(fraction)}, {asset:"CDI", weight} or {ticker, weight}; map
// back to {strategy_ref, target_pct(%)}, asset:"CDI" → CASH_CDI.
export function specToRows(spec: Record<string, unknown>): SleeveRow[] {
  const sleeves = Array.isArray(spec.sleeves)
    ? (spec.sleeves as Record<string, unknown>[])
    : [];
  return sleeves.map((s) => ({
    strategy_ref: sleeveRef(s),
    target_pct: String(Math.round(Number(s.weight ?? 0) * 1e6) / 1e4),
  }));
}

function sleeveRef(s: Record<string, unknown>): string {
  if (s.asset === "CDI") return CASH_REF;
  if (typeof s.ticker === "string") return TICKER_PREFIX + s.ticker;
  return String(s.strategy ?? "");
}

export function isTickerRef(ref: string): boolean {
  return ref.startsWith(TICKER_PREFIX);
}

export function tickerLabel(ref: string): string {
  return ref.slice(TICKER_PREFIX.length);
}

// Row → the POST/PUT /strategies/user sleeve.
export function rowToSleeve(r: SleeveRow): SleeveBody {
  const target_pct = parseFloat(r.target_pct);
  return isTickerRef(r.strategy_ref)
    ? { ticker: tickerLabel(r.strategy_ref), target_pct }
    : { strategy_ref: r.strategy_ref, target_pct };
}

// The ticker refs among the rows, deduplicated — the picker's "Ativos" group,
// so a row holding one still shows (and keeps) its value.
export function tickerRefs(rows: SleeveRow[]): string[] {
  return [...new Set(rows.map((r) => r.strategy_ref).filter(isTickerRef))];
}
