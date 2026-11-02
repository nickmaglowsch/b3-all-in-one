"""
Dividends as frames on the rebalance grid (B3)
==============================================
Cash dividends and JCP from ``corporate_actions`` — B3's own list, keyed by ISIN
and mapped to every ticker that traded under it — reduced to three (date x
ticker) frames a signal expression or a ``factor:`` can read:

  * ``div_yield_12m`` — trailing-12-month cash per share over price. Each event
    is divided by the raw close of the grid row on or before its date, then the
    events in (t - 1y, t] are summed: a split inside the window cannot inflate
    it, as summing raw amounts over today's price would.
  * ``div_count_12m`` — distinct dividend/JCP dates in (t - 1y, t].
  * ``div_years_3y`` — how many of the three trailing 12-month windows had one,
    0..3: the "regular payer" screen of the dividend indices (besst_quality's
    ``paid_regularly`` is ``div_years_3y >= min_div_years``).

``event_date`` is the last day with rights (data com), so an event is public by
the grid row that counts it. The events are read whole, not from the window, so
a frame has its history from the first row; only the yield needs prices from
before the event (warmup_size costs it 12 months).

ponytail: a name whose ISIN B3 never listed a dividend for reads 0, not NaN —
"paid nothing" and "no record" look the same.
"""
from __future__ import annotations

import functools

import pandas as pd

from b3_pipeline import storage

KEYS = ("div_yield_12m", "div_count_12m", "div_years_3y")


def load_events(db_path: str) -> pd.DataFrame:
    """[ticker, event_date, value]: every cash dividend / JCP per ticker."""
    sql = """
        SELECT DISTINCT p.ticker, ca.event_date, ca.event_type, ca.value
        FROM corporate_actions ca
        JOIN (SELECT DISTINCT isin_code, ticker FROM prices
              WHERE isin_code != 'UNKNOWN') p
          ON p.isin_code = ca.isin_code
        WHERE ca.event_type IN ('CASH_DIVIDEND', 'JCP')
    """
    with storage.get_connection(db_path) as conn:
        df = storage.read_sql(sql, conn)
    df["event_date"] = pd.to_datetime(df["event_date"])
    df["value"] = pd.to_numeric(df["value"], errors="coerce").fillna(0.0)
    return df[["ticker", "event_date", "value"]]


def _trailing(per_date: pd.DataFrame, grid: pd.DatetimeIndex, years: int,
              back: int = 0) -> pd.DataFrame:
    """Sum of ``per_date`` over (t - (back+years)y, t - back*y] for each t."""
    cum = per_date.sort_index().cumsum()

    def at(dates):
        out = cum.reindex(cum.index.union(dates)).ffill().reindex(dates).fillna(0.0)
        out.index = grid
        return out

    return at(grid - pd.DateOffset(years=back)) - at(grid - pd.DateOffset(years=back + years))


def frames(events: pd.DataFrame, raw_close: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """The three frames on ``raw_close``'s grid and columns."""
    grid, cols = raw_close.index, raw_close.columns
    ev = events[events["ticker"].isin(cols)]
    amount = ev.pivot_table(index="event_date", columns="ticker", values="value",
                            aggfunc="sum").reindex(columns=cols).fillna(0.0)
    paid = ev.assign(one=1.0).pivot_table(index="event_date", columns="ticker",
                                          values="one", aggfunc="max") \
        .reindex(columns=cols).fillna(0.0)
    price = raw_close.reindex(raw_close.index.union(amount.index)).ffill() \
        .reindex(amount.index)
    yld = (amount / price.where(price > 0)).fillna(0.0)
    count = _trailing(paid, grid, 1)
    years = sum((_trailing(paid, grid, 1, back=k) > 0).astype(float) for k in range(3))
    return {"div_yield_12m": _trailing(yld, grid, 1), "div_count_12m": count,
            "div_years_3y": years}


def attach(shared, db_path: str) -> None:
    """Register the three frames on the bundle, built together on first read."""
    @functools.cache
    def build():
        return frames(load_events(db_path), shared["raw_close"])

    for key in KEYS:
        shared.defer(key, lambda key=key: build()[key])
