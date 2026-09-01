"""USD/BRL on the plane, so a price in dollars can also be a price in reais.

Every non-B3 market quotes in something else — USD for a US listing, USDT for a
Binance spot pair — while the tax engine, the portfolio and the backtest
numeraire are all BRL. `market_prices` therefore carries both: the native close
exactly as the venue published it, and a BRL projection, with the rate that
produced it stored in the same row. That last part is the point. A BRL figure
whose rate cannot be recovered is a number nobody can check, and this codebase
does not ship those.

The rate is PTAX, from the same BCB SGS API that already serves CDI and IPCA
(`index_series.fetch_sgs`), stored in `external_prices` under the reserved
ticker `$USDBRL` alongside `$CDI` and `$IPCA`. Series 1 is the commercial
dollar's selling rate — the one Receita Federal's own conversion rules point at,
which is what makes it the right choice here rather than merely a convenient one.

Unlike CDI and IPCA this series is a level already: SGS publishes BRL per USD,
not a percent change, so it is stored as published (scale=1.0) with no
compounding.

Weekends and holidays: PTAX is published on business days only, and crypto trades
every day of the year. `as_of` therefore forward-fills — a Saturday bar converts
at Friday's rate, which is what a Saturday BRL mark actually means. It never
back-fills: a bar older than the first published rate in the window gets NaN, and
a NULL close_brl is the honest answer where "the rate is unknown" is the truth.
"""

import datetime
import logging

import pandas as pd

from .index_series import fetch_sgs

logger = logging.getLogger(__name__)

SOURCE = "BCB_PTAX"

# SGS series 1: dólar comercial (venda), BRL per USD. Free, unauthenticated.
USDBRL_SERIES = 1
USDBRL_TICKER = "$USDBRL"

# Binance's own archive starts in 2017 and the US archive is built forward from
# today, so nothing on this plane needs a rate older than this.
FX_SERIES_START = datetime.date(2010, 1, 1)


def fetch_usdbrl(end: datetime.date,
                 start: datetime.date = FX_SERIES_START) -> pd.Series:
    """PTAX venda as a date-indexed Series of BRL per USD.

    scale=1.0: the series publishes the rate itself, not a percentage.
    """
    return fetch_sgs(USDBRL_SERIES, start, end, scale=1.0)


def build(end: datetime.date, start: datetime.date = FX_SERIES_START) -> pd.DataFrame:
    """`$USDBRL` in `external_prices` shape.

    Returns an empty frame rather than raising when SGS is unreachable: the
    caller's other work (ingesting the native prices) is still worth doing, and a
    run that writes native closes with NULL BRL is recoverable on the next pass.
    An exception here would throw the whole ingest away.
    """
    try:
        rates = fetch_usdbrl(end, start)
    except Exception as e:                              # noqa: BLE001
        logger.error(f"PTAX unavailable, skipping {USDBRL_TICKER}: {e}")
        return pd.DataFrame()
    if rates.empty:
        logger.warning(f"PTAX returned nothing for {start}–{end}")
        return pd.DataFrame()

    logger.info(f"{USDBRL_TICKER}: {len(rates):,} points, "
                f"{rates.iloc[-1]:.4f} at {rates.index[-1]:%Y-%m-%d}")
    return pd.DataFrame({
        "ticker": USDBRL_TICKER,
        "isin_code": "UNKNOWN",
        "date": rates.index,
        # A published rate has no range and no volume. NaN, not None, for the
        # dtype reason index_series.build documents.
        "open": float("nan"), "high": float("nan"), "low": float("nan"),
        "close": rates.to_numpy(),
        "volume": 0.0,
        "source": SOURCE,
    })


def load(conn, start=None, end=None) -> pd.Series:
    """The stored `$USDBRL` series, as a date-indexed Series.

    Empty when the plane has none — the caller then writes NULL BRL columns
    rather than inventing a rate.
    """
    from . import storage

    sql = "SELECT date, close FROM external_prices WHERE ticker = ?"
    params = [USDBRL_TICKER]
    if start is not None:
        sql += " AND date >= ?"
        params.append(str(start))
    if end is not None:
        sql += " AND date <= ?"
        params.append(str(end))
    df = storage.read_sql(sql + " ORDER BY date", conn, params=params)
    if df.empty:
        return pd.Series(dtype=float, index=pd.DatetimeIndex([]), name=USDBRL_TICKER)
    return pd.Series(df["close"].astype(float).to_numpy(),
                     index=pd.to_datetime(df["date"]), name=USDBRL_TICKER)


def as_of(rates: pd.Series, dates) -> pd.Series:
    """The rate in force on each of `dates`: the last published on or before it.

    Forward-fill only. A date before the first published rate stays NaN, so a bar
    the plane genuinely cannot convert is reported as unconverted instead of
    being priced off a rate from the future.
    """
    idx = pd.DatetimeIndex(pd.to_datetime(dates))
    if rates.empty:
        return pd.Series(float("nan"), index=idx, name=USDBRL_TICKER)
    merged = rates[~rates.index.duplicated(keep="last")].sort_index()
    return merged.reindex(merged.index.union(idx)).ffill().reindex(idx)


def to_brl(df: pd.DataFrame, rates: pd.Series,
           columns=("close", "adj_close")) -> pd.DataFrame:
    """Add `<col>_brl` for each of `columns`, plus the `fx_rate` that made them.

    Rows the rate series cannot cover keep NULL BRL columns and a NULL rate. That
    pairing is deliberate: `close_brl IS NOT NULL AND fx_rate IS NULL` should
    never occur, so a reader can trust that any BRL number here is traceable.
    """
    if df.empty:
        return df
    out = df.copy()
    out["fx_rate"] = as_of(rates, out["date"]).to_numpy()
    for col in columns:
        if col in out.columns:
            out[f"{col}_brl"] = out[col].astype(float) * out["fx_rate"]
    return out
