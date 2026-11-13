# 05 — Strategy-Linked Portfolio (the loop, fully spec'd)

The features that connect the bench to the real portfolio: attach strategies to your
portfolio as fixed-percentage sleeves, get told exactly what to buy/sell to rebalance —
tax-optimally — and replay your real cash flows through alternative strategies ("what if").

Feasibility below is grounded in code recon of both repos; the engine changes are small
and localized.

## Concepts

- **Strategy attachment**: a tenant links one or more strategy specs (gallery or own) to
  their portfolio, each with an a-priori target percentage (must sum to 100%, cash/CDI
  counts as a sleeve). Stored, not inferred.
- **Combined target**: the platform computes each sleeve's current target weights
  (advise-only, no simulation — see Engine work) and blends them:
  `combined = Σ sleeve_pct × sleeve_weights`. This is the portfolio's target allocation.
- **One tax ledger**: tax (20k isenção, loss carry) is per CPF per calendar month — not
  per sleeve. All tax math runs on the combined portfolio. Per-sleeve numbers are
  *reporting-only* attribution (virtual TWR sub-indices, the same approach as
  `portfolio_index --sleeves` today). This matches both reality and the engine's
  `FixedWeight` nested-sleeve behavior (one merged book, one ledger).

## Feature 1 — Tax-optimal rebalance advisor

**Input:** current positions (qty, avg cost — canonical from the tenant plane), attached
sleeves, current prices, current-year realized P&L + loss carry per class (already
computable via the ported `walk_history`/`realized_per_month`).

**Pipeline:**
1. **Target**: combined target weights for today (advise-only path — no history sim).
2. **Raw diff**: `compute_trades(positions, target_weights, nav)` — the engine's rebalance
   diff loop extracted as a pure function (it currently lives inside `_execute_rebalance`,
   `simulation.py:135-179`, coupled to ledger mutation). Output in BRL, converted to
   integer share quantities via current prices.
3. **Tax-cost model**: for every candidate sell, compute the marginal tax considering:
   asset class (ações 15% + R$20k/month isenção on gross sales; FII 20%, no isenção,
   separate carry bucket; BDR/ETF 15%), month-to-date gross sales already executed,
   loss carry available per class, and unrealized loss positions that can be harvested.
   Per-ticker average cost (preço médio) is the correct Brazilian method — no lot
   tracking needed.
4. **Scheduler (the "cheapest way")**: greedy optimizer that turns the raw diff into a
   **month-by-month tranche plan**:
   - Sells with gains are scheduled to stay under R$20k gross/month in ações
     (e.g. "sell R$19.5k of PETR4 this month, the remaining R$14k next month") when the
     deferral is worth it (tax saved vs drift cost — both shown).
   - Where gains must be realized above the limit, pair them with **loss harvesting**:
     sell losers to offset, rebuy T+1 (legal in Brazil — no wash-sale rule; same-day
     rebuy is avoided because it would constitute day trade). This subsumes today's
     `tlh_plan.py` greedy harvest (worst-first, integer shares, harvest only the residual
     after carry).
   - Buys are scheduled against sale proceeds + new contributions ("I have R$X to invest
     this month" is an input).
   - Extend carry/harvest tracking to all five classes (AÇÃO/FII/BDR/ETF_RV/ETF_RF) —
     today only AÇÃO and FII buckets are computed anywhere.
5. **Output**: an ordered trade plan — per month: sells (ticker, qty, est. proceeds,
   est. tax, isenção usage), T+1 rebuys, buys — plus totals: tax paid under the plan vs
   tax if executed naively in one shot, and residual drift while the plan is in flight.
   Advisory only; nothing executes. Every number carries provenance and the standard
   "not advice / confira" framing.

**UX:** "Rebalance" tab on the portfolio dashboard. Drift view (actual vs combined target,
per position), then "plan it" → the tranche plan. Users can pin/exclude tickers
(tlh_plan's `--exclude` generalized) and set the horizon (do it now vs stretch over N
months). Re-plan any time; plans are snapshots, not state.

**Simplification adopted:** the scheduler is greedy, not an LP/MIP optimizer. Greedy
matches how tlh_plan works today, is explainable to users, and captures nearly all the
tax value (the 20k limit + harvest pairing). Revisit only if real plans prove materially
suboptimal.

## Feature 2 — What-if backtest (real cash flows through another strategy)

"What if every real I invested had followed strategy X instead?"

> **Status: shipped** as `POST /whatif` (`routes_whatif.py`). The taxcore CLIs this
> section reasons from (`portfolio_index.py`, `etf_counterfactual.py`) have since been
> deleted — flow extraction is now `routes_whatif._net_flows`, and the actual-portfolio
> curve is `GET /portfolio/index` (`routes_portfolio_index.py`). References to those
> modules below are the original design rationale; read them as history.

**Mechanics:** the tenant's real dated net external cash flows (buys − sells − income,
exactly what `portfolio_index.build_portfolio_index` already extracts into
`diag["_internals"]`) are replayed through the backtest engine running the alternative
strategy, starting from zero at the date of the user's first trade.

**Engine change (small, verified):** `run_simulation`'s `contribution` is a flat monthly
scalar applied at `simulation.py:399-408`; widen it to accept a date-indexed Series and
sum flows since the last deploy. Everything downstream already handles arbitrary BRL
amounts. Run at monthly frequency; flows deploy at the next rebalance (documented
approximation, shown in the UI).

