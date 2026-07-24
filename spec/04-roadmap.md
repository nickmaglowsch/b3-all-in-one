# 04 — Roadmap

Phased so each phase ships something usable and de-risks the next. Estimates assume one
developer + AI agents, part-time; treat as relative sizing.

## Phase 0 — Foundations & de-risking (2–4 wks)
- Monorepo `b3-all-in-one`: `platform/` (new FastAPI app), `engine/` (pipeline repo's
  `b3_pipeline` + `backtests` as an installable package), `taxcore/` (b3-portfolio's
  parsing/tax logic as an installable package). Vendored via git subtree or path deps —
  keep upstream repos alive as research benches.
- **Golden-file test suite for `taxcore`** (load_b3 + irpf) from real anonymized outputs —
  before any refactor.
- Postgres port of the market-plane schema; env-driven DB config across all hardcoded-path
  sites. Keep the 62-file engine test suite green.
- Legal spike: B3 data redistribution posture, yfinance replacement, LGPD checklist.
  Outcome gates what Phase 2+ can show publicly.
- Docker + CI (neither repo has either): arm64 images (the VPS is aarch64, incl. the
  Rust wheel), `docker-compose.prod.yml`-style deploy joining the VPS `shared` network,
  Caddyfile block, `b3` database on `infra_postgres`.

**Exit criteria:** engine tests pass on Postgres; irpf golden tests pass from `taxcore`;
scheduled ingestion job populates the shared market plane daily.

## Phase 1 — Portfolio self-care, single-tenant → multi-tenant (4–6 wks)
- Auth: Google OAuth via fastapi-users (imobindex pattern), tenant model, RLS.
- Import pipeline (upload → parse → dedupe → positions) with raw-file retention and
  incomplete-history UX.
- Dashboard: positions, status, income, benchmark, sector exposure.
- Reference tables seeded from b3-portfolio's constants + admin curation page.

**Exit criteria:** a stranger can sign up, upload their extracts, and see a correct
portfolio. Dogfood with 3–5 real users' extracts (the parser will break; that's the point).

## Phase 2 — IRPF (6–8 wks, timed to land before the IRPF season, Jan)
- IRPF report generation (all 4 sections), copy-per-field viewer, PDF export.
- DARF ledger + monthly apuração alerts.
- TLH planner (non-interactive port).
- Billing: Stripe subscriptions + webhook (imobindex pattern): Free preview vs Pro full
  report. (Pix/local PSP only if Stripe conversion proves poor for BR cards.)

**Exit criteria:** paying users file their IRPF from the platform's report. This phase is
the revenue test — if IRPF doesn't convert, revisit before building the bench.

## Phase 3 — Backtest bench (6–8 wks)
- Worker queue with quotas; shared-data parquet cache.
- Strategy gallery (platform specs), YAML editor + validation, run launcher, tear-sheet
  viewer, run comparison.
- **Engine extensions from `05-strategy-linked-portfolio.md`** (they upgrade the bench
  itself and unblock Phase 4): asset-class tax model (per-class rates/carry, isenção per
  calendar month), `compute_trades()` extraction, advise-only path, dated cash-flow
  contributions, seeded initial state.
- Benchmark data source swap (from Phase 0 legal outcome) completed before public launch.
- Quant tier billing.

**Exit criteria:** a user clones a gallery strategy, edits a factor expression, runs it,
and reads an after-tax tear sheet — all in the browser.

## Phase 4 — The loop (5–6 wks) — spec: `05-strategy-linked-portfolio.md`
- Sleeve config (attach strategies with fixed percentages; compiles to `FixedWeight`
  specs for backtesting).
- Combined-target drift view + **tax-optimal rebalance advisor** (month-tranched plan:
  20k isenção scheduling, loss-harvest T+1 rebuys, all-class carry buckets).
- **What-if backtests**: real cash flows replayed through alternative strategies,
  after-tax; the old `etf_counterfactual` baselines (ETF mix / CDI / frozen basket)
  become plain `fixed_weight` specs rather than a parallel simulator.
- Per-sleeve virtual attribution reporting.
- MCP server over the public API (revive the existing prototype against real endpoints).

Note: the advisor and what-if depend on the engine extensions and portfolio data, not on
the bench UI — if IRPF conversion is strong and the Quant tier can wait, the engine-work
slice of Phase 3 plus Phase 4 can ship to the Pro tier before the full bench opens.

## Continuous
- Parser-drift watch (new B3 extract fixtures each format change).
- Yearly IRPF rule review (new ano-base parameters, códigos) — recurring, every ~Dec.
- Reference-table curation (new tickers, CNPJs, ETF classifications).

## Explicit non-goals (all phases)
Day-trade support · broker order execution · raw market-data API · user-supplied Python
strategies · CEI/broker scraping · investment advice of any kind.
