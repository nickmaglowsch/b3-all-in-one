---
target: frontend/app/portfolio/page.tsx
total_score: 27
max_score: 40
na_heuristics: 
p0_count: 0
p1_count: 3
timestamp: 2026-07-26T21-12-06Z
slug: frontend-app-portfolio-page-tsx
---
Method: dual-agent (A: design review, Opus · B: detector + build evidence, Sonnet)

Surface mode: **Operate**. Target: `frontend/app/portfolio/page.tsx` plus its composed components. Third run.

**Render status: not rendered.** No dev server; route needs auth + backend + Postgres. Source-grounded; render-dependent severity flagged.

## Design Health Score

| # | Heuristic | Score | Key Issue |
|---|-----------|-------|-----------|
| 1 | Visibility of System Status | 3 | The trades caption updates from local state while `keepPreviousData` still shows the previous page's rows — "51–100" over rows 1–50, with no `aria-busy` or dimming. |
| 2 | Match System / Real World | 4 | Fluent Brazilian tax/market vocabulary throughout. Sole blemish: "Custo (precificado)" is an internal coinage whose meaning only arrives in the caption below it. |
| 3 | User Control and Freedom | 2 | No filter or search on either table; no sort reset; Negociações sort/page absent from the URL while Posições sort is there; book switch hard-reloads and discards scroll. |
| 4 | Consistency and Standards | 2 | Retry is a navy primary in one place and a bordered secondary in three others. `SortHeader` is duplicated verbatim across two files with divergent default-direction logic. |
| 5 | Error Prevention | 2 | The one hazard on a read-only surface is acting on the wrong client's book, and book identity survives only ~200px of scroll. A lapsed enterprise plan silently swaps the active book and reloads with no message. |
| 6 | Recognition Rather Than Recall | 3 | No sticky table header: after 200 rows the column meanings and active sort glyph are off-screen. Reconciling three differently-based totals across three cards is pure recall. |
| 7 | Flexibility and Efficiency | 1 | The floor for the declared primary persona: no search, filters, saved views, export, print, column chooser, or go-to-page. Page size caps at 250 while the API allows 500. |
| 8 | Aesthetic and Minimalist Design | 3 | Genuinely restrained and on-system. Held off 4 by the Alocação donut, which for a typical Brazilian book (ações + FIIs + ETFs all under "Mercado à Vista") may render as one ~100% slice. Needs a real render. |
| 9 | Error Recovery | 3 | Copy is excellent, but every error wears the dashed empty-state box, which in this system means "nothing here". The visual contradicts the copy. |
| 10 | Help and Documentation | 4 | Best-in-class inline explanation: the TWR note, the cross-card reconciliation, the caveat box. Gap: the sparkline's basis is documented nowhere on screen. |
| **Total** | | **27/40** | **Acceptable, upper half** (20 → 25 → 27) |

No heuristic scored `n/a`.

## Design Specificity Verdict

**Authored for this product, decisively — with one interchangeable pocket.**

Could not be lifted from another product: the mixed-basis reconciliation footnote; the TWR-vs-simple-return disclaimer ending "as duas não batem, e não deveriam"; `Unknown()` existing solely to stop a zero being invented; `IncomeSection` inverting the same rule so a genuine zero is not an em dash; the 402/422/other three-way failure split; the "O que ficou de fora" box at body weight rather than fine print; allocation split by the `market` field the API returns rather than guessed asset class, with IVVB11 named as the reason. This is a surface that argues with itself in public about provenance.

The interchangeable pocket: the 4-tile KPI row + donut + legend is the stock fintech opening, and the donut is the weakest object on the page.

**Deterministic scan: zero findings** in scope. Detector exit 0 with `[]`; a control run over the wider tree returned exit 2 with findings elsewhere, confirming the tool is live. `tsc` exit 0. `eslint` over `app components lib` returns 7 problems, **none in `app/portfolio`, `components/ui`, or `components/charts`** — all in `app/bench`, `app/assinatura`, `app/auth/callback`, `app/tlh`, `AppShell`. `py_compile` on the backend route exit 0.

