"""Retrying the days B3's daily file did not deliver.

The feed sits on a ~14-month rolling window, so a day that is not fetched is not
merely late — it is gone. Before this, `fetch_day` answered a network failure and
a genuinely empty Saturday with the same empty frame, so a failed day was
indistinguishable from a day with nothing in it and was only ever re-attempted
while it stayed inside the trailing heal window. A blip older than that window
rolled off B3's retention unnoticed, and the pipeline still exited 0.

Network-free: every HTTP call here is a monkeypatched stub.
"""
from __future__ import annotations

import datetime
import sqlite3

import pandas as pd
import pytest

from b3_pipeline import config, external_prices, storage

DAY = datetime.date(2026, 7, 24)          # a Friday
SATURDAY = datetime.date(2026, 7, 25)

def _file_for(day: datetime.date = DAY) -> bytes:
    """One real LFTB11 row, stamped with `day` — B3's file is keyed by RptDt, so
    a fixed date here would store every fetch under the same session."""
    return (
        "Status do Arquivo: Final\n"
        "RptDt;TckrSymb;ISIN;SgmtNm;MinPric;MaxPric;TradAvrgPric;LastPric;"
        "OscnPctg;AdjstdQt;AdjstdQtTax;RefPric;TradQty;FinInstrmQty;NtlFinVol\n"
        f"{day};LFTB11;BRLFTBCTF002;FORWARD;123,96;124,15;124,05;124,15;"
        "0,19;0;0;0;1234;223381;27731000,00\n"
    ).encode("latin-1")


_FILE = _file_for()


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.execute("PRAGMA foreign_keys = OFF")
    storage.init_db(c)
    yield c
    c.close()


class _Resp:
    def __init__(self, *, json_body=None, content=b"", exc=None, status_code=200):
        self._json, self.content, self._exc = json_body, content, exc
        self.status_code = status_code

    def raise_for_status(self):
        if self._exc:
            raise self._exc

    def json(self):
        return self._json


def _stub_b3(monkeypatch, *, token="tok", body=_FILE, exc=None):
    """Both legs of B3's two-step download, in one stub."""
    def fake_get(url, timeout=None):
        if exc is not None:
            raise exc
        if "requestname" in url:
            return _Resp(json_body={"redirectUrl": f"~/download?token={token}"})
        return _Resp(content=body)
    monkeypatch.setattr(external_prices.requests, "get", fake_get)


# ── fetch_day: a failure is not an empty day ─────────────────────────────────

def test_a_network_error_raises_rather_than_reading_as_an_empty_day(monkeypatch):
    """The bug this whole change exists for.

    Swallowed into an empty frame, a transient failure looks exactly like a
    holiday, so nothing downstream can know a retry is owed.
    """
    import requests as _requests
    _stub_b3(monkeypatch, exc=_requests.ConnectionError("boom"))

    with pytest.raises(external_prices.FetchFailed):
        external_prices.fetch_day(DAY)


def test_a_missing_token_raises(monkeypatch):
    """A 200 with no token is a broken answer, not an answer of 'nothing'."""
    _stub_b3(monkeypatch, token="")

    with pytest.raises(external_prices.FetchFailed):
        external_prices.fetch_day(DAY)


def test_an_empty_body_is_an_empty_day_not_a_failure(monkeypatch):
    """Dates past B3's retention answer 200 with a token and a 0-byte file.

    Verified live 2026-07-28: 2024-01-02 still hands out a token. These must stay
    silent — raising would queue a retry that can never succeed.
    """
    _stub_b3(monkeypatch, body=b"")

    assert external_prices.fetch_day(SATURDAY).empty


def test_a_400_on_the_token_leg_is_a_non_session_day_not_a_failure(monkeypatch):
    """B3 answers 400, not 200-empty, for a date it never had a file for.

    Verified live 2026-07-28: 2026-07-26 (Sunday), 2026-01-01, 2026-04-21 and
    2026-05-01 (all weekday holidays) return 400, while 2026-07-24 and
    2026-07-27 return a token. Weekends are filtered out before we ever ask, but
    holidays are weekdays — so reading a 400 as a failure would queue ~10 corpses
    a year, each retried on every daily run until it aged out, and print a
    failure warning on a run where nothing was wrong.
    """
    import requests as _requests
    resp = _Resp(exc=_requests.HTTPError("400 Client Error: Bad Request"))
    resp.status_code = 400
    monkeypatch.setattr(external_prices.requests, "get",
                        lambda url, timeout=None: resp)

    assert external_prices.fetch_day(datetime.date(2026, 5, 1)).empty


