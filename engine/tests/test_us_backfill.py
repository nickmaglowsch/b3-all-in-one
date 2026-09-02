"""The bulk US backfill: the archive reader, the conversions, and the seam.

Offline. `stooq.com` is unreachable from the environment this was built in, so
the archive is constructed in-process as a real zip — which is the right shape
for a test anyway: what has to hold is that the reader refuses anything it does
not recognise instead of storing it, and that a bulk-priced symbol is never
re-priced from the metered feed.

The last group is the one that matters most. Stooq publishes a split-adjusted
close and Alpha Vantage publishes a raw one; `market_prices` is keyed
`(market, symbol, date)`, so a symbol both of them write ends up with two
conventions in one column and a fabricated return at the seam.
"""
from __future__ import annotations

import datetime
import io
import sqlite3
import zipfile

import pandas as pd
import pytest
from b3_pipeline import storage, us_storage
from us_pipeline import stooq_archive as archive
from us_pipeline import us_adjust, us_backfill, us_prices

D = datetime.date
TODAY = D(2026, 9, 1)

# The bulk `.txt` layout: angle-bracketed headers, YYYYMMDD dates.
BULK_TXT = ("<TICKER>,<PER>,<DATE>,<TIME>,<OPEN>,<HIGH>,<LOW>,<CLOSE>,<VOL>,<OPENINT>\n"
            "AAPL.US,D,20260828,000000,98,99,97,98.0,900,0\n"
            "AAPL.US,D,20260831,000000,100,101,99,100.0,1000,0\n")

# The single-symbol CSV layout: plain headers, ISO dates. Same data.
PLAIN_CSV = ("Date,Open,High,Low,Close,Volume\n"
             "2026-08-28,98,99,97,98.0,900\n"
             "2026-08-31,100,101,99,100.0,1000\n")


def _zip(tmp_path, members: dict, name="d_us_txt.zip") -> str:
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as zf:
        for member, text in members.items():
            zf.writestr(member, text)
    return str(path)


def _universe(n: int, body=BULK_TXT) -> dict:
    """An archive big enough to clear MIN_PLAUSIBLE_SYMBOLS."""
    return {f"data/daily/us/nasdaq stocks/1/sym{i:05d}.us.txt": body
            for i in range(n)}


def _db(tmp_path, name="plane.sqlite") -> sqlite3.Connection:
    conn = sqlite3.connect(str(tmp_path / name))
    storage.init_db(conn)
    return conn


# ── the member path is the only source of venue and kind ─────────────────────

@pytest.mark.parametrize("member,expected", [
    ("data/daily/us/nasdaq stocks/1/aapl.us.txt", ("AAPL", "NASDAQ", "EQUITY")),
    ("data/daily/us/nyse etfs/spy.us.txt", ("SPY", "NYSE", "ETF")),
    ("data/daily/us/nysemkt stocks/2/brk-b.us.txt", ("BRK-B", "NYSE MKT", "EQUITY")),
])
def test_classify_reads_symbol_venue_and_kind(member, expected):
    assert archive.classify(member) == expected


@pytest.mark.parametrize("member", [
    "data/daily/us/nasdaq stocks/1/README",          # not a .txt
    "data/daily/us/nasdaq stocks/1/aapl.uk.txt",     # not a US listing
    "data/daily/world/de/foo.de.txt",                # no venue segment
    "data/daily/us/nasdaq stocks/1/.us.txt",         # empty symbol
])
def test_a_member_that_names_no_venue_is_skipped_not_guessed(member):
    """A NULL venue in `instruments` is worse than a symbol this plane skips:
    four writers share that table and none of them can repair a guess."""
    assert archive.classify(member) is None


# ── parsing by header name, in either published layout ───────────────────────

@pytest.mark.parametrize("text", [BULK_TXT, PLAIN_CSV])
def test_both_published_layouts_parse_to_the_same_bars(text):
    """Stooq serves angle-bracketed headers with YYYYMMDD in the bulk zip and
    plain headers with ISO dates from the single-symbol endpoint. This module
    was written from documentation, so it accepts both rather than betting."""
    bars = archive.parse_bars(text, "AAPL")
    assert list(bars["date"]) == [D(2026, 8, 28), D(2026, 8, 31)]
    assert list(bars["close"]) == [98.0, 100.0]
    assert list(bars["share_volume"]) == [900, 1000]


