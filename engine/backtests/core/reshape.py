"""Long price rows -> a wide (date x ticker) grid.

The reshape, on its own, because it is where the memory goes and because both
loaders go through it. `df.pivot(index, columns, values=[...])` reshapes every
value column at once, so the whole block of wide frames exists *alongside* the
long frame that produced it; here the row and column positions are factorized
once and each value column is popped off the long frame, scattered into its own
grid and released. The long frame shrinks by exactly as much as the grids grow,
so the peak is roughly the answer rather than the answer plus its input.

Nothing here knows where the rows came from — see panels.py for that.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from backtests.core import universe


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