**Navy accent audit** (the project's own Scarce Accent Rule): 9 occurrences — 6 interactive affordances, 1 active state, 1 data encoding (the "Compra" badge), 0 decoration.

## What's Working

1. **The honest-gaps machinery is real, not decorative.** `Unknown()` stops a failed price lookup printing `R$ 0,00`; `IncomeSection` inverts the rule so a genuine zero is not an em dash; `PositionsTable` gives three visually distinct renderings for history-failed / history-loading / no-history-for-this-ticker. Three components, three sides of one principle, all correct.
2. **The page argues with its own numbers in public** — the TWR pre-emption, the allocation-total explanation, the prêmios/reembolsos reconciliation. Most products hide these.
3. **Failure taxonomy in `IndexSection`.** 402 → inline plan offer; 422 → "ainda não há histórico" with no retry (retrying an answer is theatre); anything else → outage with retry, and `retry: false` so the two non-faults aren't hammered.

## Priority Issues

### [P1] "Nenhum extrato importado" is shown when the income and trades queries *fail*

`hasAnyData` reads `income.data?.length ?? 0` and `tradeProbe.data?.total ?? 0` — both coalesce a *failed* query to zero — while the error guard above covers positions only. Positions returns `[]` (normal for a movimentação-only or liquidated book), income errors, trades errors → "Nenhum extrato importado". This is verbatim the failure mode the file's own comment forbids; the guard was written for one of three queries and never extended.

**Fix:** gate on failure as well as emptiness; only reach the empty state when all three succeeded.

### [P1] No way to find an asset or a trade on the page whose job is holding them

Both tables offer sort only; `GET /portfolio/trades` accepts `limit/offset/sort/dir` and nothing else. No ticker search, date range, side filter, or corretora filter. Ctrl+F reaches positions but cannot reach server-paginated trades — a 3,000-trade book is 60 blind pages. `broker` is displayed but only sortable, so "what did I do at XP" is unanswerable, gutting the consolidation scene PRODUCT.md calls part of the job.

**Fix:** a client-side ticker input above Posições (rows are already in memory); a `ticker` query param alongside the existing allow-listed `sort`, with one bound input, resetting offset on change.

### [P1] Whose book this is survives only the first screenful

The book label lives exclusively in the `PageHeader` eyebrow. The sticky `SectionNav` carries no book identity, and below `md` the switcher is off-canvas. Past the first section an assessor reads a client's money with nothing on screen naming the client, and a screenshot of the Posições table carries no client name — while the URL does.

**Fix:** when `book.state !== "self"`, render the label in the already-sticky `SectionNav` bar, right-aligned, mono, 11px. Zero new chrome.

### [P2] The "Custo (precificado)" tile prints an em dash for a number it knows exactly

When `nothingPriced`, the tile renders `Unknown` because `pricedCost` collapses to 0 — but `totalCost` does not depend on prices at all and is printed in the caption one line below. "Never invent a number" has a mirror: never withhold a known one. The price plane being down is precisely when the user falls back to cost basis, and that is when the cost tile goes blank while restating the figure underneath it in 12px muted.

**Fix:** render `totalCost` when nothing is priced, with the label switching to "Custo total".

### [P2] Empty, error and paywall all wear the same dashed costume; retry speaks two dialects

`EmptyState` is a dashed box, and DESIGN.md defines dashed as the signal for emptiness. The same component contains API failures in four places **and** the upgrade offer. Three meanings, one chrome — and the dashed border fights the copy, which spends two sentences insisting the data is not gone inside the box the system uses for "there is nothing here". Retry is a navy primary in one place and a bordered secondary in three.

**Fix:** add a `variant` to `EmptyState` (`empty` dashed, `error` solid + status-error, `offer` solid + navy check treatment). Standardise every "Tentar de novo" as secondary.

## Persona Red Flags

**Alex (power user):** no search or filter anywhere; page size caps at 250 while the API accepts 500; no go-to-page. Two blocking waits before the first number — the page is gated on the slowest of three queries, and the trades probe hits the *same endpoint* `TradesSection` re-fetches under a different key, a sequential double round-trip; the comment claiming key-sharing was applied to income and not to trades. Posições sort is linkable and Negociações sort is not, and the positions effect writes `?ordem=&dir=` on mount even untouched. Every book switch is a full reload, killing scroll on a six-section page. No export, print stylesheet, or copy-to-clipboard on a page that is otherwise a report.

**Sam (accessibility):** `Skeleton` pulses regardless of `prefers-reduced-motion` — the global reduce block lists only landing animations, not Tailwind's `animate-pulse` — and this is the page's first paint, while the sibling placeholder 240 lines away handles it correctly. The rentabilidade chart has no accessible name at all while the allocation pie on the same page has a full `role="img"` + generated label. Focus is undesigned outside `DataTable`'s ring. No sticky table header and no responsive-table strategy: 9 columns at 14px in a horizontal scroll region, so at 200% zoom the columns people came for are scroll-only (needs a render). `Sparkline` encodes direction in stroke colour alone for sighted users, mitigated by shape and a correct `aria-label`. `PctDelta` renders neutral zero as an en dash where the system reserves the em dash for missing.

**Assessor (enterprise):** book identity lost below the fold. A cold load of any client book flashes "Carteira de cliente — nome indisponível" because `useActiveBook` conflates "still loading" with "lookup failed" — the safety intent is right, the fix is a fourth state or an eyebrow skeleton while pending. A lapsed plan clears the selection and hard-reloads with no message: the advisor's screen becomes a different person's money mid-session, unannounced. Their deliverable is a document, and the page has no export and no print stylesheet.

## Minor Observations

- **[P2] `page.offset`/`page.limit` are returned and dropped.** The trades caption recomputes `from`/`to` from local state, which is the direct cause of the caption/rows mismatch during a `keepPreviousData` fetch. Reading the server's echo fixes it for free. `Trade.source_file` is returned and used only as a React key — on a page built around provenance, "which extract this row came from" is the most on-brand tooltip available.
- **[P3] `PortfolioIndex.nav` and `.start` never rendered.** `nav` is the engine's own book valuation, a second opinion on the Resumo tile.
- **[P3] `Metric` returns `null` when a benchmark's return is null** — IBOV silently disappears with no "—" and no caveat; the caveat list only covers wholesale `!hasBench`. A silent gap in the component built to be loud about gaps.
- **[P3] Card chrome hand-copied twice inside `page.tsx`** — `Stat` and the "A partir daqui" nav both inline `rounded-lg border border-hairline bg-panel p-4` rather than using `Card`.
- **[P3] The mono eyebrow appears ~10 times on a loaded screen.** DESIGN.md frames it as "the small uppercase label above a title"; a signature at 10 instances is wallpaper.
- **[P3] `rounded-xs` token drift.** `IncomeSection` uses Tailwind v4's `rounded-xs` (2px) where DESIGN.md defines `xs` as 4px; `TickerChip` hard-codes `rounded-[4px]` to get the documented value.
- **[P3] The Alocação donut may be degenerate** — grouping by `market` is the honest choice, but a book of ações + FIIs + ETFs all lands in "Mercado à Vista" and the chart becomes one slice. Needs a render.
- **[P3] Skeleton shapes don't mirror the layout** — three grey slabs where a 4-tile grid, a chart card and a table are about to appear.
- **[P3] `Sparkline`'s window is fixed at 6 months** and its first-vs-last basis can openly contradict the Rentabilidade cell in the same row (P/L vs average cost). Neither basis is named on screen.
- **[a11y] `<section id="resumo">` has no accessible name** while `SectionNav` advertises "Resumo" as a destination.

## Questions to Consider

1. If "Mercado à Vista" is ~95% of a real Brazilian book, what is the Alocação donut telling anyone? A top-10 concentration bar — data already on the page — answers the question the donut only pretends to ask.
2. The page ships two returns that disagree by construction and says so in a 12px footnote. If the disagreement is structural and permanent, why is it a footnote instead of the framing of the Resumo section?
3. `EmptyState` contains absence, outage, and "this works but you must pay". If dashed means "nothing here", what does a dashed box around a working, purchasable feature mean?
4. An assessor's output is a document handed to a client. Why does the most report-shaped surface in the product have no print stylesheet, no export, and no client name that survives a screenshot?
5. Both tables ship eight sortable columns and zero filters. Which of those sorts has ever answered a question a ticker filter wouldn't have answered faster?
6. One comment says two components share a query key so React Query serves one request — and then the trades probe uses a different key from the trades table. Is the probe earning its round-trip, or could it be the first page itself?
