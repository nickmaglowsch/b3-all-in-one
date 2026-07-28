"""Tesouro Direto marks on the price plane.

The whole feed hinges on one join: the name this builds has to be byte-identical
to what B3 writes in the Movimentação extract, because nothing maps between them.
Network-free — `parse` takes bytes, so the published CSV is exercised as a
fixture rather than fetched.
"""
import datetime
import sqlite3

import pandas as pd
import pytest

from b3_pipeline import external_prices, storage, tesouro_direto

# Two real rows plus the ambiguity case, in the published layout.
CSV = (
    "Tipo Titulo;Data Vencimento;Data Base;Taxa Compra Manha;Taxa Venda Manha;"
    "PU Compra Manha;PU Venda Manha;PU Base Manha\n"
    "Tesouro IPCA+;15/05/2045;24/07/2026;7,43;7,55;1243,03;1216,74;1216,74\n"
    "Tesouro IPCA+;15/05/2045;29/11/2024;6,97;7,09;1110,00;1084,34;1084,34\n"
    "Tesouro Selic;01/03/2027;24/07/2026;0,10;0,20;19506,19;19506,19;19506,19\n"
    # Same name, two maturities — the 2010 Prefixados.
    "Tesouro Prefixado;01/01/2010;04/01/2009;10,00;10,10;900,00;899,00;899,00\n"
    "Tesouro Prefixado;01/07/2010;04/01/2009;10,50;10,60;880,00;879,00;879,00\n"
).encode()


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.execute("PRAGMA foreign_keys = OFF")
    storage.init_db(c)
    yield c
    c.close()


def test_the_ticker_is_the_name_b3_writes_in_the_extract():
    """"TESOURO IPCA+ 2045", upper-cased, maturity year only — what
    load_b3._rf_code derives from the Produto cell. The join has no mapping
    table behind it, so this string is the entire contract between the two."""
    assert tesouro_direto.ticker_for(
        "Tesouro IPCA+", pd.Timestamp("2045-05-15")) == "TESOURO IPCA+ 2045"
    assert tesouro_direto.ticker_for(
        "Tesouro Selic", pd.Timestamp("2027-03-01")) == "TESOURO SELIC 2027"


def test_a_name_that_maps_to_two_maturities_is_dropped_not_guessed():
    """B3 writes "Tesouro Prefixado 2010" for both the January and the July
    paper. Guessing one would price a holding off the wrong bond and say
    nothing about it."""
    tickers = set(tesouro_direto.parse(CSV)["ticker"])
    assert "TESOURO PREFIXADO 2010" not in tickers
    assert tickers == {"TESOURO IPCA+ 2045", "TESOURO SELIC 2027"}


def test_the_mark_is_the_price_the_holder_could_sell_at():
    """PU Venda, not PU Compra: a position is worth what it fetches on a
    resgate, and the spread runs against the investor."""
    row = tesouro_direto.parse(CSV).set_index(["ticker", "date"])
    mark = row.loc[("TESOURO IPCA+ 2045", pd.Timestamp("2026-07-24")), "close"]
    assert mark == pytest.approx(1216.74)


def test_build_shapes_rows_for_external_prices_and_honours_the_start(monkeypatch):
    monkeypatch.setattr(tesouro_direto, "fetch", lambda *a, **k: CSV)
    out = tesouro_direto.build(datetime.date(2025, 1, 1))

    assert list(out.columns) == ["ticker", "isin_code", "date", "open", "high",
                                "low", "close", "volume", "source"]
    assert (out["source"] == tesouro_direto.SOURCE).all()
    # 2024-11-29 is before the start; the two 2026 rows survive.
    assert len(out) == 2
    assert out["date"].min() == pd.Timestamp("2026-07-24")
    # No range and no turnover to report, and NaN rather than None so the column
    # stays float — upsert_external_prices compares close against low first.
    assert out["high"].isna().all() and out["low"].isna().all()


def test_marks_round_trip_through_the_shared_table(conn, monkeypatch):
    monkeypatch.setattr(tesouro_direto, "fetch", lambda *a, **k: CSV)
    storage.upsert_external_prices(conn, tesouro_direto.build(datetime.date(2010, 1, 1)))

    stored = conn.execute(
        "SELECT close FROM external_prices WHERE ticker = ? AND date = ?",
        ("TESOURO IPCA+ 2045", "2026-07-24")).fetchone()
    assert stored[0] == pytest.approx(1216.74)


def test_a_tesouro_row_does_not_make_the_etf_feed_skip_that_day(conn):
    """Third writer to `external_prices`, same trap as the BCB series: unscoped,
    a Tesouro mark on a date convinces the B3 ETF backfill it already has that
    session — and B3's window is ~14 months and rolling, so it never comes back.
    """
    day = datetime.date(2026, 7, 24)
    storage.upsert_external_prices(conn, pd.DataFrame([{
        "ticker": "TESOURO SELIC 2027", "isin_code": "UNKNOWN", "date": day,
        "open": float("nan"), "high": float("nan"), "low": float("nan"),
        "close": 19506.19, "volume": 0.0, "source": tesouro_direto.SOURCE,
    }]))

    assert storage.stored_external_dates(
        conn, day, external_prices.SOURCE) == set()
    assert storage.max_external_price_date(conn, external_prices.SOURCE) is None
    assert storage.max_external_price_date(
        conn, tesouro_direto.SOURCE) == day