def test_a_500_on_the_token_leg_is_still_a_failure(monkeypatch):
    """The other side of that line: B3 being broken is not B3 being closed."""
    import requests as _requests
    resp = _Resp(exc=_requests.HTTPError("500 Server Error"))
    resp.status_code = 500
    monkeypatch.setattr(external_prices.requests, "get",
                        lambda url, timeout=None: resp)

    with pytest.raises(external_prices.FetchFailed):
        external_prices.fetch_day(DAY)


def test_a_good_day_still_parses(monkeypatch):
    _stub_b3(monkeypatch)

    df = external_prices.fetch_day(DAY)
    assert list(df["ticker"]) == ["LFTB11"]
    assert df.iloc[0]["close"] == 124.15


# ── backfill: one bad day must not cost the others ───────────────────────────

def test_backfill_keeps_going_past_a_failing_day_and_reports_it(monkeypatch):
    good, bad = DAY, DAY - datetime.timedelta(days=1)

    def fake_fetch(day, tickers=None):
        if day == bad:
            raise external_prices.FetchFailed("503")
        return external_prices.parse_file(_FILE, config.RENDA_FIXA_ETFS)
    monkeypatch.setattr(external_prices, "fetch_day", fake_fetch)

    result = external_prices.backfill([bad, good])

    assert not result.rows.empty          # the good day survived the bad one
    assert set(result.failed) == {bad}
    assert "503" in result.failed[bad]


def test_backfill_of_only_empty_days_reports_no_failures(monkeypatch):
    monkeypatch.setattr(external_prices, "fetch_day",
                        lambda day, tickers=None: pd.DataFrame())

    result = external_prices.backfill([SATURDAY])

    assert result.rows.empty and result.failed == {}


# ── the failure ledger ───────────────────────────────────────────────────────

def test_a_recorded_failure_comes_back_as_pending(conn):
    storage.record_external_failure(conn, external_prices.SOURCE, DAY, "503")

    assert storage.pending_external_failures(conn, external_prices.SOURCE) == {DAY}


def test_clearing_a_failure_removes_it(conn):
    storage.record_external_failure(conn, external_prices.SOURCE, DAY, "503")
    storage.clear_external_failure(conn, external_prices.SOURCE, DAY)

    assert storage.pending_external_failures(conn, external_prices.SOURCE) == set()


def test_repeated_failures_count_attempts_rather_than_duplicating(conn):
    for _ in range(3):
        storage.record_external_failure(conn, external_prices.SOURCE, DAY, "503")

    rows = conn.execute(
        "SELECT retry_count FROM external_price_failures WHERE date = ?",
        (DAY.isoformat(),)).fetchall()
    assert len(rows) == 1 and rows[0][0] == 2   # 3 attempts = first + 2 retries


def test_the_ledger_is_scoped_by_source(conn):
    """Same trap as stored_external_dates: `external_prices` has three writers,
    and one feed's outage must not enter another's retry queue."""
    storage.record_external_failure(conn, external_prices.SOURCE, DAY, "503")

    assert storage.pending_external_failures(conn, "SOME_OTHER_FEED") == set()


def test_a_rebuild_does_not_drop_the_ledger(conn):
    """`external_prices` is exempt from the rebuild drop because its rows cannot
    be re-fetched past retention. A pending retry is a claim on exactly those
    rows, so dropping it loses the same data one step earlier."""
    storage.record_external_failure(conn, external_prices.SOURCE, DAY, "503")

    storage.init_db(conn, rebuild=True)

    assert storage.pending_external_failures(conn, external_prices.SOURCE) == {DAY}


# ── what the daily run decides to fetch ──────────────────────────────────────

def _decide(today, **kw):
    kw.setdefault("stored", set())
    kw.setdefault("pending", set())
    kw.setdefault("start", datetime.date(2025, 6, 1))
    kw.setdefault("heal_days", 45)
    kw.setdefault("retention_days", 400)
    return external_prices.days_to_fetch(today, **kw)


def test_a_stale_failure_is_retried_even_though_the_heal_window_moved_past_it():
    """The point of the ledger.

    Day 200 sits far outside the 45-day heal window, so the trailing-window scan
    will never look at it again — but B3 still has it for another ~200 days.
    """
    today = datetime.date(2026, 7, 24)
    stale = today - datetime.timedelta(days=200)

    fetch, abandoned = _decide(today, pending={stale})

    assert stale in fetch and not abandoned


