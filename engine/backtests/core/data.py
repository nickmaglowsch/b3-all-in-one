"""
Core utilities for backtesting with B3 data.
"""

import glob
import hashlib
import os
import time
from pathlib import Path

import numpy as np
import pandas as pd

from b3_pipeline import storage

from backtests.core import markets, universe


def _safe_dsn(dsn) -> str:
    """A Postgres URL with the password masked, for anything that gets printed.

    The platform captures this process's stdout/stderr and returns the tail of it
    in API error bodies, so an unmasked DSN here hands the database password to
    any authenticated tenant that can make a backtest fail.
    """
    text = str(dsn)
    if "://" not in text:
        return text  # a plain file path (SQLite) — nothing to hide
    scheme, _, rest = text.partition("://")
    creds, at, host = rest.rpartition("@")
    if not at:
        return text  # no credentials in the URL
    user = creds.split(":", 1)[0]
    return f"{scheme}://{user}:***@{host}"


# ── Disk+memory cache for external market series (benchmarks, CDI) ────────────
# Every backtest run (and every UI re-run) used to refetch IBOV/ETFs from Yahoo
# and CDI from BCB. A past [start, end] window is effectively immutable, so we
# cache by (kind, *args). Mirrors the pickle+24h-TTL pattern already used in
# research/data_loader.py, but keyed by args so it serves all callers.
_CACHE_DIR = Path(__file__).resolve().parents[2] / ".cache" / "market_data"
_CACHE_TTL_SECONDS = 86400  # 24h: refreshes an open-ended `end` (=today) once a day
_MEM_CACHE: dict = {}


def _fetch_cached(kind: str, key_parts: tuple, fetch):
    """Return a cached series or call ``fetch()`` and cache its result.

    Hit order: in-process memory → on-disk pickle (if younger than the TTL) →
    network. Empty/failed fetches are never cached (so a transient outage can't
    poison the cache). Set ``B3_DISABLE_CACHE=1`` to force a fresh refetch.
    The canonical object never escapes — callers always get a ``.copy()`` — so a
    caller mutating its result in place can't corrupt the cache.
    """
    if os.environ.get("B3_DISABLE_CACHE"):
        return fetch()

    key = (kind, *(str(p) for p in key_parts))
    if key in _MEM_CACHE:
        return _MEM_CACHE[key].copy()

    # ponytail: filename key, not security — and `usedforsecurity=False` is what
    # says so to a FIPS-mode OpenSSL, which otherwise refuses md5 outright.
    digest = hashlib.md5(
        "|".join(key).encode(), usedforsecurity=False).hexdigest()[:16]
    path = _CACHE_DIR / f"{kind}_{digest}.pkl"
    if path.exists() and (time.time() - path.stat().st_mtime) < _CACHE_TTL_SECONDS:
        try:
            obj = pd.read_pickle(path)
            _MEM_CACHE[key] = obj
            return obj.copy()
        except Exception:
            pass  # corrupt/version-incompatible pickle → fall through and refetch

    obj = fetch()
    if obj is not None and not obj.empty:
        _MEM_CACHE[key] = obj
        try:
            _CACHE_DIR.mkdir(parents=True, exist_ok=True)
            obj.to_pickle(path)
        except Exception:
            pass  # disk cache is best-effort; never fail a backtest over it
        return obj.copy()
    return obj


def load_sector_pit(fca_dir, db_path) -> pd.DataFrame:
    """
    Load point-in-time CVM sector classification joined to tickers.

    Reads every FCA geral CSV (survivorship-free — includes delisted companies)
    from `fca_dir` and maps CNPJ -> ticker via company_tickers_pit. Returns
    long-format [ticker, ref_date, sector]. Returns an empty frame if FCA data
    is unavailable.
    """
    from b3_pipeline.fca_parser import parse_fca_sectors

    zips = sorted(glob.glob(str(Path(fca_dir) / "fca_cia_aberta_*.zip")))
    if not zips:
        return pd.DataFrame(columns=["ticker", "ref_date", "sector"])

    frames = []
    for zp in zips:
        try:
            frames.append(parse_fca_sectors(zp))
        except Exception:
            continue
    if not frames:
        return pd.DataFrame(columns=["ticker", "ref_date", "sector"])
    sectors = pd.concat(frames, ignore_index=True)

    with storage.get_connection(db_path) as conn:
        cmap = storage.read_sql(
            "SELECT DISTINCT cnpj, ticker FROM company_tickers_pit", conn
        )
    out = sectors.merge(cmap, on="cnpj", how="inner")
    out["ref_date"] = pd.to_datetime(out["ref_date"], errors="coerce")
    return out[["ticker", "ref_date", "sector"]].dropna(subset=["ticker", "ref_date"])


