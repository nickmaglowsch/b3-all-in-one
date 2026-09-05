"""Long price rows -> the wide (date x ticker) panels a backtest reads.

One concern, split out of `data.py` because it is the one that decides how much
memory a backtest takes. A panel is `sessions x names` at daily resolution: on B3
a few tens of MB, on the US plane over a 2000..today window (~11,000 symbols,
~6,500 sessions) about 580 MB apiece. The loader used to build six of them for
every run whatever the strategy read, out of a long frame it held whole at the
same time — which is how a US backtest reached the worker's `mem_limit` and came
back as `killed by SIGKILL`.

**A panel is a query, not a resident structure.** That is the shape of this
module and the thing to keep true. Nothing that reads daily bars in this
repository does anything *across* tickers with them: every consumer — the
bundle's own MA200 and ATR and mean-reversion features, and all eight strategies
that touch a daily frame — is a per-ticker time-series reduction (`pct_change`,
`rolling`, `ewm`) read at rebalance dates. Cross-sectional work happens on the
rebalance grid, after the reduction. So the universe splits by column, and
`stream_market_panels` walks it a few hundred names at a time: each batch is
loaded, reduced by the caller, and dropped before the next arrives. Peak memory
becomes the batch plus whatever the caller keeps, instead of the whole market.

Everything else here supports that:

  * `load_market_panels` builds only the panels its caller names, for only the
    symbols it names, aligned to a session index it can supply — which is what
    lets a batch's output line up with every other batch's.
  * `reshape` turns each batch's rows into its grids without ever holding both
    the rows and the grids whole.

`data.py` keeps the rest — benchmarks, CDI, fundamentals, PIT sectors — and
re-exports the entry points below so every existing caller keeps its import.
"""
from __future__ import annotations

import os

import pandas as pd

from b3_pipeline import storage

from backtests.core import markets, memory, universe
from backtests.core.reshape import (
    _ffill_in_place, _normalise_long, _pivot_wide,
)


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

    df = _normalise_long(df)

    # Daily financial volume in BRL.
    # The parser already divides COTAHIST VOLTOT's 2 implied decimals,
    # so the DB `volume` column is stored in reais (verified: PETR4 ~1e9/day).
    df["fin_volume"] = df.pop("volume").astype(float)

    print("🔄  Pivoting data to wide format...")
    frames = _pivot_wide(df, ["adj_close", "close", "fin_volume"])
    del df

    # Forward fill prices to handle missing days, but leave volume as NaN/0
    # to accurately calculate averages. The fill stops at each name's delisting;
    # carried past it, it would invent a price for something that no longer
    # trades and hand a book holding it a flat, riskless position forever.
    tradable = universe.tradable_mask(
        universe.load_listing_intervals(db_path),
        frames[0].index, frames[0].columns)
    _ffill_in_place(frames, tradable, upto=2)
    adj_close, close_px, fin_vol = frames

    print(f"✅  Loaded {adj_close.shape[1]} unique standard tickers.")
    return adj_close, close_px, fin_vol


# What to SELECT per market, per panel, aliased to the B3 column names every
# downstream reader already speaks. Two things happen in the non-B3 projection:
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
#
# One entry per panel rather than one string for all six, because a caller that
# wants four of them should not pay to build the other two. See `load_market_panels`.
_B3_PANEL_SQL = {
    "adj_close": "adj_close",
    "split_adj_high": "split_adj_high",
    "split_adj_low": "split_adj_low",
    "split_adj_close": "split_adj_close",
    "close": "close",
    # Already in reais: the parser handles VOLTOT's implied decimals.
    "fin_volume": "volume AS fin_volume",
}
_CONVERTED_PANEL_SQL = {
    "adj_close": "adj_close_brl AS adj_close",
    "split_adj_high": "high * fx_rate AS split_adj_high",
    "split_adj_low": "low * fx_rate AS split_adj_low",
    "split_adj_close": "split_adj_close * fx_rate AS split_adj_close",
    "close": "close_brl AS close",
    "fin_volume": "volume * fx_rate AS fin_volume",
}

# The historical order of `load_market_hlc_data`'s tuple. Price panels first,
# turnover last, because `_ffill_in_place` fills a prefix of the list.
HLC_PANELS = ("adj_close", "split_adj_high", "split_adj_low", "split_adj_close",
              "close", "fin_volume")

# What a backtest needs before any strategy has said what it reads. The two
# missing ones are the daily high and low, whose only consumer is the
# mean-reversion high-low range — see shared_data, which asks for them lazily.
BASE_PANELS = ("adj_close", "split_adj_close", "close", "fin_volume")


# Names per streamed batch. Sized so one batch's panels are tens of MB whatever
# the market: 400 names over a 26-year daily window is ~21 MB a panel. Bigger
# batches amortise the per-query overhead but buy back the memory this exists to
# avoid; smaller ones turn one scan into hundreds. ENGINE_PANEL_BATCH overrides.
SYMBOL_BATCH = 400


