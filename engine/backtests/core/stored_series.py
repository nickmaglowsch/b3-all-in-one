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


# A benchmark whose last print sits this far behind the plane is stale, not a
# long weekend: past it the bench_* flags would read the tail as missing.
STALE_DAYS = 7


def or_proxy(level: pd.Series, spec, symbol: str, start: str, end: str) -> pd.Series:
    """`level`, completed — when `symbol` is the market's own benchmark — by that
    market's proxy read off its own plane (MarketSpec.benchmark_proxy; US: SPY
    for `$SPX`). Marked with `attrs["proxy_for"]` (and `proxy_from` when only a
    tail was filled), so shared_data can say which ruler the run used.

    Empty, the proxy stands in whole: a failed `$SPX` job left every bench_*
    flag on US reading False and every gated US book in CDI, silently. Stale —
    stopping more than STALE_DAYS before the proxy does — the proxy's returns
    carry it on from its last print: otherwise the run's tail, the part a live
    target reads, had no benchmark and the flags read False from there on."""
    from backtests.core import markets
    from backtests.core.data import _plane_series

    if not spec.benchmark_proxy or symbol != markets.plane_symbol(spec.benchmark, spec.key):
        return level
    proxy = _plane_series(spec.table, "adj_close", spec.benchmark_proxy, start, end,
                          market=spec.key, symbol_column=spec.symbol_column)
    if level.empty:
        proxy.attrs["proxy_for"] = symbol
        return proxy
    return _splice_tail(level, proxy, symbol)


def _splice_tail(level: pd.Series, proxy: pd.Series, symbol: str) -> pd.Series:
    """`level` carried past its last print by `proxy`'s returns, rescaled to meet
    it there; `level` itself when the proxy does not run on past it, or has no
    print on or before that date to chain from."""
    last = level.index[-1]
    anchor = proxy.loc[:last]
    if proxy.empty or anchor.empty or (proxy.index[-1] - last).days <= STALE_DAYS:
        return level
    tail = proxy.loc[proxy.index > last] * (level.iloc[-1] / anchor.iloc[-1])
    out = pd.concat([level, tail]).rename(level.name)
    out.attrs.update(proxy_for=symbol, proxy_from=tail.index[0].date().isoformat())
    return out
