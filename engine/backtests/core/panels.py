"""Long price rows -> the wide (date x ticker) panels a backtest reads.

One concern, split out of `data.py` because it is the one that decides how much
memory a backtest takes. A panel is `sessions x names` at daily resolution: on B3
a few tens of MB, on the US plane over a 2000..today window (~11,000 symbols,
~6,500 sessions) about 580 MB apiece. The loader used to build six of them for
every run whatever the strategy read, out of a long frame it held whole at the
same time — which is how a US backtest reached the worker's `mem_limit` and came
back as `killed by SIGKILL`.

Three things here keep that bounded, none of which changes a number:

  * `load_market_panels` builds only the panels its caller names.
  * `_pivot_wide` scatters one value column at a time, popping each off the long
    frame as it goes, so the long frame and the panels are never both whole.
  * `_ffill_in_place` overwrites its input list rather than building a second one.

`data.py` keeps the rest — benchmarks, CDI, fundamentals, PIT sectors — and
re-exports the entry points below so every existing caller keeps its import.
"""
from __future__ import annotations

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


def _normalise_long(df: pd.DataFrame) -> pd.DataFrame:
    """A freshly-read long frame, with its two key columns made cheap.

    ``date`` becomes datetime64 — every caller did this itself. ``ticker`` becomes
    a category, which is the part that matters: on pandas 2 the driver hands back
    a distinct Python ``str`` object *per row*, so the US plane's ~30M bars carry
    a couple of gigabytes of duplicated four-character symbols. Factorizing to
    codes plus one copy of each name is the same column an order of magnitude
    smaller, and `_pivot_wide` has to factorize it anyway. (pandas 3 infers a
    string dtype that is already compact; the conversion is cheap there and keeps
    one code path rather than two.)

    The categorical never leaves this module: `_pivot_wide` reads it through
    ``factorize`` and materialises the names into a plain object Index, so no
    downstream ``reindex`` ever meets a CategoricalIndex it can refuse.
    """
    df["date"] = pd.to_datetime(df["date"])
    if not isinstance(df["ticker"].dtype, pd.CategoricalDtype):
        df["ticker"] = df["ticker"].astype("category")
    return df


def _positions(keys: pd.Series) -> tuple:
    """``(position per row, the sorted distinct keys)`` — the halves of a pivot.

    A categorical column already carries exactly this: `astype("category")` sorts
    its categories, so the codes *are* the sorted positions and re-factorizing
    would only redo the work `_normalise_long` paid for. Taking the categories
    rather than the codes' own index is also what keeps the resulting frames'
    columns a plain Index: `pd.factorize` of a categorical hands back a
    CategoricalIndex, and a CategoricalIndex refuses the very `reindex` calls
    every downstream reader makes.
    """
    if isinstance(keys.dtype, pd.CategoricalDtype):
        keys = keys.cat.remove_unused_categories()
        codes = keys.cat.codes.to_numpy()
        if (codes < 0).any():
            raise ValueError(f"price rows with no {keys.name} cannot be reshaped")
        return codes, keys.cat.categories
    codes, uniques = pd.factorize(keys, sort=True)
    if (codes < 0).any():
        raise ValueError(f"price rows with no {keys.name} cannot be reshaped")
    return codes, pd.Index(uniques)


