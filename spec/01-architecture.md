# 01 — Architecture

## Guiding principle

The research code in both repos is good; the infra is single-user. So: **wrap, don't
rewrite.** Keep `backtests/core/*`, `methodologies/*`, and the tax/parsing logic as pure
Python libraries; replace only the storage layer, entry points, and configuration around
them. Every rewrite of domain logic (tax rules, corporate actions) is a chance to introduce
bugs the originals don't have — avoid it.

## System shape

Deployment target (surveyed, not hypothetical): the existing VPS — Oracle **ARM/aarch64**
box running docker compose with shared infra containers on a `shared` bridge network
(`~/infra/docker-compose.yml`): `infra_caddy` (TLS + per-domain reverse proxy),
`infra_postgres` (PostGIS 16), `infra_redis` (cache), `infra_alloy` (log shipping to
Grafana Cloud). Apps join the network as single containers; imobindex
(`real-estate-bloomber`) is the reference implementation of the whole pattern and this
platform copies it deliberately.

```
              Cloudflare (DNS/CDN)
  Browser ──▶ Next.js on Cloudflare via OpenNext (wrangler, imobindex pattern)
                   │ HTTPS, CORS (ALLOWED_ORIGINS/FRONTEND_URL env, as imobindex)
                   ▼
  VPS · docker compose · `shared` network
  ┌────────────────────────────────────────────────────┐
  │ infra_caddy ──▶ b3_api (FastAPI)                   │
  │ b3_worker ×N  (backtests, imports, IRPF, ingest)   │
  │ infra_postgres (PostGIS 16) — `b3` database        │
  │ infra_redis   (cache ONLY — see queue note)        │
  │ infra_alloy ──▶ Grafana Cloud (logs)               │
  └────────────────────────────────────────────────────┘
```

- **One language (Python) end-to-end on the backend** — both repos are Python; FastAPI wraps
  their functions directly. There is no native build step: the pipeline repo's Rust COTAHIST
  parser was removed (nothing shipped ever reached it: the image excluded the crate and every
  production call resolves to `engine/.venv`, which never had it built), so the images are
  pure Python and arm64 needs no cross-compilation.
- **Job queue on Postgres, not Redis.** The shared `infra_redis` runs
  `maxmemory 256mb --maxmemory-policy allkeys-lru` — it evicts arbitrary keys under
  pressure, so it must never hold jobs. Use a Postgres-backed queue (procrastinate, or
  plain `SELECT … FOR UPDATE SKIP LOCKED` workers): durable, transactional with tenant
  data, zero new infra. Redis stays for what it's good at here: caching rankings,
  market-status, session-adjacent lookups.
  <!-- ponytail: single VPS, Postgres queue; revisit (dedicated Redis/queue, second
       worker box) only when backtest volume outgrows this machine -->
- **Single-box sizing**: backtest workers share CPUs with the other apps on the VPS —
  worker concurrency is capped low (2–3) and per-tenant quotas (02) are the real
  admission control. Escape hatch later: a second worker VPS pointing at the same
  Postgres; nothing in the design assumes same-host.
