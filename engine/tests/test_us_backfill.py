"""The US backfill: the vendor reader, the conversions, the resume, the seam.

Offline. `stooq.com` is unreachable from the environment this was built in — the
sandbox's proxy refuses the CONNECT outright — which is exactly how the previous
version of this module shipped pointing at a URL that 404s. So the responses are
constructed in-process, and the tests are written to pin the things that go
wrong when a vendor answers something other than what the docs promised.

Two groups matter most. The first is that a body which is *not* CSV — a throttle
notice, an unknown symbol — never reaches the parser and never lands as a symbol
with no history: a throttled run that quietly recorded eleven thousand empty
symbols would look like a success. The second is the seam. WIKI writes a raw
close and this module writes a split-adjusted one; `market_prices` is keyed
`(market, symbol, date)`, so a symbol both of them wrote holds two conventions
in one column with a fabricated split-sized return where they meet.
"""
from __future__ import annotations

import datetime
import sqlite3

import pandas as pd
import pytest
from b3_pipeline import market_storage, storage, us_storage
from us_pipeline import stooq_daily as vendor
from us_pipeline import us_adjust, us_backfill, us_prices, us_seam

D = datetime.date
TODAY = D(2026, 9, 1)

# The quote endpoint's layout: plain headers, ISO dates.
PLAIN_CSV = ("Date,Open,High,Low,Close,Volume\n"
             "2026-08-28,98,99,97,98.0,900\n"
             "2026-08-31,100,101,99,100.0,1000\n")

# The bulk `.txt` layout the gated archive used: angle-bracketed headers,
# YYYYMMDD dates. Same data. Still accepted — see `stooq_daily`.
BULK_TXT = ("<TICKER>,<PER>,<DATE>,<TIME>,<OPEN>,<HIGH>,<LOW>,<CLOSE>,<VOL>,<OPENINT>\n"
            "AAPL.US,D,20260828,000000,98,99,97,98.0,900,0\n"
            "AAPL.US,D,20260831,000000,100,101,99,100.0,1000,0\n")


class FakeSession:
    """A `requests`-shaped stub returning a scripted body per symbol."""

    def __init__(self, bodies: dict, default: str = PLAIN_CSV):
        self.bodies, self.default, self.asked = bodies, default, []

    def get(self, url, params=None, timeout=None):
        symbol = (params or {}).get("s", "").upper().removesuffix(".US")
        self.asked.append(symbol)
        return _Response(self.bodies.get(symbol, self.default))


class _Response:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        return None


def _db(tmp_path, symbols=("AAPL",), name="plane.sqlite") -> str:
    """A plane with a universe on it. The walk enumerates from `instruments`."""
    path = str(tmp_path / name)
    conn = sqlite3.connect(path)
    storage.init_db(conn)
    market_storage.upsert_instruments(conn, pd.DataFrame(
        [{"market": "US", "symbol": s, "currency": "USD"} for s in symbols]))
    conn.commit()
    conn.close()
    return path


def _run(db, session, **kw):
    """A run that never sleeps and never asks the network."""
    return us_backfill.run_pipeline(
        db_path=db, today=TODAY, delay=0, session=session, **kw)


# ── a 200 is not evidence of data ────────────────────────────────────────────

@pytest.mark.parametrize("body,expected", [
    (PLAIN_CSV, "csv"),
    ("Exceeded the daily hits limit", "throttled"),
    ("exceeded the daily hits limit.\n", "throttled"),
    ("No data", "empty"),
    ("no data\n", "empty"),
])
def test_the_three_things_the_vendor_answers_are_told_apart(body, expected):
    assert vendor.classify_response(body) == expected


def test_a_real_csv_that_merely_mentions_the_words_is_still_csv():
    """The markers are matched on a short body only. A history long enough to
    be a history is CSV whatever a cell happens to spell."""
    long_csv = PLAIN_CSV + "".join(
        f"2026-0{m}-01,1,1,1,1,1\n" for m in range(1, 8)) * 10
    assert vendor.classify_response(long_csv) == "csv"


def test_the_throttle_notice_raises_rather_than_parsing_as_no_history():
    """The failure this guard exists for: a throttled run that recorded eleven
    thousand empty symbols would report success and leave a plane with no bars."""
    session = FakeSession({"AAPL": "Exceeded the daily hits limit"})
    with pytest.raises(vendor.Throttled, match="daily hit limit"):
        vendor.fetch_symbol("AAPL", session=session)


