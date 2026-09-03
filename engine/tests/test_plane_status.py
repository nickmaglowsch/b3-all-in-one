"""The operator's "do we already have this?" report.

Offline, over a synthetic plane. Like `test_us_coverage.py` this also satisfies
the orphan gate in `scripts/quality_gates.py` — a module under `engine/scripts`
that nothing imports fails the build, and a report nobody drives is a report
nobody trusts.

The assertions worth having are on the `gaps`, not the counts. Counting rows is
arithmetic; the reason this script exists is that a market with rows is not
thereby a market with coverage, and the gap lines are the part that has to stay
true as the planes change.
"""
from __future__ import annotations

import sqlite3

import pandas as pd
import pytest
from b3_pipeline import market_storage, storage

from scripts import plane_status


def _bars(market, symbol, dates, source, close=10.0, close_brl=50.0):
    return pd.DataFrame({
        "market": market, "symbol": symbol, "date": dates,
        "open": close, "high": close, "low": close, "close": close,
        "volume": 1000.0, "split_adj_close": close, "adj_close": close,
        "close_brl": close_brl, "adj_close_brl": close_brl, "fx_rate": 5.0,
        "source": source,
    })


@pytest.fixture()
def conn(tmp_path):
    c = sqlite3.connect(str(tmp_path / "plane.sqlite"))
    storage.init_db(c)
    return c


def test_an_empty_plane_reports_zeroes_rather_than_raising(conn):
    """The first thing an operator runs on a fresh box, before anything has
    ingested. It has to answer, not stack-trace."""
    report = plane_status.collect(conn)
    assert report["markets"] == [plane_status.b3_summary(conn)]
    assert report["markets"][0]["rows"] == 0


def test_a_market_is_summarised_from_its_bars_and_its_master(conn):
    market_storage.upsert_market_prices(conn, _bars(
        "CRYPTO", "BTCUSDT", ["2024-01-01", "2024-01-02"], "binance-archive"))
    market_storage.upsert_instruments(conn, pd.DataFrame([
        {"market": "CRYPTO", "symbol": "BTCUSDT", "currency": "USDT"},
        {"market": "CRYPTO", "symbol": "SRMUSDT", "currency": "USDT",
         "delisting_date": "2022-11-30"},
    ]))
    got = next(m for m in plane_status.collect(conn)["markets"]
               if m["market"] == "CRYPTO")
    assert got["symbols"] == 1
    assert got["rows"] == 2
    assert got["first_bar"] == "2024-01-01"
    assert got["last_bar"] == "2024-01-02"
    assert got["instruments"] == 2
    assert got["delisted"] == 1
    assert got["sources"] == {"binance-archive": 2}


def test_a_market_with_a_master_but_no_bars_still_appears(conn):
    """Exactly the state worth reporting — `us_listing` has run and the price
    backfill has not — so the two sources of market names are unioned."""
    market_storage.upsert_instruments(conn, pd.DataFrame([
        {"market": "US", "symbol": "AAPL", "currency": "USD"}]))
    assert "US" in {m["market"] for m in plane_status.collect(conn)["markets"]}


def test_the_us_gaps_name_the_module_that_closes_each_one(conn):
    """A gap line an operator cannot act on is a gap line they will ignore."""
    market_storage.upsert_instruments(conn, pd.DataFrame([
        {"market": "US", "symbol": "AAPL", "currency": "USD"}]))
    gaps = " | ".join(plane_status.us_gaps(conn))
    assert "us_pipeline.us_backfill" in gaps      # no bars
    assert "us_pipeline.us_main" in gaps          # no CIK
    assert "us_pipeline.us_listing" in gaps       # nothing delisted


def test_the_source_breakdown_tells_the_bulk_backfill_from_the_metered_trickle(conn):
    """The one number that says which US strategy is in play: a plane built from
    the free vendor reads `stooq-daily`, one trickled out of the free tier reads
    `alphavantage-*`, and they carry different adjustment conventions."""
    market_storage.upsert_market_prices(conn, _bars(
        "US", "AAPL", ["2024-01-01"], "stooq-daily:split_only"))
    market_storage.upsert_market_prices(conn, _bars(
        "US", "DEAD", ["2024-01-01"], "alphavantage-time-series-daily:total_return"))
    got = next(m for m in plane_status.collect(conn)["markets"]
               if m["market"] == "US")
    assert got["sources"] == {"stooq-daily:split_only": 1,
                              "alphavantage-time-series-daily:total_return": 1}


def test_rows_ptax_could_not_cover_are_counted(conn):
    """The portfolio and the tax engine read the BRL column. A NULL there is
    honest and also invisible unless something counts it."""
    rows = _bars("US", "AAPL", ["2024-01-01", "2024-01-02"], "stooq-daily:split_only")
    rows.loc[0, ["close_brl", "adj_close_brl", "fx_rate"]] = None
    market_storage.upsert_market_prices(conn, rows)
    got = next(m for m in plane_status.collect(conn)["markets"]
               if m["market"] == "US")
    assert got["unconverted_brl"] == 1


def test_a_partial_crypto_ingest_is_called_out(conn):
    """The Binance archive holds thousands of pairs and hundreds are dead. A
    handful of live ones means the run died halfway — which otherwise reads as
    a working pipeline with a small universe."""
    market_storage.upsert_instruments(conn, pd.DataFrame([
        {"market": "CRYPTO", "symbol": "BTCUSDT", "currency": "USDT"}]))
    gaps = " | ".join(plane_status.crypto_gaps(conn))
    assert "no crypto pair is marked delisted" in gaps
    assert "looks partial" in gaps


def test_a_market_filter_selects_one(conn):
    market_storage.upsert_market_prices(conn, _bars(
        "CRYPTO", "BTCUSDT", ["2024-01-01"], "binance-archive"))
    report = plane_status.collect(conn, only="CRYPTO")
    assert [m["market"] for m in report["markets"]] == ["CRYPTO"]


def test_the_rendering_survives_every_field_being_absent(conn):
    """`render` is what an operator actually sees, and an empty plane is when
    they most need it to print something."""
    text = plane_status.render(plane_status.collect(conn))
    assert "B3" in text and "symbols" in text
