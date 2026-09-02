// Hand-written types mirroring platform_app/routes.py + main.py responses.
// Keep in lockstep with the API (see GET /openapi.json for the schema-defined bits).

export interface Me {
  id: string;
  email: string;
  tenant_id: string;
}

// GET /portfolios. The own book is always first and always present; the rest are
// client books an enterprise account manages.
export interface Portfolio {
  id: string;
  label: string;
  is_self: boolean;
}

export interface Bearer {
  access_token: string;
  token_type: string;
}

export interface Health {
  status: string;
}

// GET /billing. `modules` is what the tenant can use *now* (billed_modules gated by
// status, `empresarial` expanded), so the UI never reasons about dunning or bundles.
export interface Subscription {
  modules: string[];
  billed_modules: string[];
  status: string | null;
  renews_at: string | null;
  manageable: boolean;
  /** A live subscription to edit: the cart changes with PUT /billing/modules,
   *  never a second checkout (Stripe would bill it on top of this one). */
  changeable: boolean;
}

// GET /privacy/summary — LGPD Art. 18 I/II, the whole account (own book plus
// every managed one). `records` is a row count per tenant table, keyed by the
// table name; the page maps those keys to pt-BR labels.
export interface PrivacyAccount {
  id: string;
  email: string;
  is_active: boolean;
  is_verified: boolean;
  modules: string[];
  billed_modules: string[];
  plan_status: string | null;
  plan_renews_at: string | null;
  has_stripe_customer: boolean;
  oauth_providers: string[];
}

export interface PrivacyBook {
  id: string;
  label: string;
  is_self: boolean;
  records: Record<string, number>;
}

export interface PrivacySummary {
  account: PrivacyAccount;
  books: PrivacyBook[];
  /** the encarregado's channel (Art. 41), also printed on the policy page */
  contact: string;
}

export interface MarketStatus {
  prices: {
    rows: number;
    tickers: number;
    min_date: string | null;
    max_date: string | null;
  };
  tables: Record<string, { rows: number; fresh: string | null }>;
  /** Calendar days between the newest close and today; null when the plane is empty. */
  prices_age_days: number | null;
  /** Positions, P/L, TLH and the rebalance advisor are all marked from this plane.
   *  When it is behind, every one of those numbers is stale — say so rather than
   *  presenting old marks as current. */
  stale: boolean;
  last_ingest: Record<
    string,
    { status: string; started_at: string | null; finished_at: string | null }
  >;
}

// Imports move pending → processing → done | error; backtests use "running" not
// "processing". StatusBadge handles the union.
export type JobStatus = "pending" | "processing" | "running" | "done" | "error";

export interface ImportRow {
  id: string;
  filename: string;
  kind: string; // "negociacao" | "movimentacao" | "unknown"
  status: JobStatus;
  error: string | null;
  parser_version: string | null;
  created_at: string | null;
}

export interface Position {
  ticker: string;
  qty: number;
  avg_cost: number;
  total_cost: number;
  market: string;
  // Marked to the latest engine close, or for Renda Fixa to the last per-unit
  // price B3 published for the contract; null when neither exists (delisted, no
  // posição report) — such rows stay out of the P/L base.
  last_price: number | null;
  // Set only when the mark is not the live plane: an ISO date for a renda fixa
  // mark, which is month-end. Null means "latest close".
  price_date: string | null;
  market_value: number | null;
  pnl: number | null;
  pnl_pct: number | null;
}

// GET /portfolio/index — daily time-weighted index of the book, base 100, with
// IBOV/CDI overlays. `ibov`/`cdi` are absent from every point when the engine
// could not fetch them (offline stack, network down); `excluded` says so.
// A `type` alias, not an interface: only aliases get the implicit index
// signature that the chart components' Row type needs.
export type IndexPoint = {
  date: string;
  portfolio: number;
  ibov?: number;
  cdi?: number;
};

export interface IndexMetrics {
  total_return_pct: number | null;
  /** null for windows under a year — annualising a short run misleads. */
  ann_return_pct: number | null;
  max_dd_pct: number | null;
}

