---
target: frontend/app/portfolio/page.tsx
total_score: 20
max_score: 40
na_heuristics: 
p0_count: 1
p1_count: 3
timestamp: 2026-07-26T15-14-07Z
slug: frontend-app-portfolio-page-tsx
---
Method: dual-agent (A: design review, Opus · B: detector + evidence, Sonnet)

Surface mode: **Operate**. Target: `frontend/app/portfolio/page.tsx` (182 lines) plus `PositionsTable`, `IndexSection`, `IncomeSection`, `TradesSection`, and the `ui/` + `charts/` component libraries.

**Render status: not rendered.** Nothing is listening on :3000 or :8000 (confirmed by `ss` and `curl`, connection refused). The static export at `frontend/out/portfolio.html` is a client-only shell — every number here is client-fetched — so rendering it yields an empty skeleton. All findings are source-grounded; findings needing real layout to confirm *severity* (not existence) are marked ⚠️LAYOUT. No overlay was injected and none is claimed.

## Design Health Score

| # | Heuristic | Score | Key Issue |
|---|-----------|-------|-----------|
| 1 | Visibility of System Status | 2 | No as-of date for the marks on the page — it lives at 11px in the sidebar footer (`AppShell.tsx:229`); the `history` query has no loading state, so sparklines silently swap from `—` to a line. |
| 2 | Match System / Real World | 3 | pt-BR vocabulary and formatting are strong and untranslated, but the tile labelled "Total investido" does not show total invested — it shows priced cost only (`page.tsx:108`). |
| 3 | User Control and Freedom | 1 | Sorting is the only control on the surface. No filter, search, date range, drill-down, or export; sort state is component-local (`PositionsTable.tsx:72-75`), so reload loses it and no view is shareable. |
| 4 | Consistency and Standards | 2 | `PositionsTable` sorts, `TradesSection` doesn't; a 4th bespoke stat-tile implementation (`page.tsx:22`); eyebrow tracking deviates from the app's own 0.14em standard; no `SectionNav` on a ~3000px page despite DESIGN.md and both `/estrategia` and `/tlh` having one. |
| 5 | Error Prevention | 2 | Real craft — unpriced rows out of the P/L base, unpriced rows sink on *both* sort directions, divide-by-zero guard, `keepPreviousData`. Docked for the R$ 0,00 fallback. |
| 6 | Recognition Rather Than Recall | 2 | User must recall which of three "Rentabilidade" definitions they are reading, the marks' as-of date, and which client book is active — none of it on the page or in the URL. |
| 7 | Flexibility and Efficiency | 1 | Worst score against the *declared primary user*. No keyboard shortcuts, no URL state, no saved views, no export, no page-size control, no jump-to-page; `LIMIT` hardcoded to 50 (`TradesSection.tsx:17`). |
| 8 | Aesthetic and Minimalist Design | 3 | Genuinely disciplined: no zebra, hairlines doing all separation, mono+`tnum` right-aligned, one accent, flat. Docked for the donut and for five palette colours encoding nothing in `IncomeSection`. |
| 9 | Error Recovery | 1 | The primary query has no error branch at all (`page.tsx:84-95`). `IndexSection` misdiagnoses a paywall 402 as a quote-base outage. No retry affordance anywhere. |
| 10 | Help and Documentation | 3 | Strong for Operate — the TWR footnote, the caveats block, "N de M precificadas", empty-state copy that names the next action. Docked because all help is uniform 12px muted. |
| **Total** | | **20/40** | **Acceptable (bottom edge)** |

No heuristic scored `n/a`; all ten apply to this surface.

## Design Specificity Verdict

**LLM assessment: generic composition carrying product-specific content that the visual system actively demotes.**

The structure is the category default and could be lifted into Kinvo, Gorila, or Status Invest unchanged: 4 KPI tiles → donut + legend → benchmark line → sortable holdings table → income matrix → paginated trade ledger. Nothing in the arrangement or interaction grammar is authored for a tax-aware Brazilian platform.

