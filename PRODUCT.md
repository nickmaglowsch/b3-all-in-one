# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

**Primary design center: the quant / prosumer investor** (`trader` tier). Someone who
authors strategies as YAML/DSL specs, queues backtests, reads equity and drawdown curves
closely, and compares runs against each other. When audiences conflict, the design
optimizes for this user: density, control, and charts that reward close reading beat
first-visit hand-holding.

Two other confirmed, paying audiences the product already serves:

- **Year-round portfolio owner** (`investidor`, R$29/mês) — has imported their real B3
  extracts and lives in Portfólio, Estratégia, Simulação, and TLH between tax seasons.
- **DIY investor at IRPF time** (`gratis` → `investidor`) — arrives Jan–May needing a
  copy-paste-ready declaration. Per `spec/00-overview.md`, IRPF is the seasonal
  acquisition hook; backtesting and portfolio care are the year-round retention product.

Everyone is a Brazilian retail or prosumer investor filing under Brazilian tax rules.

## Product Purpose

Close the loop between **research → investing → tax compliance** on one platform:
backtest a strategy under real Brazilian taxes, follow it with your real portfolio, and
have the same tax engine that scored the backtest file your IRPF.

Success is a user who does all three in one place across a full year — not a user who
imports once in April and leaves.

## Positioning

The mechanism a neighboring product cannot truthfully copy is **the shared tax engine**.
The taxes that score a backtest are the same taxes that produce the DARF and the ficha de
bens. Competitors each own one slice — Grana (IRPF), QuantBrasil (backtests), Kinvo
(tracking) — and none can claim continuity across all three, because none runs a
tax-aware simulator and an IRPF engine off the same code.

Supporting truth: survivorship-free B3 price and point-in-time fundamentals data, and
strategies authored as YAML/DSL specs evaluated through a safe `ast` DSL, which is what
makes user-submitted strategies safe to accept at all.

## Operating Context

- **The source document is the B3 Área do Investidor extract.** Users download `.xlsx`
  spreadsheets and upload them; the platform parses them. Exactly **two** report types are
  recognised — negociação and movimentação (`taxcore/scripts/load_b3.py` `sniff_kind`) —
  and within movimentação, 16 movement labels are categorised (`MOV_CATEGORY`), covering
  dividendos, JCP, rendimentos, amortizações and the quantity-only corporate actions.
  Uploads that are not `.xlsx` are rejected by both the client and the API. Nothing is
  entered by hand.
- **The tax calendar drives the year.** IRPF declaration season runs Jan–May. DARF is
  apurada month by month. The R$20k monthly isenção for ações, per-class loss carry-forward,
  and JCP gross-up are structural facts of every tax surface, not edge cases.
- **The informe de rendimentos is the user's check.** Reports are drafted to be verified
  against the broker's informe, then copy-pasted into the Receita Federal declaration.
- **Rebalancing is advisory.** The platform computes drift against target sleeves and
  emits an integer trade list with a tax overlay (harvest, T+1 rebuys, isenção usage).
  Nothing executes; no brokerage is connected.
- **Users hold multiple accounts across institutions.** Consolidation across them is part
  of the job, not a feature.

## Capabilities and Constraints

**Confirmed functionality** (all shipped): extract import and parsing; consolidated
positions with correct cost basis across splits, amortizations, options, and renames;
income/proventos tracking; a daily time-weighted portfolio index (base 100) against IBOV
and CDI; IRPF report (Bens e Direitos, Rendimentos, Renda Variável month grid) with DARF
apuração tracking; TLH planner; what-if simulation on real cash flows; strategy sleeves
with tax-optimal rebalance plans; a strategy gallery plus user-authored strategies; and
the backtest bench with pre/after-tax equity, drawdown, and turnover series.

**Technical shape:** Next.js App Router frontend exported as a static SPA (Cloudflare
Pages), FastAPI + Postgres backend on a VPS, Procrastinate worker for import and backtest
jobs. Auth is JWT via Google OAuth redirect, with a dev-login path locally.

**Tiers and gating:** `gratis` (imports, portfolio, market) < `investidor` R$29/mês
(IRPF, TLH, what-if, sleeves/target/rebalance/index) < `trader` R$79/mês (backtests, user
strategies). Gated routes answer `402` with `plan_required`; the frontend turns any 402
into the upgrade dialog. The strategy gallery stays public. Entitlements are written only
by the Stripe webhook — checkout never grants access.