def test_a_failure_past_b3s_retention_is_abandoned_not_retried_forever():
    """The give-up rule.

    Without it the ledger grows a permanent tail of days B3 can no longer serve,
    and every daily run spends two round trips per corpse. Abandoning is the
    honest end state: the history is genuinely gone.
    """
    today = datetime.date(2026, 7, 24)
    lost = today - datetime.timedelta(days=500)

    fetch, abandoned = _decide(today, pending={lost})

    assert lost in abandoned and lost not in fetch


def test_a_pending_day_that_later_arrived_is_neither_fetched_nor_abandoned():
    """Another writer, a manual run, or a re-run inside the heal window can fill
    a day the ledger still lists. That is resolved, not lost."""
    today = datetime.date(2026, 7, 24)
    day = today - datetime.timedelta(days=200)

    fetch, abandoned = _decide(today, pending={day}, stored={day})

    assert day not in fetch and day not in abandoned


def test_days_already_stored_are_not_refetched():
    today = datetime.date(2026, 7, 24)
    yesterday = datetime.date(2026, 7, 23)

    fetch, _ = _decide(today, stored={yesterday})

    assert yesterday not in fetch and today in fetch


def test_weekends_are_never_fetched():
    """~100 Saturdays and Sundays a year, two round trips each, to be told
    nothing. The heal window rescans them on every single run."""
    fetch, _ = _decide(datetime.date(2026, 7, 26))   # a Sunday

    assert all(d.weekday() < 5 for d in fetch)


def test_nothing_before_the_configured_start_is_fetched():
    start = datetime.date(2025, 6, 1)
    fetch, _ = _decide(datetime.date(2025, 6, 3), start=start)

    assert min(fetch) >= start


# ── the loop, end to end ─────────────────────────────────────────────────────

def test_a_day_lost_to_an_outage_is_recovered_by_a_later_run(conn, monkeypatch):
    """The whole point, exercised through real storage and the real policy.

    Run 1 is offline and loses a day. 100 days pass, so the heal window is long
    past it. Run 2 must still fetch it, store it, and clear the debt.
    """
    source = external_prices.SOURCE
    outage_day = datetime.date(2026, 3, 20)          # a Friday
    start = datetime.date(2025, 6, 1)

    def run(today, *, offline):
        stored = storage.stored_external_dates(conn, start, source)
        pending = storage.pending_external_failures(conn, source)
        fetch, abandoned = external_prices.days_to_fetch(
            today, stored=stored, pending=pending, start=start,
            heal_days=45, retention_days=400)

        def fake_fetch(day, tickers=None):
            if offline:
                raise external_prices.FetchFailed("connection refused")
            if day != outage_day:
                return pd.DataFrame()                # holiday-ish: nothing owed
            return external_prices.parse_file(
                _file_for(day), config.RENDA_FIXA_ETFS)
        monkeypatch.setattr(external_prices, "fetch_day", fake_fetch)

        external_prices.store_result(conn, source, external_prices.backfill(fetch), pending)
        return fetch, abandoned

    # Run 1: B3 unreachable. The day is owed, and recorded as owed.
    run(outage_day, offline=True)
    assert outage_day in storage.pending_external_failures(conn, source)
    assert storage.stored_external_dates(conn, start, source) == set()

    # Run 2, 100 days later — far outside the 45-day heal window, which is
    # exactly the gap the ledger exists to cover.
    later = outage_day + datetime.timedelta(days=100)
    fetch, _ = run(later, offline=False)

    assert outage_day in fetch, "the ledger did not put the lost day back"
    assert outage_day in storage.stored_external_dates(conn, start, source)
    assert storage.pending_external_failures(conn, source) == set(), \
        "the debt should be cleared once paid"


def test_a_run_that_dies_writing_rows_has_already_recorded_what_failed(conn, monkeypatch):
    """Failures go on the books before the rows do. Once rows are stored the next
    run is no longer a first run, its window shrinks back to the heal window, and
    a failed day older than that lives only in the ledger: a run killed between
    the two writes used to lose it. And a debt is cleared only after the rows
    that pay it are written, so the same crash cannot forget a paid-for day."""
    source = external_prices.SOURCE
    paid, broken = DAY, DAY - datetime.timedelta(days=1)
    storage.record_external_failure(conn, source, paid, "earlier outage")
    rows = external_prices.parse_file(_file_for(paid), config.RENDA_FIXA_ETFS)
    result = external_prices.BackfillResult(rows, {broken: "timeout"},
                                            frozenset({paid, broken}))

    def killed(*_a, **_k):
        raise RuntimeError("killed mid-write")
    monkeypatch.setattr(storage, "upsert_external_prices", killed)
    with pytest.raises(RuntimeError):
        external_prices.store_result(conn, source, result, {paid})
    assert storage.pending_external_failures(conn, source) == {paid, broken}
