"""The Ibovespa itself, in points, from B3.

The index is what every backtest and every portfolio chart is measured against,
and it is the one series the plane could not build from its own files. COTAHIST
carries the BOVESPA segment — instruments that trade — and the Ibovespa is not
an instrument. (It does carry `IBOV11`, NOMRES "IBOVESPA", whose close is the
index in points; but that is a traded security, so it prints only on sessions it
trades: 12 of 251 in 2024, 65 of 250 in 2025, 135 of 140 in 2026. Near-daily
now, near-absent three years ago, and a benchmark cannot start in 2026.)

B3 publishes the real thing on its own site, free and unauthenticated, one
request per year, every session back to 1968: the same endpoint the "série
histórica do Ibovespa" page reads. That is the official close in points — no
fund fee, no tracking error, no proxy to disclose — and it is B3's own data,
which is the same licensing posture as COTAHIST rather than Yahoo's.

Stored in `external_prices` under `$IBOV`, alongside `$CDI` and `$IPCA`; see
index_series.py for why the `$` shape cannot collide with a real holding.

Unlike those two this is a level already — the index *is* the cumulative series
— so `close` is the published number as published, and the growth between two
dates is still one division.
"""

import base64
import datetime
import json
import logging

import pandas as pd
import requests

from . import config
from .external_prices import level_rows

logger = logging.getLogger(__name__)

# Marks this loader's rows in the shared table, so storage's resume helpers
# cannot confuse them with the BCB series or the fixed-income ETF backfill.
SOURCE = "B3_INDEX"
IBOV_TICKER = "$IBOV"

# The page's own endpoint. The parameters ride as base64'd JSON in the path,
# which is B3's convention here, not ours.
INDEX_URL = ("https://sistemaswebb3-listados.b3.com.br"
             "/indexStatisticsProxy/IndexCall/GetPortfolioDay/{params}")

# One request per year, and the whole history is rewritten on every run — the
# same posture index_series takes, for the same reason: no resume state means
# nothing to heal when a run dies halfway. B3 answers back to 1968, but the
# plane's own prices start here, so a benchmark reaching further has nothing to
# be a benchmark *for*.
IBOV_START_YEAR = config.START_YEAR


def fetch_year(year: int) -> dict:
    """One year of the index, in B3's day x month matrix, as it answers it."""
    params = base64.b64encode(json.dumps(
        {"language": "pt-br", "index": "IBOV", "year": str(year)}
    ).encode()).decode()
    resp = requests.get(INDEX_URL.format(params=params), timeout=30)
    resp.raise_for_status()
    return resp.json()


def parse_year(payload: dict, year: int) -> pd.Series:
    """B3's matrix -> a date-indexed Series of closes in points.

    The grid is always 31 rows by 12 columns regardless of month length, so most
    cells are null and some are dates that do not exist. Both are dropped: a
    zero or a coerced date here would be a fabricated close on a day the index
    published nothing.
    """
    closes: dict[datetime.date, float] = {}
    for row in payload.get("results") or []:
        for month in range(1, 13):
            raw = row.get(f"rateValue{month}")
            if not raw:
                continue
            try:
                day = datetime.date(year, month, int(row["day"]))
            except (ValueError, TypeError, KeyError):
                continue        # 30 February and friends, sent on every request
            # pt-BR: "174.041,95" -> 174041.95.
            closes[day] = float(raw.replace(".", "").replace(",", "."))

    if not closes:
        return pd.Series(dtype=float)
    return pd.Series(closes).sort_index()


def build(end: datetime.date, start_year: int = IBOV_START_YEAR) -> pd.DataFrame:
    """The whole index in `external_prices` shape, up to and including `end`.

    A year B3 refuses drops that year and keeps the rest: thirty-odd requests
    per run, and one refusal costing the other thirty would make the series
    depend on network weather — which is the thing storing it was meant to end.
    """
    parts: list[pd.Series] = []
    for year in range(start_year, end.year + 1):
        try:
            series = parse_year(fetch_year(year), year)
        except Exception as e:                          # noqa: BLE001
            logger.error(f"IBOV {year} unavailable, skipping: {e}")
            continue
        if series.empty:
            logger.warning(f"IBOV {year} returned nothing")
            continue
        parts.append(series)

    if not parts:
        return pd.DataFrame()

    closes = pd.concat(parts)
    closes = closes[~closes.index.duplicated(keep="last")].sort_index()
    # The current year comes back whole, including sessions after `end`.
    closes = closes[closes.index <= end]
    if closes.empty:
        return pd.DataFrame()

    logger.info(f"{IBOV_TICKER}: {len(closes):,} sessions, "
                f"{closes.iloc[-1]:,.2f} points at {closes.index[-1]:%Y-%m-%d}")
    return level_rows(IBOV_TICKER, closes.index, closes.to_numpy(), SOURCE)


def demo() -> None:
    """Self-check: the matrix parses, bad cells vanish, points stay points."""
    grid = {"results": [
        {"day": 2, "rateValue1": "132.696,63"},
        {"day": 30, "rateValue2": "999,99", "rateValue6": "100.000,00"},
        {"day": 3, "rateValue1": None},
    ]}
    series = parse_year(grid, 2024)
    assert list(series.index) == [datetime.date(2024, 1, 2),
                                  datetime.date(2024, 6, 30)], series.index
    assert series.iloc[0] == 132696.63          # points, not cents
    assert parse_year({}, 2024).empty
    print("ok")


if __name__ == "__main__":
    demo()