**Comparisons offered per run:**
- Actual portfolio (money-weighted, from `portfolio_index`)
- One or more alternative strategies (any gallery/own spec, incl. multi-sleeve blends)
- The existing baselines: ETF mixes, 100% CDI, "frozen basket" — these become plain
  `fixed_weight` specs, unifying `etf_counterfactual.py` into the engine instead of
  maintaining a parallel simulator.

**Better than today's counterfactual:** the engine is tax-aware, so the comparison can be
shown **after-tax** (today's `etf_counterfactual` is gross on both sides). Requires the
asset-class tax model (Engine work below) to be honest about FII-heavy strategies.

**Output:** overlay equity curves (same money in, different vehicles), end value + profit
per scenario, after-tax deltas, and per-scenario metrics. "Same flows, different
strategy" framing is prominent so users don't read it as tracking error.

## Feature 3 — Multi-strategy sleeves

- **Data:** `portfolio_sleeves` table: `tenant_id, strategy_ref (spec id | 'CASH_CDI'),
  target_pct, attached_at`. Percentages validated to sum to 100.
- **Asset sleeves:** a sleeve can instead be one listed asset held directly —
  `ticker` + `market` (B3, US or CRYPTO), exactly one of the two per row. Its
  target weight is its own percentage, added to any strategy's pick of the same
  name; it is priced in reais off its own plane (`close_brl` for US/crypto) by
  `advise_weights --weights --markets`, and the save refuses a ticker the plane
  has never priced. Two honest limits, said in the API's `warnings`/`notes`
  rather than glossed over: the book is built from B3 extracts, so a US or
  crypto position counts as zero in the drift and the plan buys toward the full
  weight; and its sales follow other rules (exterior: annual, Lei 14.754/2023;
  cripto: R$35k monthly isenção, no loss carry), so they stay out of the B3 tax
  overlay. A crypto trade is sized in fractions of a coin. A saved carteira
  (backtested by `FixedWeight` on the B3 plane, one B3 ledger) takes B3 tickers
  only; the editor shows US/crypto assets disabled there and the API refuses them.
- **Backtesting a sleeve mix is already native:** the engine's `FixedWeight` spec nests
  strategies with weights (`config_strategy.py:356-371`) into one book with one tax
  ledger — a tenant's sleeve config compiles directly to a 5-line spec. "Backtest my
  sleeve mix" = generate that spec and run it (with real flows, per Feature 2, or
  synthetic DCA).
- **Live management:** the combined target drives the rebalance advisor (Feature 1).
  Changing sleeve percentages or swapping a strategy just changes the combined target —
  the advisor then plans the transition tax-optimally (a strategy swap is only a big
  rebalance).
- **Per-sleeve reporting:** virtual attribution — each sleeve's TWR computed from its
  target-weight series and market returns (the `portfolio_index --sleeves` technique),
  shown next to the sleeve's backtest expectation. Explicitly labeled "attribution is
  virtual; tax is computed on the whole portfolio."
- **Drift policy:** advisory bands (e.g. alert when a sleeve drifts >X% from target,
  configurable); no auto-anything.

## Engine work (shared by the three features)

Ordered; all in `engine/` with the existing 62-file test suite as the net, plus new tests
per item:

1. **Extract `compute_trades()`** from `_execute_rebalance`'s diff loop
   (`simulation.py:135-179`) as a pure function. Zero behavior change; enables advise-only.
2. **Advise-only path**: `build_shared_data(end="today")` → `generate_signals` →
   `dedup_target_weights` → last live row (guard all-zero warmup/park rows). This is the
   front half of `backtest_service.py:90-97` minus the simulation — mostly plumbing.
3. **Dated cash-flow Series** for `contribution` (Feature 2). Small, localized.
4. **Seeded initial state**: optional params to pre-fill `pt_pos`/`at_pos`
   (`{ticker: {cost_basis, current_value}}` — pooled average cost, which is all Brazilian
   tax needs), `loss_carryforward`, skipping the cash-init block (`simulation.py:346-366`).
   Enables "continue my real portfolio forward under strategy X" variants.
5. **Asset-class tax model** (the one genuinely missing piece): the sim currently has one
   flat `tax_rate` and one exemption bucket. Add per-ticker class tags (from the shared
   market plane — it knows which tickers are FII), per-class rates (ações/BDR/ETF 15%,
   FII 20%), per-class carry buckets, and isenção applied to **ações gross sales per
   calendar month** (today it's per-rebalance-row, which over-grants under quarterly
   rebalancing). This also upgrades every bench backtest's accuracy, and aligns the
   engine's tax semantics with the IRPF module's (`walk_history`) — long-term the two
   should share one tax-rules library keyed by ano-base.

## API additions

```
GET/PUT /portfolio/sleeves                 sleeve config (strategies, CASH_CDI, tickers)
GET  /market/assets?q=&market=             asset search for the sleeve picker
GET  /portfolio/target                     combined target weights + drift
POST /portfolio/rebalance-plans            {horizon_months, new_cash, pins/excludes} → plan
GET  /portfolio/rebalance-plans/{id}
POST /whatif                               {strategy_ids|inline specs, after_tax} → job
GET  /whatif/{id}                          curves, per-scenario metrics
GET  /portfolio/attribution                per-sleeve virtual TWR vs backtest expectation
```

## Out of scope

Auto-execution / broker routing · per-lot accounting (Brazil is preço médio by law) ·
LP-optimal scheduling (greedy first) · intra-month drift alerts more granular than daily.