def test_bars_come_back_oldest_first():
    """`ingest_symbol` reads first_bar and last_bar off the ends."""
    reversed_rows = ("Date,Open,High,Low,Close,Volume\n"
                     "2026-08-31,100,101,99,100.0,1000\n"
                     "2026-08-28,98,99,97,98.0,900\n")
    bars = archive.parse_bars(reversed_rows, "AAPL")
    assert bars["date"].iloc[0] == D(2026, 8, 28)
    assert bars["date"].iloc[-1] == D(2026, 8, 31)


def test_a_missing_column_names_what_went_missing():
    """The `av_parsers` rule: read by name, and fail loudly on drift rather
    than indexing by position into silently wrong numbers."""
    with pytest.raises(ValueError, match="close"):
        archive.parse_bars("Date,Open,High,Low,Volume\n2026-08-31,1,1,1,1\n", "AAPL")


def test_a_date_format_nobody_publishes_raises_rather_than_emptying_the_symbol():
    """`errors="coerce"` alone would turn a format change into a symbol with no
    history, which reads as a delisting rather than as a broken parser."""
    with pytest.raises(ValueError, match="date format changed"):
        archive.parse_bars(
            "Date,Open,High,Low,Close,Volume\n31/08/2026,1,1,1,1,1\n", "AAPL")


def test_an_unreadable_member_costs_only_itself(tmp_path):
    path = _zip(tmp_path, {"data/daily/us/nasdaq stocks/1/aapl.us.txt": BULK_TXT})
    with archive.open_archive(path) as zf:
        assert archive.read_member(zf, "nope/missing.us.txt", "MISSING").empty
        assert not archive.read_member(
            zf, "data/daily/us/nasdaq stocks/1/aapl.us.txt", "AAPL").empty


# ── the conversions, which are us_prices' on purpose ─────────────────────────

def test_volume_is_turnover_not_a_share_count():
    """`min_adtv` filters on money. A share count there keeps a penny stock
    trading a billion shares and drops Berkshire — the same conversion
    `us_prices.to_market_rows` makes, so a symbol that moves between the two
    sources does not change what its volume column means."""
    bars = archive.parse_bars(BULK_TXT, "AAPL")
    rows = us_backfill.to_market_rows("AAPL", bars, pd.Series(dtype=float))
    assert list(rows["volume"]) == [900 * 98.0, 1000 * 100.0]


def test_the_series_is_labelled_split_only_not_total_return():
    """The archive is split-adjusted and carries no distributions. Calling that
    `total_return` is the claim `/market/status` and the backtest warning exist
    to stop, so the state is written into the row's own source."""
    bars = archive.parse_bars(BULK_TXT, "AAPL")
    rows = us_backfill.to_market_rows("AAPL", bars, pd.Series(dtype=float))
    assert set(rows["source"]) == {f"{archive.SOURCE}:{us_adjust.SPLIT_ONLY}"}
    assert list(rows["adj_close"]) == list(rows["close"])
    assert list(rows["split_adj_close"]) == list(rows["close"])


def test_the_master_row_claims_only_what_the_file_observed():
    """`listing_date`/`delisting_date` are declared intervals and belong to
    `us_listing`. The bulk dump holds only the currently listed, so inferring a
    delisting from absence here would delist every name that ever died."""
    bars = archive.parse_bars(BULK_TXT, "AAPL")
    row = us_backfill._instrument_row("AAPL", "NASDAQ", "EQUITY", bars)
    assert row["first_seen"] == D(2026, 8, 28)
    assert row["last_seen"] == D(2026, 8, 31)
    assert "listing_date" not in row and "delisting_date" not in row
    # NOT NULL, and upsert_instruments refuses a first sighting without it.
    assert row["currency"] == archive.CURRENCY


# ── the download cache ───────────────────────────────────────────────────────

