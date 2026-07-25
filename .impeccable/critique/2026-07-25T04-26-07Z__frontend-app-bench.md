---
target: frontend/app/bench
total_score: 24
max_score: 40
na_heuristics: 
p0_count: 0
p1_count: 4
timestamp: 2026-07-25T04-26-07Z
slug: frontend-app-bench
---
Method: dual-agent (A: a68ee003a2455be89 · B: a8128a6b77be22df1)

Surface mode: **Operate**. Target: `frontend/app/bench` (10 files) plus the shared `ui/` and `charts/` primitives it renders. Source-grounded only — no browser automation is exposed in this session and the Next dev server is not running (port 3000 is held by an unrelated service returning 302/401), so no rendered-page verification, no computed contrast, and no user-visible overlay.

## Design Health Score

| # | Heuristic | Score | Key Issue |
|---|-----------|-------|-----------|
| 1 | Visibility of System Status | 2 | Queueing a run confirms nothing (`onRan` is a no-op, `page.tsx:72`); completion is never announced; a failed `/backtests` fetch renders as "you have no backtests". |
| 2 | Match System / Real World | 3 | Strong pt-BR quant vocabulary, but engine-speak leaks: `top_pct` in a user-facing error (`StrategyForm.tsx:138`), pandas codes `ME/QE/W-FRI`, "Parkear". |
| 3 | User Control and Freedom | 2 | No cancel for a queued or running backtest anywhere; "Cancelar" discards a filled form with no dirty-check (`StrategyForm.tsx:562`). |
| 4 | Consistency and Standards | 2 | Tear-sheet Sharpe/MDD/Calmar are pre-tax while ComparePanel's identically-labelled rows are after-tax; `aria-pressed` present on one toggle and absent on its twin; Enter never submits (no `<form>` in the surface). |
| 5 | Error Prevention | 3 | Real client validation, min/max constraints, unfinished runs blocked from compare, inert-knob warning — but no start<end check and no duplicate-run guard. |
| 6 | Recognition Rather Than Recall | 2 | A run's params (period, freq, aporte) appear nowhere in the runs list or compare headers; two runs of one strategy differ only by a 6-char hex id (`lib.ts:145`). |
| 7 | Flexibility and Efficiency | 2 | Declared `StrategyParam[]` are dead — `RunPanel.tsx:51` only ever sends `contribution`; no re-run, no keyboard shortcuts, no cmd-click on rows. |
| 8 | Aesthetic and Minimalist Design | 3 | Quiet, dense, on-system; five stacked zones disclose progressively. RegimeEditor cramps to 9-10px labels. |
| 9 | Error Recovery | 2 | Two silent-failure paths: a failed curves fetch renders an indefinite skeleton (`run/page.tsx:168`), a failed backtests fetch renders the empty state. No "tentar novamente" from a failed run. |
| 10 | Help and Documentation | 3 | ExprCheatSheet, field hints, regime prose and clone-scope notes are genuinely good; no metric definitions. |
| **Total** | | **24/40** | **Acceptable — significant improvements needed** |

All ten heuristics applied; none scored `n/a`. Assessment A independently scored 25/40; I lowered **Error Recovery 3 → 2** because A reviewed it without the two silent-failure paths B found mechanically. That single adjustment is the clearest illustration of why both assessments are required.

## Design Specificity Verdict

**LLM assessment: substantially authored at the conceptual layer, generic at the furniture layer.** An unrelated SaaS could not lift this surface unchanged, but it could lift most of its components.

What is genuinely authored — and could only be this product: the pre/pós-imposto framing is structural rather than decorative. It runs from the page eyebrow (`page.tsx:47`) through the equity chart's caption "o diferencial: quanto o imposto corrói o retorno" (`run/page.tsx:161-166`) to ComparePanel deliberately refusing to crown a winner on "Imposto pago", with the domain reasoning written into the code (`ComparePanel.tsx:29-31`). Brand carried in structure — which tile is highlighted, which row gets no crown — is exactly how an Instrument Panel should work. `ExprCheatSheet` (`StrategyForm.tsx:579-601`) and the `topPctInert` dead-knob warning (`RegimeEditor.tsx:446-452`) are the same instinct at component level.

What is generic: the gallery-card-with-search, the master-detail run panel, the KPI-tile grid, the runs table. Every zone is the same `Card` with a 14px semibold header; strip the copy from the tear sheet and it is a standard dashboard.