- **Two data planes**:
  - **Market plane (shared, tenant-agnostic):** prices, corporate actions, fundamentals,
    CDI, benchmarks. One copy, read-only to tenants. Ingested by scheduled jobs (the
    pipeline repo's CLIs, run on a scheduler instead of by hand).
  - **Tenant plane (per-user, private):** imported extracts, trades, positions, cash
    events, cost-basis overrides, DARF ledger, strategy specs, backtest runs. Postgres
    with `tenant_id` on every row + row-level security.
- **Async jobs** for anything slow: extract import, backtest run, IRPF generation, market
  ingestion. The Postgres-backed queue with per-tenant quotas replaced the pipeline repo's
  thread-pool job runner (deleted along with its Streamlit UI); the job/status/log model it
  used is kept as the API contract.

## Storage decisions

| Data | Store | Why |
|---|---|---|
| Market data (prices, fundamentals, corp actions) | `b3` database on shared `infra_postgres` (PostGIS 16 ≥ plain PG; Timescale only if ever needed) | Multi-reader; SQLite's single-writer is the pipeline repo's main blocker |
| Tenant transactional data + job queue | Same `b3` database, RLS by `tenant_id` | b3-portfolio's CSVs map 1:1 to tables (below); jobs transactional with data |
| Raw uploaded extracts | Docker volume on the VPS, encrypted at rest, per-tenant prefix | Small files (Excel); reprocessable when parsers improve |
| Backtest run artifacts (curves, weights) | Docker volume + `runs` metadata table | Mirrors pipeline repo's `results/<run>/metadata.json` layout |
| Cache (hot frames for backtests) | Local parquet/Arrow cache per worker | `shared_data.py` bundle build is the expensive step |
| Backups | Nightly `pg_dump` + volume snapshot → Cloudflare R2 | Offsite; R2 fits the existing Cloudflare account, no egress fees |

<!-- ponytail: VPS volumes instead of S3-style object storage; move artifacts to R2
     only if disk pressure or a second host makes local files awkward -->

New database via the existing `~/infra/init-databases.sh` pattern — but with the app
user's password from an env var, not inline in the script (the current script embeds one
in plaintext; don't copy that).

### Market-plane schema
Direct port of the pipeline repo's 12 SQLite tables (`b3_pipeline/storage.py`,
`cvm_storage.py`): `prices`, `corporate_actions`, `stock_actions`, `fundamentals_pit`,
`company_isin_map`, `company_tickers_pit`, `fundamentals_monthly`, etc. Porting work =
dialect changes (`INSERT OR REPLACE` → `ON CONFLICT DO UPDATE`) + connection injection.
`storage.get_connection()` already accepts a path — extend to accept a DSN.

### Tenant-plane schema
Direct port of b3-portfolio's CSV schemas, plus tenancy and provenance:

- `imports` — uploaded file, format detected (Negociação/Movimentação), parser version, status
- `trades` — date, side, market, broker, ticker, qty, price, amount, `import_id`, signed_qty
- `positions` — materialized from trades+events (ticker, qty, avg_cost, total_cost, market);
  recomputed on import, never hand-edited
- `cash_events` — event_category (dividend/JCP/rendimento/amortização/reembolso/option premium/…)
- `cost_basis_overrides` — manual entries (IPOs, transfers), per-tenant
- `darf_payments` — apuração ledger
- `strategy_specs` — user YAML specs (validated against DSL before save)
- `runs` — backtest runs: spec ref, params, status, metrics, artifact pointer

### De-hardcoding b3-portfolio constants
`UNITS_NOT_FII`, `TICKER_RENAMES`, per-ticker ETF classifications, `BROKER_CNPJ`,
`ISSUER_CNPJ` currently encode one user's holdings. These become **platform reference
tables** (shared plane), curated and versioned, with a per-tenant override table. This is
new work but small, and it's the single biggest generalization required.

## Multi-tenancy & security

- Auth: **Google OAuth via `fastapi-users[sqlalchemy]` + `httpx_oauth.GoogleOAuth2`,
  copied from imobindex** (`backend/app/auth/google.py`, routers mounted at `/auth/jwt` +
  `/auth/google`). Proven on this exact infra; no managed auth provider needed. JWT
  carries `tenant_id`. Config via env: `GOOGLE_OAUTH_CLIENT_ID/SECRET`, `SECRET_KEY`,
  `FRONTEND_URL`, `ALLOWED_ORIGINS`.
- Billing: **Stripe, imobindex pattern** — subscription price IDs per tier
  (`STRIPE_PRICE_ID`-style env vars for Pro monthly/annual and Quant), webhook endpoint
  with `STRIPE_WEBHOOK_SECRET`, entitlements column on the tenant row updated by webhook.