def test_a_fresh_archive_on_disk_is_not_refetched(tmp_path):
    path = tmp_path / "bulk.zip"
    path.write_bytes(b"x")
    calls = []
    us_backfill.ensure_bulk(path, fetch=lambda d: calls.append(d) or d,
                            today=TODAY, stale_after_days=7)
    assert calls == []


def test_an_archive_older_than_the_window_is_refetched(tmp_path):
    """Never re-downloading freezes the plane at whatever day the file was
    first fetched — which looks exactly like a working pipeline."""
    import os

    path = tmp_path / "bulk.zip"
    path.write_bytes(b"x")
    old = datetime.datetime(2026, 1, 1).timestamp()
    os.utime(path, (old, old))
    calls = []
    us_backfill.ensure_bulk(path, fetch=lambda d: calls.append(d) or d,
                            today=TODAY, stale_after_days=7)
    assert calls == [str(path)]


# ── the run ──────────────────────────────────────────────────────────────────

def test_a_truncated_archive_is_refused_and_writes_nothing(tmp_path):
    """The failure `symbol_directory` already guards: a proxy error page or a
    half-finished transfer parses as "the US has four hundred symbols"."""
    path = _zip(tmp_path, _universe(3))
    db = str(tmp_path / "plane.sqlite")
    with pytest.raises(RuntimeError, match="below the floor"):
        us_backfill.run_pipeline(db_path=db, zip_path=path, today=TODAY)
    assert storage.read_sql("SELECT * FROM market_prices",
                            sqlite3.connect(db)).empty


def test_a_full_run_writes_bars_master_rows_and_the_cursor(tmp_path, monkeypatch):
    monkeypatch.setattr(archive, "MIN_PLAUSIBLE_SYMBOLS", 2)
    path = _zip(tmp_path, {
        "data/daily/us/nasdaq stocks/1/aapl.us.txt": BULK_TXT,
        "data/daily/us/nyse etfs/spy.us.txt": PLAIN_CSV,
    })
    db = str(tmp_path / "plane.sqlite")
    summary = us_backfill.run_pipeline(db_path=db, zip_path=path, today=TODAY)

    assert summary["symbols"] == 2
    assert summary["rows"] == 4
    assert summary["failed_count"] == 0

    conn = sqlite3.connect(db)
    prices = storage.read_sql(
        "SELECT * FROM market_prices ORDER BY symbol, date", conn)
    assert list(prices["symbol"]) == ["AAPL", "AAPL", "SPY", "SPY"]

    instruments = storage.read_sql(
        "SELECT symbol, venue, kind, currency, listing_date, delisting_date"
        "  FROM instruments WHERE market = 'US' ORDER BY symbol", conn)
    assert list(instruments["venue"]) == ["NASDAQ", "NYSE"]
    assert list(instruments["kind"]) == ["EQUITY", "ETF"]
    assert instruments["listing_date"].isna().all()
    assert instruments["delisting_date"].isna().all()

    cursor = us_storage.load_price_cursor(conn).set_index("symbol")
    assert cursor.loc["AAPL", "first_bar"] == "2026-08-28"
    assert cursor.loc["AAPL", "last_bar"] == "2026-08-31"
    assert cursor.loc["AAPL", "adjust_state"] == us_adjust.SPLIT_ONLY


def test_one_broken_member_does_not_end_the_run(tmp_path, monkeypatch):
    monkeypatch.setattr(archive, "MIN_PLAUSIBLE_SYMBOLS", 2)
    path = _zip(tmp_path, {
        "data/daily/us/nasdaq stocks/1/aapl.us.txt": BULK_TXT,
        "data/daily/us/nasdaq stocks/1/junk.us.txt": "Date,Open\n2026-08-31,1\n",
    })
    summary = us_backfill.run_pipeline(
        db_path=str(tmp_path / "plane.sqlite"), zip_path=path, today=TODAY)
    assert summary["symbols"] == 1
    assert summary["failed"] == ["JUNK"]


