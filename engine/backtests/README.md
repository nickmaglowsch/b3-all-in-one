# B3 Quantitative Backtesting Framework

The simulator behind the bench. It reads **ISIN-linked**, fully split-and-dividend-adjusted
prices (`adj_close`) from the market plane, so it is free of survivorship bias and robust
against the ticker changes and mergers that plague the Brazilian market. It implements the
**exact mechanics of Brazilian capital-gains tax** for variable income (Lei 11.033/2004),
including infinite loss carryforwards, giving realistic after-tax equity curves.

**To author a strategy, read [`../docs/config_strategies.md`](../docs/config_strategies.md)** —
most strategies are a YAML file in `strategies/specs/`, no Python. Write a `StrategyBase`
subclass in `strategies/` only for logic the config engines don't cover. This file documents
what the simulator itself does with the target weights either path produces.

## Core modules (`core/`)

- `data.py` — loads B3 data through `storage.get_connection` (SQLite or Postgres), computes
  daily financial volume natively from COTAHIST, fetches CDI from the BCB, and reads
  benchmarks off the plane.
- `shared_data.py` — builds the frame bundle (`ret`, `log_ret`, `adj_close`, `adtv`, `f_*`,
  regime flags) every strategy consumes.
- `simulation.py` — the universal multi-asset simulator: exact target-weight rebalancing,
  slippage friction, margin borrowing, and the unified Brazilian CGT engine.
- `signal_dsl.py` — `ast`-parsed, whitelisted expression interpreter for factor signals.
- `config_strategy.py` — the `rank_and_hold` / `fixed_weight` YAML engines.
- `metrics.py` — Sharpe, Calmar, max drawdown, annualised return/vol at any frequency.

## Simulator behaviour

### Margin leverage
Let a `target_weights` row sum to more than `1.0` and add a negative cash asset (the loan).
The simulator handles the negative balance, charging the CDI risk-free rate + `BORROW_SPREAD`
on the margin, without mistakenly granting capital-gains write-offs for the interest paid.

```python
target_weights.iloc[i]["PETR4"] = 1.0       # 100% long PETR4
target_weights.iloc[i]["CDI_ASSET"] = -0.50 # borrow 50% on margin
```

### Periodic buy-ins (aportes / DCA)

Pass `contribution=<BRL per calendar month>` to `run_simulation` to fund the book with
recurring deposits instead of a single lump sum. The cash is added to NAV right before the
rebalance, so the existing target-weight logic allocates it — **whatever is farthest below
its target absorbs the most new money** — and the deposit's trades pay slippage and go
through the tax engine like any other buy.

```python
result = run_simulation(..., initial_capital=100_000, contribution=1_000)
```

Deposits accrue by calendar month and land on the next rebalance row: `1x` per month on
`ME`, `3x` at each quarter-end on `QE`, and on the first weekly row of each month on
`W-FRI`. A row with no valid targets (all-zero weights) holds its deposit until the next
live rebalance. Negative values are withdrawals, and `initial_capital=0` gives a pure DCA
run.

NAV now contains deposits, so **returns must be computed with
`value_to_ret(values, result["contributions"])`** — a plain `pct_change()` books every
aporte as a gain. The result dict also carries `invested`
(`initial_capital + cumulative buy-ins`) for the money-in vs money-out view. Max
Drawdown/Calmar via `strategy_daily_values` are unaffected: that path is rebuilt from
weights, not from NAV.

Contributions are currently a flat monthly figure. Dated cash-flow replay — the tenant's
real deposits on their real dates — is open engine work; see `spec/04-roadmap.md`.

### Rebalance frequencies
Pandas offset aliases only (`ME`, `QE`, `W-FRI`), so the same strategy scales from monthly
to weekly. If you change frequency, recalculate lookbacks: a 1-year lookback is 12 `ME`
periods but 52 `W-FRI` periods.

## Universe filters that always apply

1. **Liquidity (`MIN_ADTV`)** — B3 has thousands of illiquid micro-caps and ghost tickers.
   Filter signal candidates on the `fin_vol` frame; the standard minimum is R$ 1,000,000
   average daily traded volume in the preceding month.
2. **Penny-stock floor** — require raw `close_px` >= R$ 1.00 in the preceding month, or a
   R$ 0.01 tick on a R$ 0.10 stock produces a 10% "return".

## Known inaccuracy

The isenção is applied **per rebalance row** rather than per calendar month, and FII uses a
single flat rate. Both are fixed by the asset-class tax model in `spec/04-roadmap.md`.