**Honest-gaps constraint:** the product reports what it cannot see rather than papering
over it. The portfolio index returns an `excluded` block naming what it could not price
(Futuro, Renda Fixa, unpriced tickers) and a `coverage.clamped` flag when the chart starts
later than the user's real history. Zero-row tables are reported as zero. Benchmarks that
are unavailable are reported unavailable, never drawn flat. This is a product commitment,
not an implementation detail.

**Terminology is Brazilian tax and market vocabulary, untranslated:** proventos, DARF,
isenção, ficha de bens, informe de rendimentos, apuração, prejuízo a compensar, JCP,
FII, IBOV, CDI, TLH (colheita de prejuízo).

**Explicitly undecided:**

- Whether the platform may ever surface raw price series, or must serve only derived
  results (curves, metrics, rankings). `spec/00-overview.md` flags B3 and yfinance data as
  likely non-redistributable commercially, but this was **not** confirmed as a binding
  product commitment. Treat as open; do not design a raw-feed view against it either way
  without deciding first.
- Whether any accessibility standard (WCAG level, specific user needs) is required. No
  product-specific requirement has been established.

## Brand Commitments

- **Name:** "Quantax" — `Quant` × `Tax`, the two halves of the product in one word.
  Page title `Quantax`. The old "B3 · tudo-em-um" was dropped because `B3` is a
  registered mark of B3 S.A. and naming the product after it invites a takedown;
  `B3` still appears in body copy, which is nominative use and fine.
- **Language: pt-BR only, binding.** No i18n, no translation layer. Copy, currency, date
  formats, and tax vocabulary are Brazilian Portuguese and stay that way.
- **Voice:** direct, plain, first-person-plural where it helps, never salesy or corporate.
  Confirmed in shipped copy: "A gente lê negociações e movimentações — nada à mão",
  "O app calcula o desvio e sugere as ordens para voltar ao rumo — sem chute."
- **Type:** Archivo (display) + IBM Plex Sans (text) + IBM Plex Mono (numerals), already
  wired in `frontend/app/layout.tsx`.

## Evidence on Hand

**The product is pre-launch. There are no real users, no paying customers, no
testimonials, no usage metrics, and no reference logos.** Stripe is wired with real test
prices and the stack deploys, but nobody is paying yet. Future work must not fabricate
customer counts, quotes, logos, AUM figures, growth numbers, or "trusted by" proof. The
landing page's stats stay descriptive of capability (e.g. "2 relatórios da B3: negociação e
movimentação", "16 tipos de movimentação reconhecidos", "3 passos do extrato ao IRPF"),
never quantitative social proof. Every figure on that page must be traceable to the
parser; an earlier "7 relatórios" and "100% dos seus proventos rastreados" were removed
precisely because neither survived that check.

**All test data is synthetic.** The B3 extracts under `taxcore/b3_imports/`, the CSVs
derived from them, and the IRPF golden are fabricated by
`taxcore/tests/golden/make_fixtures.py`. Only ticker symbols and institution names are
real public market data. Screenshots and demos must never be presented as a real person's
book.

**Real assets that do exist:** the working application at eight surfaces, `spec/` (six
documents), a live CDI series from the Brazilian Central Bank (`api.bcb.gov.br`), and
vendored Fundamentus fundamentals snapshots.

## Product Principles

1. **Never invent a number.** Every value shows its provenance; nothing is estimated into
   existence. No fabricated CNPJs, values, customers, or proof.
2. **Report the gaps as loudly as the results.** What the platform could not see —
   unpriced tickers, missing benchmarks, clamped history, empty tables — is part of the
   answer, not a blemish to hide.
3. **The user files; we draft.** Output is drafted to be verified against the informe and
   copy-pasted into the declaration. This is not tax advice, and rebalance plans are
   advisory — nothing executes.
4. **Density earns its keep.** The primary user reads closely and wants control. Prefer
   information the expert needs over reassurance the novice might like.
5. **The loop is the product.** Any surface that treats backtest, portfolio, or IRPF as a
   standalone tool has lost the only thing competitors cannot copy.

## Accessibility & Inclusion

**Data custody is a user-facing commitment.** Per `spec/00-overview.md` risk 2, the
platform holds users' full trade and tax history. LGPD posture — data export, account and
data deletion, and a clear privacy statement — must be reachable in the interface, not
only backend policy.

**Not yet built:** no export or account-deletion route exists in `platform/` today. Future
work must build these surfaces, and must not imply they already exist.