def test_a_rerun_is_idempotent(tmp_path, monkeypatch):
    """The archive is republished daily and the job is scheduled weekly, so the
    second run over an overlapping file must update rather than duplicate."""
    monkeypatch.setattr(archive, "MIN_PLAUSIBLE_SYMBOLS", 1)
    path = _zip(tmp_path, {"data/daily/us/nasdaq stocks/1/aapl.us.txt": BULK_TXT})
    db = str(tmp_path / "plane.sqlite")
    us_backfill.run_pipeline(db_path=db, zip_path=path, today=TODAY)
    us_backfill.run_pipeline(db_path=db, zip_path=path, today=TODAY)
    n = storage.read_sql("SELECT COUNT(*) c FROM market_prices",
                         sqlite3.connect(db)).iloc[0]["c"]
    assert n == 2


# ── the seam: the metered feed must not re-price what the archive priced ─────

def test_covered_symbols_is_read_from_the_price_table(tmp_path, monkeypatch):
    """Not from a cursor flag: the price table is where the fact lives, so a
    missing or overwritten cursor row cannot make `us_prices` believe a
    bulk-priced symbol is safe to re-price."""
    monkeypatch.setattr(archive, "MIN_PLAUSIBLE_SYMBOLS", 1)
    path = _zip(tmp_path, {"data/daily/us/nasdaq stocks/1/aapl.us.txt": BULK_TXT})
    db = str(tmp_path / "plane.sqlite")
    us_backfill.run_pipeline(db_path=db, zip_path=path, today=TODAY)

    conn = sqlite3.connect(db)
    storage.execute(conn, "DELETE FROM us_price_cursor")
    assert us_backfill.covered_symbols(conn) == {"AAPL"}


def test_a_bulk_priced_symbol_is_never_scheduled_for_prices():
    """The seam. A raw metered close written over a split-adjusted history
    invents a return that `MARKETS["US"].glitch_down` would then mask."""
    cursor = pd.DataFrame([{"symbol": "AAPL", "last_bar": "2026-08-31",
                            "actions_fetched_at": None, "status": "ok",
                            "adjust_state": us_adjust.SPLIT_ONLY}])
    plan = us_prices.select_work(["AAPL"], cursor, budget=10, today=TODAY,
                                 bulk_covered={"AAPL"})
    assert plan == [("AAPL", False, True)]


def test_the_budget_still_buys_that_symbol_the_actions_the_archive_lacks():
    """Distributions are the one thing the bulk file does not carry, so the
    free tier's 20 requests a day buy the split_only -> total_return upgrade
    instead of re-buying bars the plane already has."""
    cursor = pd.DataFrame([{"symbol": "AAPL", "last_bar": "2026-01-01",
                            "actions_fetched_at": None, "status": "ok",
                            "adjust_state": us_adjust.SPLIT_ONLY}])
    plan = us_prices.select_work(["AAPL"], cursor, budget=10, today=TODAY,
                                 bulk_covered={"AAPL"})
    assert plan == [("AAPL", False, True)]


def test_a_bulk_priced_symbol_with_fresh_actions_drops_out_of_the_plan():
    """Nothing left to do must not occupy a slot: at 20 requests a day, a plan
    full of no-ops is a day of the backfill spent on nothing."""
    cursor = pd.DataFrame([{"symbol": "AAPL", "last_bar": "2026-08-31",
                            "actions_fetched_at": "2026-08-30", "status": "ok",
                            "adjust_state": us_adjust.SPLIT_ONLY}])
    plan = us_prices.select_work(["AAPL"], cursor, budget=10, today=TODAY,
                                 bulk_covered={"AAPL"})
    assert plan == []


def test_a_symbol_the_archive_does_not_carry_is_still_priced_by_the_feed():
    """The delisted tail, which the bulk dump does not hold, is exactly what
    the metered key is still for."""
    plan = us_prices.select_work(["AAPL", "DEAD"], pd.DataFrame(), budget=10,
                                 today=TODAY, bulk_covered={"AAPL"})
    assert plan == [("DEAD", True, True), ("AAPL", False, True)]


def test_no_bulk_coverage_leaves_the_existing_plan_untouched():
    """The paid-key path and every plane built before the archive existed."""
    universe = ["AAPL", "MSFT"]
    assert us_prices.select_work(universe, pd.DataFrame(), budget=10, today=TODAY) \
        == us_prices.select_work(universe, pd.DataFrame(), budget=10, today=TODAY,
                                 bulk_covered=frozenset())
