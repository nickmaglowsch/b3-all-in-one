# B3 All-in-One — Platform Overview

## Vision

A single platform for the Brazilian retail/prosumer investor that closes the loop between
**research → investing → tax compliance**:

1. **Backtest bench** (from `b3-pipeline-data-and-backtest-framework`): survivorship-free B3
   price + fundamentals data, and a tax-aware backtest engine where strategies are authored
   as YAML/DSL specs — safe to accept from users.
2. **Portfolio self-care** (from `b3-portfolio`): import your real B3 Área do Investidor
   extracts, get positions with correct cost basis (splits, amortizations, options,
   renames), income tracking, benchmarking, allocation suggestions, tax-loss harvesting.
3. **IRPF** (from `b3-portfolio`): a copy-paste-ready IRPF report — Bens e Direitos,
   Rendimentos, Renda Variável month grid with isenção 20k / per-class loss carry / JCP
   gross-up — plus DARF apuração tracking.

The unique selling point is the **loop**: backtest a strategy under real Brazilian taxes,
follow it with your real portfolio, and the same tax engine that scored the backtest files
your IRPF. No competitor does all three (competitors do one slice each: Grana/IRPF,
QuantBrasil/backtests, Kinvo/tracking).

## Target users & modules

There are no tiers: entitlement is a set of modules, each priced by what it costs the
server to run.

| Module | User | Gets |
|---|---|---|
| `carteira` grátis | Retail investor | Import extracts, portfolio, market status |
| `irpf` R$19/mês | DIY investor at IRPF time | IRPF report, DARF tracking |
| `mercado` R$29/mês | Year-round portfolio owner | TLH planner, what-if, sleeves/target/rebalance |
| `indice` R$12/mês | Same | Carteira vs IBOV and CDI, daily |
| `bench` R$49/mês | Prosumer/quant hobbyist | Backtest bench, user-authored strategy specs |
| `empresarial` R$249/mês | Assessor / escritório | Every module, across unlimited client books |

Shipped modules and route gating are in `README.md`; entitlements are written only by the
Stripe webhook.

IRPF is the seasonal acquisition hook (Jan–May); backtesting and portfolio care are the
year-round retention product.

## Source repositories

| Repo | Crown jewel | Reused |
|---|---|---|
| `b3-pipeline-data-and-backtest-framework` (~26k LOC + Rust, 62 test files) | Tax-aware simulator (`backtests/core/simulation.py`), YAML strategy specs + safe `ast` DSL, PIT fundamentals pipeline | Engine + pipeline nearly as-is; infra is the lift |
| `b3-portfolio` (~7.6k LOC) | B3 extract parser (`scripts/load_b3.py`), IRPF engine (`scripts/irpf_2026.py`), TLH planner | Domain logic reused; storage/config must be rebuilt (CSV → DB, hardcoded personal constants → data-driven) |

Both repos carried their own productization notes. This spec superseded them and they were
deleted; `git log` has them if the original reasoning is ever wanted.

## Known hard risks (decide early)

1. **Data redistribution licensing** — B3 market data is likely not redistributable
   commercially. Mitigations in place: the platform serves *derived* results (curves,
   metrics, rankings) and never a raw price feed, and yfinance is out of the engine —
   benchmarks read the plane (IBOV from B3's own index series) and CDI comes from the BCB.
   Still open: a written legal opinion on redistribution scope, which gates any raw-feed or
   data-API surface.
2. **Financial-data custody / LGPD** — the platform holds users' full trade history and
   tax data. Per-tenant isolation, data export/delete and the privacy posture are done
   (`08-privacidade-lgpd.md`); encryption at rest and DB-level RLS are still open.
3. **Tax-correctness liability** — reports must keep the b3-portfolio framing: "conferir
   no informe", never invent CNPJs/values, show provenance for every number. Not tax advice.
4. **Parser drift** — B3 changes extract formats; parsing needs versioned parsers, a
   quarantine flow for unrecognized files, and test fixtures per format generation.

## Spec files

- `01-architecture.md` — system architecture, tenancy, data model, migration of both codebases
- `02-backtesting.md` — backtest bench as a product feature
- `03-portfolio-irpf.md` — portfolio self-care + IRPF module
- `04-roadmap.md` — phased implementation plan
- `05-strategy-linked-portfolio.md` — the loop: sleeves, tax-optimal rebalance advisor,
  what-if backtests on real cash flows
- `06-user-strategies.md` — user-authored strategy specs
- `08-privacidade-lgpd.md` — LGPD: registro de tratamento, direitos do titular, retenção
