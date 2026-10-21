"""The US daily refresh: the split re-adjustment, the grouped reader, the window.

Offline. Every response here is constructed, because a test that reaches a
vendor tests the vendor. That is not hypothetical here: the module this one
replaces shipped against a documented endpoint that answered 404, and the one
before it against a bulk URL that did the same — both have since been deleted.

The tests that matter most are the ones about applying a split twice. A split
applied once is a history made comparable; applied twice it is a history
silently four times too small, with no error, no exception and nothing
downstream able to tell. Idempotency is the invariant, not the arithmetic.
"""
from __future__ import annotations

import datetime
import sqlite3

import pandas as pd
import pytest
from b3_pipeline import market_storage, split_storage, storage
from us_pipeline import massive_daily as vendor
from us_pipeline import us_daily

D = datetime.date


def _grouped(rows):
    return {"status": "OK", "resultsCount": len(rows), "results": rows}


def _tick(sym, c=10.0, v=1000.0):
    return {"T": sym, "o": c, "h": c, "l": c, "c": c, "v": v, "vw": c, "n": 5}


def _plane(tmp_path, symbol="AAPL"):
    """A plane holding one symbol with four sessions of history at 100."""
    path = str(tmp_path / "plane.sqlite")
    conn = sqlite3.connect(path)
    storage.init_db(conn)
    market_storage.upsert_instruments(conn, pd.DataFrame(
        [{"market": "US", "symbol": symbol, "currency": "USD"}]))
    market_storage.upsert_market_prices(conn, pd.DataFrame([
        {"market": "US", "symbol": symbol, "date": d, "open": 100.0,
         "high": 100.0, "low": 100.0, "close": 100.0, "volume": 5000.0,
         "split_adj_close": 100.0, "adj_close": 100.0,
         "source": "stooq-bulk:split_only"}
        for d in ("2026-08-25", "2026-08-26", "2026-08-27", "2026-08-28")]))
    conn.commit()
    return path, conn


SPLIT_4FOR1 = {"id": "E-aapl-4for1", "ticker": "AAPL",
               "execution_date": "2026-08-27", "split_from": 1, "split_to": 4}


# --- the ratio ------------------------------------------------------------

def test_a_forward_split_scales_old_prices_down_and_a_reverse_scales_them_up():
    assert vendor.split_factor(SPLIT_4FOR1) == 0.25
    assert vendor.split_factor(
        {"split_from": 50, "split_to": 1, "ticker": "DPU", "id": "x"}) == 50.0


@pytest.mark.parametrize("bad", [{"split_from": 0, "split_to": 4},
                                 {"split_from": 1, "split_to": 0}])
def test_a_split_with_a_zero_leg_is_refused_rather_than_zeroing_a_history(bad):
    with pytest.raises(ValueError, match="refusing to scale"):
        vendor.split_factor({**bad, "ticker": "X", "id": "x"})


# --- the re-adjustment ----------------------------------------------------

def test_only_bars_before_the_execution_date_are_rescaled(tmp_path):
    """The vendor already reports the execution day itself on the new basis, so
    scaling it too would manufacture the gap this closes."""
    path, conn = _plane(tmp_path)
    n = split_storage.adjust_history_for_split(
        conn, "US", "AAPL", "2026-08-27", 0.25)
    assert n == 2
    got = dict(storage.read_sql(
        "SELECT date, close FROM market_prices WHERE market='US'",
        conn).set_index("date")["close"])
    assert got["2026-08-25"] == 25.0 and got["2026-08-26"] == 25.0
    assert got["2026-08-27"] == 100.0 and got["2026-08-28"] == 100.0


def test_turnover_is_not_rescaled_because_a_split_leaves_money_traded_alone(tmp_path):
    """`volume` holds turnover, not a share count: a split multiplies shares by
    exactly what it divides price by. Scaling it would be a silent corruption."""
    path, conn = _plane(tmp_path)
    split_storage.adjust_history_for_split(conn, "US", "AAPL", "2026-08-27", 0.25)
    vols = storage.read_sql(
        "SELECT DISTINCT volume FROM market_prices WHERE market='US'", conn)
    assert list(vols["volume"]) == [5000.0]


def test_every_price_column_moves_together(tmp_path):
    path, conn = _plane(tmp_path)
    split_storage.adjust_history_for_split(conn, "US", "AAPL", "2026-08-27", 0.25)
    row = storage.read_sql(
        "SELECT open, high, low, close, split_adj_close, adj_close"
        " FROM market_prices WHERE date = '2026-08-25'", conn).iloc[0]
    assert set(row.values) == {25.0}


# --- idempotency, the invariant -------------------------------------------

