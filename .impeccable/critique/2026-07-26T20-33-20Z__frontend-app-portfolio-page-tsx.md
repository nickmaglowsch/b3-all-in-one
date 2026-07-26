---
target: frontend/app/portfolio/page.tsx
total_score: 25
max_score: 40
na_heuristics: 
p0_count: 1
p1_count: 3
timestamp: 2026-07-26T20-33-20Z
slug: frontend-app-portfolio-page-tsx
---
Method: dual-agent (A: design review, Opus · B: detector + build evidence, Sonnet)

Surface mode: **Operate**. Target: `frontend/app/portfolio/page.tsx` plus `PositionsTable`, `IndexSection`, `IncomeSection`, `TradesSection`, and the `ui/` + `charts/` libraries. Second run, after the fixes from the 2026-07-26T15-14-07Z critique.

**Render status: not rendered.** The route needs auth + backend + Postgres and no dev server is running. All findings source-grounded; render-dependent severity flagged.

## Design Health Score

| # | Heuristic | Score | Key Issue |
|---|-----------|-------|-----------|
| 1 | Visibility of System Status | 3 | `Tendência 6m` renders em dashes while `/portfolio/price-history` is in flight — `history.isLoading` is never consulted, so "loading" and "no history" are the same pixels. Mark date still only in the sidebar footer. |
| 2 | Match System / Real World | 2 | "Valor de mercado" is the priced subset only, while Alocação totals the same book as `market_value ?? total_cost`. Two money totals for one portfolio, one viewport, that cannot agree. |
| 3 | User Control and Freedom | 2 | No filter, search, date range, or export on either table. Sparkline window fixed at 6m. |
| 4 | Consistency and Standards | 2 | Retry is a navy primary in one place and a bordered secondary in three others. Sort persists to the URL in `PositionsTable` and not in `TradesSection`. `Stat` and `simulacao`'s `StatTile` remain two divergent implementations. |
| 5 | Error Prevention | 3 | Nulls sink on both sort directions; load-time 402 suppressed from the dialog; focus handed off before a pager disables itself. Undercut by page-scoped sort. |
| 6 | Recognition Rather Than Recall | 2 | Four money bases to hold in memory (priced-only, mixed, cost basis, TWR); only the last pair is reconciled on screen. |
| 7 | Flexibility and Efficiency | 2 | No shortcuts, saved views, column chooser, or CSV. Sort-in-URL exists but not on the table that needs it. |
| 8 | Aesthetic and Minimalist Design | 3 | Restrained and well-ruled. Diluted by the mono eyebrow appearing 8+ times in the first two screens, which erodes its signature status. |
| 9 | Error Recovery | 3 | Every query has its own error state, retry, and correct reassurance copy. Lost a point to the empty state offering the wrong recovery. |
| 10 | Help and Documentation | 3 | Best-in-class inline explanation for a surface this dense. No methodology link. |
| **Total** | | **25/40** | **Acceptable, upper half** (was 20/40) |

No heuristic scored `n/a`.

## Design Specificity Verdict

**Authored for this product, not category-interchangeable** — reversed from the previous run's verdict. The evidence is structural: the honesty apparatus (`Unknown()` em dash over `R$ 0,00`, P/L over the priced subset, the promoted "O que ficou de fora" block) appears nowhere in the category; the reconciliation copy is authored, not generated ("as duas não batem, e não deveriam"); and ending on "A partir daqui" → IRPF / TLH / Rebalancear expresses the product's loop principle as layout.

Two moves remain imported: the four-tile KPI row is the standard admin-dashboard opener, and the donut restates its own legend without adding a reading, with an empty hole where the book total belongs.

**Deterministic scan: zero findings.** `detect.mjs` exit 0, empty result set. `tsc --noEmit` exit 0, no output. `eslint` over `app/portfolio components/ui components/charts lib` exit 0, zero errors and warnings.

## What's Working

