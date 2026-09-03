"""The seam: where the free split-adjusted history stops and the metered feed starts.

These tests used to live in `test_us_backfill`, which was deleted with the
per-symbol Stooq walk it covered. They did not go with it, because none of them
were about that walk. They are about the boundary it wrote *across*, and the
plane still holds 28,057,516 rows on the far side of it.

`market_prices` is keyed `(market, symbol, date)` and its `close` carries
whatever convention its writer used: the bulk `d_us_txt` dump and `us_daily`
publish a back-adjusted close, WIKI and AlphaVantage a raw one. A symbol that
picks up two of those holds two conventions in one column with a fabricated
split-sized return where they meet — the artefact `MARKETS["US"].glitch_down`
is built to mask, which is what makes it dangerous rather than merely visible.

The most load-bearing test here is `test_the_stooq_prefix_still_fences...`.
Deleting the walk deleted the module that owned the literal `"stooq"`, and
dropping it from `SPLIT_ADJUSTED_PREFIXES` would have re-opened the seam for
every symbol the bulk dump priced, silently.
"""
from __future__ import annotations

import datetime
import sqlite3

import pandas as pd
from b3_pipeline import market_storage, storage
from us_pipeline import us_adjust, us_prices, us_seam

D = datetime.date
TODAY = D(2026, 9, 1)

BULK = "stooq-bulk:split_only"
DAILY = "massive-daily:split_only"
WIKI = "wiki-prices:total_return"


def _plane(tmp_path) -> sqlite3.Connection:
    path = str(tmp_path / "plane.sqlite")
    conn = sqlite3.connect(path)
    storage.init_db(conn)
    return conn


def _bars(conn, symbol: str, dates, source: str) -> None:
    market_storage.upsert_market_prices(conn, pd.DataFrame([
        {"market": "US", "symbol": symbol, "date": d, "open": 1.0, "high": 1.0,
         "low": 1.0, "close": 1.0, "volume": 1.0, "split_adj_close": 1.0,
         "adj_close": 1.0, "source": source} for d in dates]))
    conn.commit()


# ── which symbols the metered feed must not re-price ─────────────────────────

def test_covered_symbols_is_read_from_the_price_table(tmp_path):
    """Not from a cursor flag: the price table is where the fact lives, so a
    missing or overwritten cursor row cannot make `us_prices` believe a
    bulk-priced symbol is safe to re-price."""
    conn = _plane(tmp_path)
    _bars(conn, "AAPL", ["2026-08-31"], BULK)
    storage.execute(conn, "DELETE FROM us_price_cursor")
    assert us_seam.covered_symbols(conn) == {"AAPL"}


def test_the_stooq_prefix_still_fences_the_bulk_dump_after_its_walk_was_deleted(tmp_path):
    """The regression this file exists for.

    `SPLIT_ADJUSTED_PREFIXES` used to read the literal `"stooq"` off
    `stooq_daily.SOURCE_PREFIX`. That module is gone — its vendor started
    demanding a key nobody can obtain — but 13,347 symbols and 28,057,516 rows
    written by the bulk `d_us_txt` dump are still tagged `stooq-bulk:split_only`,
    and this prefix is the whole of what keeps `us_prices` from writing a raw
    metered close over them. Losing it with the module would not have raised."""
    conn = _plane(tmp_path)
    _bars(conn, "OLD", ["2024-01-01"], BULK)
    assert us_seam.STOOQ_PREFIX == "stooq"
    assert "OLD" in us_seam.covered_symbols(conn)


def test_the_daily_refresh_fences_identically(tmp_path):
    """`us_daily` publishes the same back-adjusted convention, so a symbol it
    has touched is closed to the metered feed on the same grounds."""
    conn = _plane(tmp_path)
    _bars(conn, "NEW", ["2026-09-01"], DAILY)
    assert "NEW" in us_seam.covered_symbols(conn)


def test_a_wiki_only_symbol_is_not_fenced(tmp_path):
    """The delisted tail the dump does not carry — 1,502 symbols on a raw
    total-return close. The metered key is exactly what is still for them, so
    reading them as covered would strand them frozen at 2018."""
    conn = _plane(tmp_path)
    _bars(conn, "DEAD", ["2018-03-27"], WIKI)
    assert us_seam.covered_symbols(conn) == set()


# ── the delete that keeps one symbol on one convention ───────────────────────

def test_wiki_bars_outside_the_new_range_are_dropped_not_left_in_front(tmp_path):
    """The failure an upsert alone cannot reach. WIKI's rows before a
    split-adjusted series' first bar are still raw, still spliced onto it — and
    now hidden, because every date the new run touched looks right."""
    conn = _plane(tmp_path)
    _bars(conn, "AAPL", ["1999-01-04", "2018-03-27"], WIKI)
    _bars(conn, "AAPL", ["2026-08-28", "2026-08-31"], BULK)

    market_storage.delete_market_prices_from_other_sources(
        conn, "US", "AAPL", us_seam.STOOQ_PREFIX)

    prices = storage.read_sql(
        "SELECT date, source FROM market_prices ORDER BY date", conn)
    assert list(prices["date"]) == ["2026-08-28", "2026-08-31"]
    assert set(prices["source"]) == {BULK}


def test_the_delete_never_touches_another_symbol(tmp_path):
    """WIKI keeps the names the dump cannot serve — mostly the ones that died
    inside its maintenance window, where nothing extends them and the question
    does not arise."""
    conn = _plane(tmp_path)
    _bars(conn, "RSHCQ", ["2015-02-02"], WIKI)
    _bars(conn, "AAPL", ["2026-08-31"], BULK)

    market_storage.delete_market_prices_from_other_sources(
        conn, "US", "AAPL", us_seam.STOOQ_PREFIX)

    kept = storage.read_sql(
        "SELECT symbol FROM market_prices WHERE source LIKE 'wiki%'", conn)
    assert list(kept["symbol"]) == ["RSHCQ"]


def test_the_delete_is_scoped_so_a_source_cannot_drop_its_own_rows(tmp_path):
    """What keeps a re-run idempotent rather than destructive."""
    conn = _plane(tmp_path)
    _bars(conn, "AAPL", ["2026-08-28", "2026-08-31"], BULK)

    gone = market_storage.delete_market_prices_from_other_sources(
        conn, "US", "AAPL", us_seam.STOOQ_PREFIX)

    assert gone == 0
    n = storage.read_sql("SELECT COUNT(*) c FROM market_prices", conn).iloc[0]["c"]
    assert n == 2


# ── how the seam re-files the metered feed's plan ────────────────────────────

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
    """Distributions are the one thing the free source does not carry, so the
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


def test_a_symbol_this_source_does_not_carry_is_still_priced_by_the_feed():
    """The delisted tail, which the dump does not hold, is exactly what the
    metered key is still for."""
    plan = us_prices.select_work(["AAPL", "DEAD"], pd.DataFrame(), budget=10,
                                 today=TODAY, bulk_covered={"AAPL"})
    assert plan == [("DEAD", True, True), ("AAPL", False, True)]


def test_no_bulk_coverage_leaves_the_existing_plan_untouched():
    """The paid-key path and every plane built before this source existed."""
    universe = ["AAPL", "MSFT"]
    assert us_prices.select_work(universe, pd.DataFrame(), budget=10, today=TODAY) \
        == us_prices.select_work(universe, pd.DataFrame(), budget=10, today=TODAY,
                                 bulk_covered=frozenset())
