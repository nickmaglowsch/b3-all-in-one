# Storage port: SQLite → Postgres

**Status:** wave 1 (backend resolver + dialect) done; wave 2 (test-suite health,
PG additive migration, research/audit backend-awareness, PG ingestion smoke) done.
See "Wave 2" at the bottom for the latest pass.

# Wave 1: backend resolver + dialect

Ports the `engine/` storage layer to run on either SQLite (unchanged default) or
Postgres 16, selected at runtime by the `ENGINE_DATABASE_URL` env var. Covers
spec `01-architecture.md` "Storage decisions" and `02-backtesting.md` migration
checklist items 1–2. Domain logic (tax, corporate actions, simulation) is
untouched — only connection resolution and SQL dialect changed.

## How the backend is selected

`b3_pipeline.storage.get_connection(db_path=None)` is the single resolver.
Precedence (the default keeps today's SQLite behaviour byte-for-byte):

1. `ENGINE_DATABASE_URL=postgresql://…` → Postgres (global switch, wins over any
   passed path — this is what flips the whole engine to PG).
2. `db_path` is a `postgresql://` DSN → Postgres.
3. `db_path` given (a file path) → SQLite at that path (unchanged).
4. `ENGINE_DATABASE_URL` is a `sqlite://`/bare path → SQLite there.
5. neither → SQLite at `config.DB_PATH` (unchanged).

`sqlite://…` prefixes are accepted and stripped. `postgres://` is treated the
same as `postgresql://`. `psycopg` (v3) is imported lazily — only in PG mode —
so SQLite-only environments never need it.

## What's ported

- **`b3_pipeline/storage.py`** — resolver + dialect helpers, all upserts/reads.
  - Helpers: `is_postgres`, `using_postgres`, `read_sql`, `execute`,
    `executemany`, `df_records`, `_xlate` (placeholder translation), `_pg_ddl`
    (DDL translation).
  - `init_db()` runs the CREATE sequence through `_pg_ddl` on Postgres; the
    SQLite-only `_migrate_schema()` (PRAGMA/ALTER) is skipped on PG (it starts
    from the full schema). `INDEX_CVM_COMPANIES_TICKER_ROOT` is created in the PG
    branch (SQLite still creates it inside `_migrate_schema`).
  - `INSERT OR REPLACE` → `INSERT … ON CONFLICT (pk) DO UPDATE SET …` for
    `corporate_actions`, `detected_splits`, `stock_actions`, `skipped_events`.
    (`prices`, `fetch_failures` already used `ON CONFLICT`.) `ON CONFLICT` is
    valid on both backends, so a single statement serves both.
- **`b3_pipeline/cvm_storage.py`** — same treatment: `INSERT OR REPLACE` →
  `ON CONFLICT` for `company_tickers_pit`, `cvm_filings`, `fundamentals_pit`,
  `fundamentals_monthly`, `company_isin_map`; NaN→None via `storage.df_records`
  for the dict-record upserts (NULL and NaN read back identically as NaN);
  executes routed through `storage.execute`/`executemany`.
- **`backtests/core/data.py`** — all 8 read sites route through
  `storage.get_connection` + `storage.read_sql` (the read path the backtest
  engine and research loader use).
- **Connection-injection sites** now routed through the resolver:
  `b3_pipeline/main.py` (`_run_update_cnpj_map`),
  `ui/services/pipeline_service.py` (`get_db_stats`, `get_table_sample`),
  `ui/services/fundamentals_service.py` (`get_fundamentals_stats`),
  and the strategy loaders in `backtests/strategies/{sp500_b3, two_leg_value,
  besst_quality, teva_ativos_reais}.py` and `backtests/sp500_b3_index.py`.
- **`requirements.txt`** — added `psycopg[binary]>=3.1`.

### Dialect / DDL decisions

- `REAL` → `DOUBLE PRECISION` (SQLite REAL is 8-byte; PG REAL is only 4-byte —
  DOUBLE PRECISION preserves values). `INTEGER` → `BIGINT` (SQLite INTEGER holds
  8-byte ints). SQLite type affinity makes these no-ops for the SQLite path, but
  DDL is only rewritten in PG mode to keep the SQLite CREATE text identical.
- `FOREIGN KEY (filing_id) REFERENCES cvm_filings` is stripped on Postgres.
  SQLite leaves FKs unenforced by default (and the tests set
  `PRAGMA foreign_keys = OFF`), so dropping it keeps behaviour identical while
  avoiding PG rejecting `fundamentals_pit` rows whose parent filing isn't loaded
  first.
- Placeholder translation (`?`→`%s`, `:name`→`%(name)s`) happens in `_xlate`,
  applied by `read_sql`/`execute`/`executemany`. `pandas.read_sql_query` emits a
  UserWarning for non-sqlite DBAPI connections; it's suppressed in `read_sql`.
- Date columns come back as `date` objects from PG vs strings from SQLite — the
  values are identical and every consumer normalises via `pd.to_datetime`. Wide
  frames differ only in DatetimeIndex resolution (`[s]` vs `[us]` under pandas
  3.x); same instants.

## Proof

`tests/test_postgres_storage.py` (auto-skipped unless `ENGINE_DATABASE_URL` is
`postgresql://`). It creates a throwaway `engine_test` database via the
superuser DSN, builds the schema, and round-trips a realistic sample:
prices insert+upsert+read, corporate actions, `fundamentals_pit` write + the
`data.py` PIT read path, `load_b3_data`, and asserts every pandas frame equals
the SQLite equivalent. All 4 tests pass on Postgres 16.

Run: `ENGINE_DATABASE_URL=postgresql://postgres:devpassword@localhost:5432/b3 \
pytest tests/test_postgres_storage.py -v`

The regression suite is fully green in default (SQLite) mode:
**470 passed, 20 skipped, 0 failed, 0 errors** (pandas 3.0.3). The two
pre-existing artifacts were test-only and are fixed in wave 2 (see below) — no
engine output semantics changed.

## What still assumes SQLite (honest list)

1. **`scripts/audit_db.py`** — standalone diagnostic CLI; uses
   `sqlite3.connect` + `conn.row_factory = sqlite3.Row`. Still SQLite-only for
   the actual auditing (dev tool, off the critical path). Wave 2 added a hard
   guard: it now exits 2 with an "sqlite-only diagnostic" message when
   `ENGINE_DATABASE_URL` is a `postgres://`/`postgresql://` DSN instead of
   silently opening the SQLite file.
2. **`b3_pipeline/storage.py::_migrate_schema`** — SQLite-only (PRAGMA
   `table_info` + `ALTER TABLE ADD COLUMN`). Wave 2 added `_migrate_schema_pg`
   (native `ALTER TABLE … ADD COLUMN IF NOT EXISTS`), run from `init_db` after
   the PG `CREATE`. It mirrors the SQLite additive columns
   (`quotation_factor`, `cvm_companies.{listing_date,delisting_date,ticker_root}`
   + `ticker_root` backfill/index, `{fundamentals_pit,fundamentals_monthly}`
   `.net_income_ttm`, `fundamentals_pit.{shares_on,shares_pn}`). It is a no-op on
   a fresh PG DB (the full CREATE already has every column) and idempotent. Still
   additive-only — column *renames/drops/type changes* have no migration path on
   either backend.
3. **`research/main.py`** — wave 2 made it backend-aware: the banner prints the
   PG DSN in Postgres mode (not the SQLite `config.DB_PATH`), and the
   missing-DB guard (`config.DB_PATH.exists()`) is skipped when
   `storage.using_postgres()` (the resolver owns the connection in PG mode).
   `research/data_loader.py` was already fine (routes through `data.py`).
4. **The existing tests** deliberately use `sqlite3.connect(":memory:")` /
   temp files directly — they are the SQLite regression net and are meant to run
   in default mode; not a coupling to remove.
5. **Default DB_PATH constants** (`config.py`, `research/config.py`,
   `strategy_returns._DEFAULT_DB`, `run_single_backtest.py`,
   `leverage_qdl_blend.py`, `strategies/{top_mcap,qdl}.py`, the UI services'
   `DB_PATH`) are still SQLite file paths. They're only the SQLite fallback —
   the resolver overrides them when `ENGINE_DATABASE_URL` is Postgres — but they
   remain SQLite-flavoured literals.

## Wave 2 (test health, PG migration, backend-awareness, ingestion smoke)

- **SQLite suite fully green** (was 1 failed / 4 errors, both test-only artifacts):
  - `tests/test_load_b3_data_pivot.py` asserted `datetime64[ns]`; pandas 3 pivots
    a `DatetimeIndex` to `[us]`/`[s]` (same instants). Made the assertion
    resolution-agnostic (`pd.api.types.is_datetime64_any_dtype`). No engine
    semantics changed — only the index *resolution*, which every consumer
    already normalises via `pd.to_datetime`.
  - `tests/test_shared_data_fundamentals.py` embedded the underscore literal
    `10_000_000_000.0` inside a raw SQL string; the bundled SQLite tokenizer
    (< 3.46) rejects `_` digit separators. Rewrote it to `10000000000.0`
    (the other underscore literals are Python params, which are fine).
- **`_migrate_schema_pg`** — see honest-list item 2.
- **`research/main.py` / `scripts/audit_db.py`** — see honest-list items 1 & 3.
- **Postgres ingestion smoke** (`engine_smoke` DB, real B3/BCB network):
  `python -m b3_pipeline.main --year 2025 --skip-corporate-actions` (the
  per-company B3 corporate-actions crawl is rate-limited and has no per-ticker
  flag, so it was skipped). Result: `prices` 83,722 rows / 432 tickers /
  2025-01-02→2025-12-30, `stock_actions` 27, `detected_splits` 27 (splits found
  from the price series), other tables 0. `adj_close` fully populated (0 NULL);
  PETR4 loads via `backtests.core.data.load_b3_data` as 250 trading days
  (36.77 → 30.82). `download_cdi_daily` returns the BCB SGS-12 series
  (~0.000483/day ≈ 13% annualised). Re-running the ingestion is idempotent —
  identical counts, 0 duplicate `(ticker, date)` rows.

## Next wave

- Connection pooling for Postgres: `get_connection` opens a fresh `psycopg`
  connection each call — fine for the CLI/ingest pattern, but the API/worker
  layer will want a pool (`psycopg_pool`).
- Non-additive PG migrations (renames / type changes) still have no path — add a
  `schema_migrations` table only if/when a destructive change is actually needed.
- `b3_pipeline/main.py`'s final banner still prints `config.DB_PATH` (the SQLite
  fallback path) even in PG mode — cosmetic; the data is written to PG correctly.
  Same backend-awareness fix as `research/main.py` if it matters.
- Checklist items 3–6 (yfinance benchmark replacement, worker task wrapping,
  engine extensions for the portfolio loop) are out of scope.
