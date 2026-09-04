"""One market and window, read a batch of names at a time.

The bundle's door to the plane. `panels.py` knows how to build a daily frame;
this knows *what a run needs* — the rebalance grid it keeps, and the daily frames
it does not — and it is where the promise "a panel is a query" is kept.

Held apart from shared_data because the two answer different questions. This one
is about the market: which names, which sessions, which bars, in what order and
how few at a time. shared_data is about the bundle: what a strategy finds under
each name, and which of it is built yet.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from backtests.core import memory, panels, splits

# The bundle's names for the plane's panels. `close_px`/`fin_vol` are what every
# strategy and every stored spec has always called them; the plane calls the same
# columns `close` and `fin_volume`.
PANEL_OF = {
    "adj_close": "adj_close",
    "split_adj_close": "split_adj_close",
    "split_adj_high": "split_adj_high",
    "split_adj_low": "split_adj_low",
    "close_px": "close",
    "fin_vol": "fin_volume",
}

# What the rebalance grid is reduced from. Not `split_adj_close`: nothing in the
# grid comes off it, and on the US plane it is ~580 MB of bars to read for that.
_GRID_PANELS = ("adj_close", "close", "fin_volume")


class MarketView:
    """The bars of one market and window, in pieces.

    Two index scans up front give the axes everything else is expressed in: the
    sessions every batch is aligned to, and the names the batches are cut from.
    After that nothing here holds the market — `grid` reduces it as it arrives,
    and `stream`/`panel` hand a caller as much or as little of it as asked for.
    """

    def __init__(self, db_path: str, start: str, end: str, spec) -> None:
        self.db_path, self.start, self.end, self.spec = db_path, start, end, spec
        self.sessions = panels.market_sessions(db_path, start, end, spec.key)
        self.symbols = panels.market_symbols(db_path, start, end, spec.key)
        # Two scans of the price table for two small lists. What they leave
        # behind is freed heap the allocator holds unless asked — ~250 MB on the
        # US plane, before a single bar has been read.
        memory.release()

    # ── the grid a run keeps ─────────────────────────────────────────────────
    def grid(self, freq: str) -> tuple:
        """``(px, raw_close, adtv)`` on the rebalance grid, a batch at a time.

        This is the change that made a US backtest fit. The grid is three frames
        of `rebalances x names` — 301 x 11,000 for a 2000..today US window, ~26 MB
        each — reduced from daily bars that are 6,540 x 11,000, ~580 MB a panel.
        Reading the market to build them meant holding twenty times the answer.

        Every step is per ticker: the split fix is a per-column loop, and
        `resample().last()`/`.mean()` reduce each column independently. So a
        batch's contribution is its own columns of the whole-market result, and
        the concatenation is the frame a whole-market load produced rather than
        an approximation of it. Cross-sectional work happens after, on the grid.
        """
        px, raw, adtv = [], [], []
        for batch in panels.stream_market_panels(
                self.db_path, self.start, self.end, self.spec.key,
                panels=_GRID_PANELS, sessions=self.sessions, symbols=self.symbols):
            bars = self._corrected(batch)
            px.append(bars.resample(freq).last())
            raw.append(batch["close"].resample(freq).last())
            # Turnover is not forward-filled, so this is the mean of the month's
            # actual prints — a day the name did not trade is missing, not a repeat.
            adtv.append(batch["fin_volume"].resample(freq).mean())
            del batch, bars

        if not px:          # a plane with no bars in the window; CI runs on one
            empty = pd.DataFrame(index=pd.DatetimeIndex([], name="date"),
                                 columns=pd.Index([], name="ticker"), dtype=float)
            return empty, empty.copy(), empty.copy()
        # Every batch was aligned to the same sessions before it was reduced, so
        # all three concatenations are exact column unions with nothing to fill.
        return (pd.concat(px, axis=1), pd.concat(raw, axis=1),
                pd.concat(adtv, axis=1))

    # ── the daily bars a run does not keep ───────────────────────────────────
    def stream(self, name, columns=None, reduce=None):
        """Yield daily bars a batch of names at a time, reduced as they go.

        The reason no daily frame has to be resident: a caller whose work is per
        ticker — which is all of them, see panels.py — gets the market in pieces
        and keeps only what it makes of them.

        `reduce` runs on each batch before it is yielded. It must be per ticker,
        and it may return a grid-shaped frame, which is how a caller turns daily
        bars into rebalance-grid features without a daily frame ever existing.

        ``name`` is one bundle key, or a tuple of them when a reduction needs
        several of the same bars — the high-low range needs all three sides. With
        a tuple the caller receives a dict keyed the same way.
        """
        keys = (name,) if isinstance(name, str) else tuple(name)
        asked = tuple(dict.fromkeys(PANEL_OF[k] for k in keys))
        for batch in panels.stream_market_panels(
                self.db_path, self.start, self.end, self.spec.key,
                panels=self._with_split_inputs(asked), sessions=self.sessions,
                symbols=list(self.symbols if columns is None else columns)):
            if "adj_close" in asked:
                batch["adj_close"] = self._corrected(batch)
            got = (batch[asked[0]] if len(keys) == 1
                   else {k: batch[PANEL_OF[k]] for k in keys})
            yield reduce(got) if reduce is not None else got
            del batch, got

    def panel(self, name, columns=None, reduce=None) -> pd.DataFrame:
        """`stream`, gathered into one frame.

        Written straight into the answer's columns rather than concatenated:
        asking `panels.load_market_panels` for ten thousand symbols in one query
        is the whole-market read this design exists to avoid — measured at US
        scale, it cost 5.6 GB to produce a 508 MB frame.
        """
        cols = list(self.symbols if columns is None else columns)
        at = {c: i for i, c in enumerate(cols)}
        out = index = None
        for frame in self.stream(name, cols, reduce):
            if out is None:
                index = frame.index
                out = np.full((len(index), len(cols)), np.nan)
            elif not frame.index.equals(index):
                frame = frame.reindex(index)
            # By name, not by position: a symbol the caller asked for that the
            # plane has no bars for is absent from its batch, and every column
            # after it would otherwise land one place to the left.
            out[:, [at[c] for c in frame.columns]] = frame.to_numpy()
        columns_index = pd.Index(cols, name="ticker")
        if out is None:
            return pd.DataFrame(index=pd.DatetimeIndex(self.sessions, name="date"),
                                columns=columns_index, dtype=float)
        return pd.DataFrame(out, index=index, columns=columns_index)

    # ── the one thing a batch cannot do alone ────────────────────────────────
    def _with_split_inputs(self, asked: tuple) -> tuple:
        """`asked`, plus what the split detector needs to run on adj_close.

        An unrecorded grupamento is corrected against the raw close and the
        turnover of the same bars, so a corrected adj_close cannot be read alone.
        B3 only; the other two planes have nothing to detect.
        """
        extra = ("close", "fin_volume") if (
            "adj_close" in asked and self.spec.detect_splits) else ()
        return tuple(dict.fromkeys((*asked, *extra)))

    def _corrected(self, batch: dict) -> pd.DataFrame:
        """`batch["adj_close"]`, with unrecorded splits fixed where they happen.

        The detector fires on a persistent, calm-volume overnight repricing by a
        clean ratio — an unrecorded grupamento on B3, an ordinary Tuesday in
        crypto, where it would silently rewrite price history. It is a per-column
        loop, so a batch corrects its own names and nothing else.
        """
        if not self.spec.detect_splits:
            return batch["adj_close"]
        return splits.detect_and_fix_unrecorded(
            batch["adj_close"], batch["close"], batch["fin_volume"])
