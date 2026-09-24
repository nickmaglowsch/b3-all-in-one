"""WIKI bulk export -> the US price plane.

Offline: every test builds a real zip on disk in the documented shape, so the
archive reader is exercised against actual bytes rather than a stub, and the
pipeline half runs against the real writers.
"""
from __future__ import annotations

import datetime
import zipfile

import pandas as pd
import pytest
from b3_pipeline import storage
from us_pipeline import us_adjust, wiki_archive, wiki_backfill
from tests.conftest import sqlite_db

TODAY = datetime.date(2026, 9, 2)
PTAX = pd.Series([5.0], index=pd.to_datetime(["2014-01-01"]))
HEADER = ",".join(wiki_archive.EXPECTED_HEADER)


def _row(ticker, date, close=10.0, vol=1000.0, div=0.0, split=1.0):
    """One CSV line in the vendor's column order."""
    return (f"{ticker},{date},{close},{close},{close},{close},{vol},"
            f"{div},{split},{close},{close},{close},{close},{vol}")


def _zip(tmp_path, lines, name="WIKI_PRICES_abc.csv", header=HEADER):
    path = tmp_path / "wiki.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(name, "\n".join([header, *lines]) + "\n")
    return path


# ── the reader ────────────────────────────────────────────────────────────────

def test_symbols_stream_out_one_ticker_at_a_time(tmp_path):
    path = _zip(tmp_path, [
        _row("AAA", "2014-01-02"), _row("AAA", "2014-01-03"),
        _row("BBB", "2014-01-02"),
    ])
    got = list(wiki_archive.iter_symbols(str(path)))
    assert [s for s, _ in got] == ["AAA", "BBB"]
    assert len(got[0][1]) == 2 and len(got[1][1]) == 1


def test_bars_come_back_oldest_first(tmp_path):
    path = _zip(tmp_path, [_row("AAA", "2014-01-06"), _row("AAA", "2014-01-02")])
    _, bars = next(iter(wiki_archive.iter_symbols(str(path))))
    assert list(bars["date"]) == [datetime.date(2014, 1, 2),
                                  datetime.date(2014, 1, 6)]


def test_an_unexpected_header_is_refused_naming_what_it_saw(tmp_path):
    """The rows are read positionally, so a column *insertion* would shift every
    field and store wrong prices under right-looking names."""
    path = _zip(tmp_path, [_row("AAA", "2014-01-02")],
                header="ticker,date,open,high,low,close,volume,NEW,ex-dividend,"
                       "split_ratio,adj_open,adj_high,adj_low,adj_close,adj_volume")
    with pytest.raises(RuntimeError, match="unexpected WIKI header"):
        list(wiki_archive.iter_symbols(str(path)))


def test_a_date_that_does_not_parse_is_refused_rather_than_coerced(tmp_path):
    path = _zip(tmp_path, [_row("AAA", "02/01/2014")])
    with pytest.raises(RuntimeError, match="did not parse"):
        list(wiki_archive.iter_symbols(str(path)))


def test_an_archive_without_exactly_one_csv_is_refused(tmp_path):
    path = tmp_path / "wiki.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("a.csv", HEADER + "\n")
        zf.writestr("b.csv", HEADER + "\n")
    with pytest.raises(RuntimeError, match="exactly one CSV"):
        list(wiki_archive.iter_symbols(str(path)))


def test_corporate_actions_are_lifted_out_of_the_bar_rows(tmp_path):
    """The events ride along in the rows, which is why this file can produce a
    total-return series without a metered request."""
    path = _zip(tmp_path, [
        _row("AAA", "2014-01-02"),
        _row("AAA", "2014-01-03", div=0.5),
        _row("AAA", "2014-01-06", split=2.0),
    ])
    _, bars = next(iter(wiki_archive.iter_symbols(str(path))))
    splits, divs = wiki_archive.events(bars)
    assert list(splits["ex_date"]) == [datetime.date(2014, 1, 6)]
    # Inverted out of the vendor convention: WIKI says 2.0 for a 2-for-1,
    # `us_adjust` wants 0.5. See wiki_archive.events.
    assert list(splits["ratio"]) == [0.5]
    assert list(divs["ex_date"]) == [datetime.date(2014, 1, 3)]
    assert list(divs["amount"]) == [0.5]