def sector_membership(sector_pit: pd.DataFrame, keywords: list) -> pd.DataFrame:
    """
    Given long-format PIT sector records [ticker, ref_date, sector] and a list of
    sector keywords (case-insensitive substring match), return [ticker,
    effective_date] — one row per FCA filing where the sector matched. A ticker
    is eligible at rebalance date t if any of its effective_date <= t.
    """
    if sector_pit.empty:
        return pd.DataFrame(columns=["ticker", "effective_date"])
    kw = [k.lower() for k in keywords]
    mask = sector_pit["sector"].str.lower().apply(lambda s: any(k in s for k in kw))
    hits = sector_pit[mask]
    return hits[["ticker", "ref_date"]].rename(columns={"ref_date": "effective_date"})


def sector_membership_asof(
    sector_pit: pd.DataFrame, keywords: list, dates: pd.DatetimeIndex
) -> pd.DataFrame:
    """
    As-of sector eligibility: for each date in `dates`, a ticker is eligible iff
    its LATEST filing with ref_date <= date matches one of `keywords`
    (case-insensitive substring). Unlike sector_membership ("ever classified"),
    a later reclassification out of the sector revokes eligibility.

    Returns a boolean DataFrame indexed by `dates` with ticker columns
    (False before a ticker's first filing).
    """
    if sector_pit.empty or len(dates) == 0:
        return pd.DataFrame(index=dates)
    kw = [k.lower() for k in keywords]
    sp = sector_pit.copy()
    sp["match"] = (
        sp["sector"].str.lower().apply(lambda s: any(k in s for k in kw)).astype(float)
    )
    wide = (
        sp.sort_values("ref_date")
        .pivot_table(index="ref_date", columns="ticker", values="match", aggfunc="last")
    )
    wide = wide.reindex(wide.index.union(dates)).ffill().reindex(dates)
    return wide.notna() & (wide > 0)


def _pivot_wide(df: pd.DataFrame, values: list[str]) -> list[pd.DataFrame]:
    """One (date x ticker) reshape covering every value column.

    Pivoting per value column rebuilt the same grid once per frame — three times
    in load_b3_data, six in load_b3_hlc_data, over the whole price history on
    every backtest.

    An empty input is special-cased: a multi-value pivot of no rows produces a
    column MultiIndex whose value level exists but is unused, so `wide[v]` raises
    KeyError where the single-value pivot returned an empty frame. A fresh
    database with no prices yet is a real state (it is what CI runs against), so
    it gets the empty frames it used to get.
    """
    if df.empty:
        return [
            pd.DataFrame(index=pd.Index([], name="date"),
                         columns=pd.Index([], name="ticker"), dtype=float)
            for _ in values
        ]
    wide = df.pivot(index="date", columns="ticker", values=values)
    return [wide[v] for v in values]


def load_b3_data(db_path: str, start: str, end: str) -> tuple:
    """
    Load data from the local B3 SQLite database.
    Filters for valid standard lot tickers (ending in 3, 4, 5, 6, 11).
    Calculates daily financial volume correctly.

    Prices are forward-filled only while each name was listed: see
    `universe.ffill_until_delisting`. On a plane whose `instruments` table is not
    yet backfilled every ticker counts as always-listed, so this is the plain
    ffill it has always been.

    Returns:
        tuple: (adj_close, close_px, fin_vol)
        All as wide DataFrames with shape (date, ticker)
    """
    # _safe_dsn, not db_path: it is a full Postgres URL with the password in it,
    # and this stdout is captured and surfaced in API error bodies.
    print(f"⬇  Loading B3 data from {_safe_dsn(db_path)} ({start} to {end})...")

    # Query: standard lot tickers ending in 3, 4, 5, 6, 11
    # BDRs (34, 35, etc.) and other weird assets are excluded using strict length and suffix checks.
    query = """
        SELECT date, ticker, close, adj_close, volume
        FROM prices
        WHERE date >= ? AND date <= ?
        AND (
            (LENGTH(ticker) = 5 AND SUBSTR(ticker, 5, 1) IN ('3', '4', '5', '6'))
            OR
            (LENGTH(ticker) = 6 AND SUBSTR(ticker, 5, 2) = '11')
        )
    """

    with storage.get_connection(db_path) as conn:
        df = storage.read_sql(query, conn, params=[start, end])

    df["date"] = pd.to_datetime(df["date"])

    # Daily financial volume in BRL.
    # The parser already divides COTAHIST VOLTOT's 2 implied decimals,
    # so the DB `volume` column is stored in reais (verified: PETR4 ~1e9/day).
    df["fin_volume"] = df["volume"].astype(float)

    print("🔄  Pivoting data to wide format...")
    adj_close, close_px, fin_vol = _pivot_wide(
        df, ["adj_close", "close", "fin_volume"])

    # Forward fill prices to handle missing days, but leave volume as NaN/0
    # to accurately calculate averages. The fill stops at each name's delisting;
    # carried past it, it would invent a price for something that no longer
    # trades and hand a book holding it a flat, riskless position forever.
    tradable = universe.tradable_mask(
        universe.load_listing_intervals(db_path), adj_close.index, adj_close.columns)
    adj_close = universe.ffill_until_delisting(adj_close, tradable)
    close_px = universe.ffill_until_delisting(close_px, tradable)

    print(f"✅  Loaded {adj_close.shape[1]} unique standard tickers.")
    return adj_close, close_px, fin_vol


