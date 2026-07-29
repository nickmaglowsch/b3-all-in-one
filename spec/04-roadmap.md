# 04 — Roadmap

Phases 0–4 (foundations, portfolio, IRPF, bench, the loop) all shipped. What each phase
was and where it landed is the codebase and `README.md`; what remains is below.

## Next

### Engine work for the loop (spec `05-strategy-linked-portfolio.md`, "Engine work")
Items 1–2 (`compute_trades()` extraction, advise-only `scripts/advise_weights.py`) shipped.
Still open, in order:

3. Dated cash-flow `contribution` Series, so what-if replays the tenant's real deposits on
   their real dates instead of a flat monthly figure.
4. Seeded initial `pt_pos` / `at_pos` / `loss_carryforward`, so a what-if can start from the
   book the tenant already holds rather than from cash.
5. Asset-class tax model — per-class rates and carry buckets, plus the ações isenção applied
   per **calendar month**. This also fixes a known inaccuracy in the bench itself: today the
   isenção is applied per rebalance row and FII uses a single flat rate.

### Postgres gaps
- **Connection pooling.** `storage.get_connection` opens a fresh `psycopg` connection per
  call. Fine for the ingest CLIs; the api/worker path wants `psycopg_pool`.
- **Non-additive migrations.** `_migrate_schema_pg` is additive-only (`ADD COLUMN IF NOT
  EXISTS`). Renames, drops and type changes have no path on either backend. Add a
  `schema_migrations` table only when a destructive change is actually needed.
- **`b3_pipeline/main.py`'s closing banner** still prints `config.DB_PATH` (the SQLite
  fallback) in Postgres mode. Cosmetic — the data is written to PG correctly.

### FATCOT quotation-factor support
See `engine/docs/todo.md`. The COTAHIST parser ignores B3's quotation-factor field, so
stocks that changed quotation basis get wrong adjustments. Currently papered over by
sanity-capping in `adjustments.py`, which silently discards legitimate adjustments.

### Still-open risks from `00-overview.md`
- **Encryption at rest and DB-level RLS** (risk 2). Per-tenant isolation and the LGPD
  data-subject routes are done (`08-privacidade-lgpd.md`); these two are not.
- **B3 data redistribution scope** (risk 1). The platform serves derived results only and
  yfinance is out of the engine, but no written legal opinion has been obtained. Gates any
  raw-feed or data-API surface.

### MCP server over the public API
A thin second client over the same endpoints and the same auth — not a separate backend.
The `b3_remote_mcp` container already deployed behind Caddy gets repointed at this API
rather than redeployed.

## Continuous
- Parser-drift watch (new B3 extract fixtures each format change).
- Yearly IRPF rule review (new ano-base parameters, códigos) — recurring, every ~Dec.
- Reference-table curation (new tickers, CNPJs, ETF classifications).

## Explicit non-goals
Day-trade support · broker order execution · raw market-data API · user-supplied Python
strategies · CEI/broker scraping · investment advice of any kind.
