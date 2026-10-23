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
   Shipped. `backtests/core/tax.py` holds `Rule` + `Ledger`: the apuração period
   is the unit, so the isenção is measured on a calendar month's gross sales
   rather than one rebalance row's, exterior accrues across the year and settles
   once, and cripto compensates nothing. Two bugs fell out of the same change —
   an isento *loss* used to be dropped instead of feeding the carry, and
   `strategy_base` shipped a `monthly_sales_exemption` parameter whose 20_000
   default beat the market's own rule. Consequence to know about: every stored
   B3 backtest's after-tax curve moves, because the timing and the isenção were
   both wrong before.
   Per-class lines followed (PR #78): a B3 month is apurated on taxcore's lines —
   AÇÃO (15%, R$20k isenção on *ações* sales only), BDR_ETF (15%, no isenção,
   same operações comuns loss pool) and FII (20%, own pool) — via
   `tax.market_ledger` and `asset_classes.b3_class`, a copy of taxcore's
   `ativos_b3.asset_class` that `test_asset_classes` checks against taxcore's
   own classifier and `monthly_apuracao`. Renda fixa (CDI_ASSET, ETFs de renda
   fixa) stays on the ações line, as before; taxing it at source is still open.
   Books of only ações and CDI score bit-for-bit as before; ETF/BDR/FII books
   pay more, and their stored runs keep the old numbers until re-run.

### US prices — decided, and what is still thin

Decided: **Alpha Vantage**, free tier first, the same code on a paid key. Two
things that were believed impossible turned out not to be.

The 403s the previous pass hit were the build environment's egress allowlist, at
the CONNECT tunnel — `sec.gov`, `alphavantage.co`, `polygon.io`, `stooq.com` and
`nasdaqtrader.com` alike, while `github.com` and `pypi.org` answered. A fact
about that sandbox, not about the internet, and not about the SEC's User-Agent
rule (`sec_downloader` has always sent one).

And the historical universe is free. `LISTING_STATUS` returns every US stock and
ETF ever listed, alive *or delisted*, with an IPO date and a delisting date, in
two requests. The security master no longer has to be built forward only, which
was the premise the whole "US history is not free" argument rested on.

What landed:

- `us_listing` — the listing record backwards; `symbol_directory` still owns the
  observed bounds and this owns the claimed intervals, and neither restates the
  other;
- `us_prices` — daily bars under a durable request budget, with a per-symbol
  cursor so a free tier's 25 requests a day add up instead of restarting;
- `us_adjust` + `us_corporate_actions` — splits and dividends fetched, stored
  and applied, because the free daily series is unadjusted and `adj_close =
  close` would fabricate a −75% day across Apple's 2020 split;
- `spx_series` — `$SPX` as SPY's BRL total return, labelled a proxy;
- `ticker_pit` — CIK↔ticker with validity, so the dead filers' fundamentals join
  their prices.

Three things stay thin, and all three are reported rather than assumed —
`GET /market/status` per market, and `metrics.json.data_warnings` per run:

1. **Price depth.** The free tier is 25 requests a day against ~11k symbols.
   The plane is correct from the first day and *shallow* for a long time; a
   window that opens before the archive says so in its own result. A paid key is
   two environment variables.
2. **Whether the vendor serves delisted symbols' history.** If it does not, the
   universe is survivorship-free and the *prices* are not, which is a materially
   different claim and the one place Sharadar's argument survives. Measured on
   the first real run, not assumed here.
3. **`adj_close` is per-symbol honest.** `total_return` where both actions
   endpoints answered, `split_only` or `unadjusted` where they did not, counted
   and reported. Crypto delisting still means *delisted from Binance spot*.

### Postgres gaps
- **Connection pooling.** `storage.get_connection` opens a fresh `psycopg` connection per
  call. Fine for the ingest CLIs; the api/worker path wants `psycopg_pool`.
- **Non-additive migrations.** `_migrate_schema_pg` is additive-only (`ADD COLUMN IF NOT
  EXISTS`). Renames, drops and type changes have no path on either backend. Add a
  `schema_migrations` table only when a destructive change is actually needed.
- **`b3_pipeline/main.py`'s closing banner** still prints `config.DB_PATH` (the SQLite
  fallback) in Postgres mode. Cosmetic — the data is written to PG correctly.

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