- Postgres RLS on every tenant table; API sets `tenant_id` from the token, never from input.
- Uploaded extracts and tax reports encrypted at rest; tenant data export + delete endpoints
  (LGPD arts. 18/16).
- User strategy specs are safe by construction: the DSL is `ast`-parsed with a whitelist
  (`signal_dsl.py`), no `eval`, no user Python. **Code strategies (StrategyBase subclasses)
  are platform-authored only** — never accepted from users.
- Backtest jobs: per-tenant concurrency quota, CPU/time limits, results private to tenant.

## API surface (v1 sketch)

```
POST /imports                    upload extract → job
GET  /portfolio/positions        GET /portfolio/status  GET /portfolio/income
GET  /portfolio/benchmark        GET /portfolio/sector-exposure
POST /irpf/reports?year=2025     → job → GET /irpf/reports/{id}
GET  /darf  POST /darf           apuração ledger
POST /tlh/plans                  non-interactive TLH plan (params in body, no stdin)
GET  /strategies                 platform + own specs
POST /strategies                 upload/validate YAML spec
POST /backtests                  {strategy_id|inline spec, params} → job
GET  /backtests/{id}             status, metrics, artifact URLs
GET  /rankings/{methodology}     equities/FII rankings (shared, cached)
GET  /market/status              data freshness per source
```

An MCP server (per the b3-portfolio plan, a prototype already exists) is a thin second
client over this same API — same auth, same endpoints, exposed as tools. Not a separate
backend.

## Frontend

Next.js deployed to Cloudflare via **OpenNext** (`wrangler.toml` → `.open-next/worker.js`,
exactly as imobindex's frontend; env vars set in the Cloudflare dashboard). Talks to the
API on its own subdomain over CORS. Three areas matching the tiers: **Portfolio**
(dashboard, positions, income, imports), **IRPF** (report viewer with copy-per-field,
DARF ledger, checklist), **Bench** (strategy editor with YAML validation, run launcher,
tear-sheet viewer). The tear sheets are rebuilt in the frontend from the run's curves
endpoint; the engine's Plotly plotting module and Streamlit UI were both deleted.

## Deployment & operations

- **Compose layout**: `docker-compose.prod.yml` in the app repo declaring `b3_api` and
  `b3_worker` services, `networks: shared (external: true)`, `env_file: .env.prod`,
  healthcheck on `/health` — clone of `real-estate-bloomber/docker-compose.prod.yml`.
- **Routing**: one Caddyfile block on the existing `infra_caddy`
  (`api.<domain> { reverse_proxy b3_api:8000 }`). TLS is automatic. The deployed
  `b3_remote_mcp` container (the b3-portfolio MCP prototype, already live behind Caddy on
  this box) gets repointed at the new API in Phase 4 instead of redeployed from scratch.
- **Images**: built for linux/arm64 (the box is aarch64) — CI cross-build or build on the
  VPS. Pure-Python images, so no per-arch native build step.
- **Logs/monitoring**: stdout JSON logs — `infra_alloy` already ships all container logs
  to Grafana Cloud; no logging stack to build. Alerting on job-queue depth and ingestion
  freshness via Grafana.
- **Ingestion schedule**: a scheduler process in the worker container (cron-style,
  e.g. procrastinate's periodic tasks) — daily prices/corp-actions/CDI, weekly CVM.
- **Secrets**: `.env.prod` on the VPS (as today's apps), never committed; Cloudflare
  dashboard for frontend vars.

## Ingestion & scheduling

The pipeline repo's CLIs become scheduled jobs (cron on the worker queue): daily prices +
corporate actions + CDI, weekly/quarterly CVM. Add freshness monitoring (`fetch_failures`
table already exists) and an admin status page. Fundamentus scraping (used by rankings)
runs as one shared scheduled fetch — never per-tenant — with the existing HTTP cache.