import requests


# What the callers ask for -> what the plane calls it. `^BVSP` is Yahoo's symbol
# for the Ibovespa; the sleeves were asked for with Yahoo's `.SA` suffix. Both
# now resolve on the plane, because every series these benchmarks need is
# already there.
#
# The sleeves are ordinary COTAHIST tickers and read `prices`. The Ibovespa is
# not an instrument and is in no COTAHIST year, so it is stored as `$IBOV` in
# `external_prices` — B3's own published index, in points, every session, by
# b3_pipeline/ibov_series.py. A `$` symbol routes to that table; see there for
# why the index and not a fund that tracks it.
BENCHMARK_ALIASES = {"^BVSP": "$IBOV", "IBOV": "$IBOV"}


def _plane_series(table: str, column: str, symbol: str, start: str, end: str,
                  market: str = None, symbol_column: str = "ticker") -> pd.Series:
    """One ticker's daily column out of the plane, as a date-indexed Series.

    Empty when the plane has no rows for it — never a fabricated flat line, which
    a chart would render as "the benchmark did not move".

    `market_prices` keys on (market, symbol), so a non-B3 read scopes on both;
    `prices` and `external_prices` key on ticker alone and pass market=None.
    """
    col = "adj_close_brl" if (market and column == "adj_close") else column
    scope = " AND market = :market" if market else ""
    query = (f"SELECT date, {col} AS {column} FROM {table} "         # noqa: S608
             f"WHERE {symbol_column} = :symbol AND date >= :start"
             f" AND date <= :end{scope} ORDER BY date")
    params = {"symbol": symbol, "start": start, "end": end}
    if market:
        params["market"] = market
    with storage.get_connection() as conn:
        df = storage.read_sql(query, conn, params=params)

    if df.empty:
        return pd.Series(dtype=float, index=pd.DatetimeIndex([]), name=symbol)
    out = pd.Series(
        df[column].astype(float).to_numpy(),
        index=pd.to_datetime(df["date"]), name=symbol)
    return out[out.notna()]


def download_benchmark(ticker: str, start: str, end: str, *,
                      market: str = markets.DEFAULT) -> pd.Series:
    """Daily total-return series for a benchmark, from the market's own plane.

    `adj_close`, not `close`, for the traded sleeves: the callers compare a
    strategy's total return against it, and that is what Yahoo's
    `auto_adjust=True` used to hand back. A stored index has no corporate
    actions to adjust for and only carries `close`.

    `market` is keyword-only with a default on purpose. The offline bench stub in
    run_backtest_json monkeypatches this function positionally, and advise_weights
    imports it by name — a positional third argument would break every
    ENGINE_BENCH_OFFLINE run at once.
    """
    spec = markets.get(market)
    symbol = ticker.strip().upper()
    symbol = spec.aliases.get(
        symbol, symbol.removesuffix(spec.suffix_strip) if spec.suffix_strip else symbol)
    if symbol.startswith("$"):
        # The `$` namespace is global by shape — $CDI, $IPCA, $USDBRL and the
        # stored indices belong to no single market, so they are not scoped.
        return download_index_level(symbol, start, end)
    return _plane_series(spec.table, "adj_close", symbol, start, end,
                         market=None if spec.table == "prices" else spec.key,
                         symbol_column=spec.symbol_column)


def download_index_level(ticker: str, start: str, end: str) -> pd.Series:
    """A stored index level (`$CDI`, `$IPCA`) out of `external_prices`.

    These are cumulative factors written by b3_pipeline/index_series.py, not
    prices — the growth between two dates is one division. Reserved `$` tickers,
    so they can never collide with a B3 code.
    """
    return _plane_series("external_prices", "close",
                         ticker.strip().upper(), start, end)