What *is* unfakeable lives in the data layer and the copy: the P/L base excluding unpriced positions (`page.tsx:59-63`), the four-mode caveats block (`IndexSection.tsx:92-102`), the market-split rationale (FIIs ride under Mercado à Vista, `page.tsx:65-67`), and the TWR footnote in plain pt-BR.

**The core failure: the product's differentiated content is styled as its least important content.** Every honesty artifact renders at `text-xs text-muted` at the bottom of a card, while the least informative number on the page — the position *count* — gets `font-display font-extrabold text-2xl` (`page.tsx:117-121`). PRODUCT.md says "Report the gaps as loudly as the results." Typographically, the gaps are the quietest thing here.

Concrete missed opportunities:
- **No tax dimension anywhere.** The shared tax engine is the one mechanism competitors cannot copy. This page shows P/L and never the tax on it: no R$20k isenção headroom, no prejuízo a compensar, no unrealised-vs-carryforward. A losing row is the most natural entry point to TLH in the product and is completely inert.
- **The loop is severed.** Principle #5. This page links to IRPF, TLH, Estratégia, Simulação zero times. It is exactly "a standalone tool".
- **The multi-institution scene is dropped at the render layer.** `broker` is returned and typed (`types.ts:166`) and never rendered. Consolidation across institutions is "part of the job, not a feature" — the donut splits by market, never by corretora.
- **`last_price` is returned, typed (`types.ts:114`), and rendered nowhere.** For the declared design center, a market value with no visible mark is a number without provenance.
- **The donut encodes the least and occupies the most.** Typically 2-4 buckets — a 3-row table with a decoration attached.

**Deterministic scan: zero findings.** `detect.mjs` exited 0 with an empty result set across the page, all 10 `ui/` components, and all 6 `charts/` files. Nothing to adjudicate as a false positive.

**The gap between those two results is the most useful thing in this report.** The detector measures design-system hygiene, and on that axis this code is genuinely excellent: zero hardcoded colour literals, zero inline pt-BR formatting, tokens throughout, no console/TODO leftovers, clean h1→h2 heading order, `role="img"` correctly paired with `aria-label`. A clean detector run confirms the surface is not sloppy. It cannot see that the surface is generic, that three queries lie on failure, or that the product's differentiator never appears. Do not read the clean scan as a passing grade.

**Visual overlays: none.** No server was listening, so no page was loaded, no injection was attempted, and no user-visible overlay exists.

## Overall Impression

This is careful, well-mannered code with a real conscience in its data layer — and it is the least product-specific surface in the app. Someone thought hard about *correctness* (the unpriced-position discipline is genuinely best-in-class) and barely at all about *what this page is for*. It renders a competent generic portfolio tracker for a company whose entire positioning is that it is not one.

The single biggest opportunity: this page is where the user spends the most time, and it contains none of the tax engine that is the reason the product exists. Fixing the honesty bugs is urgent; connecting this surface to the loop is what would make it Quantax's portfolio page rather than anyone's.

## What's Working

1. **The unpriced-position discipline is the honesty principle implemented as interaction, not copy** (`page.tsx:59-63`, `PositionsTable.tsx:82-88`). The P/L base excludes what could not be priced so a Renda Fixa row does not drag returns toward zero; the tile says how many of how many are priced; unpriced cells are em dashes per the design system; and unpriced rows sink to the bottom in *both* sort directions, so flipping a column never floats a wall of dashes to the top. That last detail is the one that proves someone actually used this.

2. **`IndexSection`'s caveats block** (`IndexSection.tsx:92-102`). Four distinct blindness modes, each a specific sentence naming actual values — clamped start with both dates, excluded markets, unpriced tickers by name, and a count of trades marked at a stale close — plus a 422-vs-fault split in the error state. Nothing else in this category ships this. Its only real problem is its type size.

3. **`Sparkline` as inline SVG with a stated ceiling** (`Sparkline.tsx:3-5`). Fifty recharts `ResponsiveContainer`s in a table is a resize-observer storm; this is a `viewBox` and a `polyline`. It also carries a real `aria-label` with direction *and* magnitude, making it — ironically — the most accessible number-bearing element on the page.

## Priority Issues

### [P0] A failed request tells the user they have no data