def test_an_unknown_symbol_is_a_skip_not_a_stop():
    """`""` back, not an exception: one name the vendor never heard of must not
    end a walk that has ten thousand good ones left."""
    session = FakeSession({"NOPE": "No data"})
    assert vendor.fetch_symbol("NOPE", session=session) == ""
    assert vendor.parse_bars("", "NOPE").empty


def test_the_symbol_is_asked_for_the_way_the_endpoint_spells_it():
    session = FakeSession({})
    vendor.fetch_symbol("BRK-B", session=session)
    assert session.asked == ["BRK-B"]


# ── parsing by header name, in either published layout ───────────────────────

@pytest.mark.parametrize("text", [BULK_TXT, PLAIN_CSV])
def test_both_published_layouts_parse_to_the_same_bars(text):
    """Stooq served angle-bracketed headers with YYYYMMDD in the gated zip and
    plain headers with ISO dates from the quote endpoint. The tolerance is the
    half of the original module that was right, so it is kept."""
    bars = vendor.parse_bars(text, "AAPL")
    assert list(bars["date"]) == [D(2026, 8, 28), D(2026, 8, 31)]
    assert list(bars["close"]) == [98.0, 100.0]
    assert list(bars["share_volume"]) == [900, 1000]


def test_bars_come_back_oldest_first():
    """`ingest_symbol` reads first_bar and last_bar off the ends."""
    reversed_rows = ("Date,Open,High,Low,Close,Volume\n"
                     "2026-08-31,100,101,99,100.0,1000\n"
                     "2026-08-28,98,99,97,98.0,900\n")
    bars = vendor.parse_bars(reversed_rows, "AAPL")
    assert bars["date"].iloc[0] == D(2026, 8, 28)
    assert bars["date"].iloc[-1] == D(2026, 8, 31)


def test_a_missing_column_names_what_went_missing():
    """The `av_parsers` rule: read by name, and fail loudly on drift rather
    than indexing by position into silently wrong numbers."""
    with pytest.raises(ValueError, match="close"):
        vendor.parse_bars("Date,Open,High,Low,Volume\n2026-08-31,1,1,1,1\n", "AAPL")


def test_a_date_format_nobody_publishes_raises_rather_than_emptying_the_symbol():
    """`errors="coerce"` alone would turn a format change into a symbol with no
    history, which reads as a delisting rather than as a broken parser."""
    with pytest.raises(ValueError, match="date format changed"):
        vendor.parse_bars(
            "Date,Open,High,Low,Close,Volume\n31/08/2026,1,1,1,1,1\n", "AAPL")


# ── the conversions, which are us_prices' on purpose ─────────────────────────

def test_volume_is_turnover_not_a_share_count():
    """`min_adtv` filters on money. A share count there keeps a penny stock
    trading a billion shares and drops Berkshire — the same conversion
    `us_prices.to_market_rows` makes, so a symbol that moves between the
    sources does not change what its volume column means."""
    bars = vendor.parse_bars(PLAIN_CSV, "AAPL")
    rows = us_backfill.to_market_rows("AAPL", bars, pd.Series(dtype=float))
    assert list(rows["volume"]) == [900 * 98.0, 1000 * 100.0]


def test_the_series_is_labelled_split_only_not_total_return():
    """The vendor is split-adjusted and carries no distributions. Calling that
    `total_return` is the claim `/market/status` and the backtest warning exist
    to stop, so the state is written into the row's own source."""
    bars = vendor.parse_bars(PLAIN_CSV, "AAPL")
    rows = us_backfill.to_market_rows("AAPL", bars, pd.Series(dtype=float))
    assert set(rows["source"]) == {f"{vendor.SOURCE}:{us_adjust.SPLIT_ONLY}"}
    assert list(rows["adj_close"]) == list(rows["close"])
    assert list(rows["split_adj_close"]) == list(rows["close"])