# What to SELECT per market, aliased to the B3 column names every downstream
# reader already speaks. Two things happen in the non-B3 projection:
#
#   * BRL, not native. The simulation's numeraire is reais for every market (see
#     markets.py), so the close, the range and the turnover are all converted
#     here rather than in six callers. `fx_rate` is NULL where PTAX could not
#     cover the date, which makes the whole row NULL — the honest outcome, and
#     visible as `unconverted_brl` in GET /market/status. There is deliberately
#     no COALESCE back to the native number: silently pricing a US book in
#     dollars is worse than reporting it unpriced.
#   * `high`/`low` stand in for `split_adj_high`/`split_adj_low`. Crypto spot
#     has no splits at all, and the US feed's bars arrive already adjusted, so
#     there is no second adjustment to keep separate.
_B3_COLUMNS = ("split_adj_high, split_adj_low, split_adj_close, adj_close,"
               " close, volume")
_CONVERTED_COLUMNS = (
    "high * fx_rate AS split_adj_high, low * fx_rate AS split_adj_low,"
    " split_adj_close * fx_rate AS split_adj_close, adj_close_brl AS adj_close,"
    " close_brl AS close, volume * fx_rate AS volume")


def load_market_hlc_data(db_path: str, start: str, end: str,
                         market: str = markets.DEFAULT) -> tuple:
    """The six price frames for any market, in the historical B3 order.

    `load_b3_hlc_data` is this with market="B3"; the B3 path is unchanged.
    """
    spec = markets.get(market)
    print(f"⬇  Loading {spec.key} HLC data from {_safe_dsn(db_path)} "
          f"({start} to {end})...")

    b3 = spec.table == "prices"
    shape = f" AND {spec.ticker_sql}" if spec.ticker_sql else ""
    scope = "" if b3 else " AND market = :market"
    query = (
        f"SELECT date, {spec.symbol_column} AS ticker,"                          # noqa: S608
        f" {_B3_COLUMNS if b3 else _CONVERTED_COLUMNS}"
        f" FROM {spec.table} WHERE date >= :start AND date <= :end{scope}{shape}"
    )
    params = {"start": start, "end": end}
    if not b3:
        params["market"] = spec.key

    with storage.get_connection(db_path) as conn:
        df = storage.read_sql(query, conn, params=params)

    df["date"] = pd.to_datetime(df["date"])
    # DB `volume` is already in reais (parser handles VOLTOT's implied decimals)
    df["fin_volume"] = df["volume"].astype(float)

    print("🔄  Pivoting HLC data to wide format...")
    # Six frames, one reshape. This is the loader every backtest goes through.
    adj_close, split_high, split_low, split_close, close_px, fin_vol = _pivot_wide(
        df, ["adj_close", "split_adj_high", "split_adj_low",
             "split_adj_close", "close", "fin_volume"],
    )

    # Forward fill prices to handle missing days, leave volume as-is. Stops at
    # each name's delisting — see load_b3_data.
    tradable = universe.tradable_mask(
        universe.load_listing_intervals(db_path, spec.key),
        adj_close.index, adj_close.columns)
    adj_close, split_high, split_low, split_close, close_px = (
        universe.ffill_until_delisting(f, tradable)
        for f in (adj_close, split_high, split_low, split_close, close_px))

    print(f"✅  Loaded HLC for {adj_close.shape[1]} unique {spec.key} tickers.")
    return adj_close, split_high, split_low, split_close, close_px, fin_vol


def load_b3_hlc_data(db_path: str, start: str, end: str) -> tuple:
    """Split-adjusted HLC + adj_close + raw close + financial volume, B3.

    Uses split_adj_* (split-only adjustment) for ATR to avoid false True Range
    spikes from ex-dividend gaps. Returns adj_close separately for the returns
    matrix (simulation needs dividend-adjusted returns).
    """
    return load_market_hlc_data(db_path, start, end, "B3")


from dateutil.relativedelta import relativedelta


def download_cdi_daily(start: str, end: str) -> pd.Series:
    """
    Download daily accumulated CDI directly from the Brazilian Central Bank (SGS API Series 12).
    Because BCB limits queries to 10 years for daily data, this safely batches requests.
    Returns a pandas Series of daily returns (e.g., 0.0001 for 0.01%). Cached by window.
    """
    return _fetch_cached("cdi", (start, end), lambda: _download_cdi_daily(start, end))


