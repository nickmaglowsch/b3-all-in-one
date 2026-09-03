"""The US price ingest: turnover, adjustment state, and the 25-a-day scheduler.

Offline. `alphavantage.Client` is replaced by a stub that answers from a dict of
canned bodies and counts requests, so what is under test is the arithmetic and
the place-keeping — the two things that decide whether a free-tier backfill
adds up over months or starts from scratch every night.
"""
from __future__ import annotations

import datetime
import sqlite3

import pandas as pd
import pytest
from b3_pipeline import market_storage, storage, us_storage
from us_pipeline import us_adjust, us_prices
from us_pipeline.alphavantage import (
    AlphaVantageError,
    BudgetExhausted,
    PremiumOnly,
)

D = datetime.date
TODAY = D(2026, 9, 1)

BARS = ("timestamp,open,high,low,close,volume\n"
        "2026-08-31,100,101,99,100.0,1000\n"
        "2026-08-28,98,99,97,98.0,900\n")


class _Stub:
    """Answers canned bodies, counts requests, and can run out of budget."""

    def __init__(self, csv=BARS, splits=None, dividends=None, budget=-1,
                 premium=(), hard_stop=None):
        self.csv, self.splits, self.dividends = csv, splits or [], dividends or []
        self.budget, self.premium, self.calls = budget, set(premium), []
        # What the shared, durable counter allows *now*, which can be less than
        # what it allowed when the plan was drawn — another job on the same key
        # spends from the same row.
        self.hard_stop = budget if hard_stop is None else hard_stop

    @property
    def spent(self):
        return len(self.calls)

    @property
    def remaining(self):
        return -1 if self.budget < 0 else max(0, self.budget - len(self.calls))

    def _charge(self, name):
        self.calls.append(name)
        if self.hard_stop >= 0 and len(self.calls) > self.hard_stop:
            raise BudgetExhausted("spent")
        if name in self.premium:
            raise PremiumOnly(f"{name} is premium")

    def get_csv(self, function, **params):
        self._charge(function)
        return self.csv

    def get_json(self, function, *, expect="data", **params):
        self._charge(function)
        return {"data": self.splits if function == "SPLITS" else self.dividends}


def _db(tmp_path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(tmp_path / "prices.sqlite"))
    storage.init_db(conn)
    return conn


def _rates(value=5.5):
    return pd.Series([value], index=pd.to_datetime([D(2020, 1, 1)]))


def _universe(conn, *symbols, cik=None):
    market_storage.upsert_instruments(conn, pd.DataFrame([
        {"market": "US", "symbol": s, "currency": "USD", "cik": cik}
        for s in symbols]))


def _prices(conn, symbol):
    return storage.read_sql(
        "SELECT * FROM market_prices WHERE market='US' AND symbol = ?"
        " ORDER BY date", conn, params=(symbol,))


# ── Turnover, not share count ─────────────────────────────────────────────────

def test_volume_is_stored_as_turnover_in_the_quote_currency(tmp_path):
    """`min_adtv` filters on this column. Fed a share count it keeps a penny
    stock trading a billion shares and drops Berkshire."""
    conn = _db(tmp_path)
    us_prices.ingest_symbol(conn, _Stub(), "IBM", _rates(),
                            want_prices=True, want_actions=True, today=TODAY)
    row = _prices(conn, "IBM").iloc[-1]
    assert row["volume"] == pytest.approx(1000 * 100.0)


def test_the_vendors_share_count_stays_recoverable(tmp_path):
    """Never destroy a source number to store a derived one: the share count is
    exactly volume / close, because that is exactly how it was made."""
    conn = _db(tmp_path)
    us_prices.ingest_symbol(conn, _Stub(), "IBM", _rates(),
                            want_prices=True, want_actions=True, today=TODAY)
    row = _prices(conn, "IBM").iloc[-1]
    assert row["volume"] / row["close"] == pytest.approx(1000)


