# Storage port: SQLite → Postgres (wave 1)

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

The 61-file regression suite stays green in default (SQLite) mode — see the
report for baseline vs final (the pre-existing failures are pandas-3.0.3 /
bundled-SQLite artifacts, not from this port).

## What still assumes SQLite (honest list)

1. **`scripts/audit_db.py`** — standalone diagnostic CLI; uses
   `sqlite3.connect` + `conn.row_factory = sqlite3.Row`. Not ported (dev tool,
   off the critical path). SQLite-only until someone needs it against PG.
2. **`b3_pipeline/storage.py::_migrate_schema`** — SQLite-only (PRAGMA
   `table_info` + `ALTER TABLE ADD COLUMN`). Postgres always starts from the full
   `CREATE`, so there is **no incremental-migration path for Postgres yet** — a
   schema change means recreating tables. A real PG migration story is wave-2
   work.
3. **`research/main.py`** — prints `config.DB_PATH` and guards on
   `DB_PATH.exists()`, which is false in Postgres mode. `research/data_loader.py`
   itself is fine (routes through `data.py`). The `.exists()` guard needs a
   `storage.using_postgres()` check.
4. **The existing 61 tests** deliberately use `sqlite3.connect(":memory:")` /
   temp files directly — they are the SQLite regression net and are meant to run
   in default mode; not a coupling to remove.
5. **Default DB_PATH constants** (`config.py`, `research/config.py`,
   `strategy_returns._DEFAULT_DB`, `run_single_backtest.py`,
   `leverage_qdl_blend.py`, `strategies/{top_mcap,qdl}.py`, the UI services'
   `DB_PATH`) are still SQLite file paths. They're only the SQLite fallback —
   the resolver overrides them when `ENGINE_DATABASE_URL` is Postgres — but they
   remain SQLite-flavoured literals.

## Next wave

- Give Postgres a real migration story (a `schema_migrations` table or idempotent
  `ALTER`s) so `_migrate_schema`'s intent isn't SQLite-only.
- Connection pooling for Postgres: `get_connection` opens a fresh `psycopg`
  connection each call — fine for the CLI/ingest pattern, but the API/worker
  layer will want a pool (`psycopg_pool`).
- Port `scripts/audit_db.py` and fix `research/main.py`'s `.exists()` guard if
  those need to run against Postgres.
- Checklist items 3–6 (yfinance benchmark replacement, worker task wrapping,
  engine extensions for the portfolio loop) are out of this wave's scope.