**What:** `page.tsx:84-95` branches on `isLoading`, then on `positions.length === 0`. There is no `isError` branch. Any 500, timeout, or 404 (a deleted client book, `tenancy.py:40-43`) leaves `q.data` undefined → `positions = []` (`page.tsx:54`) → the page renders **"Nenhum extrato importado — envie seus arquivos da B3."** `providers.tsx:11` sets `retry: 1`, so this lands after one silent retry.

Both assessments found this independently, and B established it is not one bug but a pattern: `IncomeSection.tsx:54-65` and `TradesSection.tsx:64-75` have the same missing branch, rendering "Nenhum provento registrado ainda." and "Nenhuma negociação importada." on failure. `IndexSection.tsx:55-76` is the one query that does it correctly.

**Why it matters:** The surface holding a user's money tells them their book is empty and instructs them to re-upload data they already have. For the enterprise assessor it shows an empty state *under a client that has data* — against a tier sold on exactly that trust boundary. It is a direct breach of "Never invent a number": the page states a fact it does not know.

**Fix:** Add the branch before the empty check, in all three components:
```tsx
) : q.isError ? (
  <EmptyState icon={<IconWallet size={28} />}
    title="Não foi possível carregar suas posições."
    hint="A API pode estar indisponível. Nada foi perdido — tente novamente."
    action={<button onClick={() => q.refetch()}>Tentar de novo</button>} />
) : positions.length === 0 ? (
```

**Suggested command:** `/impeccable harden`

### [P1] R$ 0,00 invented when the price plane is down

**What:** `routes.py:162-167` catches an engine failure and returns `prices = {}`, so every position comes back unpriced. `page.tsx:60-63` then reduces over an empty array: `marketValue = 0`, `pricedCost = 0`, `pnl = 0`. Lines 106 and 110 render those as **R$ 0,00** — presented as fact. The real total cost is relegated to a 12px sub on a third tile (`page.tsx:111`).

**Why it matters:** Zero is not the value; *unknown* is. This is the exact failure the product principles forbid and that DESIGN.md legislates against ("a missing value is an em dash, never N/A or an empty cell"). The app's own banner simultaneously promises the opposite behaviour — "posições e P/L aparecem só a custo" (`AppShell.tsx:185`) — and that banner only shows if `/market/status` itself succeeds, so the two can fail independently and contradict each other on screen.

**Fix:** In `Stat`, render `—` when `priced.length === 0` and make the sub say why: "Cotações indisponíveis — mostrando apenas custo". Guard at `page.tsx:106,110,113`.

**Suggested command:** `/impeccable harden`

### [P1] A paywall is reported to the user as a data outage

**What:** `/portfolio/index` is gated behind `needs_investidor` (`main.py:161`). A `gratis` user — whose tier explicitly includes Portfólio — gets a 402. `api.ts:137-139` fires the paywall modal, and `IndexSection.tsx:55-72` falls into its non-422 branch and renders "Não foi possível calcular a rentabilidade agora. A base de cotações pode estar indisponível." with `retry: false`.

**Why it matters:** Two failures at once. The app states a false cause — the quote base is fine. And a modal opens unbidden on page load; once dismissed, the card is a permanent dead end with no upgrade CTA and no self-correction, the only path back being a reload. Operate mode is explicit that a modal as first thought is usually laziness; here it is also the free tier's first impression of the headline surface.

**Fix:** Branch on 402 at `IndexSection.tsx:60` — title "Rentabilidade vs IBOV e CDI faz parte do plano Investidor.", hint naming what it gives, `action` linking to `/assinatura`. Separately, suppress the auto-modal for 402s raised by a passive page-load query: fire `PAYWALL_EVENT` only from user-initiated actions.

**Suggested command:** `/impeccable clarify`

### [P1] Screen readers hear a loss as a gain

**What:** `PctDelta.tsx:20-23` renders `<span aria-hidden>{glyph}</span>` followed by `formatPercent(Math.abs(value))`. The only direction cue is hidden from assistive tech, and the magnitude is absolute. A −8,4% position is announced as "**8,4%**". Verified directly in source.

