"""Survivorship semantics: the dead stay in the universe, and they die properly.

This file used to pin the opposite property. `load_active_tickers` /
`_apply_delisted_filter` *dropped* tickers that had delisted before the window
started — de-duplication, not survivorship correction, and it removed exactly the
names a survivorship-free backtest exists to keep. Worse, names that died mid-
window were kept and then forward-filled at a flat price to the end of the run,
so a book holding one earned a riskless fictional position forever.

What is pinned now is the real thing: an instrument is tradable exactly while it
was listed, its price stops when it stops trading, and the holder books the exit.

All synthetic SQLite in tmp_path — no real price data, no network.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

from b3_pipeline import market_storage, storage
from backtests.core import universe


# ── Helpers ────────────────────────────────────────────────────────────────────

def _setup_db(tmp_path: Path) -> str:
    db_path = str(tmp_path / "test.sqlite")
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = OFF")
    storage.init_db(conn)
    conn.commit()
    conn.close()
    return db_path


def _insert_price(conn, ticker, date, close=10.0):
    conn.execute(
        """INSERT INTO prices (ticker, isin_code, date, open, high, low, close,
                               adj_close, split_adj_high, split_adj_low,
                               split_adj_close, volume)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (ticker, "UNKNOWN", date, close, close, close, close, close, close,
         close, close, 100000))
    conn.commit()


def _instrument(db_path, ticker, listing=None, delisting=None, worthless=0):
    conn = sqlite3.connect(db_path)
    market_storage.upsert_instruments(conn, pd.DataFrame([dict(
        market="B3", symbol=ticker, currency="BRL", listing_date=listing,
        delisting_date=delisting, is_worthless=worthless)]))
    conn.close()


def _intervals(**rows):
    """[ticker, listing_date, delisting_date, is_worthless] from kwargs."""
    return pd.DataFrame([
        dict(ticker=t, listing_date=pd.Timestamp(li) if li else pd.NaT,
             delisting_date=pd.Timestamp(de) if de else pd.NaT, is_worthless=w)
        for t, (li, de, w) in rows.items()
    ])


DATES = pd.to_datetime(["2010-01-04", "2010-01-05", "2010-01-06", "2010-01-07"])


# ── tradable_mask ─────────────────────────────────────────────────────────────

def test_a_name_is_tradable_only_between_its_bounds():
    mask = universe.tradable_mask(
        _intervals(VALE3=("2010-01-05", "2010-01-06", 0)), DATES, ["VALE3"])
    assert list(mask["VALE3"]) == [False, True, True, False]


def test_both_bounds_are_inclusive():
    """The delisting date is the last day it traded, at a real price. Excluding
    it would throw away a genuine session."""
    mask = universe.tradable_mask(
        _intervals(X=("2010-01-05", "2010-01-05", 0)), DATES, ["X"])
    assert list(mask["X"]) == [False, True, False, False]


def test_an_open_ended_interval_never_closes():
    mask = universe.tradable_mask(
        _intervals(PETR4=("2010-01-05", None, 0)), DATES, ["PETR4"])
    assert list(mask["PETR4"]) == [False, True, True, True]


def test_a_ticker_with_no_interval_is_tradable_throughout():
    """The no-op property: a plane whose `instruments` table is not backfilled
    behaves exactly as it did before this module existed."""
    mask = universe.tradable_mask(_intervals(), DATES, ["MGLU3"])
    assert mask["MGLU3"].all()


def test_an_empty_master_is_all_permissive():
    mask = universe.tradable_mask(pd.DataFrame(), DATES, ["A", "B"])
    assert mask.to_numpy().all()


# ── ffill stops at delisting ──────────────────────────────────────────────────

def _px():
    return pd.DataFrame({"X": [10.0, None, None, None]}, index=DATES)


def test_the_fill_still_bridges_gaps_inside_the_listing():
    mask = universe.tradable_mask(_intervals(X=(None, None, 0)), DATES, ["X"])
    assert list(universe.ffill_until_delisting(_px(), mask)["X"]) == [10.0] * 4


def test_the_fill_stops_at_delisting_instead_of_running_forever():
    """The bug this replaces: a delisted name carried a flat price to the end of
    the window, so a book holding it earned a riskless fictional position."""
    mask = universe.tradable_mask(
        _intervals(X=(None, "2010-01-05", 0)), DATES, ["X"])
    out = universe.ffill_until_delisting(_px(), mask)["X"]
    assert list(out[:2]) == [10.0, 10.0]
    assert out[2:].isna().all()


# ── delisting returns ─────────────────────────────────────────────────────────