export interface PortfolioIndex {
  start: string;
  end: string;
  base: number;
  series: IndexPoint[];
  nav: number;
  metrics: { portfolio: IndexMetrics; ibov?: IndexMetrics; cdi?: IndexMetrics };
  /** The holdings this line was built from — the `?tickers=` selection, or the
   *  whole book when there is none. */
  tickers: string[];
  /** Every holding the index can carry, i.e. the menu the filter offers. Equal
   *  to `tickers` when nothing is filtered out. */
  universe: string[];
  excluded: {
    markets: string[];
    categories: string[];
    unpriced_tickers: string[];
    benchmarks: boolean;
    /**
     * Renda fixa contracts the index could not draw, one per row with the
     * reason already phrased in pt-BR by the API. Named individually rather
     * than rolled into `markets` because each one is a holding the user can
     * act on — send a longer extract, wait for the resgate.
     */
    renda_fixa: { ticker: string; code: string; reason: string }[];
  };

  coverage: {
    first_trade: string;
    plane_start: string;
    /** true when the chart starts after the user's real history does. */
    clamped: boolean;
    /** trades newer than the last close, marked at that stale price. */
    trades_after_plane: number;
  };
}

export interface Trade {
  date: string | null;
  side: string;
  market: string;
  broker: string;
  ticker: string;
  qty: number;
  price: number;
  amount: number;
  signed_qty: number;
  source_file: string;
}

export interface TradesPage {
  total: number;
  limit: number;
  offset: number;
  items: Trade[];
}

export interface IncomeRow {
  event_category: string;
  year: number;
  total: number;
}

export interface Sleeve {
  strategy_ref: string;
  target_pct: number;
}

export interface SleevesSaveResult {
  sleeves: Sleeve[];
  warnings: string[];
}

export interface DriftRow {
  ticker: string;
  target_w: number;
  current_w: number;
  drift_w: number;
  drift_brl: number;
}

export interface Target {
  as_of: string;
  combined_weights: Record<string, number>;
  current_weights: Record<string, number>;
  cash_weight_target: number;
  drift: DriftRow[];
  nav: number;
}

export interface StrategyParam {
  name: string;
  label: string;
  type: string;
  default: unknown;
  min: number | null;
  max: number | null;
  step: number | null;
  choices: unknown[] | null;
}

export interface Strategy {
  name: string;
  kind: "code" | "spec";
  description: string;
  needs_fundamentals: boolean;
  // Which market plane the strategy is authored for ("B3" | "US" | "CRYPTO").
  // Optional because a cached/older gallery response predates the field.
  market?: string;
  params: StrategyParam[];
  // GET /strategies tags each entry: built-in gallery vs this tenant's own.
  source?: "builtin" | "user";
}

// ── User-created strategies ───────────────────────────────────────────────────
// The engine runs strategies as data (see spec/06-user-strategies.md). Two kinds:
// a factor-ranked equity book, or a fixed-weight sleeve blend.
export type StrategyKind = "rank_and_hold" | "fixed_weight";

// GET /strategies/signals — the named-signal catalog for the factor picker.
export interface SignalDef {
  name: string;
  expr: string;
  [param: string]: unknown; // default params (lookback, …)
}

// GET /strategies/user — the tenant's rows, spec in the engine schema verbatim.
// `spec` is loosely typed here; each builder maps it to/from its own form shape.
export interface UserStrategy {
  id: string;
  name: string;
  kind: StrategyKind;
  spec: Record<string, unknown>;
  source: "user";
  created_at: string | null;
  updated_at: string | null;
}

// POST/PUT /strategies/user echo the row plus soft warnings (e.g. needs-funds).
export interface SaveStrategyResult extends UserStrategy {
  warnings: string[];
}

export interface BacktestRun {
  id: string;
  strategy: string;
  status: JobStatus;
  error: string | null;
  params: Record<string, unknown> | null;
  metrics: Record<string, unknown> | null;
  created_at: string | null;
  finished_at: string | null;
}