**Why it matters:** DESIGN.md names this component as the *enforcement point* for The Never-Colour-Alone Rule — and for assistive tech the rule is unenforced, because the non-colour cue is `aria-hidden` and colour is invisible to AT by definition. The file's own comment on line 3 asserts "the glyph carries direction (not colour alone)", which is true for sighted users and false for everyone else. In both current call sites the sign happens to be recoverable from an adjacent `<Money sign />` — that is luck, not design, and it breaks the moment `PctDelta` is used alone.

**Fix:** One line — drop `Math.abs` and let `formatPercent` render the minus, keeping the glyph decorative.

**Suggested command:** `/impeccable audit`

### [P2] Three different numbers are all called "Rentabilidade", and "Total investido" isn't

**What:** On one screen: the stat tile "Rentabilidade" = simple return on *priced* cost (`page.tsx:111-116`); the table column "Rentabilidade" = per-position P/L (`PositionsTable.tsx:157`); the card "Rentabilidade vs IBOV e CDI" = time-weighted return (`IndexSection.tsx:14`). The first and third will disagree, often materially, and only the third is explained. Separately, "Total investido" shows priced cost only, with the real total hidden as a sub on the neighbouring tile.

**Why it matters:** The primary user reads closely and will notice the discrepancy immediately. An unexplained disagreement between two of your own numbers is the fastest way to lose a quant's trust. The "Total investido" mislabel is a plain factual error corrected only in 12px muted.

**Fix:** Rename the tile to "Retorno simples"; add one line under the stat row: "Retorno simples sobre o custo; a curva abaixo usa TWR." Rename "Total investido" → "Custo (precificado)" and promote total cost to its own value line.

**Suggested command:** `/impeccable clarify`

## Persona Red Flags

**Alex (impatient power user — the declared design center):**
- Wants "my worst position and its tax loss". Can sort by Rentabilidade, but must scroll past a donut and a 280px chart to reach the table — no `SectionNav`, no anchors, on a ~3000px page, while `/estrategia` and `/tlh` both have one.
- Sorts, refreshes, loses the sort — state is component-local (`PositionsTable.tsx:72-75`), never in the URL. Cannot share or bookmark a view.
- Cannot see the mark behind any market value: `last_price` is fetched, typed, and dropped.
- A losing row is inert — no link to TLH, no lot detail, no tax overlay. The highest-value action the product offers is one click away in the nav and zero clicks away in intent, and the row does nothing.
- Trade history: 50 rows/page hardcoded, **no sorting at all** (`TradesSection.tsx:33-45`), no ticker filter, no date filter, Anterior/Próxima only. A 5-year book is dozens of blind clicks.

**Sam (keyboard / screen reader):**
- **Sortable `<th>`s have no `aria-sort` and no `scope`** (`DataTable.tsx:27-36`). The sort button label is phrased as a result, not a state — "Ordenar por Ativo (crescente)" is announced identically whether it describes the current order or the one clicking will produce.
- **The scrolling table region is keyboard-unreachable.** `DataTable.tsx:23` is `overflow-x-auto` with no `tabIndex={0}` and no accessible name. With 8 columns, a keyboard-only user cannot reach the right-hand ones. ⚠️LAYOUT for the overflow threshold; the missing `tabIndex` is unconditional.
- **Loading is silent.** `Skeleton.tsx:5` is `aria-hidden` with no `aria-busy` and no live region. Content appears with no announcement.
- **Pagination is silent**, and after the last page focus remains on a button that has just become `disabled` — focus lands on an inert control.
- **The donut has no accessible name** (`page.tsx:127-147`). The legend beside it carries the same data but is not programmatically associated.
- **Losses announced as gains** — the P1 above.