Three missed opportunities for product character, in order of size:
1. **The spec is invisible.** PRODUCT.md's design center is someone who "authors strategies as YAML/DSL specs" — yet `StrategyForm` compiles to a spec body the user never sees. No raw pane, no export, no diff between a clone and its source. The product's core artifact has no representation on its core surface.
2. **The moat is one tile.** The shared tax engine is the thing no competitor can copy, and the tear sheet spends a single "Imposto pago" tile on it (`run/page.tsx:205-208`). No isenção usage, no loss-carry consumed, no DARF footprint.
3. **No CDI on the backtest curve.** The platform owns a live BCB CDI series and already draws exactly this overlay on the portfolio index. A Brazilian equity curve without the risk-free line cannot answer "melhor que a renda fixa?"

**Deterministic scan.** The scan as normally invoked returns a false clean. `detect.mjs` resolves the design system by walking up to the first project-root marker (`.git`, `package.json`, `.impeccable`); `frontend/package.json` is a marker, so the walk halts at `frontend/` and never reaches the repo-root `DESIGN.md`. Every design-system rule was silently disabled for the whole `frontend/` tree. Verified by planting a deliberately flagrant probe file (`#ff0000`, `text-blue-500`, Inter, blue glow, `transition-all duration-1000`) inside `frontend/app/bench/` and scoring zero on it.

Corrected scan (DESIGN.md temporarily placed in `frontend/`, then removed; tree verified clean):
- `frontend/app/bench` → **11 findings**, exit 2 — all `design-system-font-size`, all sub-11px type off the documented ramp.
- `frontend/components/ui` + `charts` → **0 findings**, exit 0. Nothing propagates into bench from the shared layer.

All 11 triaged as TRUE POSITIVE; zero false positives, zero arguable. DESIGN.md's smallest sanctioned step is `label` at 11px; ten of the eleven land on exactly 10px and one on 9px (`RegimeEditor.tsx:470`). That consistency reads as an undocumented de-facto step rather than random drift — code and design system disagree about whether a step below `label` exists, and one of them should move. One finding is a named-rule violation rather than mere drift: `run/page.tsx:251`, the `MetricTile` eyebrow, breaks the **Mono Eyebrow Rule** on both size (10px, not 11px) and tracking (0.12em, which DESIGN.md sanctions for marketing labels only).

**Visual overlays: none.** No browser automation tool is exposed and no dev server is running, so injection was never attempted and no user-visible overlay exists. Contrast ratios and rendered focus affordances below are source-reasoned and **unverified**.

## Overall Impression

The bench understands what it is for. The tax framing is load-bearing, the engine's honesty is surfaced rather than hidden, and the DSL is made approachable without being dumbed down. Someone who knows this domain built this.

What undermines it is a pattern rather than a list: **the surface is careless at exactly the moments where its own product principles are most at stake.** PRODUCT.md commits to "never invent a number" and "report the gaps as loudly as the results." The bench fabricates zeros from missing metrics, reports a fetch failure as an empty portfolio, and spins an indefinite skeleton when curves fail to load. Every one of those has a correct implementation already in the codebase — `ComparePanel` does all three properly, one file over. This is not a knowledge gap; it is inconsistent application of a standard the team already holds.

The single biggest opportunity: make the tear sheet trustworthy. It is where a paying quant decides whether to believe the product, and it is currently the weakest file in the surface.

## What's Working

1. **The tax framing is structural, not cosmetic.** It appears as hierarchy decisions (the pós-imposto tile is the highlighted one), as a chart whose caption states the product thesis in one line, and as a comparison table that refuses to crown a winner on tax paid because the domain says that would be meaningless. Brand living in precise details is exactly what an Operate surface should do — and it means the page cannot be mistaken for a generic backtester.

2. **Engine honesty surfaced as UI.** `topPctInert` tells the user a knob has gone dead (`RegimeEditor.tsx:446-452`). Clone notes distinguish full-spec from params-only copies (`StrategyForm.tsx:300-312`). ComparePanel names missing curves instead of hiding them ("Sem curva para X — as métricas seguem na tabela") and states why the overflow was capped. This is the product's honest-gaps principle operating at component level, and it is rare.

