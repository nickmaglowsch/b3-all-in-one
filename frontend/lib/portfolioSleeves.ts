// A saved portfolio's sleeves ↔ the /estrategia sleeve editor's rows.
//
// A saved portfolio can hold a listed instrument directly — {ticker:
// "IVVB11.SA"}, the sleeve form the built-in blends use and the one an AI saves
// over MCP (spec/06). A row keeps that in its own `ticker` field, so "Editar" →
// "Atualizar" sends it back as a ticker and never as a strategy called
// "IVVB11.SA" (which the save would refuse) — and a strategy whose *name* looks
// like a ticker stays a strategy. Adding a new ticker sleeve stays an API/MCP
// thing; the editor only keeps the ones a carteira already has.

export const CASH_REF = "CASH_CDI";

// The plane a carteira and the live target run on (engine FixedWeight and
// advise_weights load B3); a strategy that names no market runs there too.
const SLEEVE_MARKET = "B3";

/**
 * The sleeve picker's strategy options, split into built-ins and the tenant's
 * own rule strategies: never a user portfolio (portfolios do not nest), and
 * never a US or crypto strategy, which the API refuses as a sleeve.
 */
export function pickerRefs(
  gallery: readonly { name: string; source?: string; market?: string | null }[],
  fixedWeightNames: ReadonlySet<string>,
): { builtinRefs: string[]; userRefs: string[] } {
  const builtinRefs: string[] = [];
  const userRefs: string[] = [];
  for (const s of gallery) {
    if (fixedWeightNames.has(s.name)) continue;
    if ((s.market ?? SLEEVE_MARKET) !== SLEEVE_MARKET) continue;
    (s.source === "user" ? userRefs : builtinRefs).push(s.name);
  }
  return { builtinRefs, userRefs };
}

export interface SleeveRow {
  strategy_ref: string; // "" on a ticker row
  ticker?: string;
  target_pct: string; // kept as string so the input can be cleared mid-edit
}

export type SleeveBody =
  | { strategy_ref: string; target_pct: number }
  | { ticker: string; target_pct: number };

// The row picker is one <select> over strategies and tickers. A ticker option
// says it is one in a data attribute (`data-ticker`), which is what a choice is
// read by; its value carries a prefix the platform refuses at the start of a
// strategy name (strategy_specs._RESERVED_PREFIX), so the value is unique too.
const TICKER_VALUE = "@ticker:";

// Engine fixed_weight spec → rows. Readback caveat (spec/06): sleeves are
// {strategy, weight(fraction)}, {asset:"CDI", weight} or {ticker, weight}; map
// back to {strategy_ref | ticker, target_pct(%)}, asset:"CDI" → CASH_CDI.
export function specToRows(spec: Record<string, unknown>): SleeveRow[] {
  return specSleeves(spec).map((s) => ({
    ...sleeveRef(s),
    target_pct: String(Math.round(Number(s.weight ?? 0) * 1e6) / 1e4),
  }));
}

function specSleeves(spec: Record<string, unknown>): Record<string, unknown>[] {
  return Array.isArray(spec.sleeves) ? (spec.sleeves as Record<string, unknown>[]) : [];
}

function sleeveRef(s: Record<string, unknown>): Pick<SleeveRow, "strategy_ref" | "ticker"> {
  if (typeof s.ticker === "string") return { strategy_ref: "", ticker: s.ticker };
  if (s.asset === "CDI") return { strategy_ref: CASH_REF };
  return { strategy_ref: String(s.strategy ?? "") };
}

// The tickers a saved spec holds, in order.
export function specTickers(spec: Record<string, unknown>): string[] {
  return specSleeves(spec)
    .map((s) => s.ticker)
    .filter((t): t is string => typeof t === "string");
}

// What the row's <select> shows, and what a choice in it sets on the row.
export function selectValue(r: SleeveRow): string {
  return r.ticker ? TICKER_VALUE + r.ticker : r.strategy_ref;
}

// The <select> as an onChange hands it over: its value, and the chosen option's
// dataset — the part that says "this is a ticker".
export interface PickedSelect {
  value: string;
  selectedOptions?: ArrayLike<{ dataset?: { ticker?: string } }>;
}

export function fromSelect(select: PickedSelect): Pick<SleeveRow, "strategy_ref" | "ticker"> {
  const ticker = select.selectedOptions?.[0]?.dataset?.ticker;
  return ticker
    ? { strategy_ref: "", ticker }
    : { strategy_ref: select.value, ticker: undefined };
}

export const tickerValue = (ticker: string) => TICKER_VALUE + ticker;

// Row → the POST/PUT /strategies/user sleeve.
export function rowToSleeve(r: SleeveRow): SleeveBody {
  const target_pct = parseFloat(r.target_pct);
  return r.ticker ? { ticker: r.ticker, target_pct } : { strategy_ref: r.strategy_ref, target_pct };
}

// The picker's "Ativos" group: the saved carteira's tickers plus any a row
// holds, once each — so moving a row off a ticker never takes the ticker out of
// the list it could be moved back to.
export function tickerOptions(rows: SleeveRow[], saved: string[]): string[] {
  const held = rows.map((r) => r.ticker).filter((t): t is string => !!t);
  return [...new Set([...saved, ...held])];
}

// A saved carteira's request body. Its grid (rebalance, rebalance_months — an
// MCP-made semiannual one) has no control in this editor, so an edit keeps the
// spec's own instead of resetting it to quarterly.
export function portfolioBody(
  name: string,
  desc: string,
  rows: SleeveRow[],
  spec: Record<string, unknown>,
): Record<string, unknown> {
  const kept = ["rebalance", "rebalance_months"].filter((k) => spec[k] != null);
  return {
    kind: "fixed_weight",
    name: name.trim(),
    description: desc.trim() || undefined,
    sleeves: rows.map(rowToSleeve),
    ...Object.fromEntries(kept.map((k) => [k, spec[k]])),
  };
}