1. **The gaps block is a product commitment kept in pixels.** `IndexSection.tsx:131-143` enumerates clamped coverage, excluded markets, unpriced tickers, trades marked at a stale close, and excluded cash categories — each a full sentence with real dates and tickers, at 14px ink with real list markers.
2. **Degradation is designed, not defaulted.** `nothingPriced` is a distinct third state between success and error; the load-time 402 renders inline rather than as a modal; a price-history failure prints a note and keeps the rest of the table honest.
3. **Table craft is real.** Nulls sink under both sort directions; `aria-sort` on the `<th>` with the button naming the *next* action; the scroll region focusable and named; focus moved off a button before the click disables it.

## Priority Issues

### [P0] Zero positions is reported as "nothing imported", hiding data the user has

`page.tsx:132-145` gates the entire page — including Proventos and Negociações — on `positions.length === 0`. Positions, trades and cash events are three independent outputs of one import. A movimentação-only import produces proventos with zero positions; a fully liquidated book produces years of trades with zero positions, which is the ordinary state of a Jan–May IRPF user who sold out. Both are told their import never happened, and their real data is not rendered at all. Same failure the error branch's own comment guards against, one branch later. For an assessor it makes a live client book look empty.

**Fix:** gate the page on "no data of any kind", not on positions. Keep the import CTA only when positions, income and trades are all empty; otherwise render the Posições card with its own empty state and let the other sections render.

### [P1] Two irreconcilable money totals for one book, neither labelled with its basis

`page.tsx:80-81` computes "Valor de mercado" over priced positions only; `page.tsx:96` totals Alocação as `market_value ?? total_cost` — a blend of marks and cost — and prints the result as plain `Money`. Whenever any position is unpriced, the Alocação column does not sum to the tile above it and nothing says why. The page spends a paragraph reconciling retorno simples against TWR and leaves the harder discrepancy silent.

**Fix:** mark mixed rows with a glyph and a footnote ("a custo — sem cotação"), the way the positions table already does per row, plus one line under the card: "N posições sem cotação entram pelo custo."

### [P1] The client-book name degrades silently to the generic label

`page.tsx:106` renders `eyebrow={book ?? "Carteira"}`, where `useActiveBookLabel` returns `null` while `/portfolios` is loading **and permanently if it errors** — while `X-Portfolio-Id` is still attached to every data request. On a cold load the positions request can win the race, painting a client's money under an eyebrow meaning "my own book". The enterprise tier is sold on a client's data never appearing under another client's name; this fails open.

**Fix:** fail closed. Return a discriminated result (`self` / `client` / `unknown`) instead of collapsing "own book" and "don't know yet" into one `null`; render a skeleton eyebrow while pending and an explicit "nome indisponível" on error.

### [P1] Eight sort headers on Negociações promise book-wide ordering the API cannot deliver

`TradesSection.tsx:93-109` sorts the 50 rows in hand; the backend takes only `limit`/`offset` and hard-codes `ORDER BY date DESC` (`routes.py:217-237`). On a 3.117-row book, "Valor ▼" returns the largest trade *on page 1* with the identical affordance as a real sort. The disclosure is a 12px fragment below the table, not at the control. Honesty in a caption does not repair a control that returns a plausible wrong answer.

**Fix:** push `sort`/`dir` into the query — one `order_by` mapping server-side. Until then disable the headers when `page.total > page.items.length`, naming why.

### [P2] Navy is used as a data fill, against two of the system's own named rules

`TradesSection.tsx:24-35` tints every Compra badge `bg-navy/[0.06] text-navy` — up to 250 navy chips in a viewport. `IncomeSection.tsx:150-154` draws proportion bars in `bg-navy/70`, a magnitude encoding in the interface accent. DESIGN's Scarce Accent Rule says navy appears on "roughly four things per screen"; the Two-Palette Rule says "never chart in navy… it takes `series-1`, not the brand." The income bars' own comment argues for one ink bar and then reaches for the accent.

**Fix:** income bars to `bg-ink/20` or `series-1`; Compra/Venda both neutral, since the labels already carry the distinction.

## Persona Red Flags