def _symbol_filter(spec, symbols) -> tuple:
    """``(SQL fragment, params)`` restricting a panel query to `symbols`.

    Generated placeholders, never an interpolated literal: these are database
    values, and the one place a market's own data must not reach is its query text.
    """
    if symbols is None:
        return "", {}
    names = list(symbols)
    if not names:
        # An empty IN () is a syntax error in SQLite and a footgun in Postgres.
        return " AND 1 = 0", {}
    keys = [f"sym{i}" for i in range(len(names))]
    fragment = f" AND {spec.symbol_column} IN ({', '.join(':' + k for k in keys)})"
    return fragment, dict(zip(keys, names))


def market_symbols(db_path: str, start: str, end: str,
                   market: str = markets.DEFAULT) -> list:
    """Every instrument that printed in the window, sorted — the batching axis.

    One index scan, and it is the only way to split the universe by column before
    reading any of it. The shape filter is applied here too, so a batch never
    carries a name the panels would have dropped anyway.

    Sorted through pandas, not `sorted()`: this order is where the batch
    boundaries fall, and `_pivot_wide` orders each batch's columns with pandas'
    own sort. Two orders that agree on ASCII tickers and disagree elsewhere would
    make a streamed frame a column permutation of the whole-market one.
    """
    spec = markets.get(market)
    b3 = spec.table == "prices"
    shape = f" AND {spec.ticker_sql}" if spec.ticker_sql else ""
    scope = "" if b3 else " AND market = :market"
    params = {"start": start, "end": end}
    if not b3:
        params["market"] = spec.key
    sql = (f"SELECT DISTINCT {spec.symbol_column} AS ticker FROM {spec.table}"    # noqa: S608
           f" WHERE date >= :start AND date <= :end{scope}{shape}")
    with storage.get_connection(db_path) as conn:
        df = storage.read_sql(sql, conn, params=params)
    if df.empty:
        return []
    return list(pd.Index(df["ticker"].astype(str)).sort_values())


def market_sessions(db_path: str, start: str, end: str,
                    market: str = markets.DEFAULT) -> pd.DatetimeIndex:
    """Every date the market printed on in the window, sorted.

    The index every batch is aligned to. Taken from the market rather than from a
    calendar, so holidays, half days and the plane's own gaps are whatever the
    plane says they are — which is what makes a batch's frame identical to its
    slice of a whole-market load.
    """
    spec = markets.get(market)
    b3 = spec.table == "prices"
    shape = f" AND {spec.ticker_sql}" if spec.ticker_sql else ""
    scope = "" if b3 else " AND market = :market"
    params = {"start": start, "end": end}
    if not b3:
        params["market"] = spec.key
    sql = (f"SELECT DISTINCT date FROM {spec.table}"                             # noqa: S608
           f" WHERE date >= :start AND date <= :end{scope}{shape}")
    with storage.get_connection(db_path) as conn:
        df = storage.read_sql(sql, conn, params=params)
    if df.empty:
        return pd.DatetimeIndex([], name="date")
    return pd.DatetimeIndex(sorted(pd.to_datetime(df["date"])), name="date")


def batch_size() -> int:
    """`SYMBOL_BATCH`, or ``ENGINE_PANEL_BATCH`` when a deployment has tuned it."""
    raw = os.environ.get("ENGINE_PANEL_BATCH", "").strip()
    try:
        return max(1, int(raw)) if raw else SYMBOL_BATCH
    except ValueError:
        return SYMBOL_BATCH


def stream_market_panels(db_path: str, start: str, end: str,
                         market: str = markets.DEFAULT,
                         panels: tuple = HLC_PANELS,
                         size: int | None = None,
                         sessions=None, symbols=None):
    """Yield ``{panel: frame}`` a few hundred names at a time.

    The whole market's daily bars never exist at once. Each batch is a slice of
    what `load_market_panels` would have returned — same rows, same fill, same
    dtypes, just fewer columns — so a caller that reduces each batch per ticker
    and concatenates the results gets exactly the whole-market answer, at the
    cost of one batch instead of the market. Reduce inside the loop: holding a
    batch past the next one is a bug the generator cannot prevent.
    """
    if sessions is None:
        sessions = market_sessions(db_path, start, end, market)
    if symbols is None:
        symbols = market_symbols(db_path, start, end, market)
    size = size or batch_size()
    total = (len(symbols) + size - 1) // size
    print(f"⬇  Streaming {markets.get(market).key} daily bars from "
          f"{_safe_dsn(db_path)} ({start} to {end}): {len(symbols):,} names in "
          f"{total} batch(es) of {size}...")
    for n, i in enumerate(range(0, len(symbols), size), start=1):
        batch = symbols[i:i + size]
        yield load_market_panels(db_path, start, end, market, panels,
                                 symbols=batch, sessions=sessions, quiet=True)
        # The caller has reduced this batch and dropped it, so this is the moment
        # its memory is free — and the moment glibc would otherwise keep it. See
        # memory.release: without this the batches accumulate in the arena and
        # streaming saves nothing the container can see.
        memory.release()
        print(f"   … batch {n}/{total} ({len(batch)} names) reduced", flush=True)


