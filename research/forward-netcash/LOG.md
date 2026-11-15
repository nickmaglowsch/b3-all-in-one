# Forward-test log — "FWD Net-Cash Profitable"

Frozen 2026-11-15 (commit `14c1a98`); forward window starts 2026-11-16. Rules: `PREREGISTRATION.md`.
Each monthly row is written by `checkin.py` from runs made that day. Returns are cumulative since 2026-11-16, on R$100k.

## Entry 0 — freeze (2026-11-15)
- Spec saved on Quantax; canonical fingerprint `c8bd7c0b…e9c2` matches `frozen_spec.json`.
- Context run (#52, mechanics only, does not change the spec): 2010-01-01..2026-11-15, invested from 2011-06-30, 62 quarters.
  - **After tax: 5.47%/yr, Sharpe −0.05 (t −0.21), MDD −53.6%.** CDI was 9.80%/yr over the same quarters.
  - Same quarters: BOVA11 6.77%/yr (Sharpe −0.00); DIVO11 8.63%/yr (+0.06).
  - **In the 2021–22 hiking cycle, the regime the thesis targets, it did worse than BOVA11:** 2021 −29.3% vs −12.1%;
    2022 −15.2% vs +5.1%.
  - Conclusion: the historical prior is against the thesis. The forward test proceeds as pre-registered.

## Monthly check-ins
| Check date | Data end | FWD after-tax cum. | FWD Sharpe | FWD MDD | BOVA11 cum. | DIVO11 cum. | CDI cum. | CDI net of IR | Spec | Flags |
|---|---|---|---|---|---|---|---|---|---|---|
| 2026-11-15 (dry run) | - | error | error | error | error | error | error | - | OK | expected: no data after freeze yet (runs #53–56) |
