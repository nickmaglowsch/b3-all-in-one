# LFTB11 and the fixed-income ETF gap in the price plane

Research report + implementation plan. Nothing implemented; no source files touched.

Dates/figures verified 2026-07-27 against the live archives, the live B3 endpoints and the
production database.

---

## 1. Findings

### 1.1 What I verified myself

**COTAHIST does not contain B3's fixed-income ETFs. At all. Ever.**

Scans run inside `b3_ingest` over `/opt/engine/data/raw/COTAHIST_A<year>.ZIP`, filtering on
`tipo_registro == "01"` only — **no BDI filter, no TPMERC filter**, matching both an exact
ticker set and the prefixes `LFT*`, `IMAB*`, `IB5M*`, `IRFM*`, `FIXA*`, `B5P2*`, `IRIM*`:

| Ticker | 2023 | 2024 | 2025 | 2026 | Verdict |
|---|---|---|---|---|---|
| LFTB11 | 0 | 0 | 0 | 0 | **absent** |
| IMAB11 | 0 | 0 | 0 | 0 | **absent** |
| IRFM11 | 0 | 0 | 0 | 0 | **absent** |
| IB5M11 | 0 | 0 | 0 | 0 | **absent** |
| FIXA11 | 0 | 0 | 0 | 0 | **absent** |
| B5P211 | 0 | 0 | 0 | 0 | **absent** |
| LFTS11 | 0 | 0 | 0 | 0 | **absent** |
| IRIM15 | 0 | 0 | 0 | 0 | **absent** |
| LFTT11 | 245 | 246 | 162 | 0 | present — BDI 12, TPMERC 010. **A FII, unrelated**; the only `LFT*` hit and a name collision |
| IRIM11 | 248 | 251 | 250 | 140 | present — BDI 12, TPMERC 010 (the FII whose receipt IRIM15 is) |
| IVVB11 | 248 | 251 | 250 | 140 | present — BDI 14 (control: equity ETFs *are* carried) |
| BTLG11 | 248 | 251 | 250 | 140 | present — BDI 12 (control) |
| PETR4 | 248 | 251 | 250 | 140 | present — BDI 02 (control) |

Row counts scanned: 2023 = 2,257,424 type-01 records; 2024 = 2,635,561; 2025 = 3,174,698;
2026 (YTD) = 2,190,894. The controls confirm the scanner works and that the archives are
complete.

**This is not our BDI whitelist filtering them out.** I dumped every distinct BDI-13/BDI-14
ticker at TPMERC 010 in 2026: **255 instruments** — equity ETFs (BBOV11, XBOV11, IVVB11),
international/BDR ETFs (ACWI11, SPXI11, WRLD11), crypto ETFs (BITH11, XETH11, ABTC11),
FIAGROs, FI-Infras, FIP-IEs, one FIDC. Not one fixed-income ETF among them.

**Nor is it TPMERC.** The full 2026 TPMERC histogram is `010` (198,073), `012` (24,254),
`013` (25,000), `017` (112), `020` (54,841), `021` (989), `030` (18,347), `070` (949,267),
`080` (920,011). The only value outside our 010–015 window that carries spot-like instruments
is `020` (fracionário, deliberately excluded — see `config.EQUITY_BDI_CODES` comment) and
`021` (989 rows, all BDI 93, all tickers ending `Q` — e.g. `CPTS11Q`, `IRIM11Q`; a
subscription/auction sideband, not our instruments). **There is no BDI or TPMERC value we
could add that would surface LFTB11.** The rows do not exist in the file.

**The full 2026 BDI histogram**, for completeness (`code: rows`):
`02:45,749  07:307  08:2,313  10:874  12:40,530  13:315  14:30,842  22:1,079  32:797
33:536  34:58,335  35:1,419  36:16,257  38:23,457  42:24,464  50:13  51:20  52:79  58:53
62:18,347  74:15,025  75:13,091  78:934,242  82:906,920  92:6  93:983  96:54,841`.
Note what is *missing* versus B3's own published code table: **26** (apólices/bônus/títulos
públicos), **66/68** (debêntures), **83** (BOVESPAFIX), **84** (SOMA FIX) — every legacy
fixed-income BDI code produces **zero rows**. COTAHIST stopped carrying fixed income entirely;
it did not merely skip these ETFs.

