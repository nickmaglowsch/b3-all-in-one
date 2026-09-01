"""Point-in-time tradable universe, and what happens when an instrument dies.

The plane keeps delisted instruments — that is the whole reason `instruments`
carries a listing/delisting interval rather than an `is_active` flag. This module
is what turns that record into the three things a backtest needs:

1. **Who could be traded on each date.** Not "who exists today", which is the
   survivorship bias itself, and not "who existed at the start", which forgets
   the names that listed mid-window.
2. **When to stop forward-filling a price.** The loaders `ffill` to bridge days a
   name did not print. Carried past a delisting, that same fill invents a price
   for something that no longer trades, and a book holding it earns a flat,
   riskless, entirely fictional position for the rest of the window.
3. **What the holder actually got.** A position does not evaporate at delisting;
   it is liquidated. Usually at the last mark (an acquisition, a venue delisting
   where the asset trades on elsewhere), sometimes at nothing (a bankruptcy, a
   token that went to zero). That is a return, and it has to reach the simulator
   as one so the loss lands in the tax ledger like any other.

**This is a no-op on an empty `instruments` table.** A ticker with no interval is
tradable on every date, so a plane whose security master has not been backfilled
produces exactly the frames it produced before this module existed. That is
deliberate: it lets the semantics land without silently rewriting every stored
backtest, and it means the behaviour change arrives with the backfill, visibly,
rather than with a deploy.

What it replaces: `data._apply_delisted_filter`, which *dropped* names that
delisted before the window started. That is de-duplication, not survivorship
correction — it removes exactly the dead names a survivorship-free backtest is
supposed to keep, and it left the names that died mid-window forward-filling
forever.
"""

import numpy as np
import pandas as pd

_COLUMNS = ["ticker", "listing_date", "delisting_date", "is_worthless"]


def load_listing_intervals(db_path, market: str = "B3") -> pd.DataFrame:
    """[ticker, listing_date, delisting_date, is_worthless] for one market.

    Returns an empty frame rather than raising when `instruments` is missing or
    has no rows for the market — the same graceful degradation the delisted
    filter this replaces had, and what makes the no-op property above hold on a
    plane that predates the table.
    """
    from b3_pipeline import storage

    sql = ("SELECT symbol AS ticker, listing_date, delisting_date, is_worthless"
           " FROM instruments WHERE market = ?")
    try:
        with storage.get_connection(db_path) as conn:
            df = storage.read_sql(sql, conn, params=[market])
    except Exception:                                   # noqa: BLE001
        return pd.DataFrame(columns=_COLUMNS)
    if df.empty:
        return pd.DataFrame(columns=_COLUMNS)
    for col in ("listing_date", "delisting_date"):
        df[col] = pd.to_datetime(df[col], errors="coerce")
    df["is_worthless"] = df["is_worthless"].fillna(0).astype(int)
    return df[_COLUMNS]


def _bounds(intervals: pd.DataFrame, tickers) -> tuple:
    """Per-ticker listing/delisting bounds aligned to `tickers`, as arrays.

    Tickers with no row get NaT on both sides, which the comparisons below read
    as "always listed, never delisted".
    """
    cols = pd.Index(list(tickers))
    if intervals is None or intervals.empty:
        nat = np.full(len(cols), np.datetime64("NaT"), dtype="datetime64[ns]")
        return cols, nat, nat
    # Last row wins if a ticker somehow appears twice; the writer's primary key
    # makes that impossible from the plane, but a hand-built frame in a test can.
    idx = intervals.drop_duplicates("ticker", keep="last").set_index("ticker")
    listed = idx["listing_date"].reindex(cols).to_numpy("datetime64[ns]")
    delisted = idx["delisting_date"].reindex(cols).to_numpy("datetime64[ns]")
    return cols, listed, delisted


def tradable_mask(intervals: pd.DataFrame, dates, tickers) -> pd.DataFrame:
    """Boolean (date x ticker): was this instrument tradable on this date?

    Both bounds are inclusive — the delisting date is the last day it traded, at
    a real price, and excluding it would throw away a genuine session.
    """
    idx = pd.DatetimeIndex(pd.to_datetime(dates))
    cols, listed, delisted = _bounds(intervals, tickers)
    if len(idx) == 0 or len(cols) == 0:
        return pd.DataFrame(index=idx, columns=cols, dtype=bool)

    day = idx.to_numpy("datetime64[ns]")[:, None]
    # NaT compares False under every operator, so each side is written as
    # "no bound, or inside it" rather than relying on NaT arithmetic.
    after_listing = np.isnat(listed) | (day >= listed)
    before_delisting = np.isnat(delisted) | (day <= delisted)
    return pd.DataFrame(after_listing & before_delisting, index=idx, columns=cols)