export interface BacktestCurves {
  id: string;
  // Column-oriented: { date: [...], pretax: [...], aftertax: [...], drawdown: [...], ... }
  series: Record<string, (string | number)[]>;
}

// GET /portfolio/tax-summary — the cached tlh_plan payload (no sells), i.e. the
// book's tax situation this month. `payload` is null when it was never computed;
// `stale` is true when an extract landed after `computed_at`.
export interface TaxBucket {
  status: string;
  gross_after: number;
  pnl_after: number;
  carry_used: number;
  /** IR devido on the month's realised position — an estimate to verify. */
  ir: number;
  harvest: {
    ticker: string;
    qty: number;
    price: number;
    realized_loss: number;
    partial: boolean;
  }[];
}

export interface TaxSituation {
  /** Gross sales month-to-date. Against isencao_limit this is the headroom. */
  gross_mtd: number;
  /** Realised P&L month-to-date. */
  pnl_mtd: number;
  /** Prejuízo acumulado available to offset gains. */
  carry: number;
}

/** IR owed on the month as it stands, computed by the API from `situation`.
 *  Deliberately not `TaxBucket.ir`, which is the IR *after* executing the
 *  harvest plan and therefore reads zero exactly when the real DARF is largest. */
export interface TaxOwed {
  /** Ações only: gross sales at or below the R$20k monthly limit. */
  isento: boolean;
  carry_used: number;
  taxable: number;
  ir: number;
}

export interface TaxPlan {
  month: string;
  generated: string;
  isencao_limit: number;
  rates: Record<string, number>;
  situation: Record<string, TaxSituation>;
  buckets: Record<string, TaxBucket>;
  /** Absent on payloads cached before this field existed. */
  owed?: Record<string, TaxOwed>;
}

export interface TaxSummary {
  payload: TaxPlan | null;
  computed_at: string | null;
  stale: boolean;
  /** Why it is stale: a newer import, or the month rolling over. */
  stale_reason: "import" | "month" | null;
}

/**
 * The indexer of a renda fixa contract still in the book — IPCA+x%, % do CDI,
 * CDI+x% or prefixado. Tenant-entered: neither B3 extract carries it, and a
 * contract with no resgate has only one price, so nothing implies it.
 * `rate_pct` reads against `indexer`: 95 with `cdi_pct` is 95% do CDI, 2.5 with
 * `cdi_plus` is CDI + 2,5% a.a.
 */
export interface RendaFixaTerm {
  ticker: string;
  indexer: "cdi_pct" | "cdi_plus" | "ipca_plus" | "pre";
  rate_pct: number;
}

/** One DARF the user says they paid. The only stored half of the ledger. */
export interface DarfPaymentRecord {
  id: number;
  /** "AAAA-MM" — the apuração month, not the payment month. */
  apuracao: string;
  classe: DarfClasse;
  valor_principal: number;
  data_pagamento: string | null;
  obs: string | null;
}

/** The three lines the Renda Variável ficha apurates separately. */
export type DarfClasse = "AÇÃO" | "FII" | "BDR_ETF";

/**
 * A month's apuração joined to whatever payment was recorded against it. Every
 * figure but `pagamento` is recomputed from the trade history on each read — the
 * same numbers the IRPF report's monthly tables show.
 */
export interface DarfMonth {
  apuracao: string;
  classe: DarfClasse;
  codigo: string;
  gross: number;
  pnl: number;
  compensacao: number;
  base: number;
  aliquota: number;
  ir_devido: number;
  /** Último dia útil do mês seguinte. */
  vencimento: string;
  pagamento: DarfPaymentRecord | null;
  /** `atrasado` is the alert: tax due, past the deadline, nothing recorded. */
  status: "pago" | "pendente" | "atrasado";
}

export interface DarfLedger {
  year: number;
  codigo: string;
  meses: DarfMonth[];
  total_devido: number;
  total_pago: number;
  atrasados: number;
}