**yfinance covers all of them** (`taxcore` venv, yfinance 1.3.0, `period="max"`):

| Symbol | rows | first | last | last close |
|---|---|---|---|---|
| LFTB11.SA | 202 | 2025-09-22 | 2026-07-27 | 124.16 |
| IMAB11.SA | 1791 | 2019-05-17 | 2026-07-27 | 113.53 |
| IRFM11.SA | 1702 | 2019-09-23 | 2026-07-27 | 101.91 |
| FIXA11.SA | 1956 | 2018-09-10 | 2026-07-27 | 19.27 |
| B5P211.SA | 1417 | 2020-11-16 | 2026-07-27 | 109.29 |
| IB5M11.SA | 1702 | 2019-09-23 | 2026-07-27 | 121.88 |
| LFTS11.SA | 715 | 2022-11-08 | 2026-07-27 | 156.97 |
| **IRIM15.SA** | — | — | — | **404, "Quote not found"** |

**But B3 publishes them itself, and yfinance's LFTB11 head is fabricated.** B3's public bulk
portal serves a daily consolidated trade file with no authentication:

```
GET https://arquivos.b3.com.br/api/download/requestname?fileName=TradeInformationConsolidated&date=YYYY-MM-DD
  -> {"redirectUrl": "~/download?token=<token>"}
GET https://arquivos.b3.com.br/api/download/?token=<token>
  -> semicolon-delimited CSV, latin-1
```

Header: `RptDt;TckrSymb;ISIN;SgmtNm;MinPric;MaxPric;TradAvrgPric;LastPric;OscnPctg;AdjstdQt;AdjstdQtTax;RefPric;TradQty;FinInstrmQty;NtlFinVol`

LFTB11 is in it, with ISIN and full daily OHLC-equivalent. Cross-checking Yahoo against B3
day by day:

| Date | Yahoo close | Yahoo vol | B3 `LastPric` | B3 `FinInstrmQty` | Match? |
|---|---|---|---|---|---|
| 2025-09-22 | 111.1267 | **0** | **111.32** | **136,087** | **no — Yahoo wrong** |
| 2025-09-23 | 111.2251 | **0** | **111.36** | **137,034** | **no — Yahoo wrong** |
| 2025-09-24 | 111.2985 | **0** | **111.47** | **272,627** | **no — Yahoo wrong** |
| 2026-07-23 | 123.9200 | 320,860 | 123.92 | 320,860 | exact |
| 2026-07-24 | 124.1500 | 223,381 | 124.15 | 223,381 | exact |

**65 of yfinance's 202 LFTB11 rows carry `Volume == 0` and `Open == Close == Adj Close`** — a
smooth ~0.088 %/day accretion curve. Those are not market data: B3 records real trades
(R$15 M notional) on the very same days at prices ~0.17 % higher. Yahoo synthesised the first
~third of the series and got it wrong. Where Yahoo reports real volume it matches B3 to the
cent and to the share.

**B3's file has a rolling retention window of roughly 14 months.** Probed:
2025-05-02 → empty (HTTP 200, 0 bytes); 2025-06-10 → present (`107.14`); 2025-09-19 → present;
2026-01-15 → present; 2026-07-24 → present. Cutoff sits between 2025-05-02 and 2025-06-10.

**The tenant's actual exposure** (production `b3` database, table `trades`):

```
LFTB11 | Renda Fixa | 2025-03-25 .. 2026-05-08 | 25 trades | net 1260 cotas | R$516,471.60 gross turnover
```