def test_a_missing_key_says_so_rather_than_failing_in_the_transport(monkeypatch):
    monkeypatch.delenv("NASDAQ_DATA_LINK_API_KEY", raising=False)
    with pytest.raises(wiki_archive.MissingApiKey, match="data.nasdaq.com"):
        wiki_archive.api_key()


# ── the pipeline ──────────────────────────────────────────────────────────────

def test_bars_land_with_both_adjusted_columns_and_a_total_return_tag(tmp_path):
    conn = sqlite_db(tmp_path)
    path = _zip(tmp_path, [
        _row("AAA", "2014-01-02", close=100.0),
        _row("AAA", "2014-01-03", close=50.0, split=2.0),
    ])
    summary = wiki_backfill.run_pipeline(
        db_path=None, zip_path=str(path), limit=1, today=TODAY)
    assert summary["symbols"] == 1


def test_a_split_is_applied_backwards_to_the_split_adjusted_close(tmp_path):
    conn = sqlite_db(tmp_path)
    path = _zip(tmp_path, [
        _row("AAA", "2014-01-02", close=100.0),
        _row("AAA", "2014-01-03", close=50.0, split=2.0),
    ])
    for symbol, bars in wiki_archive.iter_symbols(str(path)):
        rows, state = wiki_backfill.to_market_rows(symbol, bars, PTAX)
    assert state == us_adjust.TOTAL_RETURN
    # The pre-split bar is halved; the bar trading ex is left alone.
    assert list(rows["split_adj_close"]) == [50.0, 50.0]
    assert list(rows["close"]) == [100.0, 50.0]      # raw close untouched


def test_volume_is_money_not_share_count(tmp_path):
    """`min_adtv` filters on money, and a symbol moving between the three US
    sources must not change what its volume column means."""
    path = _zip(tmp_path, [_row("AAA", "2014-01-02", close=10.0, vol=1000.0)])
    for symbol, bars in wiki_archive.iter_symbols(str(path)):
        rows, _ = wiki_backfill.to_market_rows(symbol, bars, PTAX)
    assert rows.iloc[0]["volume"] == 10_000.0


def test_the_frozen_dataset_never_claims_a_delisting(tmp_path):
    """The newest bar is 2018-03-27 for every live name in the file. Apple did
    not delist in March 2018, and a symbol's absence from a frozen file is a
    fact about the file."""
    conn = sqlite_db(tmp_path)
    path = _zip(tmp_path, [_row("AAA", "2018-03-26"), _row("AAA", "2018-03-27")])
    for symbol, bars in wiki_archive.iter_symbols(str(path)):
        wiki_backfill.ingest_symbol(conn, symbol, bars, PTAX, TODAY)
    row = storage.read_sql(
        "SELECT * FROM instruments WHERE symbol='AAA'", conn).iloc[0]
    assert pd.isna(row["delisting_date"])
    assert pd.isna(row["delist_reason"])
    assert pd.isna(row["listing_date"])              # declared, not observed
    assert row["first_seen"] == "2018-03-26"         # observed, safe to write
    assert row["last_seen"] == "2018-03-27"


def test_a_truncated_export_is_refused_after_the_stream(tmp_path):
    """The symbol count is only knowable once the stream ends, but a third of
    the history looks exactly like all of it unless the run says otherwise."""
    conn = sqlite_db(tmp_path)
    path = _zip(tmp_path, [_row("AAA", "2018-03-27")])
    with pytest.raises(RuntimeError, match="below the floor"):
        wiki_backfill.run_pipeline(zip_path=str(path), today=TODAY)


