// The /estrategia sleeve editor's rows ↔ what the API stores, and the options
// its searchable picker offers.
//
// A sleeve is a strategy (or "Caixa (CDI)") or one listed asset held directly:
// a B3 code (PETR4, IVVB11, a FII, the BDR AAPL34), a US symbol (SPY) or a
// crypto pair (BTCUSDT) — searched on the server (GET /market/assets). A row
// keeps an asset in its own `ticker` + `market` fields, so it is sent back as a
// ticker and never as a strategy called "IVVB11.SA" (which the save would
// refuse) — and a strategy whose *name* looks like a ticker stays a strategy.
//
// Where each kind can go: the live sleeves (alvo e rebalanceamento) take all of
// them. A saved carteira backtests on the B3 plane and apurates under B3 tax
// rules, so it takes B3 assets only; the picker shows US and crypto assets
// there disabled, saying why, and the editor will not save one into a carteira.
//
// Pure and import-free, because `npm test` runs this file through `node --test`.

import type { ComboOption } from "./combobox.ts";
import { matches } from "./combobox.ts";

export const CASH_REF = "CASH_CDI";

export type Market = "B3" | "US" | "CRYPTO";
export const MARKET_LABEL: Record<Market, string> = { B3: "B3", US: "EUA", CRYPTO: "cripto" };

// The plane a carteira runs on (engine FixedWeight loads B3); a strategy that
// names no market runs there too.
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
  market?: Market; // with a ticker; B3 when absent
  target_pct: string; // kept as string so the input can be cleared mid-edit
}

export type SleeveBody =
  | { strategy_ref: string; target_pct: number }
  | { ticker: string; market?: Market; target_pct: number };

// What the API answers for one live sleeve (GET/PUT /portfolio/sleeves).
export type LiveSleeve =
  | { strategy_ref: string; target_pct: number }
  | { ticker: string; market: Market; target_pct: number };

export interface SleevesSaveResult {
  sleeves: LiveSleeve[];
  warnings: string[];
}

// What names a row's sleeve, without its percentage.
type RowRef = Pick<SleeveRow, "strategy_ref" | "ticker" | "market">;

const rowMarket = (r: Pick<SleeveRow, "market">): Market => r.market ?? "B3";

// A row's identity: a strategy name, or the asset on its market. Prefixed with
// what the platform refuses at the start of a strategy name
// (strategy_specs._RESERVED_PREFIX), so the two can never collide; a B3 code is
// keyed without the ".SA" a saved carteira spells it with, so IVVB11.SA from a
// carteira and IVVB11 from the search are one asset.
export function rowKey(r: RowRef): string {
  if (!r.ticker) return r.strategy_ref;
  const t = r.ticker.toUpperCase();
  return `@ticker:${rowMarket(r)}:${rowMarket(r) === "B3" ? t.replace(/\.SA$/, "") : t}`;
}

// Engine fixed_weight spec → rows. Readback caveat (spec/06): sleeves are
// {strategy, weight(fraction)}, {asset:"CDI", weight} or {ticker, weight} (a B3
// code); map back to {strategy_ref | ticker, target_pct(%)}, asset:"CDI" → CASH_CDI.
export function specToRows(spec: Record<string, unknown>): SleeveRow[] {
  return specSleeves(spec).map((s) => ({
    ...sleeveRef(s),
    target_pct: String(Math.round(Number(s.weight ?? 0) * 1e6) / 1e4),
  }));
}

function specSleeves(spec: Record<string, unknown>): Record<string, unknown>[] {
  return Array.isArray(spec.sleeves) ? (spec.sleeves as Record<string, unknown>[]) : [];
}

function sleeveRef(s: Record<string, unknown>): RowRef {
  if (typeof s.ticker === "string") return { strategy_ref: "", ticker: s.ticker, market: "B3" };
  if (s.asset === "CDI") return { strategy_ref: CASH_REF };
  return { strategy_ref: String(s.strategy ?? "") };
}

// The live sleeves as the API stores them → rows.
export function liveToRows(sleeves: readonly LiveSleeve[]): SleeveRow[] {
  return sleeves.map((s) =>
    "ticker" in s
      ? { strategy_ref: "", ticker: s.ticker, market: s.market, target_pct: String(s.target_pct) }
      : { strategy_ref: s.strategy_ref, target_pct: String(s.target_pct) },
  );
}

// The assets a saved spec holds, in order (always B3 codes).
export function specTickers(spec: Record<string, unknown>): PickedAsset[] {
  return specSleeves(spec)
    .map((s) => s.ticker)
    .filter((t): t is string => typeof t === "string")
    .map((ticker) => ({ ticker, market: "B3" as const }));
}

// Row → the PUT /portfolio/sleeves or POST/PUT /strategies/user sleeve. The
// market goes along only off B3, so a B3 sleeve keeps the shape the API and
// MCP have always used.
export function rowToSleeve(r: SleeveRow): SleeveBody {
  const target_pct = parseFloat(r.target_pct);
  if (!r.ticker) return { strategy_ref: r.strategy_ref, target_pct };
  const market = rowMarket(r);
  return market === "B3" ? { ticker: r.ticker, target_pct } : { ticker: r.ticker, market, target_pct };
}