`Renda Fixa` in this book has **exactly 25 trades and exactly 1 distinct ticker** — LFTB11.
The other market labels: `Mercado à Vista` 919/107, `Mercado Fracionário` 498/56, `Futuro`
128/9, `Mercado Primário de ETF` 1/1.

The position ladder matters for risk sizing — the exposure in the window neither source
covers is tiny:

| Date | Δ | cumulative | ≈ value |
|---|---|---|---|
| 2025-03-25 | +50 | 50 | R$5.2 k |
| 2025-06-03 | +100 | 150 | R$16 k |
| 2025-06-12 | +158 | 308 | R$33 k |
| … | | | |
| 2026-05-08 | −215 | **1260** | ≈R$156 k |

So: B3's file (from ~2025-05-27) misses only 2025-03-25 → late May, when the position was
**50 cotas ≈ R$5.2 k ≈ 0.4 % of book**. yfinance (from 2025-09-22) would miss up to
**308 cotas ≈ R$33 k**.

**IRIM15 is a different, much smaller animal.** It is not in `trades` at all. It appears only
in `cash_events`:

```
IRIM15 | qty_event | 2025-12-03 | qty -0.95
IRIM15 | income    | 2025-12-16 | R$345
IRIM15 | other     | 2026-01-14 | R$0.95   (leilão de fração)
```

It is the fractional residue of a subscription receipt for the IRIM11 FII, disposed of at
auction for 95 centavos. B3's live quote API returns `"Quotation not available"` for it and
Yahoo 404s it. Note the qty_event is *negative with no matching credit*, so the index carries
a −0.95 phantom short in a ticker nothing prices — worth R$0 today because unpriced, worth
−R$60 the day anything prices it.

### 1.2 What I read rather than measured

B3's official layout PDF (`SeriesHistoricas_Layout.pdf`, revisão 02, 05/10/2020) — its
`TABELA DE CODBDI` lists 02, 05–12, 14, 18, 22, 26, 32, 33, 38, 42, 46, 48–54, 56, 58, 60, 61,
62, 66, 68, 70, 71, 74, 75, 78, 82, 83, 84, 90, 96, 99. Its `TABELA DE TPMERC` lists only
010, 012, 013, 017, 020, 030, 050, 060, 070, 080.

Two documentation caveats worth recording, since our `config.py` comments lean on this table:

- **BDI 13 is not in B3's published table** (nor 34, 35, 36, 92, 93 — all of which appear in
  the data). The table is six years stale. Our comment calling 13 "FIAGRO" is an empirical
  inference, not a documented fact; it happens to be right (BBGO11, IAAG11, LAFI11 are all
  FIAGROs) but it is not citable to B3.
- **BDI 14's official name is `CERT.INVEST/TIT.DIV.PUBLICA`**, and **TPMERC 021 is undocumented**.

The layout also confirms our parser's field offsets are correct: `PREABE` 57–69, `PREMAX`
70–82, `PREMIN` 83–95, `PREMED` 96–108, `PREULT` 109–121, `FATCOT` 211–217, `CODISI` 231–242.
`config.COTAHIST_LAYOUT` mislabels several of these — it is already marked "reference only",
so no action, but the offsets in `parser.py` are the ones that match B3.

---

## 2. Why — B3's segmentation

**These instruments are not in the equity cash segment.** B3's own live quote API says so
directly. `GET https://cotacao.b3.com.br/mds/api/v1/instrumentQuotation/<TICKER>` returns a
`mkt.nm` field:

| Ticker | `mkt.nm` |
|---|---|
| LFTB11, IMAB11, IRFM11, FIXA11, B5P211, IB5M11, LFTS11 | **`RendaFixaPrivada`** |
| IVVB11 | `ETF` |
| PETR4, BTLG11, IRIM11 | `Vista` |
| IRIM15 | *no quote — `"Quotation not available."`* |

The same split shows in B3's own consolidated file's `SgmtNm` column: PETR4, IVVB11, BTLG11
and IRIM11 are `CASH`; LFTB11, IMAB11 and LFTS11 are `FORWARD` — a different segment value,
whatever one makes of the label.

