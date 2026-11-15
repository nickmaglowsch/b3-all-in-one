# Forward test — "FWD Net-Cash Profitable" (frozen 2026-11-15)

The spec in `frozen_spec.json` (SHA-256 `697cc91abdde7427ddc3ef4f2ff0db6443584d14064f108ce850c9f79553fa1c`) is frozen as of
this commit. The plane's data ends 2026-11-15, so **every return from 2026-11-16 on is unseen** at freeze time.
The spec must not change. Any change starts a new forward test with a new freeze date.

This is research, not investment advice.

## Why this idea (the game)
- **Players.** COPOM sets the CDI rate. Most Brazilian corporate debt (debentures, bank loans) is floating and CDI-linked.
  Equity investors, including retail, foreign money and P/E-anchored funds, value companies on earnings multiples.
- **Mechanism.** A rate hike raises an indebted company's interest bill within months and moves value from shareholders to
  creditors. A net-cash company earns CDI on its cash pile, so it is partly long CDI itself, the same hurdle this research
  is trying to beat.
- **Counterparty.** Shareholders of levered companies, who price on multiples and underreact to how fast CDI-linked debt
  reprices. They persist because floating-rate debt is structural in Brazil and multiples-based valuation is the market norm.
- **Honest prior.** Leverage and quality screens are well known, so any edge may already be priced. This test is meant to find
  that out on data nobody has seen.

## Rules (frozen)
- **Universe.** Non-financial B3 stocks (CVM sector excludes `banc`, `intermedia`, `segur`, `financ`), ADTV ≥ R$2M,
  price ≥ R$1, share classes de-duplicated.
- **Filter.** Positive trailing-12-month net income; EBITDA TTM > 0.
- **Rank.** By `-(net debt / EBITDA TTM)`, so net-cash companies come first. Point-in-time CVM filings, available 2010+.
- **Hold.** Top 20%, equal weight, rebalanced quarterly. R$100k start, no contributions, 10 bps slippage, the engine's tax model.

## What is measured, and when
- **Forward window.** Starts 2026-11-16. The engine's first rebalance on or after that date opens the book.
- **Monthly check-in**, around the 5th of each month. Run the frozen strategy and the benchmarks from 2026-11-16 to the latest
  data, and append to `LOG.md`:
  - `window.end`, after-tax and pre-tax cumulative return, after-tax Sharpe vs CDI, max drawdown, tax paid.
  - The same for BOVA11 buy-and-hold, DIVO11 buy-and-hold, CDI (gross), and CDI net of 15% IR (computed as 0.85 × the CDI return).
- Check the spec on Quantax still hashes to the value above (via `get_strategy_spec`). If it differs, the test is void.
- If the plane's `window.end` stops advancing for more than 10 days, log it as a data problem, not performance.

## Decision rules (fixed now; the test has low power)
- **Kill early (stop and report)** if, at any check-in, the after-tax drawdown from peak exceeds 35%, OR cumulative after-tax
  return trails gross CDI by more than 15 percentage points.
- **12-month review (≈ 2027-11).** Report only. No decision unless a kill rule fired.
- **24-month decision (≈ 2028-11).** "Keep tracking / consider ready" only if ALL hold:
  1. Cumulative after-tax return > gross CDI over the window.
  2. Cumulative after-tax return ≥ max(BOVA11, DIVO11) after-tax over the window.
  3. Max after-tax drawdown ≤ 30%.

  Otherwise: Reject.
- **Even a pass is not proof.** At a plausible Sharpe of ~0.4, a t-statistic of 2 needs ~25 years of data. Two years can
  only screen out failures and confirm the spec runs as intended.

## Context run (does not change the spec)
After this commit, one historical backtest (2010-01-01..2026-11-15) is run for mechanics only: book not empty, names
selected, no glitches. Its performance is reported as context. **Whatever it shows, the spec stays frozen.** Only a
mechanical failure (empty book, error) would void this pre-registration.