def test_bars_are_projected_into_brl_with_the_rate_that_made_them(tmp_path):
    conn = _db(tmp_path)
    us_prices.ingest_symbol(conn, _Stub(), "IBM", _rates(5.5),
                            want_prices=True, want_actions=True, today=TODAY)
    row = _prices(conn, "IBM").iloc[-1]
    assert row["fx_rate"] == pytest.approx(5.5)
    assert row["close_brl"] == pytest.approx(550.0)


def test_a_day_ptax_cannot_cover_keeps_null_brl_and_a_null_rate(tmp_path):
    """The honest outcome, and visible as `unconverted_brl` in /market/status.
    Pricing a US book in dollars silently is worse than reporting it unpriced."""
    conn = _db(tmp_path)
    late = pd.Series([5.5], index=pd.to_datetime([D(2030, 1, 1)]))
    us_prices.ingest_symbol(conn, _Stub(), "IBM", late,
                            want_prices=True, want_actions=True, today=TODAY)
    row = _prices(conn, "IBM").iloc[-1]
    assert pd.isna(row["close_brl"]) and pd.isna(row["fx_rate"])


# ── Adjustment state is a receipt ─────────────────────────────────────────────

def test_a_symbol_whose_actions_answered_is_recorded_as_total_return(tmp_path):
    conn = _db(tmp_path)
    cursor = us_prices.ingest_symbol(conn, _Stub(), "IBM", _rates(),
                                     want_prices=True, want_actions=True,
                                     today=TODAY)
    assert cursor["adjust_state"] == us_adjust.TOTAL_RETURN
    assert _prices(conn, "IBM")["source"].iloc[0].endswith("total_return")


def test_a_premium_dividends_endpoint_downgrades_to_split_only(tmp_path):
    """Not a failure — a fact about coverage, recorded rather than retried."""
    conn = _db(tmp_path)
    cursor = us_prices.ingest_symbol(
        conn, _Stub(premium=("DIVIDENDS",)), "IBM", _rates(),
        want_prices=True, want_actions=True, today=TODAY)
    assert cursor["adjust_state"] == us_adjust.SPLIT_ONLY


def test_both_endpoints_refused_means_unadjusted_and_says_so(tmp_path):
    conn = _db(tmp_path)
    cursor = us_prices.ingest_symbol(
        conn, _Stub(premium=("SPLITS", "DIVIDENDS")), "IBM", _rates(),
        want_prices=True, want_actions=True, today=TODAY)
    assert cursor["adjust_state"] == us_adjust.UNADJUSTED
    assert _prices(conn, "IBM")["source"].iloc[0].endswith("unadjusted")


def test_a_price_only_refresh_rebuilds_the_same_adjustment(tmp_path):
    """The bug this prevents: a cheap daily refresh rewriting an adjusted series
    as a raw one, because the events were not re-fetched. They are stored."""
    conn = _db(tmp_path)
    stub = _Stub(splits=[{"effective_date": "2026-08-31", "split_factor": "4.0"}])
    first = us_prices.ingest_symbol(conn, stub, "IBM", _rates(),
                                    want_prices=True, want_actions=True,
                                    today=TODAY)
    adjusted = list(_prices(conn, "IBM")["split_adj_close"])

    us_prices.ingest_symbol(conn, _Stub(), "IBM", _rates(), want_prices=True,
                            want_actions=False, today=TODAY,
                            prior_state=first["adjust_state"])
    assert list(_prices(conn, "IBM")["split_adj_close"]) == adjusted
    assert adjusted[0] == pytest.approx(98.0 * 0.25)


def test_a_price_only_refresh_of_an_unadjusted_symbol_stays_unadjusted(tmp_path):
    conn = _db(tmp_path)
    cursor = us_prices.ingest_symbol(
        conn, _Stub(premium=("SPLITS", "DIVIDENDS")), "IBM", _rates(),
        want_prices=True, want_actions=True, today=TODAY)
    again = us_prices.ingest_symbol(conn, _Stub(), "IBM", _rates(),
                                    want_prices=True, want_actions=False,
                                    today=TODAY,
                                    prior_state=cursor["adjust_state"])
    assert again["adjust_state"] == us_adjust.UNADJUSTED