def _pivot_wide(df: pd.DataFrame, values: list[str]) -> list[pd.DataFrame]:
    """One (date x ticker) grid per value column, built by scatter, not by pivot.

    ``df.pivot(index, columns, values=[...])`` reshapes every value column at
    once, so the whole block of wide frames exists *alongside* the long frame
    that produced it. On B3 that is a few hundred MB and nobody noticed. On the
    US plane a window of 2000..today is ~11,000 symbols over ~6,500 sessions:
    the six wide frames are ~3.4 GB and the long frame another ~3 GB, and both
    were live at the same instant. That is what the worker's mem_limit killed.

    Here the row/column positions are factorized once and each value column is
    ``pop``ed off the long frame, scattered into its own grid and released. The
    long frame shrinks by exactly as much as the wide frames grow, so the peak
    is roughly the answer rather than the answer plus its input. Same values,
    same dtypes, same sorted index and columns — ``pivot`` sorts both, and so
    does ``factorize(sort=True)``.

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

    rows, dates = _positions(df["date"])
    cols, tickers = _positions(df["ticker"])
    index = pd.DatetimeIndex(dates, name="date")
    columns = pd.Index(tickers, name="ticker")

    # `pivot` raises on a repeated (date, ticker); a scatter would silently keep
    # whichever row landed last. The plane's primary keys make it impossible, so
    # this is a guard rather than a code path — but a silent wrong price is not
    # an acceptable way to find out the key changed. One bool grid, O(n).
    seen = np.zeros((len(index), len(columns)), dtype=bool)
    seen[rows, cols] = True
    if int(seen.sum()) != len(rows):
        raise ValueError("Index contains duplicate entries, cannot reshape")
    del seen

    out = []
    for value in values:
        # pop, not [], so the long frame gives back the column we just consumed.
        grid = np.full((len(index), len(columns)), np.nan)
        grid[rows, cols] = df.pop(value).astype(float).to_numpy()
        out.append(pd.DataFrame(grid, index=index, columns=columns))
    return out


def _ffill_in_place(frames: list, tradable: pd.DataFrame, upto: int) -> None:
    """``ffill_until_delisting`` over ``frames[:upto]``, in place.

    ``upto`` is where the price panels end and financial volume begins: volume is
    deliberately left unfilled so a day a name did not trade averages in as
    missing rather than as a repeat of the last session's turnover.

    In place, because the obvious ``[f(x) for x in frames]`` holds every input
    alive until the last output is built — five daily panels in and five out,
    ten live at the peak. Overwriting the list slot drops each input the moment
    its output exists, so the peak is the panels plus one. Callers must hand the
    list over rather than keep their own names for its frames, or the slot
    assignment frees nothing.
    """
    for i in range(upto):
        frames[i] = universe.ffill_until_delisting(frames[i], tradable)


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


def load_market_panels(db_path: str, start: str, end: str,
                       market: str = markets.DEFAULT,
                       panels: tuple = HLC_PANELS) -> dict:
    """``{panel: wide (date x ticker) frame}`` for one market and window.

    ``panels`` is the lever this exists for. Every panel is `sessions x names` at
    daily resolution — on the US plane over a 2000..today window, ~580 MB each —
    and the loader used to build all six for every run whatever the strategy
    read. Naming the ones you need keeps the unread ones out of the query, out of
    the long frame, and out of memory.

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

    print(f"⬇  Loading {spec.key} HLC data from {_safe_dsn(db_path)} "
          f"({start} to {end})...")

    b3 = spec.table == "prices"
    projection = _B3_PANEL_SQL if b3 else _CONVERTED_PANEL_SQL
    shape = f" AND {spec.ticker_sql}" if spec.ticker_sql else ""
    scope = "" if b3 else " AND market = :market"
    query = (
        f"SELECT date, {spec.symbol_column} AS ticker,"                          # noqa: S608
        f" {', '.join(projection[p] for p in wanted)}"
        f" FROM {spec.table} WHERE date >= :start AND date <= :end{scope}{shape}"
    )
    params = {"start": start, "end": end}
    if not b3:
        params["market"] = spec.key

    with storage.get_connection(db_path) as conn:
        df = storage.read_sql(query, conn, params=params)

    df = _normalise_long(df)

    print("🔄  Pivoting HLC data to wide format...")
    frames = _pivot_wide(df, wanted)
    del df                       # the long frame is empty by now; drop the shell

    # Forward fill prices to handle missing days, leave volume as-is. Stops at
    # each name's delisting — see load_b3_data.
    tradable = universe.tradable_mask(
        universe.load_listing_intervals(db_path, spec.key),
        frames[0].index, frames[0].columns)
    _ffill_in_place(frames, tradable, upto=prices)

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