def _download_cdi_daily(start: str, end: str) -> pd.Series:
    print("⬇  Downloading Daily CDI from Brazilian Central Bank...")

    start_dt = pd.to_datetime(start)
    end_dt = pd.to_datetime(end)

    series = []
    current_start = start_dt

    while current_start <= end_dt:
        # BCB API allows max 10 years for daily data. We'll query in 5-year chunks.
        current_end = min(current_start + relativedelta(years=5), end_dt)

        s_str = current_start.strftime("%d/%m/%Y")
        e_str = current_end.strftime("%d/%m/%Y")

        url = f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.12/dados?formato=json&dataInicial={s_str}&dataFinal={e_str}"

        # BCB throttles intermittently and returns non-JSON — a silently missing
        # chunk makes every CDI-holding backtest earn 0% cash for years, so retry.
        for attempt in range(3):
            try:
                response = requests.get(url, timeout=30)
                data = response.json()
                if data:
                    df = pd.DataFrame(data)
                    df["data"] = pd.to_datetime(df["data"], format="%d/%m/%Y")
                    df["valor"] = pd.to_numeric(df["valor"]) / 100.0
                    df.set_index("data", inplace=True)
                    series.append(df["valor"])
                break
            except Exception as e:
                if attempt == 2:
                    print(f"Warning: Failed to fetch CDI chunk {s_str} to {e_str}: {e}")
                else:
                    import time
                    time.sleep(2 * (attempt + 1))

        current_start = current_end + relativedelta(days=1)

    if not series:
        import warnings
        warnings.warn("CDI daily data download returned empty. Check network connectivity.")
        return pd.Series(dtype=float, name="CDI")

    result = pd.concat(series)
    # Ensure no duplicates at chunk boundaries
    result = result[~result.index.duplicated(keep="first")]
    result.name = "CDI"

    return result


# ── Fundamentals (point-in-time) data loading ─────────────────────────────────

_FUNDAMENTALS_METRICS = [
    "revenue",
    "net_income",
    "net_income_ttm",
    "ebitda",
    "total_assets",
    "equity",
    "net_debt",
    "shares_outstanding",
]
# Ratio columns (pe_ratio, pb_ratio, ev_ebitda) intentionally excluded — compute dynamically at query time

# All metrics available in fundamentals_monthly (raw financials only — no stored ratios)
_FUNDAMENTALS_MONTHLY_METRICS = set(_FUNDAMENTALS_METRICS)


def _get_rebalance_dates(db_path, start, end, freq, market="B3") -> pd.DatetimeIndex:
    """Build the rebalance date grid from the span that market's prices cover.

    Only the first and last traded day are used, so ask the database for those
    two rather than for every distinct date in the window — the old query read
    the whole table (through idx_prices_date, but still every row) and threw all
    of it away except min() and max(), once per fundamentals metric.

    Scoped to the market: B3 trading days are not the US calendar, and a plane
    holding only one of the two would otherwise get an empty grid and, silently,
    empty fundamentals.
    """
    spec = markets.get(market)
    scope = "" if spec.table == "prices" else " AND market = :market"
    params = {"start": start, "end": end}
    params.update({"market": spec.key} if scope else {})
    with storage.get_connection(db_path) as conn:
        span = storage.read_sql(
            f"SELECT MIN(date) AS lo, MAX(date) AS hi FROM {spec.table} "       # noqa: S608
            f"WHERE date >= :start AND date <= :end{scope}",
            conn, params=params,
        )
    lo, hi = (span.iloc[0]["lo"], span.iloc[0]["hi"]) if not span.empty else (None, None)
    if lo is None or hi is None or pd.isna(lo) or pd.isna(hi):
        return pd.date_range(start=start, end=end, freq=freq)
    return pd.date_range(start=pd.to_datetime(lo), end=pd.to_datetime(hi), freq=freq)


# Which table holds a market's point-in-time fundamentals, and what orders two
# rows filed on the same day. CVM numbers a restatement (`filing_version`); XBRL
# does not, because there a restatement is a whole new accession with a later
# `filed` — so `accn` is the tiebreaker, and the semantics are identical.
_PIT_TABLES = {"B3": ("fundamentals_pit", "filing_version"),
               "US": ("fundamentals_pit_us", "accn")}


def _query_fundamentals_pit(db_path: str, metrics: list[str], end: str,
                            market: str = "B3") -> pd.DataFrame:
    """Long-format point-in-time rows for `metrics`, filed on or before `end`.

    One query for every metric the caller wants. Per-metric loading ran this
    scan once per metric and each carried its own `AND {metric} IS NOT NULL`;
    that predicate moves to a per-metric dropna in _pit_frame, which selects the
    same rows.

    A market with no fundamentals table (crypto) returns an empty frame rather
    than raising: `validate_spec` already rejects a spec that asks for f_* there,
    and a backtest that reaches here anyway should get empty frames, not a crash.
    """
    if market not in _PIT_TABLES:
        return pd.DataFrame()
    table, version_col = _PIT_TABLES[market]
    cols = ", ".join(f"f.{m}" for m in metrics)
    sql = f"""
        SELECT
            f.ticker,
            f.filing_date,
            f.period_end,
            f.{version_col} AS filing_version,
            {cols}
        FROM {table} f
        WHERE f.ticker IS NOT NULL
          AND f.filing_date <= :end
        ORDER BY f.ticker, f.filing_date, f.{version_col}
    """                                                                          # noqa: S608
    with storage.get_connection(db_path) as conn:
        return storage.read_sql(sql, conn, params={"end": end})


