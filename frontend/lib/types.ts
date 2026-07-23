// Hand-written types mirroring platform_app/routes.py + main.py responses.
// Keep in lockstep with the API (see GET /openapi.json for the schema-defined bits).

export interface Me {
  id: string;
  email: string;
  tenant_id: string;
}

export interface Bearer {
  access_token: string;
  token_type: string;
}

export interface Health {
  status: string;
}

export interface MarketStatus {
  prices: {
    rows: number;
    tickers: number;
    min_date: string | null;
    max_date: string | null;
  };
  tables: Record<string, { rows: number; fresh: string | null }>;
}

// Imports move pending → processing → done | error; backtests use "running" not
// "processing". StatusBadge handles the union.
export type JobStatus =
  | "pending"
  | "processing"
  | "running"
  | "done"
  | "error";

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
  params: StrategyParam[];
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
