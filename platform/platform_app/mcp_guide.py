"""What an AI client reads before it designs a strategy over MCP (spec/09).

The engine's own reference is engine/docs/config_strategies.md, written for
people editing YAML in the repo. This is the same language from the other side
of the API: the request shape create_strategy accepts (which is not the YAML
shape — portfolios take strategy_ref/target_pct, not raw sleeves), the limits
the platform enforces, and how to read a result without fooling yourself.
Keep it in step with strategy_specs.py when that model grows a field.
"""

INSTRUCTIONS = """\
Quantax Bench: design, save and backtest systematic equity strategies on the
B3 (Brazil), US and crypto price planes, after Brazilian capital-gains tax.
Everything you save or run appears in the user's /bench page.

Workflow: get_authoring_guide (once) -> list_signals / list_strategies ->
get_strategy_spec on a similar built-in -> validate_strategy -> create_strategy
-> run_backtest (wait_seconds polls) -> list_backtests(sort_by=...) to compare
-> update_strategy and iterate. Judge on after-tax metrics, test more than one
window before calling anything a discovery, and delete_strategy what failed so
the user's list holds only candidates worth a look."""

GUIDE = """\
# Quantax Bench — strategy authoring guide

Strategies are **data, not code**: a JSON spec over a safe expression language.
Two kinds exist. The engine builds target weights; a shared simulator does the
rebalancing, slippage and the Brazilian tax engine (15%, R$20k monthly isenção
for B3 stocks, loss carry-forward), so every result has a pre-tax and an
after-tax curve. The after-tax one is the truth.

## 1. Rank-and-hold (`kind: "rank_and_hold"`)

Each rebalance date: compute a factor (date x ticker, **higher = better**),
keep the liquid universe, rank, hold the top slice, weight, hold until the next
rebalance.

```json
{
  "kind": "rank_and_hold",
  "name": "AI Momentum 6m Top20",          // unique; may not reuse a built-in name
  "description": "6-month momentum, top 20 names, monthly.",
  "market": "B3",                          // B3 (default) | US | CRYPTO
  "rebalance": "ME",                       // ME monthly | QE quarterly | W-FRI weekly
  "weighting": "equal",                    // equal | adtv (liquidity-weighted)
  "universe": {"min_adtv": 1000000, "min_price": 1.0, "smallcap_below_median": false},
  "signal": {"factor": "momentum", "lookback": 6},
  "selection": {"top_n": 20, "min_names": 5, "dedup_roots": false}
}
```

- `signal` needs exactly one of `factor` (a name from list_signals, or a
  shared-data frame such as `mf_composite`, `vol_20d`, `f_pe_ratio_dyn`) or
  `expr` (an inline expression, max 2000 chars). `lookback` is 1..36 periods
  and is available inside the expression as `lookback`. Any extra numeric key
  in `signal` is also a scalar the expression can read (e.g. `"mom_weight": 0.6`).
  Optional: `shift` (>=0), `range: [lo, hi]` (keep factor values in the band),
  `apply_glitch` (default true: NaN-out bad prints before ranking; set false
  when your expr calls `mask_glitch` itself).
- `selection` needs exactly one of `top_pct` (0 < x <= 1) or `top_n` (>= 1).
  `min_names`: fewer candidates than this => stay out. `min_hold`: floor on names
  held. `sticky: true`: keep the previous book instead of going flat.
  `dedup_roots: true` collapses share classes (PETR3/PETR4) — **required for any
  fundamentals factor on B3**, or a company is booked twice.
- `needs_fundamentals` is inferred when the factor reads an `f_*` frame.
  `warmup_pad` (default 1) skips extra leading rows; use 2 with `shift(...,1)`.
- Amounts are always BRL, on every market (the holder is Brazilian).

### Expression language

Data names: `ret`, `log_ret` (period returns), `adj_close`, `raw_close`,
`close_px` (daily), `adtv` (avg daily traded value), `fin_vol`, `vol_20d`,
`vol_60d`, `atr_m`, `mf_composite`, the benchmark `bench_ret`/`bench_px`, and on
B3/US fundamentals `f_*` (`f_revenue`, `f_net_income`, `f_net_income_ttm`,
`f_ebitda`, `f_total_assets`, `f_equity`, `f_net_debt`, `f_shares_outstanding`;
B3-only derived: `f_pe_ratio_dyn`, `f_pb_ratio_dyn`, `f_ev_ebitda_dyn`, `f_*_m`).

| group | functions |
|---|---|
| time-series | `shift(x,n)`, `roll_sum/mean/std/var/min/max/median/skew/kurt(x,w)`, `pct_change(x,w)`, `diff(x,n)`, `ewm_mean(x,span)`, `ewm_std(x,span)` |
| elementwise | `log(x)`, `sign(x)`, `abs(x)`, `clip(x,lo,hi)` |
| cross-sectional | `rank(x)` (percentile per date), `zscore(x)`, `demean(x)` |
| masking | `mask_glitch(x)`, `where(x, cond)` (keep x where cond, else NaN) |
| combining | `nan_add(a, b)` — sum two legs that have different coverage |
| calendar | `at_rebalance(x)` — daily frame onto the rebalance grid |

Operators: `+ - * / **`, unary `-`, comparisons, `&` / `|`. Parenthesise
comparisons: `(a > 0) & (b < 1)`. No attribute access, subscripts or other calls.

Recipes: momentum `roll_sum(shift(log_ret, 1), lookback)`; low volatility
`-roll_std(ret, lookback)`; risk-adjusted momentum
`roll_sum(shift(log_ret,1),lookback) / roll_std(shift(ret,1),lookback)`;
short-term reversal `-roll_sum(ret, 1)`; a composite
`0.5*rank(mask_glitch(roll_sum(shift(log_ret,1),lookback))) + 0.5*rank(mask_glitch(-roll_std(shift(ret,1),lookback)))`
(with `apply_glitch: false`); daily vol on a monthly grid
`-at_rebalance(roll_std(pct_change(adj_close), lookback * 21))`.

Traps: use `nan_add`, not `+`, when legs come from different tables (P/B and
ROE), or every name missing one leg disappears. Off B3, prices are BRL and
filings USD: **rank** fundamentals ratios, never band them with `range`. On B3
CVM income lines are year-to-date — set them against prices only via the `_ttm`
frames.

### Regime overlay (optional, rank_and_hold only)

Gate or scale the book on a macro flag; when "off", the book parks in CDI.

```json
"regime": {
  "park_when_too_few": true,
  "inputs": {
    "trend":  {"flag": "bench_above", "offset": 1},
    "stress": {"flag": "ibov_vol_pctrank", "offset": 1, "gt": 0.7}
  },
  "rules": [
    {"trend": true, "stress": false},
    {"trend": true, "stress": true, "top_pct": 0.05},
    {"default": true, "park": true}
  ]
}
```

Flags: `bench_above` (benchmark above its 10-month MA), `bench_calm`,
`bench_uptrend`, `bench_vol_pctrank` (numeric — use `gt`), `is_easing` (COPOM
cutting rates, B3). Rules match first to last; a rule may set its own `top_pct`;
`{"min_true": 2}` is an N-of-M vote over the inputs. `offset: 1` reads the
previous period (no look-ahead); `offset: 0` the current row.

## 2. Portfolio (`kind: "fixed_weight"`)

A constant-weight blend of strategies plus cash, rebalanced on its own grid.
Sleeves name built-ins, the user's rank_and_hold strategies, or `CASH_CDI`;
percentages must sum to 100. A portfolio cannot contain another user portfolio.

```json
{"kind": "fixed_weight", "name": "AI Blend", "rebalance": "QE",
 "sleeves": [{"strategy_ref": "MomSharpe", "target_pct": 40},
             {"strategy_ref": "AI Momentum 6m Top20", "target_pct": 40},
             {"strategy_ref": "CASH_CDI", "target_pct": 20}]}
```

## 3. Running backtests

`run_backtest(strategy, start, end, freq, params)`: dates `YYYY-MM-DD`
(history starts ~2005 on B3; US and crypto are shorter — read `data_warnings`).
`params` overrides the strategy's parameters (list_strategies with
`include_params: true` shows them): e.g. `lookback`, `top_pct`, `min_adtv`,
`initial_capital` (default 100000), `contribution` (BRL per period), `slippage`,
`tax_rate`. Runs execute on a shared worker: a few may be queued at once per
account (HTTP 429 means wait for one to finish). A run takes seconds to minutes.

## 4. Reading results without fooling yourself

Metrics come pre-tax and after-tax: `Ann. Return (%)`, `Ann. Volatility (%)`,
`Sharpe` (excess over the CDI — Brazil's risk-free rate is high, so a Sharpe
near 0 means "no better than cash"), `Max Drawdown (%)` (daily, negative),
`Calmar`. `total_tax_paid` shows how much turnover costs after tax.

- Compare against built-ins over the **same window** (list_backtests).
- Every variant you try is a hypothesis test; many tries on one window find
  noise. Confirm a candidate on a second, non-overlapping window (e.g. design
  on 2005-2016, confirm on 2017-today) and on a parameter neighbourhood
  (lookback +-3) before calling it robust. Report how many variants you tried.
- Prefer economically motivated factors over curve-fitted thresholds.
- Look at get_backtest_curve's yearly returns: one great year is not a strategy.
- Name strategies descriptively; put the hypothesis and the evidence in
  `description` — that is what the user reads in /bench.
"""
