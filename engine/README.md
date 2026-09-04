# B3 Quant Research Platform

An end-to-end Python platform for Brazilian equity research: it **downloads and
cleans** historical B3 market data, **enriches** it with CVM fundamentals, and
**backtests** strategies against a tax-accurate simulator.

It started life as a COTAHIST price scraper. It is now two layers:

| Layer | Where | What it does |
|-------|-------|--------------|
| **Data pipeline** | `b3_pipeline/` | Prices, corporate actions, and fundamentals into the market plane |
| **Backtesting framework** | `backtests/` | Tax-aware simulator + config/DSL-driven strategies |

Storage is selected at runtime: **Postgres** when `ENGINE_DATABASE_URL` is a
`postgresql://` DSN (what the platform runs), otherwise a local SQLite file
(`b3_market_data.sqlite`) — the default for standalone research. Either way the
data carries split/dividend-adjusted prices (Yahoo-style `adj_close`),
point-in-time fundamentals, and ISIN-linked identity so the dataset is
**survivorship-bias free** across ticker changes, mergers, and delistings.

## Data sources & provenance

Earlier versions claimed "B3 is the single source of truth." That is no longer
true — the platform stitches together several official feeds:

| Source | Feed | Provides |
|--------|------|----------|
| **B3** | COTAHIST annual files | Daily OHLC + volume (1994→present) |
| **B3** | `listedCompaniesProxy` API | Dividends, JCP, splits, reverse splits, bonus shares |
| **CVM** | `dados.cvm.gov.br` bulk CSVs (DFP/ITR/FCA) | Structured fundamentals (2010→present) + PIT ticker map |
| **CVM** | "Download Múltiplo" legacy channel | Pre-2010 fundamentals (~2006–2009), *opt-in, credentialed* |
| **BCB** | Central Bank API | CDI risk-free daily series |
| **B3** | the plane itself (`prices`, `external_prices`) | Benchmarks: the Ibovespa (`$IBOV`, B3's own series), ETF sleeves (IVVB11, DIVO11, …) |

Splits use B3's **official factors** (not heuristic jump detection), so
100:1 splits, `0.01` reverse-split factors, and `Bonificação` bonus shares are
all handled exactly.

## Installation

Requirements: **Python 3.9+**.

```bash
cd engine

python -m venv .venv
source .venv/bin/activate        # Linux/macOS  (.venv\Scripts\activate on Windows)

pip install -r requirements.txt  # pipeline + backtests
```

`requirements.txt` covers everything.

## Running the data pipelines

There are three independent entry points. All write to the same database.

### 1. Prices + corporate actions — `b3_pipeline.main`

```bash
python -m b3_pipeline.main                     # download missing years, update DB
python -m b3_pipeline.main --rebuild           # drop tables & recompute adjustments
python -m b3_pipeline.main --year 2024         # a single year (testing)
python -m b3_pipeline.main --start-year 2010   # from a year onward
python -m b3_pipeline.main --skip-corporate-actions   # faster; prices only
python -m b3_pipeline.main --retry-failures    # re-fetch only companies in fetch_failures
python -m b3_pipeline.main --update-cnpj-map   # just refresh ticker→CNPJ map (fast)
```

Pipeline steps: download COTAHIST → parse → upsert raw prices → fetch B3
corporate actions → compute split factors → compute dividend factors → write
`split_adj_*` and total-return `adj_close`. It is **idempotent**
(`INSERT OR REPLACE`, existing-file checks) and resilient to rate limits and
partial years.

### 2. CVM fundamentals 2010+ — `b3_pipeline.cvm_main`

```bash
python -m b3_pipeline.cvm_main                       # all available years
python -m b3_pipeline.cvm_main --start-year 2020     # from 2020 onward
python -m b3_pipeline.cvm_main --rebuild             # rebuild fundamentals tables
python -m b3_pipeline.cvm_main --skip-ticker-fetch   # skip B3 ticker-map fetch
python -m b3_pipeline.cvm_main --include-historical  # + FCA/CAD/IPE metadata (register, PIT tickers)
```

Ingests structured DFP/ITR filings into **point-in-time** `fundamentals_pit`
(keyed by filing, so backtests can query "what was known on date X" with no
look-ahead), then materialises a monthly snapshot (`fundamentals_monthly`).
Valuation ratios (P/E, P/B, EV/EBITDA) are computed **dynamically at query
time**, not stored. `--include-historical` adds the CAD company register
(listing/delisting dates) and FCA point-in-time ticker map — which closes the
survivorship hole for delisted names — but does **not** provide pre-2010
financials.

Fundamentals therefore start at 2010. A credentialed CVM channel ("Download
Múltiplo") that reaches back to ~2006 was built and removed: it needed
per-operator `CVM_DM_USER` / `CVM_DM_PASS` credentials, was never
integration-tested against live files, and nothing in the platform scheduled it —
the ingest worker runs `b3_pipeline.main` and `b3_pipeline.cvm_main` only. Pull it
from git history if pre-2010 fundamentals become a requirement.

## Database schema

The market plane holds 12 tables. Identity is **ISIN-linked** so ticker
renames don't fragment a company's history.

**Market data**

| Table | Key | Contents |
|-------|-----|----------|
| `prices` | `(ticker, date)` | Raw OHLCV + `split_adj_*` + total-return `adj_close`; carries `isin_code` |
| `corporate_actions` | `(isin_code, event_date, event_type)` | Cash dividends, JCP, and stock events (`value`, `factor`, `source`) |
| `stock_actions` | `(isin_code, ex_date, action_type)` | Splits / reverse splits / bonus shares with factors |
| `detected_splits` | `(isin_code, ex_date)` | Legacy split factors derived from `stock_actions` |
| `skipped_events` | `(isin_code, event_date, label)` | Corporate events skipped during adjustment, with reason |
| `fetch_failures` | `(company_code, endpoint)` | Failed API fetches for `--retry-failures` |

**Fundamentals & identity**

| Table | Key | Contents |
|-------|-----|----------|
| `fundamentals_pit` | `filing_id` | Point-in-time financials per filing (revenue, net income, EBITDA, assets, equity, net debt, shares, TTM net income) |
| `fundamentals_monthly` | `(month_end, ticker)` | Monthly fundamentals snapshot for fast backtest joins |
| `cvm_filings` | `filing_id` | Filing metadata (doc type, period end, filing date, version) |
| `cvm_companies` | `cnpj` | CNPJ ↔ ticker, CVM code, listing/delisting dates |
| `company_isin_map` | `(cnpj, isin_code)` | CNPJ ↔ ISIN ↔ ticker ↔ share class |
| `company_tickers_pit` | `(cnpj, ticker, start_date)` | Point-in-time ticker map from CVM FCA (covers delisted names) |

All CVM financial values are stored in **thousands of BRL**, as reported.

### Example query — a clean, Yahoo-style series

```sql
SELECT date, ticker, open, high, low, close AS raw_close, adj_close
FROM prices
WHERE ticker = 'PETR4'
ORDER BY date DESC
LIMIT 10;
```

### B3 corporate-action labels & split factors

B3's Portuguese labels map to standard event types, and localised factor strings
(e.g. `"100,00000000000"`) are converted to adjustment multipliers:

| B3 Label | Event Type | Adjustment factor |
|----------|------------|-------------------|
| DIVIDENDO / RENDIMENTO | CASH_DIVIDEND | via total-return `adj_close` |
| JRS CAP PROPRIO | JCP | via total-return `adj_close` |
| DESDOBRAMENTO (split, factor > 1) | STOCK_SPLIT | `1 / factor` (e.g. 100:1 → 0.01) |
| GRUPAMENTO (reverse, factor < 1) | REVERSE_SPLIT | `1 / factor` (e.g. 0.01 → 100) |
| BONIFICACAO (bonus %) | BONUS_SHARES | `1 / (1 + pct/100)` (e.g. 33.33% → ≈0.75) |

## Backtesting framework

`backtests/` is a modular framework built on the pipeline DB. It uses
fully-adjusted `adj_close`, computes daily financial volume natively from
COTAHIST, pulls CDI from the BCB and benchmarks off the plane itself
(`$IBOV` is B3's own index series; ETF sleeves are ordinary COTAHIST tickers —
no yfinance), and — critically — implements the **exact mechanics of Brazilian
capital-gains tax** for variable income (15% CGT, infinite loss carryforward,
the R$20k/month sale exemption, slippage, and margin borrow at CDI + spread).
The result is a realistic **after-tax** equity curve, not a gross one. Simulator
behaviour (aportes, margin, rebalance frequencies) is in
**[backtests/README.md](backtests/README.md)**.

### Strategies are data — config + a signal DSL

**Most strategies need no Python.** A large class ("rank a factor, hold the top
N, rebalance" and "hold fixed sleeves and rebalance to weights") is defined by
dropping a YAML file in `backtests/strategies/specs/`. Factors themselves are
**string expressions** in a safe DSL (`backtests/core/signal_dsl.py`, parsed
with `ast` — no code execution) evaluated over shared data frames.

```yaml
# backtests/strategies/specs/my_momentum.yaml
name: MyMomentum
kind: rank_and_hold
rebalance: ME                 # ME | QE | W-FRI
universe: {min_adtv: 1000000, min_price: 1.0}
signal:   {factor: momentum, lookback: 6}   # a named signal from signals.yaml
selection: {top_pct: 0.10, min_names: 5}
weighting: equal
```

It auto-registers and appears in the UI's backtest runner. Named signals live in
`backtests/strategies/signals.yaml` (also just `expr` + defaults); research-discovered
features are referenceable as `store:<feature_id>`. Full field reference, the
expression vocabulary, regime overlays, and parity-porting gotchas are in
**[docs/config_strategies.md](docs/config_strategies.md)** — the guide for
humans *and* AI agents.

Write a `StrategyBase` subclass in `backtests/strategies/` only for genuinely
bespoke logic the config engines don't cover (per-row fundamental ranking,
inverse-vol weighting, index reconstruction, vol-targeting, asset rotation):

```python
from backtests.core.strategy_base import StrategyBase, ParameterSpec, COMMON_START_DATE, COMMON_END_DATE

class MyStrategy(StrategyBase):
    name = "My Strategy"
    def get_parameter_specs(self): return [COMMON_START_DATE, COMMON_END_DATE]
    def generate_signals(self, shared_data, params):
        ...  # return returns_matrix, target_weights
```

Both paths flow through one registry (`get_registry()`, auto-discovered) and the
shared `run_simulation`. **39 strategies** are registered today (16 plugin classes
+ 23 YAML specs) — factor books
(momentum, low-vol, Sharpe-momentum, multifactor, value/quality, low-P/E,
smallcap momentum, frog-in-the-pan, volume breakout, mean reversion), regime
overlays (COPOM easing, IBOV trend/vol, dual momentum), index-style books, and
fixed ETF/CDI blends.

### Core modules (`backtests/core/`)

| Module | Description |
|--------|-------------|
| `data.py` | Loads B3 data through `storage.get_connection` (SQLite or Postgres); downloads CDI (BCB); reads benchmarks off the plane |
| `shared_data.py` | Builds the shared frame bundle (`ret`, `log_ret`, `adj_close`, `adtv`, `f_*`, regime flags…) consumed by every strategy; the daily panels in it are built on first read |
| `memory.py` | The backtest's memory budget (`ENGINE_BACKTEST_MEM_MB`, `ENGINE_MEM_TRACE`) and the lazy bundle `shared_data` returns |
| `simulation.py` | Tax-aware multi-asset simulator (CGT, loss carryforward, slippage, margin, R$20k exemption) |
| `signal_dsl.py` | Safe `ast`-parsed expression interpreter for factor signals |
| `config_strategy.py` | YAML `rank_and_hold` / `fixed_weight` engines |
| `strategy_base.py` / `strategy_registry.py` | Plugin base class + auto-discovery registry (plugin classes *and* `specs/*.yaml`) |
| `metrics.py` | Sharpe, Calmar, max drawdown, annualised return/vol at any frequency |

#### Memory

A backtest's working set is set by the market, not by the tenant: a daily
`(date x ticker)` panel over a US 2000..today window is ~11,000 symbols by
~6,500 sessions, several hundred MB apiece, and the bundle derives a dozen of
them. Three things keep that inside a container:

* **The panels are built on first read.** `shared_data` returns a
  `memory.LazyShared`, so a momentum book never allocates the mean-reversion
  features, the ATR proxy or the MA200. Reads are unchanged — `shared["ret"]`,
  `.get()`, `in`, `dict(shared)`.
* **The load holds one copy, not two.** `data._pivot_wide` scatters one value
  column at a time off the long frame instead of reshaping all of them at once,
  and both database paths fetch in batches (`b3_pipeline/sql_stream.py`).
* **The process caps its own heap.** `memory.guard()` sets `RLIMIT_DATA` from
  the container's cgroup limit, so an overrun is a `MemoryError` naming a line
  rather than a SIGKILL naming nothing. Every run reports `peak_rss_mb` in its
  metrics.

| Variable | Effect |
|----------|--------|
| `ENGINE_BACKTEST_MEM_MB` | Heap cap in MB. `0` disables it. Default: 80% of the cgroup limit, or none when there is no limit |
| `ENGINE_BACKTEST_MEM_FRACTION` | The fraction used for that default |
| `ENGINE_MEM_TRACE=1` | Print peak RSS per stage to stderr |

## Testing

```bash
make test                                  # full Python suite (pytest)
python -m pytest tests/test_config_strategy.py   # config-strategy behaviour tests
```

The `tests/` suite covers the parser, corporate actions, CVM ingestion (PIT,
storage, historical), the config-strategy engines, and the simulator.

## License

MIT License