/** The rows a saved carteira cannot hold (US and crypto assets), named. */
export function offB3Rows(rows: readonly SleeveRow[]): string[] {
  return rows.filter((r) => r.ticker && rowMarket(r) !== "B3").map(rowLabel);
}

/** How a row is named in the picker's field and in messages. */
export function rowLabel(r: RowRef): string {
  if (r.ticker) {
    const m = rowMarket(r);
    return m === "B3" ? r.ticker : `${r.ticker} · ${MARKET_LABEL[m]}`;
  }
  return r.strategy_ref === CASH_REF ? "Caixa (CDI)" : r.strategy_ref;
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

// ── the picker's options ─────────────────────────────────────────────────────

export interface PickedAsset {
  ticker: string;
  market: Market;
}

// One GET /market/assets result.
export interface AssetHit extends PickedAsset {
  name: string | null;
  asset_class: string;
  price_brl: number | null;
  price_date: string | null;
}

// A picker option and what choosing it writes on the row.
export interface SleeveOption extends ComboOption {
  pick: RowRef;
}

const ASSET_CLASS_LABEL: Record<string, string> = {
  STOCK: "Ação",
  UNIT: "Unit",
  FUND: "FII ou ETF",
  ETF: "ETF",
  ETF_RF: "ETF de renda fixa",
  BDR: "BDR",
  CRYPTO: "Cripto",
};

const brl = (v: number) =>
  v.toLocaleString("pt-BR", { style: "currency", currency: "BRL", maximumFractionDigits: 2 });

export interface PickerInput {
  query: string;
  builtinRefs: readonly string[];
  userRefs: readonly string[];
  needsFunds: ReadonlySet<string>;
  held: readonly PickedAsset[]; // assets already in the rows / the saved carteira
  hits: readonly AssetHit[]; // the server's search for `query`
  carteira: boolean; // editing a saved carteira: B3 assets only
}

function strategyOption(name: string, group: string, needsFunds: ReadonlySet<string>): SleeveOption {
  return {
    id: name,
    label: name,
    group,
    detail: needsFunds.has(name) ? "precisa de fundamentos" : undefined,
    pick: { strategy_ref: name, ticker: undefined, market: undefined },
  };
}

function assetOption(a: PickedAsset, group: string, carteira: boolean, hit?: AssetHit): SleeveOption {
  const row = { strategy_ref: "", ticker: a.ticker, market: a.market };
  const detail = hit
    ? [hit.name, ASSET_CLASS_LABEL[hit.asset_class] ?? hit.asset_class,
       hit.price_brl === null ? null : brl(hit.price_brl)].filter(Boolean).join(" · ")
    : undefined;
  let disabledReason: string | undefined;
  if (hit && hit.price_brl === null) disabledReason = "sem cotação no plano de preços";
  else if (carteira && a.market !== "B3") disabledReason = "só nos sleeves ativos — carteira salva é B3";
  return { id: rowKey(row), label: rowLabel(row), group, detail, disabledReason, pick: row };
}

/**
 * Everything the picker lists for `query`: the cash sleeve, the B3 strategies
 * and the assets already in play, filtered here; then the server's search
 * results for the query, grouped by market, minus any already listed.
 */
export function pickerOptions(input: PickerInput): SleeveOption[] {
  const { query, needsFunds, carteira } = input;
  const local: SleeveOption[] = [
    { id: CASH_REF, label: "Caixa (CDI)", group: "Caixa", detail: "rende o CDI",
      pick: { strategy_ref: CASH_REF, ticker: undefined, market: undefined } },
    ...input.builtinRefs.map((n) => strategyOption(n, "Estratégias da plataforma", needsFunds)),
    ...input.userRefs.map((n) => strategyOption(n, "Minhas estratégias de regra", needsFunds)),
    ...input.held.map((a) => assetOption(a, "Ativos nesta alocação", carteira)),
  ].filter((o) => matches(query, `${o.label} ${o.detail ?? ""} ${o.pick.ticker ?? ""}`));
  const seen = new Set(local.map((o) => o.id));
  const found = input.hits
    .map((h) => assetOption(h, `Ativos · ${MARKET_LABEL[h.market]}`, carteira, h))
    .filter((o) => !seen.has(o.id))
    .sort((a, b) => MARKET_ORDER[a.pick.market!] - MARKET_ORDER[b.pick.market!]);
  return [...local, ...found];
}

// The server ranks hits by relevance across markets; the list groups them, so
// a market's hits stay together in this order (the sort is stable).
const MARKET_ORDER: Record<Market, number> = { B3: 0, US: 1, CRYPTO: 2 };

// The picker's "assets in play" group: the saved carteira's tickers plus any a
// row holds, once each — so moving a row off a ticker never takes the ticker
// out of the list it could be moved back to.
export function heldAssets(rows: readonly SleeveRow[], saved: readonly PickedAsset[]): PickedAsset[] {
  const out = new Map<string, PickedAsset>();
  const fromRows = rows
    .filter((r) => r.ticker)
    .map((r) => ({ ticker: r.ticker!, market: rowMarket(r) }));
  for (const a of [...saved, ...fromRows]) {
    const key = rowKey({ strategy_ref: "", ...a });
    if (!out.has(key)) out.set(key, a);
  }
  return [...out.values()];
}