COTAHIST's scope is the equity exchange. Its header and trailer records carry
`CÓDIGO DA ORIGEM = FIXO "BOVESPA"` (layout §3.1, §3.3), and every legacy fixed-income BDI
code (26, 66, 68, 83 BOVESPAFIX, 84 SOMA FIX) now yields zero rows. B3 retired fixed income
from this series; the renda-fixa ETFs were never in it because they list into
`RendaFixaPrivada`, not into `Vista`/`ETF`.

That is also exactly why the tenant's B3 "Negociação" extract stamps these trades
`Mercado = "Renda Fixa"`. The extract is not being sloppy — it is reporting B3's own segment
classification, and the same classification is what keeps the rows out of COTAHIST. One cause,
two symptoms.

**Where B3 does publish them:** the public bulk portal, `TradeInformationConsolidated` (see
§1.1 for the exact two-step call). This is not a paid UP2DATA subscription — it is the same
free "Arquivos" portal the site's own download buttons use. `brapi.dev` now demands an API
token (`MISSING_TOKEN`) and `stooq.com` is behind a proof-of-work bot wall; both are out.

**Answer to "is there a BDI/TPMERC we still filter out": no.** Verified exhaustively in §1.1.
The `EQUITY_BDI_CODES` whitelist is not the problem and widening it further would only pull
in options and termo noise.

---

## 3. Options

### Option 0 — Do nothing

**Effort:** zero. **Reliability:** n/a. **What breaks:** nothing new; what is already broken
stays broken.

The honest case for it: the index already discloses exclusions via `excluded.markets`, and
LFTB11 is a Selic tracker whose return is nearly the CDI benchmark the chart draws anyway.

The case against, which I find decisive:

1. **The error is systematic, not noise.** Dropping 11.2 % of the book biases total return by
   roughly `(portfolio_return − CDI) × 0.112` in a single direction, every window. It also
   inflates reported volatility and max drawdown, because the excluded sleeve is the calm one.
   A product whose headline feature is "your book vs IBOV vs CDI" cannot mis-state the book by
   11 % against CDI specifically.
2. **The two layers already disagree.** `taxcore/scripts/load_b3.py` (~L850–866) *deliberately*
   keeps LFTB11 as a real holding via `is_rf_etf`, so `positions` says 1260 cotas / R$149,583
   while the index says the market does not exist. Same book, two screens, two answers. That
   is a bug regardless of which answer is right.
3. **The data is available, free, from B3 itself.** "We cannot get it" would justify
   do-nothing. "We have not asked B3 for it" does not.

**Verdict: reject** — but keep the disclosure machinery, and lean on it for the residual gap.

### Option 1 — Classification only (split half (a))

Stop excluding the whole `"Renda Fixa"` market label; exclude only the rows that are genuinely
OTC contracts. **Effort:** ~10 lines. **Reliability:** total — no network, no new source.

**Effect on numbers: none.** LFTB11 moves from silently dropped to `excluded.unpriced_tickers`,
which the UI already surfaces. It buys honesty, not accuracy.

**Is it worth doing even if pricing turns out infeasible? Yes, unambiguously.** Today the
tenant is told "Renda Fixa was excluded", which reads as "your CDB was excluded" and is
dismissable. After the change they are told "LFTB11 is held but unpriced", which is the true
statement and is actionable. It also removes the taxcore/platform contradiction in §3-Option-0
point 2. It is a prerequisite for any pricing work and it stands alone. **Ship it regardless.**

**What breaks:** if a tenant genuinely holds a CDB/LCA whose contract code happens to match the
B3 ticker shape, it would leak into the index as an unpriced ticker. `_TICKER_RE` in
`taxcore/scripts/load_b3.py:179` is `^[A-Z][A-Z0-9]{3}\d{1,2}$`; real CDB codes are longer
alphanumeric strings and do not match. This predicate is already load-bearing in taxcore and
has been in production, so reuse it rather than inventing a second classifier.

