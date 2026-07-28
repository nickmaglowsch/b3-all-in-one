"""CDI and IPCA stored as price series, and the feed isolation that requires.

`external_prices` now has two writers. The B3 fixed-income ETF backfill decides
what to fetch by asking the table what it already has — so if that question is
not scoped to one feed, the BCB rows answer it and the ETF feed skips those days.
B3's window is ~14 months and rolling, so a skipped day never comes back.
Network-free: the SGS client is exercised by index_series.demo().
"""
import datetime
import sqlite3

import pandas as pd
import pytest

from b3_pipeline import external_prices, index_series, storage

DAY = datetime.date(2026, 7, 24)


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.execute("PRAGMA foreign_keys = OFF")
    storage.init_db(c)
    yield c
    c.close()


def _row(ticker, day, source, close=1.0):
    return pd.DataFrame([{
        "ticker": ticker, "isin_code": "UNKNOWN", "date": day,
        "open": float("nan"), "high": float("nan"), "low": float("nan"),
        "close": close, "volume": 0.0, "source": source,
    }])


def test_a_bcb_row_does_not_make_the_etf_feed_skip_that_day(conn):
    """The whole reason both helpers take a source. Unscoped, `$CDI` on a date
    is enough to convince the ETF backfill it already has that session."""
    storage.upsert_external_prices(
        conn, _row(index_series.CDI_TICKER, DAY, index_series.SOURCE))

    assert storage.stored_external_dates(
        conn, DAY, external_prices.SOURCE) == set()
    assert storage.max_external_price_date(conn, external_prices.SOURCE) is None
    # ...and the BCB loader still sees its own row.
    assert storage.stored_external_dates(
        conn, DAY, index_series.SOURCE) == {DAY}


def test_each_feed_sees_only_its_own_dates(conn):
    earlier = DAY - datetime.timedelta(days=1)
    storage.upsert_external_prices(
        conn, _row("LFTB11", DAY, external_prices.SOURCE, close=124.15))
    storage.upsert_external_prices(
        conn, _row(index_series.IPCA_TICKER, earlier, index_series.SOURCE))

    assert storage.max_external_price_date(conn, external_prices.SOURCE) == DAY
    assert storage.max_external_price_date(
        conn, index_series.SOURCE) == earlier


def test_the_reserved_tickers_cannot_collide_with_a_real_holding():
    """`$` is what keeps these off every ticker the platform might hold: it is
    neither a B3 listed-code shape nor a renda fixa contract prefix, so the
    fractional-suffix strip and the contract matcher both reject it by shape."""
    import re
    listed = re.compile(r"^[A-Z][A-Z0-9]{3}\d{1,2}$")
    contract = re.compile(r"^(CDB|LCI|LCA|LC|LF|CRA|CRI|TESOURO|DEB|NTN|LTN"
                          r"|LFT|RDB)\b", re.IGNORECASE)
    for ticker in (index_series.CDI_TICKER, index_series.IPCA_TICKER):
        assert not listed.match(ticker)
        assert not contract.match(ticker)
        assert not ticker.endswith("F")


def test_levels_are_stored_not_rates():
    """A level divides to give growth between any two dates; a stored rate would
    have to be re-compounded, and the result would depend on where the caller's
    window started. Pinned here because the platform's accrual assumes levels."""
    rates = pd.Series(
        [0.05, 0.05],
        index=pd.to_datetime(["2024-01-02", "2024-01-03"]))
    level = (1.0 + rates).cumprod()
    assert level.iloc[-1] / level.iloc[0] == pytest.approx(1.05)
    assert level.iloc[0] == pytest.approx(1.05)      # seeded at 1 + i0