def _pit_frame(
    df: pd.DataFrame,
    metric: str,
    rebal_dates: pd.DatetimeIndex,
    start: str,
    staleness_days: int,
) -> pd.DataFrame:
    """Long fundamentals rows -> wide point-in-time frame on the rebalance grid.

    Split out of load_fundamentals_pit so the single-metric entry point and the
    batched one share one implementation and cannot drift.
    """
    sub = df.dropna(subset=[metric]) if not df.empty else df
    if sub.empty:
        return pd.DataFrame(index=rebal_dates, dtype=float)

    # Pivot: index = filing_date, columns = ticker.
    # aggfunc="last" picks the highest version when multiple versions share
    # the same filing_date (rare, but possible) — the caller sorted by
    # (ticker, filing_date, filing_version) so "last" is "highest version".
    wide = sub.pivot_table(
        index="filing_date",
        columns="ticker",
        values=metric,
        aggfunc="last",
        sort=False,
    )
    wide = wide.sort_index()
    wide.index = pd.to_datetime(wide.index)
    wide.columns.name = None
    wide = wide.reindex(columns=sorted(wide.columns))

    # Reindex to the rebalance calendar and forward-fill.
    # To allow pre-period filings to seed the forward-fill, we first reindex
    # to the union of the filing dates and the rebalance calendar, then ffill,
    # then slice to only the rebalance calendar dates within [start, end].
    extended_index = wide.index.union(rebal_dates).sort_values()
    wide = wide.reindex(extended_index)

    # Track the filing date behind each forward-filled cell so we can blank
    # values once they exceed the staleness cutoff.
    obs_date = pd.DataFrame(
        np.where(wide.notna(), wide.index.values[:, None], np.datetime64("NaT")),
        index=wide.index,
        columns=wide.columns,
    )
    wide = wide.ffill()
    obs_date = obs_date.ffill()
    stale = (wide.index.values[:, None] - obs_date.values) > np.timedelta64(
        staleness_days, "D"
    )
    wide = wide.mask(stale)

    # Slice to only the rebalance calendar dates within the requested window
    wide = wide.reindex(rebal_dates)
    return wide.loc[wide.index >= pd.Timestamp(start)]


def load_fundamentals_pit(
    db_path: str,
    metric: str,
    start: str,
    end: str,
    freq: str = "ME",
    staleness_days: int = 400,
) -> pd.DataFrame:
    """
    Load point-in-time fundamentals for a single metric.

    For each (rebalance_date, ticker), returns the metric value from the most
    recently filed version of the most recently filed period whose filing_date
    <= rebalance_date.

    Args:
        db_path:  Path to the B3 SQLite database file.
        metric:   Column name in fundamentals_pit (e.g. 'pe_ratio', 'revenue').
        start:    Start date string.
        end:      End date string.
        freq:     Rebalance calendar frequency (default 'ME' = month-end).
        staleness_days: Values whose source filing is older than this many days
                  become NaN instead of forward-filling forever (mirrors the
                  400-day cut in sp500_b3.pit_snapshot), so delisted companies
                  don't carry years-stale fundamentals.

    Returns:
        Wide DataFrame: DatetimeIndex (rebalance dates) × ticker columns.
        Values are forward-filled from the last filing_date on or before each date.
    """
    if metric not in _FUNDAMENTALS_METRICS:
        raise ValueError(f"Unknown metric {metric!r}. Choose from: {_FUNDAMENTALS_METRICS}")

    # Sorted by (ticker, filing_date, filing_version) in SQL, so that within a
    # given filing_date the highest version is last and wins the aggfunc below.
    # Versions are NOT deduplicated across time — each keeps its own filing_date
    # so forward-fill preserves strict point-in-time semantics: a restatement
    # only becomes visible after its own filing_date.
    df = _query_fundamentals_pit(db_path, [metric], end)
    rebal_dates = _get_rebalance_dates(db_path, start, end, freq)
    return _pit_frame(df, metric, rebal_dates, start, staleness_days)


def _broadcast_roots_to_tickers(df: pd.DataFrame, tickers) -> pd.DataFrame:
    """Broadcast a root-keyed (4-char company) fundamentals frame onto full
    tickers: each company's value is copied to every ticker sharing its root
    (e.g. PETR -> PETR3, PETR4). Company-level metrics (net income, equity, …)
    apply to all share classes, so this is the correct projection and matches the
    full-ticker keying of the _m / _dyn frames. Empty in -> empty out."""
    if df is None or df.empty:
        return pd.DataFrame() if df is None else df
    roots = [str(t)[:4] for t in tickers]
    out = df.reindex(columns=roots)          # dup roots -> repeated columns
    out.columns = pd.Index(list(tickers))
    return out


