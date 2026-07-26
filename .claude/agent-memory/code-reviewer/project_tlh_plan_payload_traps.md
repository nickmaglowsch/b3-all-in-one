---
name: tlh-plan-payload-traps
description: taxcore tlh_plan --json has two payload traps any consuming surface hits — buckets[cls] is absent (not zero) and buckets[cls].ir is post-harvest, not owed-today
metadata:
  type: project
---

`taxcore/scripts/tlh_plan.py --json` (`build_result`) is consumed by
`POST /tlh/plans`, the rebalance tax overlay, and the Portfólio situação-fiscal
panel. Two traps bite every new consumer:

1. **`buckets[cls]` is ABSENT, not zeroed**, when `not bucket_sells and
   mtd[cls]["gross"] == 0` (`tlh_plan.py` ~line 521 `continue`). `situation[cls]`
   and `rates[cls]` are always present for both AÇÃO and FII; `buckets` is not.
   `?? 0` is numerically safe there only because gross 0 implies pnl 0.

2. **`buckets[cls].ir` means different things per branch.**
   - isenção / `pnl_after <= 0` / `carry >= pnl_after` → `0.0` (really owed: 0)
   - "TLH desejável mas sem candidatos" → `residual * rate` (really owed)
   - **"TLH recomendado" → `final_taxable * rate`, i.e. IR AFTER executing the
     recommended harvest the user has not executed.** Typically ~0 while the
     real DARF is `(pnl_after - carry) * rate`.
   `buckets[cls].status` is the only field that distinguishes them.
   `buckets[cls].harvest` non-empty ⇔ you are in the post-harvest branch.

**Why:** the engine was written for an interactive planner where the report text
carries the conditionality. A UI that prints `ir` as "IR devido" understates a
real tax liability — a "Never invent a number" violation with money attached.

**How to apply:** on any PR that reads a tlh_plan payload, check whether `ir` is
labelled as owed-today. If `harvest` is rendered next to it, the label is wrong
unless it says the figure assumes the harvest. Also check the isenção boundary:
the engine is `gross <= 20_000` → isento, so a UI `>= limit` test is off by one
at exactly R$20.000,00.

See also [[platform-tenancy-review-checklist]].
