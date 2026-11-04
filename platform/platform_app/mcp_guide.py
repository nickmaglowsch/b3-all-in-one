"""What an AI client reads before it designs a strategy over MCP (spec/09).

The engine's own reference is engine/docs/config_strategies.md, written for
people editing YAML in the repo. This is the same language from the other side
of the API: the request shape create_strategy accepts (which is not the YAML
shape — portfolios take strategy_ref|ticker + target_pct, not raw sleeves), the limits
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
for B3 stocks only — not ETFs or BDRs —, FIIs 20%, loss carry-forward), so every
result has a pre-tax and an
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
  "weighting": "equal",                    // equal | adtv (liquidity) | expr (weight_expr)
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
- `universe.tickers` (optional, 1..50 codes) makes the universe exactly those
  names: `["BTCUSDT"]` on CRYPTO for a Bitcoin-only book (the top pair by `adtv`
  is often a stablecoin pair), `["IVVB11", "BOVA11"]` on B3. Spelled for the
  spec's market; a code the plane never priced is refused on save.
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
| sector (B3) | `sector_rank(x)`, `sector_zscore(x)`, `sector_demean(x)` — the same, within each CVM sector |

Dividends (B3 only, from B3's corporate actions): `div_yield_12m`
(trailing-12-month cash dividends + JCP per share over price, each payment
over the price it was paid at), `div_count_12m` (distinct dividend/JCP dates —
the last day with rights, "data com" — in the last 12 months), `div_years_3y`
(how many of the last three 12-month windows had one, 0..3 — `div_years_3y >= 2`
is the indices' "regular payer" screen).
A name with no record reads 0.

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

### Multi-leg signals and named sub-expressions

Instead of `factor`/`expr`, a signal may be `legs`: a weighted sum of
sub-factors, each put on a common scale first.

```json
"signal": {
  "lookback": 6,
  "vars": {"mom": "roll_sum(shift(log_ret, 1), lookback)",
           "qual": "f_net_income_ttm / f_equity"},
  "legs": [
    {"factor": "mom", "weight": 0.5},
    {"expr": "sector_rank(qual)", "weight": 0.3, "transform": "raw"},
    {"factor": "div_yield_12m", "weight": 0.2, "transform": "zscore"}
  ],
  "coverage": "any"
}
```

- A leg is exactly one of `factor` / `expr`; `weight` (default 1) may be
  negative; `transform` is `rank` (default, percentile per date), `zscore` or
  `raw`. Every leg reads the signal's `lookback` and extra numeric keys — for a
  different window, write the number into the leg's expr.
- `coverage: "all"` (default) scores only names every leg covers; `"any"`
  scores a name on the legs it has — a leg it lacks counts as that leg's
  average on the date, whatever the sign of the weight. That pulls such a name
  toward the middle, so a top-N picks it a little less often than one with
  every leg. Use it when legs come from different tables (prices vs filings).
- A leg's expr must read market data or a var; a bare number is refused.
- `vars` (up to 16) are named expressions, evaluated in order, each able to
  read the earlier ones; `expr`, every leg and `weight_expr` can read them, by
  name inside an expression or as a leg's `factor`. A var's name must be new:
  not a function (`rank`), a named signal (`momentum`), a data frame (`ret`,
  anything starting `f_`, `div_`, `bench_`, `ibov_`, `cdi_`, `sector_`), a key
  of the signal block or `lookback`.

### Weights, caps, sectors and the calendar

- `"weighting": "expr"` with `"weight_expr": "<expression>"` (same language,
  same `vars`) weights each held name in proportion to the expression's value
  at the signal's date. Inverse volatility: `"1 / vol_60d"`; signal-weighted:
  `"rank(mom)"`. A held name scoring NaN, infinite (a zero volatility) or <= 0
  gets nothing; if none score above 0 the book is equal-weighted.
- `"max_weight": 0.1` caps every name at 10% and hands the excess to the
  others in proportion. Hold at least `1 / max_weight` names (`min_names`),
  or the cap cannot be met and the book is equal-weighted instead.
- `"universe": {"sectors": {"include": ["banc", "energia"], "exclude": []}}`
  (B3) keeps names whose latest CVM sector (SETOR_ATIV) contains an `include`
  keyword and none of the `exclude` ones, case-insensitive. CVM's sectors
  include "Bancos", "Energia Elétrica", "Saneamento, Serv. Água e Gás",
  "Telecomunicações", "Seguradoras e Corretoras", "Comércio (Atacado e Varejo)",
  "Intermediação Financeira" (Portuguese, as CVM writes them; validate_strategy
  lists them all and refuses a keyword matching none). A holding company
  ("Emp. Adm. Part. - Energia Elétrica") counts as its sector, here and in
  `sector_rank` and co. A name with no sector on file fails `include`. A run fails, rather than sitting in cash, when the plane has
  no sector data.
- `"rebalance_months": [6, 12]` trades only on the first grid row of those
  calendar months and holds (lets the book drift, no trades, no tax) on every
  other row: a semiannual book on `rebalance: "ME"`; `[12]` is annual. On `QE`
  only 3, 6, 9 and 12 exist. Works on portfolios too. Between listed months a
  run's `contribution` waits for the next one, earning nothing. As a sleeve of
  a portfolio, the strategy's book is the one from its last listed month, but
  the portfolio still rebalances back to it on the portfolio's own grid.

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
`bench_uptrend`, `bench_vol_pctrank` (numeric — use `gt`), `bench_drawdown`
(numeric, 0..1 — use `gt`: how far the benchmark is below its all-time high,
read off its whole history; on CRYPTO the benchmark is BTC, in BRL), `is_easing`
(COPOM cutting rates, B3). Rules match first to last; a rule may set its own
`top_pct`, or `exposure` (0..1: invest that fraction of the book, the rest in
CDI; not beside `park`); `{"min_true": 2}` is an N-of-M vote over the inputs.
`offset: 1` reads the previous period (no look-ahead); `offset: 0` the current row.

Accumulation tilt — hold more Bitcoin the further it is below its high:

```json
{"kind": "rank_and_hold", "name": "BTC drawdown tilt", "market": "CRYPTO",
 "rebalance": "ME",
 "universe": {"min_adtv": 0, "min_price": 0, "tickers": ["BTCUSDT"]},
 "signal": {"factor": "adtv", "apply_glitch": false},
 "selection": {"top_n": 1, "min_names": 1},
 "regime": {"add_ibov": false,
   "inputs": {"deep":    {"flag": "bench_drawdown", "offset": 1, "gt": 0.50},
              "cheap":   {"flag": "bench_drawdown", "offset": 1, "gt": 0.25},
              "neutral": {"flag": "bench_drawdown", "offset": 1, "gt": 0.10}},
   "rules": [{"deep": true, "exposure": 1.0}, {"cheap": true, "exposure": 0.6},
             {"neutral": true, "exposure": 0.35}, {"default": true, "exposure": 0.2}]}}
```

That gates on the **market's benchmark** (BTC on CRYPTO, the Ibovespa on B3, the
S&P on US). To size a book by **each asset's own** drawdown, use the data name
`ath_drawdown` (per name: `1 - close / its all-time high`, 0 at a new high, read
off its whole history) and `exposure_expr`: an expression like `weight_expr`
(same vars and scalars) after which each held name keeps `clip(value, 0, 1)` of
its weight and the rest sits in CDI (NaN keeps nothing). It stacks with a rule's
`exposure` (they multiply) and works without a regime. Any asset, any market:

```json
{"kind": "rank_and_hold", "name": "ETH own-drawdown tilt", "market": "CRYPTO",
 "rebalance": "ME",
 "universe": {"min_adtv": 0, "min_price": 0, "tickers": ["ETHUSDT"]},
 "signal": {"factor": "adtv", "apply_glitch": false},
 "selection": {"top_n": 1, "min_names": 1},
 "exposure_expr": "clip(0.2 + ath_drawdown, 0, 1)"}
```

(20% at the high, all in 80% below it; `"ath_drawdown"` alone is the
indicator's own mapping, deploy fraction = depth below the high. On B3,
`"tickers": ["PETR4"]` or `["BOVA11"]`; on US, any listed name.)

Both drawdowns are **in BRL**, like every price here — the holder's price: on
US and CRYPTO a weakening real lifts a name toward a new BRL high while its
dollar price is still well below its peak, so they read shallower than a
dollar chart of the same asset. On B3 the peak history starts at the Plano
Real (1994-07). `ath_drawdown` is a frame (one value per name) — not a
regime flag; `bench_drawdown` is a flag — not readable in an expression.
Reading `ath_drawdown` streams the full price history of the run's market,
so on US it noticeably lengthens a run.

Exposure is rebalanced to on every row the book trades (all of them, or only the
`rebalance_months` ones): when the holding rallies the book
sells back down to its exposure, and the sale is taxed (crypto: 15%, R$35k
monthly isenção, no loss carry). Compare it with a constant-exposure twin (one
`default` rule at the average exposure) on the same window: the difference is
the timing, the rest is just holding less BTC. Weekly (`W-FRI`) rebalancing
multiplies the turnover.

## 2. Portfolio (`kind: "fixed_weight"`)

A constant-weight blend, rebalanced on its own grid — the same engine the
built-in blends ("DIVO11 / CDI / IVVB11 (thirds)") run on. Each sleeve is
exactly one of:

- `{"strategy_ref": "<name>", "target_pct": N}` — a built-in, one of the user's
  rank_and_hold strategies, or `CASH_CDI` (cash earning the CDI). A portfolio
  cannot contain another user portfolio.
- `{"ticker": "<B3 code>", "target_pct": N}` — one listed instrument held
  directly, e.g. `IVVB11.SA` (S&P 500 in BRL), `DIVO11.SA`, `BOVA11.SA`
  (Ibovespa); `.SA` is optional. Indices (`^BVSP`) are not tradable — use the ETF.

Kinds mix freely; percentages must sum to 100 and a ticker appears in one
sleeve only.

```json
{"kind": "fixed_weight", "name": "Example 40/30/30", "rebalance": "QE",
 "sleeves": [{"ticker": "IVVB11.SA", "target_pct": 40},
             {"ticker": "DIVO11.SA", "target_pct": 30},
             {"strategy_ref": "CASH_CDI", "target_pct": 30}]}
```

```json
{"kind": "fixed_weight", "name": "AI Blend", "rebalance": "QE",
 "sleeves": [{"strategy_ref": "MomSharpe", "target_pct": 40},
             {"strategy_ref": "AI Momentum 6m Top20", "target_pct": 40},
             {"ticker": "IVVB11.SA", "target_pct": 20}]}
```

- validate_strategy refuses a ticker the B3 price plane has never priced, by
  name, and says why for an index ($IMAB, ^BVSP: hold the ETF that tracks it).
  A renda-fixa ETF (IMAB11, IB5M11, B5P211, IRFM11, LFTS11) has its own prices
  only from mid-2025; before that the backtest follows its ANBIMA index minus
  the fund's fee, and a warning names the index, the dates and the fee — and
  says when the index is a stand-in: B5P211 follows IMA-B 5 and IRFM11 follows
  IRF-M, because their own indexes (the P2 variants) have no public history;
  LFTS11 follows IMA-S. LFTB11 and FIXA11 have no free index history and are
  refused. One that lists after the history starts
  (IVVB11: 2014) comes back as a warning with its first price date: until then
  its weight is held in CDI, as the built-ins do — so a backtest from before
  that date is partly a cash one. One that stopped trading comes back with its
  last date: after it the sleeve sits at its last price, earning 0%.
- Tax: the same engine as the built-in blends, per asset class as the IRPF
  apurates it. Ações (and units) pay 15% with the R$20k monthly isenção, tested
  on the month's ações sales only; ETFs (IVVB11, BOVA11, DIVO11…) and BDRs pay
  15% with no isenção at all; both share one loss carry. FIIs pay 20% in a
  carry of their own; a `*11` the bench does not know as an ETF or unit is
  presumed a FII (so a rank strategy holding old units pays 20% on them). Cash
  (CASH_CDI, and where a regime parks) is booked like ações, isenção included,
  and so is the IBOV column a regime overlay can hold. `loss_carryforward` in a
  run's curves is the two carries summed.
- A portfolio with ticker sleeves backtests and lists like any other, but the
  live rebalance target ("Ativar" in /estrategia) only takes strategies and
  CASH_CDI, so it cannot be activated there.

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

Warm-up does not eat the window. The history a signal needs before it exists
(a 12-month lookback, a `roll_std(..., 252)`, a portfolio's strategy sleeves)
is read from **before** `start`; trades, taxes and the curve still begin at
`start`, and a signal at date t only uses data up to t. So a run is invested
from its first rebalance, and two windows of one strategy usually hold the same
book on the dates they share — not always: a regime flag ranked against all
the history loaded (`bench_vol_pctrank`, `bench_calm`), a `sticky` selection, or
any book whose choice depends on what it held before can differ while the two
paths converge. `window.invested_from` is the first rebalance on which every
book in the run (each strategy sleeve too) was invested. When the data does not
reach far enough back (a start near the beginning of history), the warm-up
falls inside the window: `invested_from` is later and `data_warnings` has
`{"code": "warmup_inside_window", "detail": {"strategy", "invested_from"}}`.
`not_invested_at_start` (same detail) means the history was there but the book
was empty anyway (too few names passed the filters). Either way the book sat in
cash or CDI until `invested_from`, so judge the run from there.

`benchmark_missing` (`{market, ticker}`) means the run had no benchmark data in
its window: every `bench_*`/`ibov_*` regime flag read False, so a gated book sat
in CDI — a plane problem, not a calm strategy. `benchmark_proxy_from_plane`
(`{market, ticker, proxy}`) means the stored series was empty and the run used
the market's proxy off its own plane instead (US: `$SPX` from SPY, in BRL).

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