def test_the_master_row_claims_only_what_this_source_observed():
    """`listing_date`/`delisting_date` are declared intervals and belong to
    `us_listing`. Stooq serves the currently quoted, so inferring a delisting
    from absence here would delist every name that ever died. `venue` is absent
    too: this endpoint names none, and a guess would overwrite what
    `symbol_directory` actually captured."""
    bars = vendor.parse_bars(PLAIN_CSV, "AAPL")
    row = us_backfill._instrument_row("AAPL", bars)
    assert row["first_seen"] == D(2026, 8, 28)
    assert row["last_seen"] == D(2026, 8, 31)
    assert "listing_date" not in row and "delisting_date" not in row
    assert "venue" not in row
    # NOT NULL, and upsert_instruments refuses a first sighting without it.
    assert row["currency"] == vendor.CURRENCY


# ── the resume, which is what makes a daily ceiling workable ─────────────────

def test_a_symbol_this_source_has_never_priced_is_due():
    assert us_backfill.select_work(["AAPL"], pd.DataFrame(), today=TODAY) == ["AAPL"]


def test_a_symbol_priced_recently_is_skipped():
    """The scarce resource is requests, not patience: re-asking about a symbol
    that already has last week's bars spends one on ground already covered."""
    cursor = pd.DataFrame([{"symbol": "AAPL", "prices_fetched_at": "2026-08-30",
                            "adjust_state": us_adjust.SPLIT_ONLY}])
    assert us_backfill.select_work(["AAPL"], cursor, today=TODAY) == []


def test_a_symbol_priced_longer_ago_than_the_window_comes_back():
    cursor = pd.DataFrame([{"symbol": "AAPL", "prices_fetched_at": "2026-01-01",
                            "adjust_state": us_adjust.SPLIT_ONLY}])
    assert us_backfill.select_work(["AAPL"], cursor, today=TODAY) == ["AAPL"]


def test_a_symbol_the_vendor_does_not_quote_is_not_asked_about_again():
    """The failure that would make a ceiling-bounded walk never finish. Stooq
    has never heard of most of the delisted tail `instruments` carries, and
    without a record of having asked, every run spends its whole ceiling
    re-asking about them and never reaches a symbol it could price."""
    cursor = pd.DataFrame([{"symbol": "DEAD", "prices_fetched_at": "2026-08-30",
                            "adjust_state": None,
                            "status": us_backfill.UNKNOWN_STATUS}])
    assert us_backfill.select_work(["DEAD"], cursor, today=TODAY) == []


def test_that_record_still_expires_with_the_refresh_window():
    """A ticker can start trading. The skip is a cache, not a blacklist."""
    cursor = pd.DataFrame([{"symbol": "DEAD", "prices_fetched_at": "2026-01-01",
                            "adjust_state": None,
                            "status": us_backfill.UNKNOWN_STATUS}])
    assert us_backfill.select_work(["DEAD"], cursor, today=TODAY) == ["DEAD"]


def test_a_symbol_another_source_priced_is_still_due():
    """The whole point of the walk is replacing a raw, frozen WIKI history with
    a live one, so WIKI's own cursor row must not read as work already done."""
    cursor = pd.DataFrame([{"symbol": "AAPL", "prices_fetched_at": str(TODAY),
                            "adjust_state": us_adjust.TOTAL_RETURN}])
    assert us_backfill.select_work(["AAPL"], cursor, today=TODAY) == ["AAPL"]


# ── the run ──────────────────────────────────────────────────────────────────

def test_a_full_run_writes_bars_master_rows_and_the_cursor(tmp_path):
    db = _db(tmp_path, ("AAPL", "SPY"))
    summary = _run(db, FakeSession({}))

    assert summary["symbols"] == 2
    assert summary["rows"] == 4
    assert summary["failed_count"] == 0
    assert summary["throttled"] is False

    conn = sqlite3.connect(db)
    prices = storage.read_sql(
        "SELECT * FROM market_prices ORDER BY symbol, date", conn)
    assert list(prices["symbol"]) == ["AAPL", "AAPL", "SPY", "SPY"]

    instruments = storage.read_sql(
        "SELECT symbol, listing_date, delisting_date FROM instruments"
        " WHERE market = 'US' ORDER BY symbol", conn)
    assert instruments["listing_date"].isna().all()
    assert instruments["delisting_date"].isna().all()

    cursor = us_storage.load_price_cursor(conn).set_index("symbol")
    assert cursor.loc["AAPL", "first_bar"] == "2026-08-28"
    assert cursor.loc["AAPL", "last_bar"] == "2026-08-31"
    assert cursor.loc["AAPL", "adjust_state"] == us_adjust.SPLIT_ONLY


