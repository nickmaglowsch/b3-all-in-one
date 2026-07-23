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
docker compose up -d postgres redis        # infra only
docker compose --profile app up --build -d # + api & worker (self-contained images)
```

The images bake in taxcore's code (`scripts/`, `methodologies/`) and its deps in a
separate venv at `/opt/taxcore`, plus the backtest engine's code (`b3_pipeline/`,
`backtests/`, `scripts/`) and its deps at `/opt/engine`, so the import → portfolio →
IRPF loop AND the backtest bench run entirely inside the containers — no host
`../taxcore`/`../engine` checkout needed. taxcore's **data** (`b3_imports/`, `data/`,
`reports/`, `tests/`) and the engine's **data/db/tests/ui/Rust parser**
(`b3_pipeline_rs/`) are deliberately kept out of the image — backtests read the market
plane from Postgres, not from files.

### Backtest bench

- `GET /strategies` — the strategy gallery (name, kind `code`|`spec`, params/defaults).
  Cached in-process; discovery runs inside the engine venv.
- `POST /backtests {strategy, params?, start?, end?, freq?}` — queues a run; the worker
  shells into the engine venv (`scripts/run_backtest_json.py`), stores metrics on the
  row and artifacts (`metrics.json`, `curves.csv`) under `ARTIFACTS_DIR/{run_id}/`
  (shared `artifacts_data` volume, mirroring `uploads_data`).
- `GET /backtests`, `GET /backtests/{id}`, `GET /backtests/{id}/curves` — history, a run
  row + metrics, and the pre/after-tax equity + drawdown + turnover series.
- `GET /market/status` — price-plane row count, distinct tickers, min/max date, and
  per-table freshness for `corporate_actions`/`fundamentals_pit` (0-row tables reported
  honestly).

**Network:** a backtest run fetches **CDI daily from the Brazilian Central Bank**
(`api.bcb.gov.br`, required by `build_shared_data`, reliable) and **never touches
yfinance** — the IBOV benchmark is stubbed because the bench reports the strategy's own
pre/after-tax metrics, not an index comparison. Set `ENGINE_BENCH_OFFLINE=1` (compose
sets it on api+worker; `platform/tests` set it too) to stub CDI as well and run fully
offline — valid for price-only strategies (`LowVol`, `MomSharpe`, …). ETF/regime
strategies still need real benchmarks and are not supported on the offline path.

On boot the **api** runs `alembic upgrade head` before uvicorn (via the entrypoint,
gated on `RUN_MIGRATIONS=1`); the **worker** applies its own procrastinate schema.
Uploaded files land on a shared `uploads_data` volume mounted into both services at
`/data/uploads`, so files the api saves are visible to the worker's import jobs.

### End-to-end smoke

```
./scripts/docker_smoke.sh          # clean up --build, import 3 real extracts,
                                   # assert positions + IRPF + a backtest run,
                                   # then tear down (keeps volumes)
./scripts/docker_smoke.sh --keep   # same, but leave the stack running
```

### Env vars (see `.env.example`)

| Var | Default (host) | In-image |
|---|---|---|
| `UPLOADS_DIR` | `./uploads` | `/data/uploads` (shared volume) |
| `ARTIFACTS_DIR` | `./artifacts` | `/data/artifacts` (shared volume) |
| `TAXCORE_DIR` | *(empty → `../taxcore`)* | `/opt/taxcore` |
| `TAXCORE_PYTHON` | *(empty → `taxcore/.venv`)* | `/opt/taxcore/.venv/bin/python` |
| `ENGINE_DIR` | *(empty → `../engine`)* | `/opt/engine` |
| `ENGINE_PYTHON` | *(empty → `engine/.venv`)* | `/opt/engine/.venv/bin/python` |
| `ENGINE_DATABASE_URL` | `…@localhost:5432/b3` | `…@postgres:5432/b3` |
| `ENGINE_BENCH_OFFLINE` | *(unset)* | `1` on api+worker (stub benchmarks) |
| `RUN_MIGRATIONS` | *(unset)* | `1` on the api only |

Leaving `TAXCORE_DIR`/`TAXCORE_PYTHON`/`ENGINE_DIR`/`ENGINE_PYTHON` unset keeps host
runs (and `platform/tests`) working against local `taxcore/`/`engine/` checkouts.