3. **Colour discipline is the surface's strongest mechanical result.** Zero hex literals, zero `rgb()`, zero Tailwind palette classes across every file — colour is entirely semantic tokens. The one raw-colour passthrough (`ComparePanel.tsx:264`) is token-sourced. Most codebases cannot claim this.

## Priority Issues

### [P1] Fetch failures are reported to the user as "you have no data"

**What.** Two paths, same defect. `page.tsx:51-57` handles `strategies.error` but never `backtests.error`; `backtests` is consumed only as `.data ?? []` and `.isLoading`, so a failed fetch reaches `RunsList` as `runs=[]`, `loading=false` and renders the empty state — *"Nenhum backtest ainda — rode uma estratégia acima."* Separately, `run/page.tsx:168` and `:188-190` guard on `curvesLoading || !curves`; when `useCurves` errors, loading goes false and curves stays null, so the skeleton **never resolves**. `curves.error` is never read.

**Why it matters.** A user whose network blipped is told their work does not exist. Someone who just queued a run and lost connectivity sees "you have no backtests" and reasonably concludes the run failed. This is the direct inverse of PRODUCT.md principle 2 — "report the gaps as loudly as the results" — on the surface that is supposed to demonstrate it. It is also the failure mode that erodes trust fastest, because the interface is confidently wrong rather than visibly broken.

**Fix.** Read `backtests.error` in `page.tsx` and pass an error branch into `RunsList` (which currently has no error state at all). In `run/page.tsx`, split the guard: `curvesLoading` → skeleton, `curves.error` → the failure message. Copy `ComparePanel.tsx:216-220` verbatim; it already solves this exact case correctly.

**Suggested command:** `/impeccable harden`

### [P1] Missing metrics are fabricated as zeros

**What.** `run/page.tsx` coerces absent metrics with `?? 0` throughout: `:108-109` (annual return, pre and post), `:146` (Sharpe), `:151` (max drawdown), `:154` (Calmar), `:207` (`total_tax_paid`), `:215`, `:218` (final NAVs). A run with a partial metrics block renders "+0,0%" and "R$ 0,00" — visually identical to a genuine zero. `taxDrag` at `:110` compounds it, reporting a 0% tax drag computed from data that is not there.

**Why it matters.** This breaks PRODUCT.md principle 1 — "never invent a number" — and DESIGN.md's explicit instruction that a missing value renders as an em dash, never a stand-in. For the quant/prosumer design center, a fabricated "Imposto pago: R$ 0,00" on the product's flagship claim is disqualifying: it is wrong in the direction that flatters the product.

**Fix.** Guard every tile with `Number.isFinite`; render `—` and drop the gain/loss tone when the value is absent. `ComparePanel.tsx:172` already does exactly this.

**Suggested command:** `/impeccable harden`

### [P1] The same metric shows two different numbers on two screens

**What.** Tear-sheet tiles labelled "Sharpe" (`run/page.tsx:146`), "Max drawdown" (`:151`) and "Calmar" (`:154`) render **pre-tax** values with no basis in the label. ComparePanel's identically-named rows (`ComparePanel.tsx:49-69`) render **after-tax**. The same run reports Sharpe 1,42 on one screen and 1,17 on the other, with nothing on either screen explaining why.

**Why it matters.** For a product whose entire thesis is that taxes change the answer, an unlabelled metric basis is the worst available ambiguity. A quant who screenshots the tear sheet into his research notes records a number he cannot later reconcile with the comparison view. The tear sheet has already half-solved this — the annual-return tiles *are* labelled pré and pós — which makes the inconsistency look like an oversight rather than a decision.

**Fix.** Either label the basis on every tile, or give the tear sheet the same Pós/Pré toggle ComparePanel has, defaulting to pós — the product's own thesis.

**Suggested command:** `/impeccable clarify`

### [P1] Runs list rows are unreachable by keyboard and cannot be linked

**What.** `RunsList.tsx:71-77` renders each row as `<tr onClick={router.push}>`. The only focusable element per row is the compare checkbox (`:79`, `h-4 w-4`, `aria-label` only, with `onClick` stopping propagation). There is no anchor anywhere in the row.

**Why it matters.** A keyboard or screen-reader user cannot open any run's results from the list — they are blocked from the surface's entire payoff, not merely inconvenienced. A power user cannot cmd-click to open three runs in tabs, which is the natural way to compare tear sheets. The in-file comment at `:11-13` explains why `DataTable` was not reused, but does not address the click-only row.