def test_a_symbol_the_vendor_does_not_know_costs_only_itself(tmp_path):
    db = _db(tmp_path, ("AAPL", "NOPE"))
    summary = _run(db, FakeSession({"NOPE": "No data"}))
    assert summary["symbols"] == 1
    assert summary["unknown_to_vendor"] == 1
    assert summary["failed"] == []


def test_the_next_run_does_not_re_ask_about_a_symbol_the_vendor_lacks(tmp_path):
    """End to end, because the cursor row and the resume policy have to agree:
    the walk is bounded by a daily ceiling, so a name that comes back every run
    is a name that eats the ceiling forever."""
    db = _db(tmp_path, ("AAPL", "NOPE"))
    _run(db, FakeSession({"NOPE": "No data"}))

    session = FakeSession({"NOPE": "No data"})
    _run(db, session)
    assert session.asked == []


def test_one_broken_response_does_not_end_the_run(tmp_path):
    db = _db(tmp_path, ("AAPL", "JUNK"))
    summary = _run(db, FakeSession({"JUNK": "Date,Open\n2026-08-31,1\n"}))
    assert summary["symbols"] == 1
    assert summary["failed"] == ["JUNK"]


def test_a_rerun_is_idempotent(tmp_path):
    """A second walk over an overlapping history must update, not duplicate."""
    db = _db(tmp_path)
    _run(db, FakeSession({}))
    _run(db, FakeSession({}), refresh_after_days=0)
    n = storage.read_sql("SELECT COUNT(*) c FROM market_prices",
                         sqlite3.connect(db)).iloc[0]["c"]
    assert n == 2


# ── the daily ceiling is the end of the work, not a failure ──────────────────

def test_the_throttle_stops_the_walk_and_keeps_what_it_wrote(tmp_path):
    """`us_prices` makes the same call about an exhausted request budget: the
    day's allowance running out is what the day's allowance does."""
    db = _db(tmp_path, ("AAPL", "MSFT", "ZZZZ"))
    session = FakeSession({"MSFT": "Exceeded the daily hits limit"})
    summary = _run(db, session)

    assert summary["throttled"] is True
    assert summary["symbols"] == 1
    assert summary["failed"] == []
    # It stopped rather than carrying on into the rest of the universe.
    assert "ZZZZ" not in session.asked

    prices = storage.read_sql("SELECT DISTINCT symbol FROM market_prices",
                              sqlite3.connect(db))
    assert list(prices["symbol"]) == ["AAPL"]


def test_the_next_run_resumes_where_the_throttled_one_stopped(tmp_path):
    db = _db(tmp_path, ("AAPL", "MSFT"))
    _run(db, FakeSession({"MSFT": "Exceeded the daily hits limit"}))

    session = FakeSession({})
    summary = _run(db, session)
    assert session.asked == ["MSFT"]        # AAPL is fresh; not re-bought
    assert summary["symbols"] == 1


# ── the seam with WIKI: one symbol, one source, one convention ───────────────

def _wiki_bars(conn, symbol: str, dates: list) -> None:
    """A raw, frozen WIKI history, as `wiki_backfill` would have left it."""
    market_storage.upsert_market_prices(conn, pd.DataFrame([
        {"market": "US", "symbol": symbol, "date": d, "open": 1.0, "high": 1.0,
         "low": 1.0, "close": 1.0, "volume": 1.0, "split_adj_close": 1.0,
         "adj_close": 1.0, "source": "wiki-prices:total_return"} for d in dates]))
    conn.commit()


def test_wiki_bars_outside_the_new_range_are_dropped_not_left_in_front(tmp_path):
    """The failure an upsert alone cannot reach. WIKI's rows before this
    source's first bar are still raw, still spliced onto a split-adjusted
    series — and now hidden, because every date the run touched looks right."""
    db = _db(tmp_path)
    conn = sqlite3.connect(db)
    _wiki_bars(conn, "AAPL", ["1999-01-04", "2018-03-27"])
    conn.close()

    _run(db, FakeSession({}))

    prices = storage.read_sql(
        "SELECT date, source FROM market_prices ORDER BY date",
        sqlite3.connect(db))
    assert list(prices["date"]) == ["2026-08-28", "2026-08-31"]
    assert set(prices["source"]) == {f"{vendor.SOURCE}:{us_adjust.SPLIT_ONLY}"}


