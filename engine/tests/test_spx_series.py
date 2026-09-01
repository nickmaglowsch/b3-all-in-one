"""$SPX: the US benchmark, as a labelled ETF proxy in reais.

Offline. What matters here is not the fetch but the three decisions the stored
rows encode — proxy, currency, and which column the total-return number lives in
— because each one is a way to be quietly wrong that nothing downstream would
report.
"""
from __future__ import annotations

import datetime
import sqlite3

import pandas as pd
import pytest

from b3_pipeline import config, storage
from backtests.core.data import download_benchmark
from us_pipeline import spx_series

D = datetime.date
TODAY = D(2026, 9, 1)

BARS = ("timestamp,open,high,low,close,volume\n"
        "2026-08-31,500,502,499,500.0,70000000\n"
        "2026-08-28,495,499,494,498.0,65000000\n")


class _Stub:
    def __init__(self, csv=BARS, splits=None, dividends=None):
        self.csv, self.splits, self.dividends = csv, splits or [], dividends or []

    def get_csv(self, function, **params):
        return self.csv

    def get_json(self, function, *, expect="data", **params):
        return {"data": self.splits if function == "SPLITS" else self.dividends}


def _rates(value=5.5):
    return pd.Series([value], index=pd.to_datetime([D(2020, 1, 1)]))


def _db(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "spx.sqlite"))
    storage.init_db(conn)
    return conn


def test_the_level_is_in_reais_because_the_strategy_return_is(tmp_path):
    """metrics.json already declares currency BRL because a US book's Sharpe has
    the dollar inside it. A benchmark without the same conversion is the wrong
    ruler, and the gap would read as skill."""
    rows = spx_series.build(_Stub(), _rates(5.5))
    assert rows["close"].iloc[-1] == pytest.approx(500.0 * 5.5)


def test_the_total_return_number_lands_in_close_not_adj_close(tmp_path):
    """`download_benchmark` routes a $-prefixed symbol to `download_index_level`,
    which reads `close`. external_prices has no adjusted column."""
    conn = _db(tmp_path)
    rows = spx_series.build(_Stub(dividends=[
        {"ex_dividend_date": "2026-08-31", "amount": "1.0"}]), _rates(1.0))
    storage.upsert_external_prices(conn, rows)
    stored = storage.read_sql(
        "SELECT * FROM external_prices WHERE ticker='$SPX' ORDER BY date", conn)
    # The dividend lifts everything strictly before its ex-date.
    assert stored["close"].iloc[0] == pytest.approx(498.0 * (1 - 1.0 / 498.0))
    assert stored["close"].iloc[1] == pytest.approx(500.0)


def test_it_is_labelled_a_proxy_in_the_row_itself(tmp_path):
    """The one place this plane serves a proxy, and it is named as one rather
    than left for a reader to discover."""
    rows = spx_series.build(_Stub(), _rates())
    assert (rows["source"] == "ALPHAVANTAGE_SPY_BRL").all()
    assert spx_series.PROXY_SYMBOL == "SPY"


def test_the_shape_matches_the_other_stored_index_series(tmp_path):
    """$IBOV, $CDI and $IPCA all store NaN bars and zero volume — NaN and not
    None, because a column that is None all the way down infers object dtype and
    upsert_external_prices compares close against low before it checks notna()."""
    rows = spx_series.build(_Stub(), _rates())
    for col in ("open", "high", "low"):
        assert rows[col].isna().all()
    assert (rows["volume"] == 0.0).all()
    assert (rows["isin_code"] == "UNKNOWN").all()


def test_a_date_ptax_cannot_cover_is_dropped_not_stored_in_dollars(tmp_path):
    """This table has one number per day and no fx_rate column to carry the
    caveat, so a row is either a BRL level or it is not written."""
    late = pd.Series([5.5], index=pd.to_datetime([D(2030, 1, 1)]))
    assert spx_series.build(_Stub(), late).empty


@pytest.fixture
def plane(tmp_path, monkeypatch):
    """A plane with $SPX in it, wired up as the engine's default connection."""
    path = tmp_path / "spx.sqlite"
    conn = sqlite3.connect(str(path))
    storage.init_db(conn)
    storage.upsert_external_prices(conn, spx_series.build(_Stub(), _rates()))
    conn.commit()
    conn.close()
    monkeypatch.setattr(config, "DB_PATH", path)
    return path


def test_the_bench_reads_back_through_the_engines_own_loader(plane):
    """The seam that matters: markets.MARKETS["US"].benchmark has said "$SPX"
    since the US plane was built and nothing has ever written it, so a US
    backtest ran with no ruler at all."""
    series = download_benchmark("$SPX", "2026-08-01", "2026-09-30", market="US")
    assert len(series) == 2
    assert series.iloc[-1] == pytest.approx(500.0 * 5.5)


def test_the_alias_the_market_declares_resolves_to_the_stored_ticker(plane):
    """MARKETS["US"].aliases maps ^GSPC and SPX onto $SPX."""
    assert len(download_benchmark("^GSPC", "2026-08-01", "2026-09-30",
                                  market="US")) == 2