def test_a_split_is_applied_once_however_often_the_window_reports_it(tmp_path, monkeypatch):
    """The window looks back further than the price gap on purpose, so the same
    split is *seen* many nights running. Seeing it twice must not scale twice."""
    path, conn = _plane(tmp_path)
    monkeypatch.setattr(vendor, "fetch_splits", lambda *a, **k: [SPLIT_4FOR1])

    first = us_daily.apply_due_splits(conn, D(2026, 8, 1), D(2026, 9, 1), {"AAPL"})
    second = us_daily.apply_due_splits(conn, D(2026, 8, 1), D(2026, 9, 1), {"AAPL"})

    assert first["splits_applied"] == 1 and first["rows_rescaled"] == 2
    assert second["splits_applied"] == 0 and second["splits_already_done"] == 1
    close = storage.read_sql(
        "SELECT close FROM market_prices WHERE date='2026-08-25'", conn
    ).iloc[0]["close"]
    assert close == 25.0, "a second application would have made this 6.25"


def test_a_split_for_a_symbol_the_plane_does_not_hold_is_still_recorded(tmp_path, monkeypatch):
    """Genuinely done — there was nothing to scale — and leaving it unrecorded
    would re-ask about it every night for as long as the window reaches back."""
    path, conn = _plane(tmp_path)
    other = {**SPLIT_4FOR1, "id": "E-zzz", "ticker": "ZZZ"}
    monkeypatch.setattr(vendor, "fetch_splits", lambda *a, **k: [other])
    summary = us_daily.apply_due_splits(conn, D(2026, 8, 1), D(2026, 9, 1), {"AAPL"})
    assert summary["splits_applied"] == 1 and summary["rows_rescaled"] == 0
    assert "E-zzz" in split_storage.applied_split_ids(conn, "US")


def _committed_close(path, date="2026-08-25"):
    """What another process would read: a fresh connection sees commits only."""
    other = sqlite3.connect(path)
    try:
        return other.execute(
            "SELECT close FROM market_prices WHERE date = ?", (date,)).fetchone()[0]
    finally:
        other.close()


def test_a_crash_between_rescale_and_record_leaves_neither(tmp_path, monkeypatch):
    """Killed after the UPDATE and before the record: the next run's window
    still reaches the split, so a committed UPDATE with no record would be
    scaled again (4-for-1 becoming 16-for-1). Both or neither."""
    path, conn = _plane(tmp_path)
    monkeypatch.setattr(vendor, "fetch_splits", lambda *a, **k: [SPLIT_4FOR1])

    real_execute = storage.execute

    def dies_recording_rows(c, sql, params=None):
        if sql.startswith("UPDATE applied_splits"):
            raise RuntimeError("killed")
        return real_execute(c, sql, params)

    monkeypatch.setattr(storage, "execute", dies_recording_rows)
    with pytest.raises(RuntimeError, match="killed"):
        us_daily.apply_due_splits(conn, D(2026, 8, 1), D(2026, 9, 1), {"AAPL"})
    assert _committed_close(path) == 100.0
    assert split_storage.applied_split_ids(conn, "US") == set()

    monkeypatch.setattr(storage, "execute", real_execute)
    summary = us_daily.apply_due_splits(conn, D(2026, 8, 1), D(2026, 9, 1), {"AAPL"})
    assert summary["splits_applied"] == 1 and summary["rows_rescaled"] == 2
    assert _committed_close(path) == 25.0
    assert conn.execute(
        "SELECT rows_adjusted FROM applied_splits WHERE split_id = ?",
        (SPLIT_4FOR1["id"],)).fetchone() == (2,)


def test_a_split_already_recorded_is_not_rescaled_even_past_the_id_check(tmp_path):
    """The record is the claim: a run that read `applied_split_ids` before
    another one committed the same split must still not scale it again."""
    path, conn = _plane(tmp_path)
    assert split_storage.apply_split(conn, "US", SPLIT_4FOR1, 0.25) == 2
    assert split_storage.apply_split(conn, "US", SPLIT_4FOR1, 0.25) is None
    assert _committed_close(path) == 25.0


# --- the grouped reader ---------------------------------------------------

def test_a_full_session_parses_by_key_name(tmp_path):
    rows = [_tick(f"S{i}") for i in range(vendor.MIN_PLAUSIBLE_TICKERS + 1)]
    df = vendor.parse_grouped(_grouped(rows), D(2026, 9, 1))
    assert len(df) == len(rows)
    assert list(df.columns) == ["symbol", "open", "high", "low", "close",
                                "share_volume", "date"]
    assert df["date"].iloc[0] == D(2026, 9, 1)


def test_an_empty_result_set_is_a_closed_market_not_a_failure():
    """A weekend or a holiday answers this way, and a refresh that paged an
    operator every Saturday would be turned off inside a month."""
    assert vendor.parse_grouped(_grouped([]), D(2026, 9, 5)).empty