### Option 2 — yfinance fallback, following the `fetch_external_prices.py` pattern

**Effort:** ~40 lines, plus wiring the output somewhere the platform can read (the taxcore
script writes a dated CSV under `taxcore/data/external_prices/`, which the asyncpg-backed
index cannot see; it would need a DB path anyway). **Reliability: poor, and I would not have
known that without checking.**

- History starts **2025-09-22**, five months after the tenant's first buy. The uncovered head
  holds up to **308 cotas ≈ R$33 k**.
- **65 of 202 rows are fabricated** (§1.1). ~0.17 % level error and a false zero volume on days
  that traded R$15 M.
- Yahoo silently changes coverage; there is no contract and no way to detect a regression
  except by diffing against B3.

**What breaks:** the missing head is worse than it sounds. `build_index` forward-fills but never
back-fills, so a ticker whose first mark lands mid-window contributes 0 to NAV up to that day and
then jumps. A +R$33 k NAV step with no matching `buys` flow is a **spurious one-day return of
~2.5 %** in the chain. Partial coverage is *worse* than no coverage here.

**Verdict: reject as the primary source.** It remains the right tool in taxcore, where it prices
today's snapshot and nothing more.

### Option 3 — B3 `TradeInformationConsolidated`, into a sibling table (recommended)

Pull B3's own daily file, keep the fixed-income ETF rows, store them in a table that is *not*
`prices`, and union that table into the index's close lookup only.

**Effort:** ~110 lines across 5 files. **Reliability: highest available** — it is B3's own
publication, matches to the cent, carries ISIN, no auth, no key, no vendor.

**What breaks / what to watch:**

- **Rolling ~14-month retention.** Anything not ingested is eventually unrecoverable. Mitigated
  by ingesting daily alongside the existing pipeline; the table is append-only, so history
  accumulates past the window. This is the one operational obligation the option creates.
- **One HTTP round trip per trading day.** Backfill from ~2025-05-27 is ~290 days
  (two requests each, ~1 GB total transfer, once). Ongoing cost is one file/day.
- **Residual head gap 2025-03-25 → ~2025-05-27**, where the position was 50 cotas ≈ R$5.2 k
  ≈ 0.4 % of book. Handled by the `build_index` back-fill fix below, and disclosed.
- **Provenance.** See §4 for why these rows must not go into `prices`.

### Option 4 — Model renda fixa as CDI units

Value the sleeve by compounding the CDI accrual from cost, as the retired taxcore CLI did.

**Effort:** ~30 lines (the engine already fetches CDI — `engine/scripts/bench_series.py` calls
`download_cdi_daily`). **Reliability:** approximate by construction, and it makes the book
*look* like the benchmark it is being compared against, which is circular. Would also be the
only sane path for a real CDB.

**Verdict: reject for LFTB11** — it is an exchange-listed instrument with a real, published,
free market price. Modelling a price you can simply fetch is the wrong trade. Keep this idea on
the shelf for genuine OTC fixed income, which is a separate feature.

---

## 4. Recommendation

**Option 1 + Option 3.** Do the classification fix unconditionally; source the marks from B3's
own file, never from Yahoo; keep the foreign rows out of `prices`.

### Why the rows must not go into `prices`

`prices` is the COTAHIST plane and three things run over *all* of it:

1. `main.py` step 6 derives `ticker_roots = sorted(set(t[:4] for t in tickers))` from
   `storage.get_all_tickers(conn)` and fetches B3 corporate actions per root. Adding LFTB11
   adds root `LFTB`, which has no listed company — it 404s, lands in `fetch_failures`, and
   gets retried on every subsequent run, forever.
2. `main.py` step 7 runs `adjustments.detect_splits_from_prices` over the whole table. LFTB11's
   ratios sit at ~1.000 so no false split would fire *today*, but the step exists to infer
   corporate actions from COTAHIST semantics and a non-COTAHIST series is outside its contract.