def ffill_until_delisting(px: pd.DataFrame, tradable: pd.DataFrame) -> pd.DataFrame:
    """Forward-fill prices, then blank everything past each name's delisting.

    The fill still bridges the gaps it was there for — a name that did not print
    on a given day keeps its last price — but it stops at the edge of the
    instrument's life instead of running to the end of the window.
    """
    filled = px.ffill()
    if tradable is None or tradable.empty:
        return filled
    mask = tradable.reindex(index=filled.index, columns=filled.columns)
    return filled.where(mask.fillna(True).astype(bool))


def delisting_returns(intervals: pd.DataFrame, dates, tickers) -> pd.DataFrame:
    """The terminal return each delisted name booked, NaN everywhere else.

    Placed on the first grid date strictly after the delisting date: the last
    traded day is a normal session at a real price, and what the holder recovers
    (or does not) is settled in the period after it.

      is_worthless = 0  ->   0.0   liquidated at the last mark
      is_worthless = 1  ->  -1.0   wiped out

    A name whose delisting falls beyond the end of the window gets no cell — it
    was still trading for every date the backtest can see, which is true.
    """
    idx = pd.DatetimeIndex(pd.to_datetime(dates))
    cols, _, delisted = _bounds(intervals, tickers)
    out = pd.DataFrame(np.nan, index=idx, columns=cols)
    if len(idx) == 0 or intervals is None or intervals.empty:
        return out

    worthless = (intervals.drop_duplicates("ticker", keep="last")
                 .set_index("ticker")["is_worthless"].reindex(cols).fillna(0))
    grid = idx.to_numpy("datetime64[ns]")
    for pos, ticker in enumerate(cols):
        stamp = delisted[pos]
        if np.isnat(stamp):
            continue
        after = np.flatnonzero(grid > stamp)
        if len(after) == 0:
            continue
        out.iloc[after[0], pos] = -1.0 if worthless.iloc[pos] else 0.0
    return out


def fill_returns(ret: pd.DataFrame, delist_ret: pd.DataFrame) -> pd.DataFrame:
    """The returns matrix the simulator consumes: delistings first, then zeros.

    Replaces a bare `ret.fillna(0.0)`. Once the forward fill stops at delisting a
    dead name's return is NaN, and filling that with zero would freeze the
    position at its last value for the rest of the run — the very bug stopping
    the fill was meant to remove, moved one step downstream.
    """
    if delist_ret is None or delist_ret.empty:
        return ret.fillna(0.0)
    aligned = delist_ret.reindex(index=ret.index, columns=ret.columns)
    return ret.where(aligned.isna(), aligned).fillna(0.0)


def survivorship(db_path, prices: pd.DataFrame, market: str = "B3") -> tuple:
    """`(tradable, delist_ret)` shaped like `prices`, off one read of the master.

    The two frames always travel together — a mask that hides a dead name without
    the return that killed it just makes the position vanish — so they come from
    one `load_listing_intervals` rather than two. Taking the price frame rather
    than an index and a column list is what guarantees all three align.
    """
    intervals = load_listing_intervals(db_path, market)
    return (tradable_mask(intervals, prices.index, prices.columns),
            delisting_returns(intervals, prices.index, prices.columns))


def realise_write_offs(positions: dict) -> float:
    """Drop positions worth nothing from a simulator ledger; return the loss.

    The last step of a delisting at zero, and the one that is easy to miss. The
    terminal return above takes the position's value to nothing, but the ledger
    entry stays: `compute_trades` diffs a zero holding against a zero target and
    gets zero, so the name lands in neither sells nor buys and sits there forever
    as a ghost still carrying its cost basis.

    That basis is a loss the holder really took. In Brazil it is the prejuízo a
    compensar, and abandoning it overstates the tax the book owes on its next
    gain — so the position is removed and its cost basis returned as a realised
    loss for the caller to fold into the carryforward.
    """
    dead = [t for t, p in positions.items() if p["current_value"] == 0]
    return sum(max(0.0, positions.pop(t)["cost_basis"]) for t in dead)