**Alex (power user):** two-stage load by construction — the lower sections only mount inside the success branch, so their queries fire after positions resolves. Zero filtering anywhere. No export, and `PageHeader`'s action slot is unused. Sort-in-URL is on the wrong table, and writes `?ordem=…` on mount even when he never sorted. `Tendência 6m` has no axis, baseline, cost mark, or hover, and its green/red answers a different question than the adjacent Rentabilidade column in the same colour vocabulary.

**Sam (accessibility):** `<section id="resumo">` has no heading and no `aria-label` while `SectionNav` advertises it as a destination; the other sections wrap a `Card`, itself a `<section>`, producing unnamed section-in-section nesting. `Skeleton` uses `animate-pulse` with no `motion-reduce:` variant, and the global `prefers-reduced-motion` block covers only landing animations. Sort-button names contain but do not lead with the visible label (WCAG 2.5.3). The `Tendência` column during load is em dashes with no `aria-busy`.

**The assessor (enterprise):** the book-identity failure above is aimed squarely at her. Arriving from Carteiras, `router.push("/portfolio")` carries no `?carteira=` param, contradicting the intent recorded in `api.ts` — she screenshots a page whose URL identifies nothing. No printable or exportable form. The two-tab flow and the hard-reload-on-switch are genuinely well engineered, which makes the missing name sharper.

## Minor Observations

- **[P2] Numerals in sans.** `page.tsx:158,166,183` interpolate `formatMoney`/`formatInt` into `text-xs text-muted` sub-lines — currency and counts in Plex Sans, against the Numeric hierarchy. Same in `IndexSection.tsx:133-141`. `simulacao`'s `StatTile` renders its sub-line in mono; the sibling got it right.
- **[P2] Em dash for a computed zero.** `IncomeSection.tsx:35-37` renders `—` when `value === 0`. The em dash is reserved for *missing*; zero rows are reported as zero. A year with genuinely zero JCP is now indistinguishable from a year with no rows.
- **[P2] Series colours assigned by preference.** `IndexSection.tsx:120-125` gives IBOV `SERIES[2]` and CDI `SERIES[1]`, skipping index 1 for the second series, against "assign by index, never by aesthetic preference". Also worth a render check: `series-3` aqua as a 2px stroke on Panel White is ≈2.8:1, marginally under the 3:1 floor.
- **[P3] No totals row on Posições**, though `DataTable` now supports `footRowKey` and `IncomeSection` uses it. The biggest table doesn't tie to the tiles above it.
- **[P3] Empty donut hole** — `innerRadius="58%"` reserves a centre holding nothing; the book total belongs there.
- **[P3] "A partir daqui" is a hand-rolled card** rather than a `Card`, with a mono eyebrow where a heading belongs — a fourth surface vocabulary.
- **[P3] Alocação legend row may overflow on mobile** — a single flex row with un-truncated market name, count, percent and a `w-32` money cell. Needs a render.
- **[P3] `aria-current="true"`** on SectionNav anchors where DESIGN specifies `"page"`.
- **[P3] `signed_qty` and `PortfolioIndex.nav` returned and dropped.** `nav` is the one number that would let a user reconcile the Rentabilidade card against the Valor de mercado tile.

## Questions to Consider

1. If the page shows four money totals on three bases, why is only one pair reconciled in prose? What if every figure carried its basis as a first-class label — a `Money` variant that renders its own provenance?
2. The stale-quote banner lives in `AppShell` so no page can forget it. Why doesn't the *mark date* live there too, always — instead of appearing only once it's bad news?
3. "Report the gaps as loudly as the results" is honoured brilliantly for the index and not at all for the allocation. Is the caveat block a component waiting to be extracted, or is honesty a thing this page does once?
4. The primary persona sorts to find outliers, and the sort is a lie past row 50. Would a book-wide "top 10 by |P/L|" panel serve that user better than eight headers that can't reach page two?
5. The empty state assumes "no positions" means "no import". What else here confuses the shape of the data with the state of the user's work?
6. DESIGN says navy appears on roughly four things per screen. On a loaded Negociações page it appears on 250. Is the Scarce Accent Rule a rule, or a description of a page that no longer exists?