**The assessor / escritório (`enterprise`, project-specific):**
- **The page never names the book it is showing.** `PageHeader` is a hardcoded `eyebrow="Carteira" title="Portfólio"` (`page.tsx:82`) regardless of active client. The only identification is the sidebar `<select>`, which is behind an off-canvas drawer below `md` and absent from any screenshot, print, or PDF the advisor sends the client. The tier is sold on "keeping a client's data from ever appearing under another client's name"; this surface carries no name at all.
- **No URL scoping.** `/portfolio` is the same URL for every book (sessionStorage only). Cannot bookmark a client, cannot tell from the address bar which book a tab is on. The sessionStorage design deliberately enables two-tab comparison; the URL refuses to express it.
- **Book deleted mid-session** → positions 404s before the switcher's reconciliation refetches → "Nenhum extrato importado" under a client name that is not shown, for a book that had data.

## Minor Observations

- **[P2] The Tabular Column Rule is broken by `DataTable`'s own design.** `DataTable.tsx:48-52` couples Plex Mono + `tnum` to `align === "right"`, so every *left-aligned* numeric loses it: the `Data` column of 50 `dd/mm/aaaa` values in proportional Plex Sans, and the `Ano` column of years. DESIGN.md explicitly lists date under Numeric. One line per column — `Column` already accepts `className`.
- **[P2] The grand total is not ruled off.** `IncomeSection.tsx:84-90` pushes totals as an ordinary row with the same `hairline/70` as every year. Worse, the Total *column* is `font-semibold` on every row, so semibold no longer marks the total row at all.
- **[P3] Mono eyebrow tracking deviates from the app's own standard.** `page.tsx:33` and `IndexSection.tsx:24` use `tracking-wide` (0.025em) where DESIGN.md mandates 0.14em and every other eyebrow in the codebase uses `tracking-[0.14em]`. ⚠️LAYOUT for visual severity; the token deviation is factual.
- **[P3] The stat row mixes numeral typefaces.** Three tiles render Mono via `Money`; the fourth is `font-display font-extrabold` (`page.tsx:118`). `bench/run/page.tsx:246-250` carries an explicit comment on why these tiles must be mono + `tnum` — this row violates its own sibling's documented reasoning.
- **[P3] `rounded-full` outside its sanctioned scope.** `IncomeSection.tsx:123-129` renders proportion bars as full-radius pills; DESIGN.md reserves `rounded.full` for status pills and state dots.
- **[P3] Colour encoding nothing.** `IncomeSection.tsx:127` assigns `SERIES[i]` per category when each bar is already labelled and length carries magnitude.
- **[P3] The loading skeleton does not resemble the page.** Two blocks (~500px) for a surface resolving to six sections and several thousand pixels. Operate mode calls for skeletons over spinners; this is a skeleton in name only, and the reflow on resolve is large. ⚠️LAYOUT.
- **[P3] `history` errors are invisible.** No `isError` handling on `page.tsx:49-52`; a failed price-history renders a full column of `—`, indistinguishable from "these tickers have no price data".
- **[P3] `excluded.categories` is returned and dropped.** Typed at `types.ts:148`, never rendered. Low information *except* that `IncomeSection` displays "Prêmios" and "Reembolsos" as real cash on the same page while the rentabilidade curve silently excludes them. Two cards, contradicting scopes, no reconciliation.
- **[P3] Caveat list uses a literal `·` glyph** rather than a list marker (`IndexSection.tsx:128`) — a manual bullet inside a `<ul>`.

## Questions to Consider

1. If the shared tax engine is the one thing competitors cannot copy, why does the surface where the user spends the most time contain **zero tax information** — no isenção headroom, no prejuízo a compensar, no realised-vs-unrealised split?
2. Principle #2 says report the gaps **as loudly as** the results. Every gap renders at `text-xs text-muted` and every result at `text-2xl`. Is the principle a commitment or a comment?
3. The primary design center authors YAML strategy specs and compares backtest runs. What on this page rewards that person more than Kinvo's free tier does?
4. `broker` and `last_price` are computed, serialised, and typed — then thrown away in the render. Is the API ahead of the UI, or is the UI silently deciding these do not matter?
5. Enterprise is sold on "a client's data never appearing under another client's name." What on this page is the client's name?
6. Would you sort a 3,000-row trade ledger by clicking "Próxima" 60 times? The positions table got sorting; the table that actually needs it did not.
7. A `gratis` user's headline surface currently opens with a modal and a card that lies about why it is broken. Is that the acquisition funnel, or a leak in it?