def test_the_delete_never_touches_another_symbol(tmp_path):
    """WIKI keeps the names this vendor cannot serve — mostly the ones that
    died inside its maintenance window, where nothing extends them and the
    question does not arise."""
    db = _db(tmp_path)
    conn = sqlite3.connect(db)
    _wiki_bars(conn, "RSHCQ", ["2015-02-02"])
    conn.close()

    _run(db, FakeSession({}))

    kept = storage.read_sql(
        "SELECT symbol FROM market_prices WHERE source LIKE 'wiki%'",
        sqlite3.connect(db))
    assert list(kept["symbol"]) == ["RSHCQ"]


def test_the_delete_is_scoped_so_a_source_cannot_drop_its_own_rows(tmp_path):
    """What keeps a re-run idempotent rather than destructive."""
    db = _db(tmp_path)
    _run(db, FakeSession({}))
    conn = sqlite3.connect(db)
    gone = market_storage.delete_market_prices_from_other_sources(
        conn, "US", "AAPL", vendor.SOURCE_PREFIX)
    assert gone == 0
    n = storage.read_sql("SELECT COUNT(*) c FROM market_prices", conn).iloc[0]["c"]
    assert n == 2


# ── the seam: the metered feed must not re-price what this source priced ─────

def test_covered_symbols_is_read_from_the_price_table(tmp_path):
    """Not from a cursor flag: the price table is where the fact lives, so a
    missing or overwritten cursor row cannot make `us_prices` believe a
    bulk-priced symbol is safe to re-price."""
    db = _db(tmp_path)
    _run(db, FakeSession({}))
    conn = sqlite3.connect(db)
    storage.execute(conn, "DELETE FROM us_price_cursor")
    assert us_seam.covered_symbols(conn) == {"AAPL"}


def test_rows_left_by_the_gated_bulk_attempt_fence_identically(tmp_path):
    """A plane that ran the old bulk path carries `stooq-bulk` rows. Both
    conventions are split-adjusted, so both must keep the metered feed's raw
    close out — which is why the match is on the prefix, not the exact tag."""
    db = _db(tmp_path)
    conn = sqlite3.connect(db)
    market_storage.upsert_market_prices(conn, pd.DataFrame([
        {"market": "US", "symbol": "OLD", "date": "2024-01-01", "open": 1.0,
         "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0,
         "split_adj_close": 1.0, "adj_close": 1.0,
         "source": "stooq-bulk:split_only"}]))
    conn.commit()
    assert "OLD" in us_seam.covered_symbols(conn)


def test_a_bulk_priced_symbol_is_never_scheduled_for_prices():
    """The seam. A raw metered close written over a split-adjusted history
    invents a return that `MARKETS["US"].glitch_down` would then mask."""
    cursor = pd.DataFrame([{"symbol": "AAPL", "last_bar": "2026-08-31",
                            "actions_fetched_at": None, "status": "ok",
                            "adjust_state": us_adjust.SPLIT_ONLY}])
    plan = us_prices.select_work(["AAPL"], cursor, budget=10, today=TODAY,
                                 bulk_covered={"AAPL"})
    assert plan == [("AAPL", False, True)]


def test_the_budget_still_buys_that_symbol_the_actions_the_vendor_lacks():
    """Distributions are the one thing this source does not carry, so the free
    tier's 20 requests a day buy the split_only -> total_return upgrade instead
    of re-buying bars the plane already has."""
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


def test_a_symbol_this_source_does_not_carry_is_still_priced_by_the_feed():
    """The delisted tail, which the quote endpoint does not hold, is exactly
    what the metered key is still for."""
    plan = us_prices.select_work(["AAPL", "DEAD"], pd.DataFrame(), budget=10,
                                 today=TODAY, bulk_covered={"AAPL"})
    assert plan == [("DEAD", True, True), ("AAPL", False, True)]


def test_no_bulk_coverage_leaves_the_existing_plan_untouched():
    """The paid-key path and every plane built before this source existed."""
    universe = ["AAPL", "MSFT"]
    assert us_prices.select_work(universe, pd.DataFrame(), budget=10, today=TODAY) \
        == us_prices.select_work(universe, pd.DataFrame(), budget=10, today=TODAY,
                                 bulk_covered=frozenset())