def test_a_liquidated_name_books_a_zero_return_after_its_last_day():
    """Delisted is not worthless. An acquisition, or a venue delisting where the
    asset trades on elsewhere, pays the last mark."""
    dr = universe.delisting_returns(
        _intervals(X=(None, "2010-01-05", 0)), DATES, ["X"])
    assert dr["X"].iloc[2] == 0.0
    assert dr["X"].drop(dr.index[2]).isna().all()


def test_a_worthless_name_books_a_total_loss():
    dr = universe.delisting_returns(
        _intervals(X=(None, "2010-01-05", 1)), DATES, ["X"])
    assert dr["X"].iloc[2] == -1.0


def test_the_terminal_return_lands_after_the_last_traded_day():
    """The last traded day is a normal session at a real price; what the holder
    recovers is settled in the period after it."""
    dr = universe.delisting_returns(
        _intervals(X=(None, "2010-01-05", 1)), DATES, ["X"])
    assert pd.isna(dr["X"].iloc[1])


def test_a_delisting_past_the_window_books_nothing():
    """It was still trading on every date the backtest can see, which is true."""
    dr = universe.delisting_returns(
        _intervals(X=(None, "2011-01-01", 1)), DATES, ["X"])
    assert dr["X"].isna().all()


def test_a_live_name_books_nothing():
    dr = universe.delisting_returns(_intervals(X=(None, None, 0)), DATES, ["X"])
    assert dr["X"].isna().all()


# ── fill_returns ──────────────────────────────────────────────────────────────

def test_the_terminal_return_survives_the_nan_fill():
    """A bare fillna(0.0) would overwrite the write-off with a zero and freeze
    the position — the same bug as the runaway ffill, one step downstream."""
    ret = pd.DataFrame({"X": [0.01, None, None, None]}, index=DATES)
    dr = universe.delisting_returns(
        _intervals(X=(None, "2010-01-05", 1)), DATES, ["X"])
    out = universe.fill_returns(ret, dr)
    assert list(out["X"]) == [0.01, 0.0, -1.0, 0.0]


def test_without_delistings_it_is_the_old_fillna():
    ret = pd.DataFrame({"X": [0.01, None]}, index=DATES[:2])
    assert list(universe.fill_returns(ret, pd.DataFrame())["X"]) == [0.01, 0.0]


# ── through the real loaders ──────────────────────────────────────────────────

def test_a_name_that_died_before_the_window_is_still_loaded(tmp_path):
    """The inverted filter dropped exactly this ticker. It has to be here: the
    whole point of keeping the dead is that a backtest can hold them."""
    from backtests.core.data import load_b3_data

    db_path = _setup_db(tmp_path)
    conn = sqlite3.connect(db_path)
    _insert_price(conn, "VALE3", "2010-01-04")
    conn.close()
    _instrument(db_path, "VALE3", delisting="2005-06-30")

    adj_close, _, _ = load_b3_data(db_path, "2010-01-01", "2010-01-31")
    assert "VALE3" in adj_close.columns


def test_a_mid_window_delisting_goes_nan_not_flat(tmp_path):
    from backtests.core.data import load_b3_data

    db_path = _setup_db(tmp_path)
    conn = sqlite3.connect(db_path)
    for d in ("2010-01-04", "2010-01-05"):
        _insert_price(conn, "DEAD3", d)
        _insert_price(conn, "LIVE3", d)
    _insert_price(conn, "LIVE3", "2010-01-06")
    conn.close()
    _instrument(db_path, "DEAD3", delisting="2010-01-05")

    adj_close, _, _ = load_b3_data(db_path, "2010-01-01", "2010-01-31")
    assert pd.isna(adj_close["DEAD3"].iloc[-1])
    assert adj_close["LIVE3"].iloc[-1] == 10.0


def test_an_unbackfilled_plane_loads_exactly_as_before(tmp_path):
    """With no `instruments` rows the loaders must be byte-identical to their
    pre-change behaviour — which is what lets this land without silently
    rewriting every stored backtest."""
    from backtests.core.data import load_b3_data

    db_path = _setup_db(tmp_path)
    conn = sqlite3.connect(db_path)
    _insert_price(conn, "MGLU3", "2010-01-04")
    conn.close()

    adj_close, close_px, _ = load_b3_data(db_path, "2010-01-01", "2010-01-31")
    assert list(adj_close.columns) == ["MGLU3"]
    assert not adj_close["MGLU3"].isna().any()


def test_intervals_degrade_gracefully_without_the_table(tmp_path):
    """A plane created by an older engine has no `instruments`. That must read as
    "no information", not as an exception in the middle of a backtest."""
    db_path = _setup_db(tmp_path)
    conn = sqlite3.connect(db_path)
    conn.execute("DROP TABLE instruments")
    conn.commit()
    conn.close()
    assert universe.load_listing_intervals(db_path).empty
