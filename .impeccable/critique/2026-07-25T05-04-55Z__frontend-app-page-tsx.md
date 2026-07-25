---
target: frontend/app/page.tsx
total_score: 20
max_score: 28
na_heuristics: 7,9,10
p0_count: 0
p1_count: 3
timestamp: 2026-07-25T05-04-55Z
slug: frontend-app-page-tsx
---
Method: dual-agent (A: a2583e834b09fee26 · B: adebbd2ee31c99e23)

Surface mode: **Persuade**. Target: `frontend/app/page.tsx` (688 lines) plus `frontend/components/landing/`. Source-grounded only — no browser automation is exposed and the Next dev server is not running (port 3000 held by an unrelated service, `302 → /public/`), so no rendered verification, no screenshots, no overlay. Findings needing real layout are flagged.

## Design Health Score

| # | Heuristic | Score | Key Issue |
|---|-----------|-------|-----------|
| 1 | Visibility of System Status | 3 | Good hover/scroll feedback; no active-section state in the sticky nav; 19 `.reveal` blocks ship at `opacity:0` in the static export and depend on JS to appear. |
| 2 | Match System / Real World | 3 | Best-in-class pt-BR tax vocabulary and correct DARF mechanics — undercut by copy that tells users to upload PDFs when the product accepts only `.xlsx`. |
| 3 | User Control and Freedom | 3 | `prefers-reduced-motion` is handled thoroughly including a JS guard in `CountUp`; but marquee pause is hover-only (no keyboard/touch), and two hover-translate effects escape the reduced-motion block. |
| 4 | Consistency and Standards | 2 | Seven violations of its own DESIGN.md: gradient heading (3 rules at once), series colour as chrome, status badge without icon and off-vocabulary, gain/loss on non-signed verbs, plus `h3` rendered at three different visual weights. |
| 5 | Error Prevention | 2 | The page sets the user up to fail: wrong file format in the instructions, and "Entrar" and "Começar grátis" both resolve to `/login` with nothing distinguishing sign-up from sign-in. |
| 6 | Recognition Rather Than Recall | 4 | Show-don't-tell peeks, numbered steps, explicit "Tudo do plano X" ladders. Genuinely strong. |
| 7 | Flexibility and Efficiency | n/a | Single-pass Persuade surface; no repeat-use efficiency dimension exists. |
| 8 | Aesthetic and Minimalist Design | 3 | Strong system discipline, but the first viewport runs six concurrent motion systems and carries one filler stat. |
| 9 | Error Recovery | n/a | Static page, no forms, no error states possible. |
| 10 | Help and Documentation | n/a | Persuade surface; the footer disclaimer is the only doc-like element and appropriately so. |
| **Total** | | **20/28** | **71% — Good, at the bottom of the band** |

Three heuristics are `n/a` under the Persuade mode rule, so the maximum is 28, not 40. Assessment A independently scored 23/28. I lowered three after weighing B's mechanical evidence, which A never saw:

- **H2 4 → 3.** A praised the real-world fit, correctly. B then found the landing instructs "Suba os PDFs da B3" while both client (`UploadZone.tsx:37,42,89`) and server (`routes.py:64-65`) reject anything but `.xlsx`. I verified this myself. A page that tells you the wrong file format is not matching the real world, however good its vocabulary.
- **H4 3 → 2.** A found the gradient breaking three documented rules. B found four more DESIGN.md conflicts plus the heading-weight decoupling. Seven consistency violations is a 2.
- **H5 3 → 2.** The signup/login ambiguity A found, compounded by the format error, means the page actively creates the two most likely first-attempt failures.

## Design Specificity Verdict

**LLM assessment: genuinely authored at the core, template-grade at the scaffolding — roughly 60/40.**

