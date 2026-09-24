"""Parsing of B3's daily consolidated trade file (external_prices.parse_file).

Marks for the instruments COTAHIST does not carry. Network-free: the samples are
verbatim lines from the real 2026-07-24 file, so the layout is pinned by data
rather than by a docstring. The live round trip is external_prices.demo().
"""
from b3_pipeline import config
from b3_pipeline.external_prices import _num, parse_file

HEADER = (
    "Status do Arquivo: Final\n"
    "RptDt;TckrSymb;ISIN;SgmtNm;MinPric;MaxPric;TradAvrgPric;LastPric;OscnPctg;"
    "AdjstdQt;AdjstdQtTax;RefPric;TradQty;FinInstrmQty;NtlFinVol\n"
)
LFTB11 = ("2026-07-24;LFTB11;BRLFTBCTF002;FORWARD;123,96;124,15;124,08;124,15;"
          "0,18;;;;4427;223381;26721497,04\n")
PETR4 = ("2026-07-24;PETR4;BRPETRACNPR6;CASH;30,10;30,50;30,30;30,40;0,50;;;;"
         "1000;50000;1515000,00\n")


def _rows(body: str, tickers=None):
    return parse_file(body.encode("latin-1"),
                      config.RENDA_FIXA_ETFS if tickers is None else tickers)


def test_parses_a_real_line():
    df = _rows(HEADER + LFTB11)
    assert len(df) == 1
    row = df.iloc[0]
    assert row["ticker"] == "LFTB11"
    assert row["isin_code"] == "BRLFTBCTF002"
    assert row["close"] == 124.15          # LastPric
    assert row["low"] == 123.96            # MinPric
    assert row["high"] == 124.15           # MaxPric
    assert row["open"] is None             # the file publishes no opening price
    assert row["source"] == "B3_TRADE_INFO"


def test_volume_is_reais_not_a_share_count():
    """`prices` stores COTAHIST's volume in BRL. A sibling table meaning something
    else by the same column name would be a trap for whoever joins them."""
    assert _rows(HEADER + LFTB11).iloc[0]["volume"] == 26_721_497.04


def test_only_the_listed_fixed_income_etfs_are_kept():
    """The file is the whole exchange. Everything COTAHIST already carries must
    stay out, or the two tables stop being disjoint and the index double-counts."""
    df = _rows(HEADER + LFTB11 + PETR4)
    assert set(df["ticker"]) == {"LFTB11"}


def test_empty_and_headerless_bodies_are_not_crashes():
    """Weekends, holidays and dates past B3's retention all answer 200 with an
    empty body — routine, not failure."""
    assert _rows("").empty
    assert _rows(HEADER).empty


def test_comma_decimals():
    assert _num("1.234,56") == 1234.56
    assert _num("0,18") == 0.18
    assert _num("") is None
    assert _num(None) is None
    assert _num("n/a") is None


def test_level_rows_shape_and_dtypes():
    import numpy as np
    import pandas as pd

    from b3_pipeline.external_prices import level_rows

    dates = pd.DatetimeIndex(["2024-01-02", "2024-01-03"])
    df = level_rows("$X", dates, np.array([1.0, 1.5]), "src")
    assert list(df.columns) == ["ticker", "isin_code", "date", "open", "high",
                                "low", "close", "volume", "source"]
    assert df["ticker"].tolist() == ["$X", "$X"]
    assert (df["isin_code"] == "UNKNOWN").all()
    assert list(df["date"]) == list(dates)
    # NaN (float dtype), never None (object dtype): see level_rows.
    for col in ("open", "high", "low"):
        assert df[col].dtype == np.float64 and df[col].isna().all()
    assert df["close"].tolist() == [1.0, 1.5]
    assert df["volume"].dtype == np.float64 and (df["volume"] == 0.0).all()
    assert (df["source"] == "src").all()
    # Per-row tickers pass through, as tesouro_direto's titles do.
    per_row = level_rows(np.array(["A", "B"]), dates, np.array([1.0, 2.0]), "src")
    assert per_row["ticker"].tolist() == ["A", "B"]
