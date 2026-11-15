# Long-only B3 vs CDI after tax — research report (2026-11-15)

**Outcome: no strategy found.** None of the three pre-registered hypotheses beats CDI after Brazilian
taxes out of sample. The one finalist that reached the holdout (6-1 momentum) earned 2.33%/yr after
tax against CDI at ~10.1%/yr from 2020 to 2026-11 (after-tax Sharpe −0.14). This is a research finding,
not investment advice.

- Pre-registration: `PREREGISTRATION.md`, committed (`7aacfba`) before any backtest.
- Raw evidence: `evidence/` (every run's JSON and curve, `ledger.tsv`, `summary.txt`, analysis code).
- Backtests used: **29 of 30**, including 1 duplicate (client retry after a 502) and 1 invalid run (see Engine findings).
- Data: B3 plane 2005-01 → 2026-11-15 (`window.end`). The warm-up falls inside the design window for every
  strategy (`warmup_inside_window`), so each design run is judged from its own `invested_from`.
  The IBOV11/SMLL11 fix was already in place (all runs were made on 2026-11-15).

## Method notes
- After-tax "Sharpe" = the engine's excess-over-CDI Sharpe. **t-proxy = Sharpe × √years.** |t| < 2 → statistically weak; that applies to every number below.
- Curve-derived metrics (Sortino, drawdown duration, monthly win rate vs CDI, yearly returns) come from monthly
  `get_backtest_curve` NAVs. They reproduce the engine's after-tax Sharpe exactly on all 24 hypothesis runs.
  The CDI series came from a quarterly 100% CASH_CDI run, geometrically interpolated to months (an approximation).
  BOVA11 curve metrics are quarterly. BOVA11 lists 2008-12-02, so the "BOVA" baseline is CDI before that date.
- MDD is the engine's daily figure; drawdown duration is the curve-derived figure in months.
- Inferred turnover: slippage is charged on buys + sells, so traded value ≈ Δ(pre-tax ann. return) / 10 bps
  between the 10 and 20 bps runs.
- R$100k capital. At this size the R$20k/month ações exemption covers months with light trading, but these
  books trade roughly 40–45% of NAV a month one way (about R$40k+ of sales), so most rebalance months are taxable.
  A smaller account would pay less tax and a larger one more. Parked CDI is booked on the ações line, so it gets the exemption and is taxed only at
  sale. A real CDI fund is taxed at source / via come-cotas, so the engine flatters the parked state of H2.

## Baselines (same windows)
| Window | CDI ann. | B_EW (naive EW liquid) AT Sharpe (t) / ann. | BOVA11 B&H AT Sharpe (t) / ann. |
|---|---|---|---|
| Design 2005-04..2014-12 | 11.48% | −0.07 (−0.22) / 7.42%, MDD −54% | −0.13 (−0.39) / 7.41% (CDI pre-2008-12) |
| Validation 2015..2019 | 9.86% | +0.64 (+1.43) / 21.96% | +0.50 (+1.12) / 18.04% |
| Holdout 2020..2026-11 | ~10.0% | −0.47 (−1.22) / −2.60% | +0.02 (+0.05) / 6.22% |

## H1 — Low volatility (leverage-constrained / lottery-seeking counterparty) — **Reject**
- **Hypothesis.** Retail investors who can't borrow, Ibovespa-benchmarked funds with convex flow incentives and foreign
  beta-seekers overpay for high-volatility names. Levering low-vol is expensive in BRL, so the effect should be less arbitraged.
- **Spec.** Universe ADTV ≥ R$2M, price ≥ 1, `dedup_roots`. Rank by `-at_rebalance(roll_std(pct_change(adj_close), N))`,
  top 20%, equal weight.

| Run | Window | AT ann. | PT ann. | Vol | AT Sharpe (t) | Sortino | MDD | DD dur | Win | Tax paid |
|---|---|---|---|---|---|---|---|---|---|---|
| H1a 126d ME (#5) | design | 9.14% | 10.74% | 16.2% | −0.03 (−0.09) | −0.04 | −47.6% | 37m | 52% | R$23.7k |
| H1b 252d ME (#6) | design | 5.96% | 6.76% | 15.2% | −0.21 (−0.64) | −0.27 | −46.8% | 35m | 45% | R$10.1k |
| H1c 126d QE (#7) | design | 5.04% | 6.06% | 17.5% | −0.23 (−0.68) | −0.28 | −45.8% | 51m | 42% | R$12.0k |

- **Verdict: refuted at design.** Every variant has after-tax Sharpe ≤ 0. Low-vol cut volatility versus B_EW (16% vs 21%),
  but the book still fell ~47% in 2008 and didn't earn back the CDI hurdle.
- Not taken to validation or holdout, per the pre-registered rules. 3 runs.

## H2 — COPOM-cycle gate on the EW book (slow-allocator counterparty) — **Reject**
- **Hypothesis.** COPOM cycles are persistent and public, but allocators move between CDI and equity funds with inertia.
  Equity funds become forced sellers into tightening and late buyers in easing.
- **Spec.** The B_EW book, invested iff `is_easing` (offset 1), else parked in CDI (H2a). H2b used easing OR `bench_above`;
  H2c used easing AND `bench_above`.

| Run | Window | AT ann. | PT ann. | Vol | AT Sharpe (t) | Sortino | MDD | DD dur | Win | Tax paid |
|---|---|---|---|---|---|---|---|---|---|---|
| H2a (#8) | design | 13.37% | 15.54% | 14.6% | **+0.19 (+0.58)** | +0.30 | −24.2% | 25m | 41% | R$40.6k |
| H2b (#9) | design | 10.91% | 12.44% | 17.2% | +0.05 (+0.17) | +0.08 | −30.0% | 25m | 43% | R$29.5k |
| H2c (#10) | design | 9.36% | 10.82% | 12.2% | −0.10 (−0.31) | −0.14 | −24.2% | 25m | 40% | R$22.2k |
| H2a 20 bps (#14) | design | 12.46% | 14.60% | 14.7% | +0.13 (+0.41) | +0.21 | −24.2% | 25m | 41% | R$38.3k |
| H2a gate delayed +1 (#15) | design | 12.27% | 14.24% | 13.4% | +0.12 (+0.39) | +0.18 | −30.6% | 25m | 43% | R$32.8k |
| H2a ADTV ≥ 1M (#19) | design | 14.28% | 16.52% | 14.8% | +0.24 (+0.75) | +0.39 | −24.0% | 25m | 43% | R$44.4k |
| H2a ADTV ≥ 4M (#20) | design | 12.44% | 14.43% | 14.4% | +0.13 (+0.41) | +0.21 | −24.0% | 25m | 38% | R$34.9k |
| **H2a (#24)** | **validation** | 17.99% | 20.45% | 14.2% | **+0.58 (+1.29)** | +1.00 | −16.7% | 14m | 39% | R$15.0k |

- **Inferred turnover:** ≈ 9.4× NAV traded per year (buys + sells). Tax drag ≈ 2.2 pp/yr (design, pre-tax minus after-tax).
- **Robustness:** sign-stable across costs, delay and the liquidity neighbourhood (+0.12 to +0.24).
- **Regime (2005–2019, calendar years vs CDI):** beat CDI in 8/10 up-market years but only 1/5 down-market years.
- **Why Reject: the signal is not what the game says it is.** Engine source (`core/shared_data.py`):
  `is_easing = cdi_monthly.shift(1) < cdi_monthly.shift(4)`, where `cdi_monthly` is the *compounded monthly CDI return*.
  That return moves with the number of business days in the month (20–23 weekdays, a 15% relative swing), which swamps
  a 25–50 bp COPOM move.
  - With a **constant** rate, the flag would fire in 34% of months from weekday counts alone (holidays ignored, so that understates it).
  - Inferred from the curves, H2a was **invested during known COPOM hiking periods** (public record, not tool data): Apr–Jul 2008 and Feb–May 2015.
  - The design edge comes mainly from sitting out Aug–Dec 2008 (2008: +0.3% vs B_EW −48.8%). That is about one event, not
    evidence about the COPOM counterparty.
- In validation H2a (+0.58) did **not** beat the naive EW book (+0.64). It never reached the holdout (H3c had the higher
  validation Sharpe, per the pre-registered rule). 9 runs.

## H3 — Intermediate momentum (disposition-effect counterparty, R$20k-exemption gain harvesting) — **Reject**
- **Hypothesis.** Retail investors sell winners and hold losers, and the monthly R$20k exemption adds an incentive to
  harvest gains. Both slow price adjustment, so winners should keep winning over 6–12 months.
- **Spec (finalist H3c).** Same universe, signal `roll_sum(shift(log_ret, 1), 6)` (6-1 month), top 20%, equal weight, ME.
- H3b's spec was corrected before it ran: on the QE grid `log_ret` is quarterly, so I used daily-price 12-1 instead.

| Run | Window | AT ann. | PT ann. | Vol | AT Sharpe (t) | Sortino | MDD | DD dur | Win | Tax paid |
|---|---|---|---|---|---|---|---|---|---|---|
| H3a 12-1 ME (#11) | design | 5.96% | 6.97% | 20.8% | −0.10 (−0.30) | −0.12 | −62.4% | 43m | 57% | R$12.1k |
| H3b 12-1 QE (#12) | design | 6.41% | 7.72% | 23.1% | −0.05 (−0.14) | −0.06 | −63.9% | 51m | 56% | R$15.0k |
| **H3c 6-1 ME (#13)** | design | 11.03% | 13.21% | 22.3% | **+0.11 (+0.34)** | +0.15 | −60.5% | 35m | 57% | R$34.9k |
| H3c 20 bps (#16) | design | 9.97% | 12.13% | 22.3% | +0.07 (+0.21) | +0.09 | −60.5% | 51m | 56% | R$33.5k |
| H3c delay +1 (#18) | design | 9.21% | 11.12% | 22.1% | +0.04 (+0.13) | +0.05 | −63.5% | 62m | 58% | R$29.8k |
| H3c 4-1 (#21) | design | 12.74% | 15.51% | 22.4% | +0.17 (+0.54) | +0.24 | −59.5% | 35m | 58% | R$49.7k |
| H3c 8-1 (#22) | design | 8.77% | 10.59% | 22.4% | +0.03 (+0.08) | +0.03 | −64.8% | 52m | 55% | R$27.4k |
| H3c top 15% (#23) | design | 8.34% | 10.00% | 23.0% | +0.01 (+0.03) | +0.01 | −63.7% | 51m | 53% | R$24.0k |
| **H3c (#25)** | **validation** | 25.93% | 29.91% | 20.3% | **+0.77 (+1.72)** | +1.44 | −20.3% | 11m | 53% | R$28.4k |
| H3c 20 bps (#26) | validation | 24.88% | 28.83% | 20.3% | +0.73 (+1.63) | +1.35 | −20.3% | 12m | 53% | R$27.7k |
| H3c delay +1 (#27) | validation | 25.93% | 29.88% | 19.4% | +0.80 (+1.78) | +1.47 | −21.3% | 11m | 54% | R$28.8k |
| H3c top 15% (#28) | validation | 29.77% | 34.38% | 21.7% | +0.88 (+1.94) | +1.71 | −23.8% | 12m | 53% | R$34.3k |
| **H3c (#29)** | **holdout 2020..2026-11** | **2.33%** | 2.94% | 25.9% | **−0.14 (−0.38)** | −0.18 | −53.3% | 52m | 50% | R$5.3k |

- **Inferred turnover:** ≈ 10.8× NAV traded per year (design and validation). Tax drag ≈ 2.2 pp/yr (design).
- **Design / validation / holdout:** +0.11 / +0.77 / **−0.14**. The holdout loses to CDI and BOVA11 (+0.02) and beats only
  the naive EW book (−0.47).
- **Sensitivity (red flag):** in design, a 25% cut in `top_pct` takes Sharpe from +0.11 to +0.01; 8-1 gives +0.03, and one extra
  month of delay gives +0.04.
- **Regime (calendar years, market = B_EW):** beat CDI in 10/12 up-market years and **0/10 down-market years**
  (average excess +21.7 pp vs −17.8 pp/yr). This is levered market beta, not a disposition-effect premium.
- **Outlier check:** largest month is −37.6% (March 2020). It is consistent with B_EW −30.7% that month and BOVA11 −37.7%
  over Q1 2020, so it's the COVID crash, not a data error. The only other months beyond ±20% are +23.0% (2006-01),
  −20.0% (2008-09), −20.4% (2008-10) and +21.6% (2020-04). H2a never had a month beyond ±20%.
- 14 runs on H3 (6 parameter combinations plus cost/delay reruns, one of them invalid).

## Engine findings worth fixing / knowing
1. **`signal.shift` is ignored for `expr` signals.** Run #17 (`shift: 1`, `warmup_pad: 2`) was identical to #13 down to
   tax paid and `invested_from`, although `compiled_spec` showed `shift: 1`. The workaround is to put the delay in the
   expression (`shift(log_ret, 2)`).
2. **`is_easing` measures the business-day calendar more than COPOM.** It compares monthly compounded CDI *returns*.
   Comparing daily-rate averages (e.g. the mean daily CDI factor per month) would remove the calendar effect.
3. `log_ret` / `ret` are per-rebalance-period, so a `roll_sum(..., 12)` on QE is 36 months. Use daily `adj_close` for
   grid-independent windows.
4. A 502 on a long `wait_seconds` still runs the backtest. Clients that retry on 502 create duplicate runs.

## What I would test next (new pre-registration, new data)
- Re-run H2 with a corrected easing flag (daily-rate based). The game is plausible, but it was never actually tested here.
  After that, design → validation → holdout is no longer clean, since every window has been seen, so it would need fresh
  out-of-sample time or a different market.
- H3's evidence says "beta"; a beta-hedged or market-relative version can't be built in a long-only engine.
- Any further work on these windows is contaminated: the holdout has now been seen once.

## Artifacts kept / deleted
- Kept: the baselines `GTC B_EW liquid equal-weight`, `GTC B_BOVA BOVA11 buy-hold` and `GTC B_CDI cash`.
- Deleted: all 14 rejected hypothesis and robustness strategies.
