"""The vendor parsers, driven from samples built in-process.

Offline and pure. These pin two things: that the documented shape is read
correctly, and — the half that matters more — that a *changed* shape stops the
run loudly instead of writing wrong numbers. That is the parser-drift mitigation
`spec/00-overview.md` asks for, and `test_av_fixtures.py` is the other half of
it: the same functions, driven from real captures once they exist.
"""
from __future__ import annotations

import pandas as pd
import pytest
from us_pipeline import av_parsers as p

DAILY = (
    "timestamp,open,high,low,close,volume\n"
    "2026-08-31,190.0,192.0,189.0,191.5,52000000\n"
    "2026-08-28,188.0,190.5,187.5,189.9,48000000\n"
)

LISTING = (
    "symbol,name,exchange,assetType,ipoDate,delistingDate,status\n"
    "AAPL,Apple Inc,NASDAQ,Stock,1980-12-12,null,Active\n"
    "SPY,SPDR S&P 500 ETF,NYSE ARCA,ETF,1993-01-22,null,Active\n"
    "LEH,Lehman Brothers,NYSE,Stock,1994-01-01,2008-09-15,Delisted\n"
)


# ── Daily bars ────────────────────────────────────────────────────────────────

def test_daily_bars_come_back_in_date_order(tmp_path):
    """The vendor sends newest first; everything downstream assumes ascending."""
    out = p.parse_time_series_daily(DAILY, "AAPL")
    assert list(out["date"].astype(str)) == ["2026-08-28", "2026-08-31"]
    assert out["close"].iloc[-1] == 191.5


def test_the_vendors_share_count_keeps_its_own_name(tmp_path):
    """`volume` here is a count of shares. `market_prices.volume` is turnover in
    the quote currency — the number `min_adtv` filters on. Conflating them
    builds a liquidity filter that keeps penny stocks and drops Berkshire, so
    the two never share a name."""
    out = p.parse_time_series_daily(DAILY, "AAPL")
    assert "share_volume" in out.columns
    assert "volume" not in out.columns
    assert out["share_volume"].iloc[-1] == 52_000_000


@pytest.mark.parametrize("dropped", ["volume", "close", "timestamp"])
def test_a_missing_column_names_itself(dropped):
    header, *rows = DAILY.strip().split("\n")
    cols = header.split(",")
    keep = [i for i, c in enumerate(cols) if c != dropped]
    text = "\n".join(
        ",".join(line.split(",")[i] for i in keep) for line in [header, *rows])
    with pytest.raises(ValueError, match=dropped):
        p.parse_time_series_daily(text, "AAPL")


def test_a_changed_date_format_raises_instead_of_emptying_the_series():
    """A pinned format plus errors='coerce' would otherwise turn a vendor
    switching to DD/MM/YYYY into "this company has no history"."""
    with pytest.raises(ValueError, match="timestamp"):
        p.parse_time_series_daily(DAILY.replace("2026-08-31", "31/08/2026")
                                       .replace("2026-08-28", "28/08/2026"), "AAPL")


# ── Listing status ────────────────────────────────────────────────────────────

def test_listing_status_carries_both_ends_of_the_interval():
    out = p.parse_listing_status(LISTING).set_index("symbol")
    assert str(out.loc["LEH", "ipo_date"]) == "1994-01-01"
    assert str(out.loc["LEH", "delisting_date"]) == "2008-09-15"
    assert out.loc["LEH", "status"] == "delisted"
    assert pd.isna(out.loc["AAPL", "delisting_date"])


def test_asset_type_maps_onto_the_vocabulary_instruments_already_uses():
    out = p.parse_listing_status(LISTING).set_index("symbol")
    assert out.loc["SPY", "kind"] == "ETF"
    assert out.loc["AAPL", "kind"] == "EQUITY"


def test_an_all_null_delisting_column_is_not_mistaken_for_drift():
    """Every row of the `active` file carries "null" there. That is the file
    working, not the format changing."""
    active = "\n".join(line for line in LISTING.strip().split("\n")
                       if "Delisted" not in line) + "\n"
    out = p.parse_listing_status(active)
    assert out["delisting_date"].isna().all()


def test_a_missing_listing_column_names_itself():
    with pytest.raises(ValueError, match="delistingDate"):
        p.parse_listing_status(LISTING.replace(",delistingDate", ",delisted_on"))


# ── Corporate actions ─────────────────────────────────────────────────────────

def test_a_four_for_one_split_becomes_a_quarter_not_a_four():
    """The vendor reports new-over-old ("4.0"); `b3_pipeline.adjustments` uses
    old-over-new. Getting this backwards turns Apple's 2020 split into a +300%
    day, which looks exactly like a stock that quadrupled and no glitch filter
    would catch it."""
    out = p.parse_splits(
        {"data": [{"effective_date": "2020-08-31", "split_factor": "4.0"}]}, "AAPL")
    assert out["ratio"].iloc[0] == pytest.approx(0.25)


def test_a_reverse_split_lands_above_one():
    out = p.parse_splits(
        {"data": [{"effective_date": "2023-05-01", "split_factor": "0.1"}]}, "XYZ")
    assert out["ratio"].iloc[0] == pytest.approx(10.0)


def test_an_empty_actions_payload_is_a_statement_not_a_failure():
    """"No splits on record" is exactly what most symbols return, and it is what
    lets `adjust_state` be 'total_return' rather than 'unadjusted'."""
    assert p.parse_splits({"data": []}, "AAPL").empty
    assert p.parse_dividends({"data": []}, "AAPL").empty


def test_dividends_carry_the_ex_date_the_xbrl_cannot_give():
    out = p.parse_dividends(
        {"data": [{"ex_dividend_date": "2026-08-08", "amount": "0.26"}]}, "AAPL")
    assert str(out["ex_date"].iloc[0]) == "2026-08-08"
    assert out["amount"].iloc[0] == pytest.approx(0.26)


@pytest.mark.parametrize("parser,payload,missing", [
    (p.parse_splits, {"data": [{"effective_date": "2020-08-31"}]}, "split_factor"),
    (p.parse_dividends, {"data": [{"amount": "0.26"}]}, "ex_dividend_date"),
])
def test_a_renamed_action_field_names_itself(parser, payload, missing):
    with pytest.raises(ValueError, match=missing):
        parser(payload, "AAPL")