`IrpfPeek` (`:298-336`) is the most product-specific artifact on the page and the best thing on it. "DARF · abril", "Código 6015 · vencimento 31/05", "Alíquota 15%", "Prejuízo a compensar" are real Brazilian renda-variável mechanics, correct in detail — 6015 *is* the DARF code, 15% *is* the alíquota, end-of-following-month *is* the due date. No neighbouring product ships that card without owning a tax engine. For a pre-launch product with no social proof, credibility-by-demonstrated-competence is not merely a good strategy, it is the only one available, and this executes it. `BenchPeek` (CDI as the benchmark) and `AllocPeek` (advisory orders that don't execute) do the same job well.

The scaffolding around it is interchangeable: the 6-up icon+heading+text FEATURES grid with `hover:-translate-y-1 hover:shadow-lg` is the stock SaaS kit card, the two `blur-3xl` hero glows are the default 2023 hero gesture, and the gradient headline is the single least "this product" move on a page whose design system is explicitly defined *against* gradient fintech.

The sharpest structural observation: **`DashboardPeek` is the least specific of the four peeks and it holds the hero slot.** A patrimônio card with a big number and an area chart is something Kinvo could run unchanged. The one image no competitor could truthfully render — the loop, where a backtest's after-tax curve feeds the same DARF card — is never drawn anywhere on the page.

Related and larger: **the page never states its uncopyable claim.** PRODUCT.md hands it one sentence competitors cannot say — the same tax engine that scores the backtest produces the DARF and fills the ficha de bens. The three showcases run as three parallel pitches (Estratégia / Performance / Imposto), each of which Kinvo, QuantBrasil or Grana can individually match. That is precisely the framing PRODUCT.md Principle 5 warns loses the loop.

**Deterministic scan.** 5 findings, exit 2 — 1 warning, 4 advisory, all in `page.tsx`; `components/landing/` is clean. **5/5 true positive, 0 false positive.** B positively confirmed the design system was actually loaded rather than silently defaulting: `text-[11px]` appears 5× and was correctly *not* flagged while `text-[10px]` was, exactly matching the 11px label step — a negative control that proves the ramp is being read. The detector also correctly declined to flag the marketing-sanctioned `tracking-[0.12em]` and the Archivo Headline steps.

- `gradient-text` @ `:449` — warning
- `design-system-font-size` @ `:137`, `:209`, `:309`, `:619` — all 10px against the 11px label step

**Visual overlays: none.** No browser tool exists in this session and no dev server is running; injection was never attempted. Contrast figures below are computed from tokens, not measured on screen.

## Overall Impression

This page is better than its score suggests, and its problems are almost all of one kind: **it manufactures proof it doesn't need, on a page whose genuine proof is already its strongest asset.**

The four peek components demonstrate real domain mastery to an expert audience. That earns trust. Then the page bolts on a pulsing "ao vivo" dot over admittedly-mock numbers, an animated "100%" that was never measured, a "7 relatórios" count the code contradicts, and a data-custody promise the product cannot currently honour. Every one of those is a withdrawal from the credibility the peeks deposited — and this is a product whose own principles are "never invent a number" and "report the gaps as loudly as the results."

Riley, the stress tester, is the persona that matters most here, because the target audience *is* Riley: a quant who reads closely. The moment he notices "ao vivo" over fake quotes, he re-reads the correct DARF card as marketing too.

The single biggest opportunity is not a fix — it is that the page has never told anyone what the product actually is. Say the loop.

## What's Working

1. **The peeks are the testimonials.** Real tax mechanics rendered in the product's own visual system prove competence more convincingly to this audience than any logo wall, and they do it without fabricating a single customer — the exact constraint a pre-launch product operates under. The hardest marketing problem here is solved with the best asset the team actually has.
2. **Voice and terminology discipline.** "A gente lê negociações e movimentações — nada à mão", "sem chute", DARF/isenção/proventos untranslated, comma decimals and R$ spacing correct in every mock. The page sounds like it was written by someone who files a Brazilian declaration.
3. **Motion engineering under the hood.** An all-CSS vocabulary, a complete `prefers-reduced-motion` story, and — the part that impressed me — a JS-side reduced-motion guard in `CountUp` (`Motion.tsx:65,70-71`) that a CSS media query cannot provide, with the value pinned at mount and the rAF loop never scheduled. Also zero hardcoded colours: no hex, no `rgb()`, no stock Tailwind palette classes anywhere on the page.

## Priority Issues

### [P1] The page instructs an action the product rejects
**What.** `:348` "Suba os PDFs da B3" and `:574` "Do PDF ao imposto em três passos". The product accepts `.xlsx` only, enforced twice: `UploadZone.tsx:37,42,89` (`accept=".xlsx"`, "Apenas arquivos .xlsx são aceitos") and `routes.py:64-65` (`HTTPException(400, "only .xlsx accepted")`). I verified both.
**Why it matters.** This is the first instruction the page gives and it is wrong. A user who does exactly what the landing says gets rejected at step one of the funnel they were just sold. It also makes the product look broken when it isn't.
**Fix.** Replace "PDFs" with the real artifact in both places — the B3 Área do Investidor extracts are `.xlsx` spreadsheets. "Suba as planilhas da B3" / "Da planilha ao imposto em três passos".

### [P1] Two headline stats are untrue or unmeasurable
**What.** `:500` "7 relatórios da B3 suportados" — `sniff_kind` (`taxcore/scripts/load_b3.py:268-274`) recognizes exactly **two** report files, negociação and movimentação, corroborated by `routes.py:45-51` and the import UI's own labels. What the parser handles many *of* is event types *within* movimentação (Dividendo, JCP, Rendimento, Amortização, Desdobro, Bonificação, Leilão de Fração…), so "relatórios" is the wrong noun whichever number was intended. `:501` "100% dos seus proventos rastreados" is an animated CountUp of a metric that was never measured.
**Why it matters.** Both break PRODUCT.md Principle 1, "never invent a number", on the product's front door. The "100%" is worse than merely unverified: a product that ships an `excluded` block precisely so it can report what it *couldn't* see should not claim 100% of anything.
**Fix.** Make the noun true ("2 relatórios da B3: negociação e movimentação") or count the thing that is actually impressive — the corporate-event types handled. Replace the 100% tile with a capability fact: "IBOV e CDI" as benchmarks, or the number of event types.

### [P1] The trust layer is missing, and its one sentence overclaims
**What.** The high-stakes moment is a stranger deciding to hand over their complete trade and tax history. The page offers no privacy link, no terms, no LGPD statement, no security note — the footer (`:677-685`) has only the brand and the not-advice disclaimer. The entire trust apparatus is one line at `:483`: "Sem cartão de crédito. Seus dados ficam com você." PRODUCT.md records that no export or account-deletion route exists in `platform/` and that future work "must not imply they already exist."
**Why it matters.** The one trust sentence on the page is a claim the product cannot currently honour, and it sits at the exact moment the visitor is deciding whether to trust it. This is a product-principle violation, not a style note.
**Fix.** Replace `:483` with something true today — "Sem cartão de crédito. Só leitura dos seus extratos — nada executa na sua corretora." Add privacy/terms to the footer once those pages exist. Do not ship "seus dados ficam com você" until export and deletion are real.

### [P2] Manufactured liveness over admittedly-mock data
**What.** `:145-146` renders a pulsing `bg-gain` dot labelled "ao vivo" above numbers the file's own comment at `:16` calls "illustrative product mockups, not live data" — a comment that is never rendered. No peek carries any "exemplo" marker. Compounding it, B found the three `Sparkline`s at `:170` are `role="img"` with computed `aria-label`s, so screen readers announce three fabricated trend claims ("Tendência de 6 meses: alta 113,3%") as fact; the label also hardcodes "6 meses" while the arrays carry 7 points.
**Why it matters.** This is the tell that costs the page everything else. Once a reader clocks "ao vivo" over fake quotes, the genuinely correct DARF card becomes suspect too.
**Fix.** Change "ao vivo" to "exemplo" — same layout, converts an assertion into a disclosure. Add a small "exemplo ilustrativo" caption to the peek group. Either `aria-hidden` the Sparklines or make their labels non-factual.

### [P2] The gradient headline: three rule violations, a verified contrast failure, and a high-contrast-mode fault
**What.** `:449` `bg-gradient-to-r from-navy via-series-1 to-series-3 bg-clip-text`. It violates the Oscilloscope Navy spec ("never fills a card, **tints a heading**, or colours a data series"), the Two-Palette Rule (series-1 and series-3 are data tokens used as chrome), and the Don't against drifting toward gradient fintech. I computed the stops on Bench Paper: navy 13.61:1 pass, series-1 4.23:1 pass, **series-3 2.69:1 fail** against the 3:1 large-text floor — on the words "imposto de renda". B also found no `@media (forced-colors: active)` fallback anywhere in `globals.css`, so under Windows High Contrast the background-image drops while `color: transparent` persists and the h1 reads "Do extrato da B3 ao , tudo em um só lugar."
**Why it matters.** It is the least on-brand element on the page, it fails contrast on the most important words in the headline, and it can erase them entirely for high-contrast users. DESIGN.md already grants the hero the Display step as "the only place type is allowed to be the loudest thing on screen" — the gradient is loudness borrowed from a world this brand rejects.
**Fix.** Set the span in Instrument Ink (16.97:1) and let Archivo 800 at 60px carry the emphasis. If the gradient is a deliberate brand decision, amend DESIGN.md to sanction it and fix the aqua endpoint — but as shipped it is an undocumented violation with an AA failure.

### [P2] Structural accessibility and the one unresponsive section
**What.** Three mechanical defects B established. **There is no `<main>` landmark anywhere** — `page.tsx` has none, `layout.tsx` has none, and `AppShell.tsx:40` early-returns for `/` and `/login`, bypassing the shell's own `<main>` at `:132`; all body content is orphaned outside any landmark. **The sticky header nav anchors** (`:409`, `:415`) have zero padding — 20px tall, failing even the WCAG 2.2 AA 24×24 floor, inside a 64px header with 44px of unused vertical space. **The stats band** (`:498`) is `grid grid-cols-3` unprefixed — the only section on the page with no mobile treatment; at 360px each column is ~98px and the 25-29 character labels wrap to 4-5 ragged lines. There is also no skip link and the wordmark is not a link, so there is no home affordance.
**Why it matters.** The `<main>` omission affects every screen-reader and reader-mode user on the product's most public page, and it is a one-line fix.
**Fix.** Wrap the sections in `<main>`. Give the nav anchors `py-2 -my-2`. Change the stats grid to `grid-cols-1 sm:grid-cols-3`.

## Persona Red Flags

**Jordan (confused first-timer).** Clicks "Começar grátis" (`:466`) and lands on a page called `/login` — expected to create an account, got a sign-in surface; "Entrar" (`:421`) points to the identical URL, so nothing tells him which he is doing. "Colheita de prejuízo" appears in the hero subhead (`:459`) 100 lines before its plain-language explanation at `:358`. The pricing ladder's "Tudo do plano Grátis / Investidor" forces him to reconstruct the feature set across three columns, and nothing tells him the IRPF he came for is paid until he reads the Investidor column closely. Then he follows the instructions and uploads a PDF, which is rejected.

**Riley (deliberate stress tester).** Reads "Carteira +18,2% vs CDI +10,7%" (`:286`, `:291`) and asks whose portfolio that is — no peek carries an "exemplo ilustrativo" marker; the disclaimer exists only as an unrendered code comment at `:16`. Spots the "ao vivo" pulse over obviously mock data and downgrades everything else, including the correct DARF card. Scrolls to the footer looking for privacy and terms before uploading tax data and finds neither. Notices the ticker shows raw B3 quotes for a product whose own spec has not decided whether it may ever surface raw price series. Clicks "Assinar Trader" while logged out — outcome unverifiable from source, and a P2 in waiting if it bounces through a guard and loses the intent.

**Casey (distracted mobile user).** Below `sm` the header collapses to Brand + "Entrar" at 36px tall — the nav anchors are `hidden` with no hamburger and no drawer, so for the entire scroll past the hero the only persistent action offered is *log in*, on a product with no users, and the next "Começar grátis" is roughly eight sections away at `:667`. The stats band renders three ~98px columns of 4-5-line wrapped labels. Perpetual animations — 42s marquee, 6s float, 2.4s pulse — run indefinitely on battery, and the marquee's only pause control is mouse hover, which she does not have.

## Minor Observations

- **The ticker tape sits above the sticky header** (`:402` before `:405`) — the first pixel of the page is fake market data in motion, before the brand.
- **The duplicated marquee half is not `aria-hidden`** (`:95`), so screen readers announce 24 ticker/price/delta triplets, twice.
- **19 `.reveal` blocks ship at `opacity:0`** in the committed static export with no `is-visible` — with JS disabled or slow hydration, everything below the hero is invisible. `output: "export"` means this is the real served document. Needs a rendered/no-JS check.
- **`CountUp` SSG/hydration mismatch:** the shipped HTML contains `R$ 0` and `0%`; on a reduced-motion client the lazy initializer returns the final value at hydration, producing a React text mismatch at `:141` and `:500-502`. Also no `matchMedia` change listener, so toggling the OS setting after mount does nothing.
- **Two hover transforms escape the reduced-motion block:** `hover:-translate-y-1` at `:528` and `:612` — the block only nulls `animation` on five named classes.
- **Three more DESIGN.md conflicts** beyond the detector: `series-1` as chip chrome (`:243`), the status badge at `:303-305` rendering "Pronto" with no icon and a label outside the documented four, and gain/loss tokens on the non-signed verbs "Comprar"/"Vender" (`:215`, `:221`).
- **Heading levels are decoupled from size:** `h3` renders at 24-30px Archivo 800 (`:555`), 16px Plex 600 (`:532`, `:584`) and 14px Plex 600 (`:623`). The showcase section (`:542-565`) has no `h2`, so its three h3s nest under the *Features* heading in the outline.
- **The FEATURES grid re-covers four capabilities** the showcase deep-dives 60 lines later, and is the page's only chunking failure (6 items in one undifferentiated group).
- **Pricing cards lift on hover without a shadow** (`:612`), which reads as a jump rather than elevation.
- **The footer disclaimer is well-written and legally prudent** — it deserves a companion privacy line more than the page deserves another animation.

## Questions to Consider

1. **Why is the most generic peek in the hero?** What if it showed the loop itself — a backtest's after-tax curve feeding the same DARF card — the one image no competitor can truthfully render?
2. **Could the ticker tape become an honesty device instead of ambiance?** A tape of tax facts — "isenção R$ 20 mil · código 6015 · T+1 · JCP 15%" — would be brand-unique, would sidestep the undecided raw-price-series question, and would out-signal every fake quote on it.
3. **The product's proudest behaviour is reporting what it cannot see. Why doesn't the landing show it?** A peek containing "Não precificado: Futuro (2) — excluído do índice" would be the most trustworthy screenshot in Brazilian fintech, and only this product would dare ship it as marketing.
4. **IRPF season is the stated acquisition hook (Jan–May).** Should the page have a seasonal register — a deadline-aware hero — or is the evergreen page deliberately leaving the hook unbaited?
5. **If the pt-BR quant is the design center, does the page need a features grid at all,** or would three showcases plus steps plus pricing be the denser, more confident page this brand claims to be?