# ── The scheduler ─────────────────────────────────────────────────────────────

def _cursor(**rows):
    return pd.DataFrame([{"symbol": s, **v} for s, v in rows.items()]) \
        if rows else pd.DataFrame(columns=us_storage.CURSOR_COLUMNS)


def test_a_symbol_with_no_bars_costs_three_requests(tmp_path):
    plan = us_prices.select_work(["A", "B"], _cursor(), budget=3, today=TODAY)
    assert plan == [("A", True, True)]


def test_the_budget_is_never_overspent():
    plan = us_prices.select_work(list("ABCDEFGH"), _cursor(), budget=7,
                                 today=TODAY)
    cost = sum(1 * p[1] + 2 * p[2] for p in plan)
    assert cost <= 7


def test_leftover_budget_still_buys_bars_when_actions_do_not_fit():
    """One request left is a day of bars for one more symbol. Spending it beats
    banking it — the budget does not roll over."""
    plan = us_prices.select_work(list("ABC"), _cursor(), budget=4, today=TODAY)
    assert plan == [("A", True, True), ("B", True, False)]


def test_retries_are_drained_before_new_work():
    """A transient failure must not send a symbol to the back of an
    eleven-thousand-symbol queue for the rest of the year."""
    cursor = _cursor(Z={"status": "retry", "last_bar": None,
                        "actions_fetched_at": None})
    plan = us_prices.select_work(["A", "B", "Z"], cursor, budget=3, today=TODAY)
    assert plan[0][0] == "Z"


def test_a_symbol_current_through_today_is_left_alone():
    cursor = _cursor(A={"status": "ok", "last_bar": str(TODAY),
                        "actions_fetched_at": str(TODAY)})
    assert us_prices.select_work(["A"], cursor, budget=10, today=TODAY) == []


def test_stale_actions_are_refreshed_without_re_fetching_the_bars():
    cursor = _cursor(A={"status": "ok", "last_bar": str(TODAY),
                        "actions_fetched_at": "2020-01-01"})
    assert us_prices.select_work(["A"], cursor, budget=10, today=TODAY) == \
        [("A", False, True)]


def test_an_unlimited_budget_plans_the_whole_universe():
    """A paid key runs this identical code; only the ceiling changes."""
    plan = us_prices.select_work(list("ABCDEFGH"), _cursor(), budget=-1,
                                 today=TODAY)
    assert len(plan) == 8


# ── Resumability ──────────────────────────────────────────────────────────────

def test_a_run_that_dies_halfway_keeps_the_symbols_it_finished(tmp_path,
                                                               monkeypatch):
    """The property the whole free-tier design rests on. The cursor is written
    inside the loop, before the next request goes out, so tomorrow's run starts
    where this one stopped instead of at the top of the alphabet."""
    path = str(tmp_path / "prices.sqlite")
    conn = sqlite3.connect(path)
    storage.init_db(conn)
    _universe(conn, "AAA", "BBB", "CCC")
    conn.commit()

    # The plan is drawn for four requests, but the shared counter runs out
    # after three: another job on the same key spent while this one ran.
    monkeypatch.setattr(us_prices, "Client",
                        lambda c, **kw: _Stub(budget=4, hard_stop=3))
    monkeypatch.setattr(us_prices.fx_series, "build", lambda d: pd.DataFrame())
    monkeypatch.setattr(us_prices.fx_series, "load", lambda c: _rates())

    out = us_prices.run_pipeline(db_path=path, today=TODAY)
    assert out["budget_exhausted"] is True

    done = us_storage.load_price_cursor(sqlite3.connect(path))
    assert set(done["symbol"]) == {"AAA"}
    assert done["last_bar"].iloc[0] == "2026-08-31"
    assert done["adjust_state"].iloc[0] == us_adjust.TOTAL_RETURN