Worth stating plainly: PRODUCT.md records that no accessibility standard has been established for this product, so how much weight this carries is genuinely the team's call. The defect is real regardless, and the fix is one line.

**Fix.** Make the strategy-name cell a `<Link href={...}>` and keep the row `onClick` as a pointer enhancement. Restores keyboard access, screen-reader semantics and new-tab behaviour in a single change.

**Suggested command:** `/impeccable audit`

### [P2] The surface drifts from three of its own Named Rules

**What.** Three distinct violations of DESIGN.md, all on the tear sheet and gallery:

- **Two-Palette Rule** ("interface colour and data colour are separate systems"). `KindBadge` uses `series-3`/`series-7` (`StrategyGallery.tsx:14-18`), `FundamentalsBadge` uses `series-4` (`:38`), warning boxes use `series-4` text on `series-4/10` (`StrategyForm.tsx:382`, `UserStrategies.tsx:95`), the "regra" badge repeats series-3 (`UserStrategies.tsx:129`). Chart series colours are doing interface work. The warning case is the sharp one: DESIGN.md darkened status amber to `#b5760a` *specifically* to clear 3:1 on white, while `series-4` is `#eda100` — as 10-12px warning text on near-white that is roughly 1.9:1, a hard WCAG failure on text the user is being asked to read. **Unverified without a browser; source-reasoned only.**
- **Tabular Column Rule** ("every number that will ever sit above or below another gets Plex Mono and `tnum`"). `MetricTile` (`run/page.tsx:253`) renders its value in `font-display` Archivo with no `tnum`. Six tiles pass plain strings through it (`:132, :137, :146, :151, :154, :211`), plus three sub-values (`:147, :155, :218`). These are the largest, most-read numbers on the page. DESIGN.md also documents the Archivo Headline step as "Marketing surfaces only" — the tear sheet is an app surface.
- **Sign-Carries-Meaning Rule** (gain/loss colour only where the sign is the information). ComparePanel's "best" cell uses `bg-gain/10` and a `text-gain` dot (`:179-186`) to mark a winner, which is not a signed value.

**Why it matters.** These are the rules the project wrote down for itself last, and they are the ones that keep an instrument reading as an instrument. The contrast issue is a genuine accessibility defect, not a stylistic quibble. Individually each is small; together they are how a documented system quietly stops being the system.

**Fix.** Warnings take `status-processing` amber. Kind badges take ink/muted on paper — they already carry a text label, so colour is doing nothing legitimate. Add `tnum` to `MetricTile` at minimum; better, move its value to the Numeric role. Mark the compare winner with weight or rule-strength rather than gain green.

**Suggested command:** `/impeccable polish`

## Persona Red Flags

**Alex (impatient power user).** Cannot vary a built-in strategy's declared params at run time — `Strategy.params` carries label/min/max/step (`types.ts:167-176`) but `RunPanel.tsx:51` sends only `contribution`, so testing lookback 6 vs 12 means cloning into a user strategy, naming it, saving it and reselecting it: a five-step detour for a one-knob sweep. Enter submits nothing anywhere in the surface (`RunPanel.tsx:129`, `StrategyForm.tsx:550-561` are `type="button"` with no `<form>`). Peeking at one tear sheet destroys his compare selection (`page.tsx:19`). No cmd-click on run rows, no "rodar de novo" from a tear sheet or a failed run, no cancel on a run he immediately regrets. At 80 runs the flat unfiltered Rodadas list means scanning 8-char hex ids.

**Sam (screen reader + keyboard-only).** Blocked outright from opening any run's results (`RunsList.tsx:71-77`). The compare winner is imperceptible — marked by `title` plus an `aria-hidden` dot plus a background tint (`ComparePanel.tsx:176-189`), so the table reads as undifferentiated numbers. Run completion is silent; there is no `aria-live` region anywhere on the bench. Both recharts charts are SVG with no text alternative. Four form controls have no accessible name at all: the gallery search input (`StrategyGallery.tsx:70`, placeholder only), both RegimeEditor rule selects (`:328`, `:339`, bare), and the factor textarea (`StrategyForm.tsx:329`, placeholder only inside a fieldset — a legend names the group, not the control). The Top %/Top N toggle lacks `aria-pressed` (`StrategyForm.tsx:424-435`) while its identical twin in ComparePanel (`:297-304`) has it, so the surface contradicts itself. Mechanically confirmed clean: all 19 buttons have accessible names, and all 5 `focus:outline-none` are paired with a visible `focus:border-navy` replacement — though `focus-visible` appears zero times and the replacement is a 1px border-colour swap whose rendered visibility could not be verified.