def test_a_short_session_is_refused_as_a_truncated_response():
    with pytest.raises(ValueError, match="under the"):
        vendor.parse_grouped(_grouped([_tick("A"), _tick("B")]), D(2026, 9, 1))


def test_a_payload_missing_the_results_key_names_what_it_saw():
    with pytest.raises(ValueError, match="no 'results'"):
        vendor.parse_grouped({"status": "OK", "count": 3}, D(2026, 9, 1))


def test_rows_missing_a_pinned_field_raise_rather_than_yielding_nan():
    rows = [{"T": f"S{i}", "o": 1, "h": 1, "l": 1, "c": 1}
            for i in range(vendor.MIN_PLAUSIBLE_TICKERS + 1)]
    with pytest.raises(ValueError, match=r"missing \['v'\]"):
        vendor.parse_grouped(_grouped(rows), D(2026, 9, 1))


# --- the window -----------------------------------------------------------

def test_only_weekdays_strictly_between_the_last_bar_and_today_are_asked_for():
    """Today is excluded because the free tier refuses a session that has not
    closed; the weekend is excluded because asking spends a request to be told
    the market was shut."""
    due = us_daily.sessions_to_fetch(D(2026, 8, 27), D(2026, 9, 2))
    assert due == [D(2026, 8, 28), D(2026, 8, 31), D(2026, 9, 1)]


def test_a_long_gap_is_capped_so_one_run_cannot_outspend_the_free_ceiling():
    due = us_daily.sessions_to_fetch(D(2026, 1, 1), D(2026, 9, 2), max_sessions=3)
    assert len(due) == 3


def test_an_empty_plane_asks_for_nothing_because_this_module_does_not_backfill():
    assert us_daily.sessions_to_fetch(None, D(2026, 9, 2)) == []


# --- the basis boundary ---------------------------------------------------

def test_a_wiki_only_symbol_is_not_written_to(tmp_path):
    """WIKI ships a total-return close and this vendor a split-adjusted one.
    Appending one session of the second onto a history of the first fabricates a
    return at the join, and a single session is not a history to replace the old
    one with — so the symbol is skipped, not corrupted and not half-replaced."""
    path, conn = _plane(tmp_path)
    market_storage.upsert_instruments(conn, pd.DataFrame(
        [{"market": "US", "symbol": "DEAD", "currency": "USD"}]))
    market_storage.upsert_market_prices(conn, pd.DataFrame([
        {"market": "US", "symbol": "DEAD", "date": "2018-03-27", "open": 9.0,
         "high": 9.0, "low": 9.0, "close": 9.0, "volume": 1.0,
         "split_adj_close": 9.0, "adj_close": 9.0,
         "source": "wiki-prices:total_return"}]))
    conn.commit()

    writable = us_daily.writable_symbols(conn)
    assert "AAPL" in writable, "the bulk-loaded history is the basis we extend"
    assert "DEAD" not in writable


def test_a_symbol_the_universe_does_not_carry_is_not_written_to(tmp_path):
    """`us_listing` and `symbol_directory` own what the US market contains; a
    price feed adding rows there would undo that ownership."""
    path, conn = _plane(tmp_path)
    market_storage.upsert_market_prices(conn, pd.DataFrame([
        {"market": "US", "symbol": "GHOST", "date": "2026-08-28", "open": 1.0,
         "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0,
         "split_adj_close": 1.0, "adj_close": 1.0,
         "source": "stooq-bulk:split_only"}]))
    conn.commit()
    assert "GHOST" not in us_daily.writable_symbols(conn)


def test_the_split_window_starts_after_the_last_stored_session(tmp_path, monkeypatch):
    """The load-time trap, pinned. A back-adjusted history already contains every
    split up to the day it was cut, so a run that asked for a fixed lookback
    would re-apply those on its very first pass and quarter a correct history —
    silently, since re-applying a ratio raises nothing. The window must begin the
    day *after* the last stored bar."""
    path, conn = _plane(tmp_path)   # history ends 2026-08-28
    seen = {}

    def fake(since, until=None, limit=1000):
        seen["since"], seen["until"] = since, until
        return []

    monkeypatch.setattr(vendor, "fetch_splits", fake)
    monkeypatch.setattr(us_daily.fx_series, "build", lambda *a, **k: pd.DataFrame())
    monkeypatch.setattr(us_daily.fx_series, "load", lambda *a, **k: pd.Series(dtype=float))
    monkeypatch.setattr(us_daily.vendor, "fetch_grouped",
                        lambda *a, **k: _grouped([]))
    conn.close()
    us_daily.run_pipeline(path, today=D(2026, 9, 2))

    assert seen["since"] == D(2026, 8, 29), (
        "asked about splits the stored basis had already absorbed")
    assert seen["until"] == D(2026, 9, 2)
