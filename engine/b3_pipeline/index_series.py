"""CDI and IPCA as price series, so renda fixa can be accrued off the plane.

A CDB indexed to 95% do CDI has no market and no feed. What it does have is a
published rate, and the platform needs that rate the same way it needs a close:
per day, on the plane, already there when the request arrives.

Fetching it per request was the alternative, and it is worse than it looks. The
benchmark overlays already degrade to "unavailable" when the engine subprocess
fails, which is right for decoration — but a portfolio *line* that flips between
accrued and unpriced on network weather reports two different total returns for
the same window on two different days, for reasons that have nothing to do with
the market. So it is stored, and it is stored as what it already is: an index.

Stored in `external_prices` under reserved tickers (`$CDI`, `$IPCA`) — the `$`
matches no B3 code and no renda fixa contract prefix, by shape, so the platform's
existing UNION picks them up with no new query and no chance of collision.

Levels, not rates: `close` is the cumulative factor since INDEX_SERIES_START, so
the growth between any two dates is one division. A consumer that needs the daily
rate back differentiates (i_t = L_t/L_{t-1} - 1), which is exact — the reverse,
rebuilding a level from stored rates, is not, because it depends on where the
window starts.
"""

import datetime
import logging

import pandas as pd
import requests
from dateutil.relativedelta import relativedelta

from .external_prices import level_rows

logger = logging.getLogger(__name__)

# Marks this loader's rows in the shared table. storage's resume helpers scope on
# it so the fixed-income ETF backfill cannot mistake these dates for its own.
SOURCE = "BCB_SGS"

# SGS series. 12 is the CDI's daily rate in percent per business day; 433 is the
# IPCA's monthly change in percent. Both are free and unauthenticated.
CDI_SERIES, IPCA_SERIES = 12, 433
CDI_TICKER, IPCA_TICKER = "$CDI", "$IPCA"

# The whole series is refetched and rewritten on every run, from one fixed date,
# so a stored level never depends on which window last happened to be asked for.
# ~4,000 CDI rows and ~200 IPCA rows: cheaper than the resume logic would be.
INDEX_SERIES_START = datetime.date(2010, 1, 1)


def fetch_sgs(series: int, start: datetime.date, end: datetime.date,
              scale: float = 100.0) -> pd.Series:
    """One BCB SGS series as a date-indexed Series, divided by `scale`.

    Chunked because SGS caps a daily query at ten years, and retried because it
    throttles intermittently and answers with HTML — a silently dropped chunk
    would leave a hole that reads as "the CDI paid nothing that year".

    `scale` defaults to 100 because the rate series this was written for publish
    percent (1% -> 0.01). A price series such as PTAX (series 1, BRL per USD)
    publishes the number itself and passes scale=1.0; see fx_series.py.
    """
    chunks: list[pd.Series] = []
    cursor = start
    while cursor <= end:
        stop = min(cursor + relativedelta(years=5), end)
        url = (f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.{series}/dados"
               f"?formato=json&dataInicial={cursor:%d/%m/%Y}"
               f"&dataFinal={stop:%d/%m/%Y}")
        for attempt in range(3):
            try:
                data = requests.get(url, timeout=30).json()
                if data:
                    df = pd.DataFrame(data)
                    df["data"] = pd.to_datetime(df["data"], format="%d/%m/%Y")
                    chunks.append(
                        df.set_index("data")["valor"].astype(float) / scale)
                break
            except Exception as e:                      # noqa: BLE001
                if attempt == 2:
                    raise RuntimeError(
                        f"SGS {series} {cursor}–{stop} failed: {e}") from e
        cursor = stop + relativedelta(days=1)

    if not chunks:
        return pd.Series(dtype=float)
    out = pd.concat(chunks)
    return out[~out.index.duplicated(keep="first")].sort_index()


def build(end: datetime.date, start: datetime.date = INDEX_SERIES_START) -> pd.DataFrame:
    """Both series in `external_prices` shape, as cumulative levels.

    A partial answer is a usable answer here — a book with only CDI-indexed paper
    does not care that IPCA was unreachable — so each series is fetched
    independently and a failure drops that one rather than both.
    """
    frames = []
    for ticker, series in ((CDI_TICKER, CDI_SERIES), (IPCA_TICKER, IPCA_SERIES)):
        try:
            rates = fetch_sgs(series, start, end)
        except Exception as e:                          # noqa: BLE001
            logger.error(f"SGS {series} unavailable, skipping {ticker}: {e}")
            continue
        if rates.empty:
            logger.warning(f"SGS {series} returned nothing for {start}–{end}")
            continue
        level = (1.0 + rates).cumprod()
        frames.append(level_rows(ticker, level.index, level.to_numpy(), SOURCE))
        logger.info(f"{ticker}: {len(level):,} points, "
                    f"level {level.iloc[-1]:.4f} at {level.index[-1]:%Y-%m-%d}")

    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def demo() -> None:
    """Self-check: levels compound, are monotonic, and round-trip to rates."""
    rates = pd.Series(
        [0.01, 0.02, 0.0],
        index=pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"]))
    level = (1.0 + rates).cumprod()
    assert abs(level.iloc[0] - 1.01) < 1e-12
    assert abs(level.iloc[1] - 1.01 * 1.02) < 1e-12
    assert abs(level.iloc[2] - level.iloc[1]) < 1e-12       # a 0% day is flat
    # What the platform does to recover a daily rate for the "% do CDI" case.
    back = level / level.shift(1) - 1.0
    assert abs(back.iloc[1] - 0.02) < 1e-12
    assert abs((level.iloc[0] - 1.0) - rates.iloc[0]) < 1e-12   # seeded at 1+i0
    print("ok")


if __name__ == "__main__":
    demo()
