# Pre-registration — Phase 2 (written before any Phase-2 backtest)

Date: 2026-11-15. Phase 1 (H1–H3) found nothing; see REPORT.md. The user asked to continue the search.

## What is now contaminated (disclosed up front)
- The 2020-01..2026-11 window is **no longer a clean holdout**. I have seen, for that window: H3c (6-1 momentum) after-tax
  Sharpe −0.14; naive EW liquid book −0.47 (−2.6%/yr); BOVA11 +0.02 (6.2%/yr); CDI ≈ 10.0%/yr. So I know broad-market
  and small/mid-cap books did badly there, and large caps roughly matched CDI.
- From general knowledge (not tool data) I also know that high-dividend large caps (oil, banks) paid very large dividends
  in 2021–2023. That biases me toward dividend hypotheses, so a dividend result in 2020+ deserves extra suspicion.
- I have also seen 2005–2019 results for low-vol, momentum and the EW book under various gates.
- Trials so far: 24 hypothesis/robustness runs across 3 hypotheses. Phase 2 adds to that count. Any final t-proxy will be
  read against the total number of variants tried, not on its own.

## Budget and windows
- New budget: **30 backtests** (runs #30–#59), at most 3 hypotheses, at most 6 parameter combos each. Every run is logged.
- Windows unchanged: design 2005..2014, validation 2015..2019, contaminated holdout 2020..latest, which gets exactly ONE run
  on ONE final spec.
- Costs: 10 bps (rerun at 20 bps); R$100k; no contributions; universe ADTV ≥ R$2M, price ≥ 1, `dedup_roots`, `min_names` 5;
  equal weight.

## Stricter bar for "good" (all must hold)
1. After-tax Sharpe vs CDI > 0 in design, validation and holdout.
2. In design and validation, still > 0 at 20 bps and with one extra period of delay.
3. Design neighbourhood (±25–50% on key parameters): every variant has Sharpe > 0.
4. Holdout after-tax Sharpe > BOVA11 buy-and-hold over the same window (+0.02).
5. It beats the naive EW book's after-tax Sharpe in validation (+0.64).
If nothing meets all five, the answer remains "no strategy found".

## H4 — Regular dividend payers (tax-clientele + agency game)
- **Players.** The government exempts dividends for resident individuals. The marginal price-setters are foreign investors
  (about half of B3 volume) and tax-exempt pension and investment funds; for them the exemption is worth nothing, so it is not
  priced in. Controlling shareholders can extract private benefits (tunneling) from minority holders. A sustained, regular
  payout is a costly commitment not to do that.
- **Counterparty.** Tax-indifferent price-setters leave the subsidy unpriced, and minority holders of non-payers bear the
  agency cost. The commitment is persistent: firms that cut regular dividends get punished, and the mandatory 25% payout is
  a floor.
- **Engine caveat.** Dividends are folded into position value and taxed as gains on sale, so the exemption itself is NOT
  modelled. The test is conservative on that leg.
- **Rules.** Signal `where(div_yield_12m, div_years_3y >= 3)`, top 20%.
  - H4a (primary): QE.
  - H4b: rebalanced only in Jun/Dec (`rebalance_months [6,12]`).
  - H4c: QE, top 30%.
  - Neighbourhood (robustness only): top 15%, top 25%, and `div_years_3y >= 2`.
  - Delay: `shift(...,1)` inside the expression.

## H5 — Market trend gate on a defensive book (slow / forced foreign-flow game)
- **Players.** Foreign institutions dominate B3 flows. They de-risk with committee lags and VaR/drawdown limits, so Brazilian
  sell-offs unfold over quarters (2008, 2014–15, 2020). Locals who buy dips early absorb those flows. When the trend is down,
  CDI is a high-yield alternative, so stepping aside is cheap.
- **Counterparty.** Slow, forced sellers and early dip-buyers. Mandate and risk-limit mechanics make them persistent.
- **Rules.**
  - H5a (primary): 126-day low-vol top 20%, ME, invested iff `bench_above` (Ibovespa > 10m MA, offset 1), else CDI.
  - H5b: the same gate on the naive EW book (isolates the gate).
  - H5c: H5a on QE.
  - Neighbourhood (robustness only): the gate rebuilt with `exposure_expr`, MA 7 and MA 13 months (±30%), with `shift(bench_px,1)`.
  - Delay: `bench_above` offset 2.

## H6 — Earnings yield (non-fundamental foreign-flow game)
- **Players.** EM-dedicated and passive money buys and sells Brazil as a block, by country allocation, ignoring firm
  valuation. Locals extrapolate recent growth. Cheap stocks overshoot during outflows.
- **Counterparty.** Non-fundamental flow sellers and extrapolators. Mandate-driven, so persistent.
- **Data caveat.** CVM fundamentals start in 2010 (DFP), so the effective design window is about 2011–2014: short and weak.
- **Rules.** Signal `where(1 / f_pe_ratio_dyn, f_pe_ratio_dyn > 0)`, top 20%.
  - H6a (primary): QE.
  - H6b: `sector_rank(...)` (sector-relative), QE.
  - H6c: ME.
  - Neighbourhood (robustness only): top 15%, top 25%.
  - Delay: `shift(...,1)` inside the expression.

## Decision rules
- Within each hypothesis, the finalist is the design combo with the highest design after-tax Sharpe. It must have Sharpe > 0
  and beat the EW book over the same months (design EW −0.07).
- Finalists get 20 bps, delay and neighbourhood runs on design. Survivors of bar items 2–3 get one validation run, then
  20 bps and delay on validation.
- Holdout: one run, on the single survivor with the highest validation after-tax Sharpe that meets bar items 2, 3 and 5.
  If none does, the holdout is not run, because there would be nothing worth spending the last contaminated look on.

## Addendum — "readiness" tests for H4b (written before running them, 2026-11-15)
Spec frozen: signal `where(div_yield_12m, div_years_3y >= 3)`, top 20%, equal weight, ADTV ≥ R$2M, `dedup_roots`.
Runs (from the remaining Phase-2 budget):
- R1: H4b on the monthly grid, Jun/Dec rebalance, one continuous run 2005..latest (the reference curve).
- R2: same, rebalanced Mar/Sep. R3: same, rebalanced Jan/Jul.
- R4: DIVO11 buy-and-hold, 2005..latest (its weight sits in CDI before it lists).
No-run checks: a regression of quarterly excess returns over CDI on BOVA11's (2009Q1 onward, once BOVA11 trades), and a
read of the engine code for delisted-stock handling.

Pass criteria (all must hold for "keep, start forward tracking"):
- T1 timing: all three schedules have after-tax Sharpe > 0 over 2006..latest AND above BOVA11 over the same months;
  the max-min spread across the three is ≤ 0.15.
- T2 DIVO11: R1's after-tax Sharpe over DIVO11's trading life > DIVO11's own, both curve-derived on the same months.
- T3 alpha: the regression intercept (alpha) is > 0 with t ≥ 1.5. t < 2 is still reported as unproven.
- T4 survivorship: delisted names stay in the history, so a stock that stops trading counts as a loss rather than disappearing.
Any failure ends the candidate. Passing all four does NOT make it proven; it makes it worth freezing and tracking forward.
