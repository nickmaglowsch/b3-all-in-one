# b3-all-in-one

SaaS platform combining a tax-aware B3 backtest bench with portfolio self-care and IRPF.
Full spec in `spec/`. Local-only for now: everything runs via docker compose, no deploys.

## Layout

- `spec/` — the platform spec (read `spec/00-overview.md` first)
- `engine/` — vendored from `b3-pipeline-data-and-backtest-framework` (data pipeline +
  backtest engine + Rust COTAHIST parser). Being ported SQLite → Postgres.
- `taxcore/` — vendored from `b3-portfolio` (B3 extract parsing, IRPF engine, TLH,
  methodologies), including real data fixtures for golden tests.
- `platform/` — the new FastAPI application (auth, tenants, jobs, API).

## PRIVACY — read before pushing anywhere

`taxcore/` contains **real personal financial data** (B3 extracts, trades, the owner's
IRPF report) used as golden-test fixtures with the owner's consent. This repo must stay
**private** until those fixtures are anonymized/replaced.

## Quickstart

```
cp .env.example .env
docker compose up -d postgres redis     # infra only
docker compose --profile app up -d      # + api & worker (once platform/ exists)
```