def load_all_fundamentals(
    db_path: str,
    start: str,
    end: str,
    freq: str = "ME",
    tickers=None,
    market: str = "B3",
) -> dict:
    """
    Load all fundamentals metrics and return them as a dict of wide DataFrames.

    On B3, fundamentals_pit is keyed by 4-char company **root**. When ``tickers``
    is given, each frame is broadcast onto the full tickers sharing that root so
    it joins directly against the full-ticker price/return matrix; without it the
    frames stay root-keyed — a footgun that silently fails to join (bit
    ValueQuality + the research discovery path). Callers that have a ticker
    universe (build_shared_data, research.data_loader) should pass it.

    The root broadcast is B3-specific and must not run elsewhere. Off B3 the rows
    are already keyed by the traded symbol, and a four-character prefix would
    merge GOOG's numbers into GOOGL's — two real companies' worth of one
    company's financials.

    Returns:
        dict with keys matching _FUNDAMENTALS_METRICS:
        {"revenue": df, "net_income": df, "ebitda": df, "total_assets": df,
         "equity": df, "net_debt": df, "shares_outstanding": df}
        Ratio columns (pe_ratio, pb_ratio, ev_ebitda) are excluded — compute dynamically.
    """
    # One scan of fundamentals_pit and one of prices for the whole set. Looping
    # load_fundamentals_pit meant eight of each — sixteen passes over two large
    # tables to fill one dict, on every backtest that needs fundamentals.
    rebal_dates = _get_rebalance_dates(db_path, start, end, freq, market)
    try:
        df = _query_fundamentals_pit(db_path, _FUNDAMENTALS_METRICS, end, market)
    except Exception as e:
        import warnings
        warnings.warn(f"Failed to load fundamentals: {e}")
        df = pd.DataFrame()

    result = {}
    for metric in _FUNDAMENTALS_METRICS:
        try:
            result[metric] = _pit_frame(df, metric, rebal_dates, start, 400)
        except Exception as e:
            import warnings
            warnings.warn(f"Failed to load fundamentals metric '{metric}': {e}")
            result[metric] = pd.DataFrame()
    if tickers is not None and market == "B3":
        result = {m: _broadcast_roots_to_tickers(df_m, tickers) for m, df_m in result.items()}
    return result


# ── fundamentals_monthly snapshot reader ──────────────────────────────────────

def load_fundamentals_monthly(
    db_path: str,
    metric: str,
    start: str,
    end: str,
) -> pd.DataFrame:
    """
    Load a single metric from the pre-materialized fundamentals_monthly snapshot.

    Args:
        db_path:  Path to the B3 SQLite database file.
        metric:   Column name in fundamentals_monthly (raw metrics or ratio columns).
        start:    Start date string (inclusive).
        end:      End date string (inclusive).

    Returns:
        Wide DataFrame: DatetimeIndex (month_end) × ticker columns.
        No forward-fill — the snapshot is already forward-filled.

    Raises:
        ValueError: If metric is not in the allowed set.
    """
    if metric not in _FUNDAMENTALS_MONTHLY_METRICS:
        raise ValueError(
            f"Unknown metric {metric!r}. Choose from: {sorted(_FUNDAMENTALS_MONTHLY_METRICS)}"
        )

    # metric is validated above — safe to interpolate in column name
    sql = f"""
        SELECT month_end, ticker, {metric}
        FROM fundamentals_monthly
        WHERE month_end >= ?
          AND month_end <= ?
          AND {metric} IS NOT NULL
        ORDER BY month_end, ticker
    """

    with storage.get_connection(db_path) as conn:
        df = storage.read_sql(sql, conn, params=[start, end])

    if df.empty:
        return pd.DataFrame(dtype=float)

    df["month_end"] = pd.to_datetime(df["month_end"])
    wide = df.pivot_table(index="month_end", columns="ticker", values=metric, aggfunc="last")
    wide.index = pd.to_datetime(wide.index)
    wide.columns.name = None
    return wide


def load_all_fundamentals_monthly(
    db_path: str,
    start: str,
    end: str,
) -> dict:
    """
    Load all metrics from fundamentals_monthly and return them as a dict of wide DataFrames.

    Returns:
        dict with keys matching _FUNDAMENTALS_MONTHLY_METRICS:
        {"revenue": df, "net_income": df, "ebitda": df, "total_assets": df,
         "equity": df, "net_debt": df, "shares_outstanding": df}
        Each df has DatetimeIndex (month_end dates) and ticker columns.
        Ratio columns excluded — computed dynamically in shared_data / strategies.
    """
    result = {}
    for metric in _FUNDAMENTALS_MONTHLY_METRICS:
        try:
            result[metric] = load_fundamentals_monthly(db_path, metric, start, end)
        except Exception as e:
            import warnings
            warnings.warn(f"Failed to load monthly metric '{metric}': {e}")
            result[metric] = pd.DataFrame()
    return result