**Rafael (project-specific — the tax-aware quant from PRODUCT.md's design center; authors specs, screenshots results into research notes, decides real allocations).** He will quote the wrong Sharpe, because the tear sheet and the compare table disagree and neither says which basis it used. A partially-failed metrics payload hands him "+0,0%" and "Imposto pago R$ 0,00" as though real — for the persona whose trust *is* the product, fabricated zeros are disqualifying. His comparison of "momentum · a1b2c3" against "momentum · 9f3e21" documents nothing: no params, period or aporte appear in the runs list or compare headers, so the screenshot he files is unreadable in a month. He authors YAML/DSL specs by identity, yet the bench never shows him a spec. And the tear sheet reports no backtest gaps — no universe coverage, no excluded or unpriced tickers, no warmup clamp — while the portfolio index has an entire `excluded` block for exactly this. (Possibly an API gap; the layout reserves no room for it either.)

## Minor Observations

- **Touch targets:** exactly one interactive element in ~40 clears 44×44px (the strategy card, `StrategyGallery.tsx:103`). The `×` remove button (`ComparePanel.tsx:274`) has no padding at all and computes to roughly 11×8px. Low priority for a desktop analytics surface, but the `×` is small enough to be a nuisance with a mouse.
- **`formatDateTime` slices the raw ISO string** (`RunsList.tsx:118`) — if `created_at` is UTC, a 22h BRT run displays as tomorrow 01h.
- **Frequency options are ordered Mensal, Trimestral, Semanal** (`RunPanel.tsx:11-15`) — non-monotonic.
- **RegimeEditor lists are keyed by array index** (`:240`, `:322`), so removing a row can misbind focus and state.
- **`PendingCard` does not distinguish queued from running** (`run/page.tsx:273`) and shows no elapsed time — for a multi-minute job this trains the user to babysit the page.
- **`onRan` is vestigial** (`RunPanel.tsx:19`, no-op at `page.tsx:72`).
- **Bench-local formatters** (`lib.ts:88-101`) sidestep the "route all formatting through `lib/format.ts`" Do. The reasoning comment is legitimate — metrics arrive as percentage-numbers, not fractions — but `formatMetricPct` belongs in `format.ts` beside `formatPercent` to stop the two drifting. `formatRatio` and `compactBRL` have no equivalent there at all. Note also that `format.ts` hoists its `Intl.NumberFormat` instances while `bench/lib.ts` constructs one per call.
- **Card-Stack Rule tension:** gallery and run panel sit side by side (`page.tsx:59`) as master-detail, not "genuinely a comparison" as DESIGN.md's rule requires. The layout is right; the rule as written is too narrow. Amend the rule rather than the layout.
- **The 10px de-facto type step.** Ten of the eleven detector findings land on exactly 10px. Either DESIGN.md gains a documented step below `label`, or these move to 11px. Leaving them undocumented is the only wrong answer.

## Questions to Consider

1. The product's identity is "strategies authored as YAML/DSL specs" — what if the form and a raw spec pane were two synchronized views of the same document, so the quant can read, paste and diff the artifact the engine actually runs?
2. The shared tax engine is the moat and the tear sheet spends one tile on it. What would a tax panel no competitor could draw look like — isenção months consumed, loss carry-forward burned, the DARF calendar the strategy would have generated?
3. Where is CDI on the backtest chart? A Brazilian equity curve without the risk-free line cannot answer the only question that matters: better than doing nothing?
4. What if a run were a reproducible object rather than a row — params always visible, "rodar de novo com ajustes" one click from anywhere, runs of one strategy grouped as an experiment family?
5. Compare state is ephemeral component memory. Quants share screenshots and links — why isn't a comparison a URL?
6. The Rodadas table becomes the bench's home screen at 100 runs. Is a flat, unfiltered, id-labelled list the archive a paying quant deserves, or should the primary unit be the strategy with runs nested beneath it?