3. `storage.init_db(conn, rebuild=True)` drops `prices`. Foreign rows put there are silently
   destroyed by a routine rebuild, and unlike COTAHIST they cannot be re-downloaded past the
   14-month window.

A `prices.source` column would fix (3) but forces every existing consumer — backtests,
adjustments, split detection, `/portfolio/price-history` — to start filtering, i.e. it makes
every reader pay for one writer. **A sibling table forces nobody.** The index is the only
consumer that wants these marks, so the union lives in the index's own query.

### Files and functions to touch

| File | Change | ~lines |
|---|---|---|
| `engine/b3_pipeline/config.py` | `RENDA_FIXA_ETFS = {"LFTB11","LFTS11","IMAB11","IRFM11","IB5M11","FIXA11","B5P211"}` + `B3_TRADE_INFO_URL` templates. A `ponytail:` comment naming the ceiling: it is a list, not a rule; derive from the file's `SgmtNm` if these proliferate. | 8 |
| `engine/b3_pipeline/external_prices.py` **(new)** | `fetch_day(date) -> DataFrame`: the two-step token call, latin-1 CSV parse, filter `TckrSymb ∈ RENDA_FIXA_ETFS`, map `LastPric`→`close` / `MinPric`→`low` / `MaxPric`→`high` / `FinInstrmQty`→`volume` (comma decimals), return empty on the 0-byte past-retention response. Plus `backfill(start, end)` skipping days already stored. Plus a `demo()` `__main__` self-check asserting one known day parses to the known close. | 55 |
| `engine/b3_pipeline/storage.py` | `CREATE TABLE IF NOT EXISTS external_prices (ticker TEXT, date DATE, open/high/low/close DOUBLE PRECISION, volume BIGINT, isin_code TEXT, source TEXT, PRIMARY KEY (ticker, date))` in the same style as the `prices` DDL at L117; register it in `init_db`; `upsert_external_prices(conn, df)` mirroring `upsert_prices` at L578; `max_external_price_date(conn)` so the daily run knows where to resume. **Not dropped by `rebuild`** — it is not COTAHIST-derived and cannot be re-fetched. | 35 |
| `engine/b3_pipeline/main.py` | One step after step 5: resume from `max_external_price_date` (or the configured backfill start) to today, call `backfill`, `upsert_external_prices`. Before step 6, so the roots step still reads only `prices`. | 10 |
| `platform/platform_app/portfolio_index.py` | `EXCLUDED_MARKETS` → `frozenset({"Futuro"})`. Add `RENDA_FIXA_MARKET = "Renda Fixa"` and `_LISTED_TICKER_RE = re.compile(r"^[A-Z][A-Z0-9]{3}\d{1,2}$")` — the same pattern as `taxcore/scripts/load_b3.py:179`, copied not imported (the platform venv does not have taxcore on its path) with a comment naming the original so the two stay findable. Add `is_excluded(ticker, market) -> bool`. | 14 |
| `platform/platform_app/portfolio_index.py` → `build_index` | Seed `last_close[ticker]` with each series' **first** value before the day loop, so a ticker whose marks begin mid-window is valued flat at its earliest known close instead of 0-then-jump. **This is the root-cause fix, not an LFTB11 patch** — it removes the spurious-return class for every ticker whose plane history starts after the tenant bought it, and it is what makes the residual 2025-03-25→May head gap harmless. | 3 |
| `platform/platform_app/routes_portfolio_index.py` → `_book` | Replace `if t.market in pidx.EXCLUDED_MARKETS` with `if pidx.is_excluded(t.ticker, t.market)`. Keep appending to `excluded_markets` so the disclosure still fires when a real OTC row is dropped. | 4 |
| `platform/platform_app/routes_portfolio_index.py` → `_plane` | `UNION ALL` `external_prices` into the **closes** query only. Leave the **grid** query on `prices` alone — the grid must stay the exchange calendar, and B3's file shares it. | 6 |
| `platform/tests/test_portfolio_index.py` | Two asserts: a `Renda Fixa` trade with a listed-shape ticker is indexed while a contract-code one is excluded; a ticker whose closes start mid-window produces no return spike on its first marked day. | 20 |