# ── Dynamic ratio helpers (pure pandas, no DB access) ─────────────────────────
#
# History: a 2026-03-07 investigation observed some stored share counts 1000x+
# too large (CIEL 6e12, VSPT 2.1e14) and concluded CVM reports shares in
# thousands, adding a /1000 correction here. The 2026-07-17 FRE parser fix
# found the real root cause: the parser was keeping arbitrary Tipo_Capital rows
# ("Capital Autorizado" = issuance ceiling, or fat-fingered duplicate entries)
# instead of "Capital Integralizado". shares_outstanding is now stored in RAW
# UNITS, so no scaling is applied. Formulas assume: prices in BRL, financial
# metrics in thousands BRL, shares in raw units.

_SHARES_SCALE = 1.0  # shares_outstanding stored in raw units (see history above)


def compute_pe_ratio_dynamic(
    shares: pd.DataFrame,
    net_income: pd.DataFrame,
    prices: pd.DataFrame,
) -> pd.DataFrame:
    """
    Compute P/E ratio from wide DataFrames aligned on the same DatetimeIndex.

    Args:
        shares:     Wide DataFrame: rebalance_date × ticker, shares_outstanding as stored
                    in DB (divide by 1000 to get raw unit count — see _SHARES_SCALE note).
        net_income: Wide DataFrame: rebalance_date × ticker, net_income in thousands BRL.
        prices:     Wide DataFrame: rebalance_date × ticker, close price (BRL).

    Returns:
        Wide DataFrame with same shape: pe_ratio = (prices × shares / SCALE) / (net_income × 1000).
        Entries where net_income × 1000 <= 0 or shares <= 0 or prices <= 0 are NaN.
    """
    market_cap = prices * (shares / _SHARES_SCALE)
    net_income_brl = net_income * 1_000.0
    result = market_cap / net_income_brl
    # Mask invalid denominators/numerators
    result = result.where(net_income_brl > 0)
    result = result.where(shares > 0)
    result = result.where(prices > 0)
    return result


def compute_pb_ratio_dynamic(
    shares: pd.DataFrame,
    equity: pd.DataFrame,
    prices: pd.DataFrame,
) -> pd.DataFrame:
    """
    Compute P/B ratio from wide DataFrames aligned on the same DatetimeIndex.

    Args:
        shares: Wide DataFrame: shares_outstanding as stored in DB (divide by 1000
                to get raw unit count — see _SHARES_SCALE note).
        equity: Wide DataFrame: equity in thousands BRL.
        prices: Wide DataFrame: close price (BRL).

    Returns:
        Wide DataFrame: pb_ratio = (prices × shares / SCALE) / (equity × 1000).
        Entries where equity × 1000 <= 0 or shares <= 0 or prices <= 0 are NaN.
    """
    market_cap = prices * (shares / _SHARES_SCALE)
    equity_brl = equity * 1_000.0
    result = market_cap / equity_brl
    result = result.where(equity_brl > 0)
    result = result.where(shares > 0)
    result = result.where(prices > 0)
    return result


def compute_ev_ebitda_dynamic(
    shares: pd.DataFrame,
    ebitda: pd.DataFrame,
    net_debt: pd.DataFrame,
    prices: pd.DataFrame,
) -> pd.DataFrame:
    """
    Compute EV/EBITDA from wide DataFrames aligned on the same DatetimeIndex.

    Args:
        shares:   Wide DataFrame: shares_outstanding as stored in DB (divide by 1000
                  to get raw unit count — see _SHARES_SCALE note).
        ebitda:   Wide DataFrame: EBITDA in thousands BRL.
        net_debt: Wide DataFrame: net debt in thousands BRL.
        prices:   Wide DataFrame: close price (BRL).

    Returns:
        Wide DataFrame: ev_ebitda = (prices × shares / SCALE + net_debt × 1000) / (ebitda × 1000).
        Entries where ebitda × 1000 <= 0 or shares <= 0 or prices <= 0 are NaN.
    """
    market_cap = prices * (shares / _SHARES_SCALE)
    ebitda_brl = ebitda * 1_000.0
    net_debt_brl = net_debt * 1_000.0
    ev = market_cap + net_debt_brl
    result = ev / ebitda_brl
    result = result.where(ebitda_brl > 0)
    result = result.where(shares > 0)
    result = result.where(prices > 0)
    return result
