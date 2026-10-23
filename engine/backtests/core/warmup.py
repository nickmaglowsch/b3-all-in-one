"""Signal warm-up, taken from before the window instead of from inside it.

A ranked strategy cannot rank on twelve months of momentum until it has twelve
months of returns. The runner used to load the market from the requested start,
so that history came out of the window itself: MomSharpe asked for 2019 sat in
cash through 2019 and into 2020 and never saw the COVID crash, and a split-window
test compared books that were not invested over the same dates — a 2019 start
of the 40/40/20 blend printed a -10% max drawdown against -25% for the same
blend over 2013..2026.

So the runner now loads `months_for(...)` of extra history before `start` (sized
by backtests.core.warmup_size), lets the strategy build its signals over all of
it, and cuts the pre-roll off with `cut` before anything is simulated. No trade,
tax event or NAV point lands before `start`. Look-ahead is not touched — a
weight at row t still reads row t-1 — the rows before the window are only
*there* now.

When the plane does not reach back that far (a window opening at the first bar
of the archive) the warm-up falls back inside the window, as before, and
`late_books` says so in `data_warnings`.
"""
from __future__ import annotations

import math
import os

import pandas as pd

from backtests.core import warmup_size
from backtests.core.simulation import _apply_returns

# Slack on top of what the definition asks for: a period label is the period's
# *end*, so a pre-roll cut at an arbitrary date loses up to one period off its
# front — and on a quarterly grid three months is exactly that one period.
MARGIN_MONTHS = 3
# The ceiling. A longer load is a bigger heap (see backtests.core.memory), and a
# window that needs more than ten years of warm-up is a spec to look at, not to
# feed.
MAX_MONTHS = 120
CODE_MONTHS = warmup_size.CODE_MONTHS

# The pre-roll ran short: the archive opens after the date it was read from.
WARNING = "warmup_inside_window"
# Not invested on the window's first rebalance although the history was there:
# the book was empty for a reason of its own (thin universe, a filter, late
# fundamentals coverage).
NOT_INVESTED = "not_invested_at_start"
# How far after the requested load date the first session may fall and still
# count as covered: a pre-roll starting on a weekend or a holiday is not short.
_GRACE = pd.Timedelta(days=7)


def months_for(strat, params: dict, freq: str) -> int:
    """Months of history to load before `start` so `strat` is live on it.

    ``ENGINE_WARMUP_MONTHS`` overrides it for every strategy; 0 is the old
    behaviour, warm-up inside the window — the knob to reach for if a short
    window's pre-roll is what tips a run over its memory budget.
    """
    override = os.environ.get("ENGINE_WARMUP_MONTHS", "").strip()
    if override:
        try:
            return max(0, int(override))
        except ValueError:
            raise ValueError(
                f"ENGINE_WARMUP_MONTHS must be a whole number of months "
                f"(0 disables the pre-roll), got {override!r}") from None
    need = warmup_size.strategy_months(strat, params, freq)
    return int(min(MAX_MONTHS, math.ceil(need) + MARGIN_MONTHS))


def load_start(start: str, months: int) -> str:
    """The date the plane is read from: `months` before `start`."""
    return (pd.Timestamp(start) - pd.DateOffset(months=months)).strftime("%Y-%m-%d")


def in_window(frame, start):
    """`frame`'s rows on or after `start` — the pre-roll cut off.

    A grid label is the end of its period, so the first label kept is the period
    `start` falls in: the same first row a run loaded from `start` had.
    """
    return frame.loc[frame.index >= pd.Timestamp(start)]


def _live(tw: pd.DataFrame) -> pd.Index:
    return tw.index[tw.fillna(0.0).abs().sum(axis=1) > 0]


