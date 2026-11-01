"""Series the plane stores in `external_prices` rather than in `prices`.

Two kinds, both read by `data.download_benchmark`: the reserved `$` levels
(`$CDI`, `$IBOV`, the ANBIMA indexes) and the renda-fixa ETFs, which B3 books
outside COTAHIST. Split out of data.py, which was already past the module-size
gate; data re-exports `download_index_level` so its callers keep their import.

`_plane_series` is imported per call, not at the top: data imports this module
while it is itself still loading.
"""
from __future__ import annotations

import pandas as pd

from b3_pipeline import config


def rf_etf_series(symbol: str, start: str, end: str) -> pd.Series:
    """A renda-fixa ETF's closes, with its ANBIMA index spliced in front of them.

    B3 books these ETFs outside COTAHIST, so their own closes are in
    `external_prices` and only from mid-2025 (b3_pipeline/external_prices.py).
    Before the first one, the series follows the fund's index
    (config.RF_ETF_INDEX) day by day, each day's return reduced by the fund's
    fee — (1 + r) · (1 − fee)^(1/252) — and chained backward from the ETF's own
    first close, so the line is continuous where the two meet and every return
    the backtest reads before it is the index's, net of what the fund charges.

    The chain needs the index's level on the ETF's first day. Without it, the
    step from the index's last level to that first close is a return nobody
    published, so nothing is spliced at all rather than inventing it: the series
    is the ETF's own closes, the blend parks the weight in CDI before them, and
    the save-time check (sleeve_tickers._splice_warning) says why. An index that
    starts after `start` leaves the series starting there too — the same CDI
    parking, as for any late listing. An ETF with no own close has nothing to
    anchor to and comes back empty, never a level invented from the index.
    """
    from backtests.core.data import _plane_series

    # The whole own history, not the window: the anchor is the ETF's first
    # close ever, which lies past `end` for a window that ends before it.
    own = _plane_series("external_prices", "close", symbol, "1900-01-01", "2999-12-31")
    proxy = config.RF_ETF_INDEX.get(symbol)
    window = slice(pd.Timestamp(start), pd.Timestamp(end))
    if own.empty or proxy is None or own.index[0] <= pd.Timestamp(start):
        return own.loc[window]
    anchor = own.index[0]

    base = _plane_series("external_prices", "close", proxy.index,
                         start, str(anchor.date()))
    if base.empty or base.index[-1] != anchor:
        return own.loc[window]
    daily = ((base / base.shift(1)).fillna(1.0)
             * (1.0 - proxy.fee) ** (1.0 / 252))
    level = daily.cumprod()
    # level[t] / level[anchor] is the net growth from t to the anchor, so the
    # anchor itself lands exactly on the ETF's first close.
    head = level / level.iloc[-1] * own.iloc[0]
    out = pd.concat([head[head.index < anchor], own])
    out.name = symbol
    return out.loc[window]


def download_index_level(ticker: str, start: str, end: str) -> pd.Series:
    """A stored index level (`$CDI`, `$IPCA`) out of `external_prices`.

    These are cumulative factors written by b3_pipeline/index_series.py, not
    prices — the growth between two dates is one division. Reserved `$` tickers,
    so they can never collide with a B3 code.
    """
    from backtests.core.data import _plane_series

    return _plane_series("external_prices", "close",
                         ticker.strip().upper(), start, end)
