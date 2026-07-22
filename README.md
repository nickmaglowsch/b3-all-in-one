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
separate venv at `/opt/taxcore`, so the import → portfolio → IRPF loop runs entirely
inside the containers — no host `../taxcore` checkout needed. taxcore's **data**
(`b3_imports/`, `data/`, `reports/`, `tests/`) is deliberately kept out of the image.

On boot the **api** runs `alembic upgrade head` before uvicorn (via the entrypoint,
gated on `RUN_MIGRATIONS=1`); the **worker** applies its own procrastinate schema.
Uploaded files land on a shared `uploads_data` volume mounted into both services at
`/data/uploads`, so files the api saves are visible to the worker's import jobs.

### End-to-end smoke

```
./scripts/docker_smoke.sh          # clean up --build, import 3 real extracts,
                                   # assert positions + IRPF, then tear down (keeps volumes)
./scripts/docker_smoke.sh --keep   # same, but leave the stack running
```

### Env vars (see `.env.example`)

| Var | Default (host) | In-image |
|---|---|---|
| `UPLOADS_DIR` | `./uploads` | `/data/uploads` (shared volume) |
| `TAXCORE_DIR` | *(empty → `../taxcore`)* | `/opt/taxcore` |
| `TAXCORE_PYTHON` | *(empty → `taxcore/.venv`)* | `/opt/taxcore/.venv/bin/python` |
| `RUN_MIGRATIONS` | *(unset)* | `1` on the api only |

Leaving `TAXCORE_DIR`/`TAXCORE_PYTHON` unset keeps host runs (and `platform/tests`)
working against a local `taxcore/` checkout exactly as before.
