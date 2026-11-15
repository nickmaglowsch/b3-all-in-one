# Pre-registration — long-only B3 vs CDI after tax (written before any backtest)

Date: 2026-11-15. No backtest, leaderboard or `list_backtests` output had been seen when this was written.
Only the authoring guide, `list_signals`, built-in strategy names/descriptions and two specs
(LowVol, CDI+MA200 — vocabulary only) were read. Engine facts used: `slippage` is a fraction
(0.001 = 10 bps); `is_easing` = CDI one month ago < CDI four months ago.

## Common settings (all hypotheses and baselines)
- Market B3, `kind: rank_and_hold`, initial capital R$100,000, no contributions, slippage 0.001 (rerun at 0.002).
- Universe: `min_adtv` 2,000,000, `min_price` 1.0, `dedup_roots: true`, `min_names` 5.
- Equal weighting unless stated.
- Windows: design 2005-01-01..2014-12-31; validation 2015-01-01..2019-12-31; holdout 2020-01-01..latest.
- Judge from `window.invested_from` if the warm-up falls inside the window.
- Primary metric: after-tax Sharpe vs CDI. t-proxy = Sharpe × sqrt(years). |t| < 2 is "statistically weak".

## Baselines (not hypotheses)
- B_EW: the universe above, signal `adtv`, `top_pct` 1.0, equal weight, ME. (Naive equal-weight liquid book.)
- B_BOVA: fixed_weight 100% BOVA11.SA (in CDI before BOVA11 lists, ~2008).
- CDI: Sharpe 0 by definition.
Baselines run once over 2005-01-01..latest; only design-window slices are read until the holdout step.

## H1 — Low volatility (leverage-constrained / lottery-seeking counterparty)
- Game: Players are (a) retail investors who cannot borrow and like lottery-like payoffs, (b) Ibovespa-benchmarked
  active funds whose flows are convex in relative rank, so they tilt to high beta, (c) foreign macro money that uses
  liquid high-beta names as a Brazil beta bet. Information is public; incentives push demand to high-beta/high-vol stocks.
- Counterparty: the high-beta buyers above, who overpay for volatility; they persist because leverage constraints and
  benchmark contracts are structural. Arbitrage (levering low-vol) is unusually expensive in Brazil because borrowing
  costs ≥ CDI, so the anomaly should be less arbitraged here.
- Rules: signal `expr` = `-at_rebalance(roll_std(pct_change(adj_close), 126))` (6-month daily vol), top_pct 0.2, ME.
- Pre-registered combos (max 6):
  - H1a (primary): 126-day vol, top_pct 0.2, ME
  - H1b: 252-day vol, top_pct 0.2, ME
  - H1c: 126-day vol, top_pct 0.2, QE
  - Neighbourhood (robustness only, never for selection): top_pct 0.15, top_pct 0.25, vol window 189 days (or 84 if H1b chosen).
- Refuted if: design after-tax Sharpe ≤ 0, or not above B_EW's design after-tax Sharpe, or validation after-tax Sharpe ≤ 0.

## H2 — Monetary-cycle gate (COPOM is a predictable public player; slow allocators pay)
- Game: COPOM moves rates in persistent cycles. Households and fund allocators shift between CDI products and equity
  funds with inertia (performance chasing, D+30 redemption windows), so equity flows lag the cycle: equity funds are
  forced sellers into early tightening and late buyers in easing.
- Counterparty: the late allocators — they hold equities into tightening and buy late in easing. Persistence is behavioural
  and institutional; the signal is public, so my prior is that the edge is weak or priced.
- Rules: the B_EW book with a regime overlay, `add_ibov: false`, `park_when_too_few: true`; parked = CDI.
  - H2a (primary): invested iff `is_easing` (offset 1); else park.
  - H2b: invested iff `is_easing` OR `bench_above` (offset 1 each; `min_true: 1`); else park.
  - H2c: invested iff `is_easing` AND `bench_above` (offset 1 each); else park.
- Refuted if: design after-tax Sharpe ≤ 0, or not above B_EW design after-tax Sharpe, or validation after-tax Sharpe ≤ 0.

## H3 — Intermediate momentum (disposition-effect counterparty, amplified by the R$20k exemption)
- Game: Brazilian retail investors show a strong disposition effect (sell winners, hold losers). The R$20k/month
  tax-free sales allowance adds an incentive to harvest gains on winners. Sell pressure on winners and reluctance to sell
  losers slows price adjustment → underreaction → continuation over 6–12 months.
- Counterparty: disposition-prone sellers of winners / holders of losers. Persistence: behavioural; arbitrage is limited
  by momentum-crash risk and costly shorting of losers on B3.
- Rules: signal `expr` = `roll_sum(shift(log_ret, 1), 12)` (12-1 month), top_pct 0.2, ME.
  - H3a (primary): 12-1, top_pct 0.2, ME
  - H3b: 12-1, top_pct 0.2, QE
  - H3c: 6-1 (`roll_sum(shift(log_ret, 1), 6)`), top_pct 0.2, ME
  - Neighbourhood (robustness only): lookback 9 and 15 (or 4/8 if H3c), top_pct 0.15.
- Refuted if: design after-tax Sharpe ≤ 0, or not above B_EW design after-tax Sharpe, or validation after-tax Sharpe ≤ 0.

## Decision rules
1. Within a hypothesis, the finalist is the combo (a/b/c) with the highest design after-tax Sharpe. Neighbourhood runs never pick.
2. A finalist goes to validation only if it passes its design refutation test AND keeps after-tax Sharpe > 0 at 20 bps
   AND with one extra period of delay (`shift: 1`, `warmup_pad: 2`).
3. Validation: one run per surviving finalist on 2015-01-01..2019-12-31, at 10 bps.
4. Holdout: exactly one run, the single validated finalist with the highest validation after-tax Sharpe. If none validates,
   the holdout is run once on the best-validation finalist for information only, and the verdict stays Reject.
5. Budget: 30 backtests total, ≤ 6 parameter combos per hypothesis. Every run is logged, failures included.