def test_a_second_run_starts_where_the_first_one_stopped(tmp_path, monkeypatch):
    path = str(tmp_path / "prices.sqlite")
    conn = sqlite3.connect(path)
    storage.init_db(conn)
    _universe(conn, "AAA", "BBB", "CCC")
    conn.commit()
    monkeypatch.setattr(us_prices.fx_series, "build", lambda d: pd.DataFrame())
    monkeypatch.setattr(us_prices.fx_series, "load", lambda c: _rates())

    monkeypatch.setattr(us_prices, "Client", lambda c, **kw: _Stub(budget=3))
    us_prices.run_pipeline(db_path=path, today=TODAY)
    monkeypatch.setattr(us_prices, "Client", lambda c, **kw: _Stub(budget=3))
    us_prices.run_pipeline(db_path=path, today=TODAY)

    done = us_storage.load_price_cursor(sqlite3.connect(path))
    assert set(done["symbol"]) == {"AAA", "BBB"}


def test_a_failing_symbol_is_queued_for_retry_rather_than_lost(tmp_path,
                                                               monkeypatch):
    path = str(tmp_path / "prices.sqlite")
    conn = sqlite3.connect(path)
    storage.init_db(conn)
    _universe(conn, "AAA")
    conn.commit()

    class _Broken(_Stub):
        def get_csv(self, function, **params):
            self._charge(function)
            raise AlphaVantageError("the vendor did not answer")

    monkeypatch.setattr(us_prices, "Client", lambda c, **kw: _Broken())
    monkeypatch.setattr(us_prices.fx_series, "build", lambda d: pd.DataFrame())
    monkeypatch.setattr(us_prices.fx_series, "load", lambda c: _rates())

    out = us_prices.run_pipeline(db_path=path, today=TODAY)
    assert out["failed_count"] == 1
    row = us_storage.load_price_cursor(sqlite3.connect(path)).iloc[0]
    assert row["status"] == "retry"
    assert "did not answer" in row["error"]


def test_symbols_with_fundamentals_are_priced_first(tmp_path):
    """A backtest is useless with eight random tickers and useful with the two
    hundred largest; the ones the SEC ingest already covers are the ones a
    fundamental strategy can actually select."""
    conn = _db(tmp_path)
    _universe(conn, "ZZZZ")
    _universe(conn, "AAPL", cik="0000320193")
    assert us_prices._universe(conn)[0] == "AAPL"


def test_placeholders_and_test_issues_are_not_priced(tmp_path):
    conn = _db(tmp_path)
    _universe(conn, "REAL")
    market_storage.upsert_instruments(conn, pd.DataFrame([
        {"market": "US", "symbol": "CIK0000806085", "currency": "USD"},
        {"market": "US", "symbol": "TEST", "currency": "USD", "is_test_issue": 1}]))
    assert us_prices._universe(conn) == ["REAL"]


# ── a vendor that has stopped answering must not read as a green run ─────────

def test_a_run_where_every_symbol_failed_is_not_a_success():
    """The failure mode that hid a dead vendor for six months.

    `us_backfill` walked Stooq's quote endpoint after it began demanding a key
    nobody can obtain, 404ed on every symbol, and reported "0 rows over 0
    symbols, 20 failed" with exit code 0. Procrastinate recorded success. The US
    plane stopped advancing in March and nothing said so until September."""
    assert us_prices.total_failure({"planned": 20, "failed_count": 20})


def test_a_run_where_some_symbols_failed_is_still_a_success():
    """A walk over eleven thousand names always has a few that 404 for their own
    reasons. A job that pages on those is a job that gets muted."""
    assert not us_prices.total_failure({"planned": 20, "failed_count": 3})


def test_an_exhausted_budget_is_not_a_failure():
    """The day's allowance running out is what the day's allowance does."""
    assert not us_prices.total_failure(
        {"planned": 20, "failed_count": 0, "budget_exhausted": True})


def test_a_run_with_nothing_to_do_is_not_a_failure():
    """An empty plan means the plane is current, not that the vendor is gone."""
    assert not us_prices.total_failure({"planned": 0, "failed_count": 0})