**≈155 lines total, no new dependency** — `requests` and `pandas` are already engine deps, `re`
is stdlib.

### Sequencing

1. Land the classification fix + `build_index` back-fill + tests. Independently shippable,
   changes no numbers, ends the taxcore/platform contradiction, and makes the gap visible.
2. Land the B3 fetcher and run the backfill once.
3. Confirm `excluded.unpriced_tickers` no longer lists LFTB11 and that `coverage` reports the
   residual head honestly.

### What to disclose either way

The index should keep saying, in `excluded`, that LFTB11 has no marks before ~2025-05-27 and
that the 50-cota opening position is carried flat at its first known close. That is a 0.4 %
approximation stated out loud, which is the right posture for this codebase.

### On IRIM15

**Different problem, and not worth solving.** It is a subscription-receipt residue of 0.95
units, worth ~R$60, already resolved to cash at auction in January 2026, and no source prices
it — not B3's own quote API, not B3's file, not Yahoo. The general fallback will not cover it
and should not try. The one thing worth a line of code is the unmatched `qty_event`: IRIM15
carries −0.95 with no credit, a latent phantom short that is worth R$0 only because nothing
prices it. If you touch it at all, drop `qty_event` rows for tickers with no positive
counterpart, or leave it and let `unpriced_tickers` disclose it. Do not build a receipt
pricing path.

---

## 5. Open questions

1. **The B3 retention cutoff is bracketed, not pinned.** 2025-05-02 empty, 2025-06-10 present.
   The backfill start date should be found by bisecting between those two at implementation
   time; it may also drift, since the window looks rolling.
2. **Is the retention window rolling or calendar-based?** If rolling, the daily ingest is
   mandatory from day one and a week of downtime costs a week of history permanently. If it is
   "current year + previous", there is slack. Worth one probe a month from now.
3. **`SgmtNm = "FORWARD"` for fixed-income ETFs is unexplained.** It is stable across every day
   I sampled and it does distinguish them from `CASH`, so it is usable — but I could not find
   B3 documentation for the enum, and I would not key production logic on it. The ticker
   allowlist avoids depending on it.
4. **The daily file's size varies 25× between dates** (212 KB for 2026-07-24 vs 6.2 MB for
   2026-01-15) with the same `Status do Arquivo: Final` header. LFTB11 was present in both, but
   the reason for the difference is unknown and it is a plausible silent-truncation vector.
   The fetcher should assert the expected tickers are present and log loudly if not.
5. **LFTB11's true inception is not established.** Yahoo starts 2025-09-22 (synthetically),
   B3's window starts ~2025-05-27, the tenant's first trade is 2025-03-25, and press coverage
   puts the launch in 2024. Nothing free that I found reaches back further, so the pre-March-2025
   history may simply be unobtainable. It does not matter for this book.
6. **`taxcore/data/trades.csv` disagrees with production.** The local file shows LFTB11 buys on
   2024-02-08 at 108.40 and a 120-cota position; the production `b3` database shows first trade
   2025-03-25 at 103.98 and 1260 cotas. A Selic tracker cannot fall from 108.40 (Feb 2024) to
   107.14 (Jun 2025), so the local CSV is demo/scaled data. I used the database throughout.
   Worth confirming nothing else in the repo treats that CSV as real.
7. **The coordinating brief quoted a stale `EXCLUDED_MARKETS`** containing
   `"Mercado Primário de ETF"`. On `main` at bf56d94 it is `frozenset({"Futuro", "Renda Fixa"})`
   — the primary-ETF entry was removed and replaced by the `PRIMARY_ETF_MARKET` / `canonical`
   H-strip. Also note `canonical` already takes `(ticker, market)`, so the market label is
   plumbed into the folding path.