def cut(returns: pd.DataFrame, tw: pd.DataFrame, start) -> tuple:
    """``(returns, tw)`` from `start` on, with the book held into `start` kept.

    An all-zero row means "hold" to the simulator once it is invested and "not
    started" before — so a hold row that lands first in the window would leave
    the run in cash until the next live row, where a longer run of the same
    strategy is holding a book. The first row instead buys that book: the last
    live pre-roll row, drifted through the returns since, exactly as the
    simulator would have marked it (`_apply_returns`, same clipping). With no
    live row before `start` there is nothing held, and the row stays empty.

    `returns` must be the matrix the simulator gets (`universe.fill_returns`),
    so the drift is the one a longer run books.
    """
    r_in, tw_in = in_window(returns, start), in_window(tw, start)
    if len(tw_in) and not len(_live(tw_in.iloc[:1])):
        held = _held_book(returns, tw, tw_in.index[0])
        if held is not None:
            tw_in = tw_in.copy()
            tw_in.iloc[0] = held.reindex(tw_in.columns).fillna(0.0)
    return r_in, tw_in


def _held_book(returns: pd.DataFrame, tw: pd.DataFrame, first) -> pd.Series | None:
    """The weights the book bought on its last live row before `first` has
    drifted to by the close of `first`, or None if there is no such book."""
    live = _live(tw.loc[tw.index < first])
    if not len(live):
        return None
    row = tw.loc[live[-1]].fillna(0.0)
    book = {t: {"current_value": float(w), "cost_basis": float(w)}
            for t, w in row[row != 0].items()}
    for date in returns.index[(returns.index > live[-1]) & (returns.index <= first)]:
        _apply_returns(book, returns.loc[date])
    nav = sum(p["current_value"] for p in book.values())
    if nav <= 0:
        return None         # wiped out while held; nothing left to carry
    return pd.Series({t: p["current_value"] / nav for t, p in book.items()})


# ── What the run reports ──────────────────────────────────────────────────────

def _first_live(book: pd.DataFrame, opens):
    """The first rebalance from `opens` on that `book` holds something on."""
    live = _live(book.loc[book.index >= opens])
    return live[0] if len(live) else None


def _iso(date) -> str | None:
    return None if date is None else date.date().isoformat()


def _short(shared, loaded_from: str, months: int) -> bool:
    """Whether the history before `start` fell short of the pre-roll asked for."""
    sessions = shared.get("sessions")
    if not months or sessions is None or not len(sessions):
        return True
    return pd.Timestamp(sessions[0]) > pd.Timestamp(loaded_from) + _GRACE


def late_books(name: str, tw: pd.DataFrame, shared,
               loaded_from: str, months: int) -> tuple[str | None, list]:
    """``(invested_from, warnings)`` for a run's in-window weights `tw`.

    `tw` is the book `cut` returned — so a book held into the window already
    shows on its first row, and one wiped out while held does not. `invested_from`
    is the first rebalance on which every book in the run holds something: the
    strategy's own and, in a blend, each strategy sleeve's. A sleeve with an
    empty row has its share parked in CDI, which keeps the blend's rows
    non-empty and would otherwise hide it — whether it is still warming up or
    holding nothing that period, it is not invested in what it is there for.
    None if some book never goes live in the window.

    A book not live on `opens` is reported: `warmup_inside_window` when the
    archive did not reach back as far as the pre-roll asked (the warm-up fell in
    the window), `not_invested_at_start` when it did and the book was empty
    anyway.
    """
    opens = tw.index[0]
    books = {name: tw, **(shared.get("_sleeve_books") or {})}
    firsts = {k: _first_live(b, opens) for k, b in books.items()}
    code = WARNING if _short(shared, loaded_from, months) else NOT_INVESTED
    warnings = [{"code": code,
                 "detail": {"strategy": k, "invested_from": _iso(d),
                            "first_rebalance": _iso(opens)}}
                for k, d in firsts.items() if d is None or d > opens]
    dates = list(firsts.values())
    invested = None if any(d is None for d in dates) else max(dates)
    return _iso(invested), warnings
