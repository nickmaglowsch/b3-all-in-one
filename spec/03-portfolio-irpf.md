# 03 — Portfolio Self-Care & IRPF

The product wrapper around b3-portfolio's domain logic. The tax math, extract parsing, and
corporate-action handling are ported as libraries; the CSV storage and stdout/stdin
interfaces are replaced.

## Import (the front door)

- Upload B3 **Área do Investidor → Extrato** Excel files (Negociação and Movimentação),
  auto-detected by column structure exactly as `load_b3.py` does today. Negociação takes
  precedence for trades; Movimentação supplies income, corporate actions, renda fixa.
- Import is an async job: parse → normalize → dedupe against existing trades → write
  trades/cash_events → recompute positions. Raw file retained (encrypted) for
  reprocessing when parsers improve; `parser_version` stamped on every derived row.
- All of `load_b3.py`'s hard-won handling is preserved: splits/bonificação/frações applied
  chronologically, amortizations reduce cost basis, option exercise/assignment rewritten as
  underlying trades, ticker renames, BTC lending excluded from income, reembolsos separated.
- **Coverage gaps surfaced, not hidden**: orphan sells → `incomplete_history` warnings
  become a first-class UI state ("your history starts after this position existed — add
  older extracts or a cost-basis override"). Manual cost-basis override form covers IPOs,
  spin-offs, transfers.
- Unrecognized file formats quarantine with a friendly error + ops alert (parser drift is
  an expected, permanent maintenance load).
- v1 input is B3 extracts only — no broker APIs, no CEI scraping (matches today's reality;
  B3 extracts are the one canonical, universal source).

## Portfolio dashboard

Ports of the existing scripts, as API + UI instead of stdout markdown:
- **Positions & status** — shipped as `GET /positions` (`routes.py`): current positions,
  avg cost, market value (from the shared market plane — replaces per-user snapshot
  CSVs), P&L, income received.
- **Benchmarking**: portfolio vs IBOV/CDI. Two complementary views, both shipped:
  `GET /portfolio/index` (`routes_portfolio_index.py`) charts the book the tenant
  actually holds as a daily time-weighted index — holdings rebuilt from their own
  trades + qty_events, valued at the market plane's raw closes, deposits and
  withdrawals neutralised; `POST /whatif` (`routes_whatif.py`) is the
  "same money, different strategy" point comparison. Benchmarks come from the
  engine (`scripts/bench_series.py`, Yahoo ^BVSP + BCB CDI) and degrade to
  portfolio-only when unavailable rather than drawing a flat line.
  Renda fixa (CDB, LCI, LCA, CRI, CRA, debêntures, Tesouro) has no feed, so it is
  priced from the tenant's own extract instead: `portfolio_index.accrue_rf`
  interpolates geometrically between the per-unit price of every aplicação and
  every resgate B3 reported — two real marks imply the rate exactly, so no
  indexer has to be known for a contract that has been redeemed. One still held
  has a single mark, and its indexer (`renda_fixa_terms`, tenant-entered, since
  neither extract carries it) accrues it forward from there against the BCB CDI
  and IPCA levels the engine stores on the plane under `$CDI` / `$IPCA`
  (`b3_pipeline/index_series.py`). Anything left undrawable — a custody freeze,
  an unmodelled amortização, a missing indexer — is named contract by contract,
  with its reason, in the response's `excluded.renda_fixa`.
- **Sector exposure** — not built. The taxcore script that did this read a Fundamentus
  CSV cache, which the market plane replaces; a port would be written fresh, not ported.
- **Rankings & allocation** — not built. Same story: the `rank*.py` + `methodologies/`
  scripts ranked off scraped snapshot CSVs and were deleted with them. The shape still
  holds if this is picked up — pure `score(df, weights)` over the plane, computed once
  per market-data refresh and cached, with per-tenant allocation suggestions on top —
  but there is no code to port, and `POST /rebalance` already covers "what should I buy".
- **TLH planner** (`tlh_plan.py`): refactor from stdin-interactive to parameterized —
  request carries the choices (target month, candidates to include/exclude), response is
  the harvest + T+1 rebuy plan. Same greedy algorithm. Long-term this merges into the
  rebalance advisor's tax-cost model (`05-strategy-linked-portfolio.md`) — the advisor
  subsumes TLH as its "pair gains with harvested losses" step; a standalone "harvest
  only" mode remains for users not following a strategy.

## IRPF module (the seasonal hook)

Port of `irpf_2026.py` (804 LOC), generalized:

### Report sections (as today)
1. **Bens e Direitos** — per-position cost basis at 31/12 prev/current year, with IRPF
   Grupo/Código classification (ações 03-01, FII 07-03, ETFs, BDR 03-09, renda fixa
   04-02/04-03), broker/issuer CNPJs from the shared reference tables.
2. **Rendimentos** — dividends (09), isenção-20k gains (20), reembolsos (26), FII
   rendimentos (99), JCP with 15% gross-up (10), renda fixa (06), LCI/LCA isentos (12).
3. **Renda Variável** — month-by-month realized P&L per asset class with the full rule
   set: ações 20k/month isenção with best-of-both-worlds carry treatment, FII 20% separate
   carry bucket, BDR/ETF 15%, per-class loss carryforward.
4. **Checklist** — "conferir no informe" items. The liability posture is a feature: every
   number shows provenance (which trades/events produced it), low-confidence CNPJs are
   flagged, nothing is invented.

### Product form
- UI report viewer with **copy-per-field** (each Bens e Direitos entry, each rendimento
  line formatted exactly for the IRPF program) + full markdown/PDF export.
- **Year selector**; report is regenerable any time from the trade history (it's a pure
  function of the data — no stored tax state to corrupt).
- **DARF ledger**: port of `darf_payments.csv` as CRUD + monthly apuração view; alert when
  a month's computed tax is due and unrecorded. (DARF *generation* with barcode: later.)
- **Multi-year**: loss carryforward chains across years automatically once history covers them.

### Generalization work (the real new code here)
- Hardcoded personal constants → shared reference tables with per-tenant overrides
  (see 01-architecture): units-vs-FII, ticker classifications, CNPJ maps, ETF tax types.
  Seed from the existing maps; add a curation/admin flow because these will need updates
  every year.
- Tax-rule parameters (rates, isenção threshold, códigos) keyed by ano-base, since rules
  change (e.g. any future reform) — table-driven, not code-forked per year.
- **Test the tax core first**: `irpf_2026.py` and `load_b3.py` currently have no tests.
  Before porting, build a golden-file suite from the existing repo's real outputs
  (anonymized) — this is non-negotiable for a product whose value is tax correctness.

## Out of scope (v1)
- ~~Day-trade taxation~~ — now apurado (taxcore `realizacao`: same ticker, same day,
  same broker → 20%, own loss bucket), because leaving it out did not leave it
  untaxed: it billed day trades as isento swing sales and corrupted the swing PM.
- Automatic DARF emission/payment, e-CAC integration.
- ~~IRRF "dedo-duro" auto-deduction~~ — now estimated from the sales (0,005% /
  1% day trade, Lei 11.033 art. 2º) and deducted in the monthly DARF, labelled as an
  estimate to confirm against the notas de corretagem.
- Fundamentus-scrape-dependent features at scale without ToS review.