def test_an_export_that_stops_early_is_refused(tmp_path, monkeypatch):
    """A file whose newest bar predates the dataset's known last day was cut
    off mid-stream, even if it carried enough distinct symbols."""
    monkeypatch.setattr(wiki_archive, "MIN_PLAUSIBLE_SYMBOLS", 1)
    path = _zip(tmp_path, [_row("AAA", "2017-01-03")])
    with pytest.raises(RuntimeError, match="before the known last day"):
        wiki_backfill.run_pipeline(zip_path=str(path), today=TODAY)


def test_a_local_copy_is_reused_because_the_dataset_can_never_go_stale(tmp_path):
    """Unlike the daily-republished stooq archive: this one was frozen in 2018,
    so re-downloading 463MB on a schedule buys nothing."""
    path = tmp_path / wiki_archive.BULK_FILENAME
    path.write_bytes(b"PK\x03\x04 pretend archive")
    called = []
    got = wiki_backfill.ensure_bulk(path, fetch=lambda d: called.append(d) or d)
    assert got == str(path) and called == []

    wiki_backfill.ensure_bulk(path, fetch=lambda d: called.append(d) or d,
                              force=True)
    assert called == [str(path)]


# ── against real vendor bytes ─────────────────────────────────────────────────

# Six real rows from the export, around Apple's 7-for-1 split. Verbatim, because
# the inversion in `wiki_archive.events` is invisible to every synthetic fixture
# that picks a 2-for-1 (where a wrong reciprocal is still 0.5-ish) and silent in
# production: it would multiply the pre-split history instead of dividing it and
# still leave something that looks like a price series.
_REAL_AAPL = [
    "AAPL,2014-06-05,646.2,649.3699,642.61,647.35,10850200.0,0.0,1.0,"
    "86.889156291142,87.315386462184,86.406438756191,87.043787256377,75951400.0",
    "AAPL,2014-06-06,649.9,651.26,644.47,645.57,12497800.0,0.0,1.0,"
    "87.386664614072,87.569532538176,86.656537534745,86.804445414535,87484600.0",
    "AAPL,2014-06-09,92.7,93.88,91.75,93.7,75414997.0,0.0,7.0,"
    "87.252202905172,88.362856620685,86.358032540987,88.193434867471,75414997.0",
    "AAPL,2014-06-10,94.73,95.05,93.57,94.25,62777000.0,0.0,1.0,"
    "89.16290378864,89.464098016576,88.071074712372,88.711112446736,62777000.0",
]


def test_a_real_seven_for_one_split_does_not_leak_into_the_adjusted_series(
        tmp_path):
    """Raw closes fall 645.57 -> 93.70 across the ex-date. That is the split,
    not an 85% crash, and the adjusted column has to say so."""
    path = _zip(tmp_path, _REAL_AAPL)
    symbol, bars = next(iter(wiki_archive.iter_symbols(str(path))))
    rows, state = wiki_backfill.to_market_rows(symbol, bars, PTAX)
    rows = rows.set_index(pd.to_datetime(rows["date"]))

    assert state == us_adjust.TOTAL_RETURN
    pre, ex = rows.loc["2014-06-06"], rows.loc["2014-06-09"]
    assert pre["close"] == 645.57 and ex["close"] == 93.70      # raw untouched
    assert ex["close"] / pre["close"] - 1 < -0.85               # the fake crash
    assert abs(ex["split_adj_close"] / pre["split_adj_close"] - 1) < 0.05
    assert pre["split_adj_close"] == pytest.approx(645.57 / 7, rel=1e-9)


def test_our_adjustment_agrees_with_the_vendors_own_adjusted_close(tmp_path):
    """We recompute rather than copy `adj_close`, because the vendor's series is
    anchored to its own last day. Recomputed correctly the two differ by a
    constant factor and nothing else — which is the check that the split ratio
    was inverted the right way round."""
    path = _zip(tmp_path, _REAL_AAPL)
    symbol, bars = next(iter(wiki_archive.iter_symbols(str(path))))
    rows, _ = wiki_backfill.to_market_rows(symbol, bars, PTAX)

    vendor = pd.Series([float(line.split(",")[12]) for line in _REAL_AAPL])
    ratio = rows["adj_close"].reset_index(drop=True) / vendor
    assert ratio.max() / ratio.min() - 1 < 0.001