def load_market_panels(db_path: str, start: str, end: str,
                       market: str = markets.DEFAULT,
                       panels: tuple = HLC_PANELS,
                       symbols=None, sessions=None, quiet: bool = False) -> dict:
    """``{panel: wide (date x ticker) frame}`` for one market and window.

    Three levers, and the whole point of the module is that a caller uses them
    rather than asking for the market:

    ``panels`` — which frames to build. Every one is `sessions x names` at daily
    resolution; on the US plane over a 2000..today window that is ~580 MB each.

    ``symbols`` — which names to build them for. ``None`` means every name in the
    window, which is what makes the answer big; `stream_market_panels` passes a
    few hundred at a time.

    ``sessions`` — the daily index to align to. A symbol subset trades on only
    some of the market's days, so its own scatter produces a shorter index than
    another subset's. Reindexing onto the market's full session index *before*
    the forward fill lines every batch up with every other, and makes each one
    identical to its slice of a whole-market load: the fill then bridges a gap
    the batch did not print on exactly as it would have in the larger frame.

    Prices are forward-filled while each name was listed and blanked after
    (`universe.ffill_until_delisting`); turnover is left unfilled so a day a name
    did not trade averages in as missing rather than as a repeat.
    """
    spec = markets.get(market)
    unknown = [p for p in panels if p not in HLC_PANELS]
    if unknown:
        raise ValueError(f"Unknown price panel(s) {unknown}. "
                         f"Choose from: {', '.join(HLC_PANELS)}")
    # Filled last, and only if asked for — `_ffill_in_place` fills a prefix.
    wanted = [p for p in HLC_PANELS if p in panels and p != "fin_volume"]
    prices = len(wanted)
    if "fin_volume" in panels:
        wanted.append("fin_volume")

    if not quiet:
        print(f"⬇  Loading {spec.key} HLC data from {_safe_dsn(db_path)} "
              f"({start} to {end})...")

    b3 = spec.table == "prices"
    projection = _B3_PANEL_SQL if b3 else _CONVERTED_PANEL_SQL
    shape = f" AND {spec.ticker_sql}" if spec.ticker_sql else ""
    scope = "" if b3 else " AND market = :market"
    pick, params = _symbol_filter(spec, symbols)
    params.update(start=start, end=end)
    if not b3:
        params["market"] = spec.key
    query = (
        f"SELECT date, {spec.symbol_column} AS ticker,"                          # noqa: S608
        f" {', '.join(projection[p] for p in wanted)}"
        f" FROM {spec.table} WHERE date >= :start AND date <= :end"
        f"{scope}{shape}{pick}"
    )

    with storage.get_connection(db_path) as conn:
        df = storage.read_sql(query, conn, params=params)

    df = _normalise_long(df)

    if not quiet:
        print("🔄  Pivoting HLC data to wide format...")
    frames = _pivot_wide(df, wanted)
    del df                       # the long frame is empty by now; drop the shell

    if sessions is not None:
        # Before the fill, never after: the fill has to see the market's whole
        # calendar to bridge a day this subset did not print on the way the
        # whole-market frame would have.
        index = pd.DatetimeIndex(sessions, name="date")
        for i in range(len(frames)):
            frames[i] = frames[i].reindex(index)

    # Forward fill prices to handle missing days, leave volume as-is. Stops at
    # each name's delisting — see load_b3_data.
    tradable = universe.tradable_mask(
        universe.load_listing_intervals(db_path, spec.key),
        frames[0].index, frames[0].columns)
    _ffill_in_place(frames, tradable, upto=prices)

    if not quiet:
        print(f"✅  Loaded HLC for {frames[0].shape[1]} unique {spec.key} tickers.")
    return dict(zip(wanted, frames))


def load_market_hlc_data(db_path: str, start: str, end: str,
                         market: str = markets.DEFAULT) -> tuple:
    """The six price frames for any market, in the historical B3 order.

    `load_b3_hlc_data` is this with market="B3"; the B3 path is unchanged. A
    caller that does not want all six should use `load_market_panels` and name
    them — this one is the whole-panel convenience the older call sites use.
    """
    panels = load_market_panels(db_path, start, end, market)
    return tuple(panels[p] for p in HLC_PANELS)


def load_b3_hlc_data(db_path: str, start: str, end: str) -> tuple:
    """Split-adjusted HLC + adj_close + raw close + financial volume, B3.

    Uses split_adj_* (split-only adjustment) for ATR to avoid false True Range
    spikes from ex-dividend gaps. Returns adj_close separately for the returns
    matrix (simulation needs dividend-adjusted returns).
    """
    return load_market_hlc_data(db_path, start, end, "B3")
