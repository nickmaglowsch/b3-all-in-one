# 02 — Backtest Bench

The product wrapper around `backtests/core/` from the pipeline repo. The engine is reused
as-is; this spec covers how it's exposed to tenants.

## Why it sells

- **After-tax truth**: the simulator models Brazilian variable-income CGT exactly — 15% on
  net realized gains, infinite loss carryforward, R$20k/month sale exemption, slippage,
  margin at CDI+spread, DCA contributions. Pre-tax AND after-tax equity curves per run.
  No Brazilian retail backtester does this.
- **Clean data**: survivorship-free (ISIN linkage), official B3 split factors,
  point-in-time fundamentals (no look-ahead), 1994→present.
- **Strategies as data**: users author YAML specs, not code — safe multi-tenant by design.

## Feature scope (v1)

### Strategy authoring
- Web editor for YAML specs, two kinds (as today): `rank_and_hold` and `fixed_weight`.
- Factor expressions in the existing DSL (`signal_dsl.py`); named signals from
  `signals.yaml` become a browsable **platform signal library**; users can also inline `expr:`.
- Server-side validation on save: parse spec, parse DSL, resolve signals, dry-run against
  a small window. Errors returned with line/expr context.
- The ~23 platform specs ship as a public gallery (momentum_sharpe, low_vol, multifactor,
  value_quality, regime_switching, …) — clone-and-edit is the primary authoring flow.
- Code strategies (the 15 `StrategyBase` subclasses) run only as platform-curated
  gallery entries. No user Python, ever.

### Running
- `POST /backtests` with spec + params: date range, rebalance frequency (ME/QE/W-FRI),
  initial capital, monthly contribution (DCA), slippage, tax deferral toggle, benchmark set.
- Queued job on the worker pool; per-tenant quotas (e.g. Free 0/mo, Quant N concurrent,
  M per day). Live status + captured logs (keep the job_runner status contract).
- The expensive `shared_data.py` bundle is built once per (data-version, universe) and
  cached in parquet per worker — runs share it.

### Results
- Per run: pre/after-tax equity curves, tax_paid, loss_carryforward, turnover, drawdown
  series, target weights over time; metrics (Ann Return/Vol, Sharpe, Max DD, Calmar) vs
  IBOV and CDI. Rendered as the existing Plotly tear sheets, ported to the web UI.
- Run history per tenant with side-by-side comparison (metrics table across runs).
- Artifacts stored as today's `results/<run>/` layout in object storage.

### The loop with Portfolio (the differentiator)
Fully spec'd in `05-strategy-linked-portfolio.md`: strategy attachment as fixed-percentage
sleeves, a tax-optimal rebalance advisor (month-tranched sells under the 20k isenção +
loss-harvest rebuys), and what-if backtests replaying the tenant's real cash flows through
alternative strategies. The engine extensions those features need (advise-only weights,
dated cash flows, seeded initial state, asset-class tax model) are listed there.

## Deferred (explicitly out of v1)
- `research/discovery/` IC factor pipeline as a user-facing feature — platform-internal
  first; expose discovered features later as `store:<id>` signals in the library.
- ML research modules (`research/`) — internal only.
- Intraday/day-trade simulation — both repos deliberately exclude day trade; keep it that way.
- Live/paper trading, broker order routing — different regulatory universe. Never in scope
  without CVM analysis.

## Engine migration — what's left

The storage port (env/DSN-driven connections, SQLite → Postgres dialect), the worker
wrapping of `run_simulation` + `build_metrics`, and the yfinance removal are all done;
`download_benchmark` reads the plane (IBOV from B3's own published index series, sleeves by
their own tickers).

Still open: the loop's engine extensions — dated cash flows, seeded initial state, and the
asset-class tax model (see `05-strategy-linked-portfolio.md` "Engine work" and
`04-roadmap.md`). The tax-model item also fixes a known inaccuracy in the bench itself:
isenção is applied per rebalance row instead of per calendar month, and FII uses a single
flat rate.

The engine test suite is the regression net for all of it — it covers simulation, the DSL,
the config engines and corporate actions. Keep it green.
