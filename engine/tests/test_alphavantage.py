"""The metered client, and the guard that stands between a 200 and the database.

Offline by construction: `requests.get` is monkeypatched, exactly as
`test_sec_downloader.py` does it. What is under test is the refusal handling and
the budget arithmetic, which is where this vendor differs from every other feed
in the repository — it answers 200 with an apology instead of erroring.
"""
from __future__ import annotations

import datetime
import sqlite3

import pytest
from b3_pipeline import storage, us_storage
from us_pipeline import alphavantage as av

DAY = datetime.date(2026, 9, 1)


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setenv("ALPHAVANTAGE_API_KEY", "TESTKEY")
    monkeypatch.delenv("ALPHAVANTAGE_RATE_PER_MIN", raising=False)
    monkeypatch.delenv("ALPHAVANTAGE_DAILY_BUDGET", raising=False)


def _db(tmp_path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(tmp_path / "av.sqlite"))
    storage.init_db(conn)
    return conn


class _Response:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        return None


def _answer(monkeypatch, body: str):
    """Make every request return `body` with HTTP 200, and record the calls."""
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(params)
        return _Response(body)

    monkeypatch.setattr(av.requests, "get", fake_get)
    return calls


def _client(conn, **kw):
    # per_min=0 disables pacing so the suite does not sleep.
    kw.setdefault("per_min", 0)
    return av.Client(conn, today=DAY, **kw)


# ── The key ───────────────────────────────────────────────────────────────────

def test_a_blank_key_is_refused_by_name(monkeypatch):
    """Compose interpolates an unset variable to "", and unlike the SEC's
    User-Agent there is no useful default to fall back to."""
    monkeypatch.setenv("ALPHAVANTAGE_API_KEY", "   ")
    with pytest.raises(av.MissingApiKey, match="ALPHAVANTAGE_API_KEY"):
        av.api_key()


# ── HTTP 200 with a refusal envelope ──────────────────────────────────────────

@pytest.mark.parametrize("body,expected", [
    ('{"Note": "Our standard API call frequency is 5 calls per minute."}',
     av.RateLimited),
    ('{"Information": "Our standard API rate limit is 25 requests per day."}',
     av.RateLimited),
    ('{"Information": "This is a premium endpoint. Subscribe to unlock it."}',
     av.PremiumOnly),
    ('{"Error Message": "Invalid API call. Please retry."}', av.BadRequest),
    ('<html><body>Forbidden</body></html>', av.AlphaVantageError),
])
def test_a_refusal_at_http_200_never_reaches_the_parser(tmp_path, monkeypatch,
                                                        body, expected):
    """The bug this prevents: `{"Note": ...}` handed to pandas parses as a
    one-column frame and lands in market_prices as a row."""
    conn = _db(tmp_path)
    _answer(monkeypatch, body)
    with pytest.raises(expected):
        _client(conn).get_csv("TIME_SERIES_DAILY", symbol="IBM")


def test_real_csv_passes_through_untouched(tmp_path, monkeypatch):
    conn = _db(tmp_path)
    body = "timestamp,open,high,low,close,volume\n2026-08-31,1,2,0.5,1.5,10\n"
    _answer(monkeypatch, body)
    assert _client(conn).get_csv("TIME_SERIES_DAILY", symbol="IBM") == body


def test_a_json_endpoint_needs_its_documented_key_present(tmp_path, monkeypatch):
    """A JSON endpoint cannot use the brace heuristic — a refusal and a payload
    are both objects — so the check has to be positive."""
    conn = _db(tmp_path)
    _answer(monkeypatch, '{"symbol": "IBM", "data": [{"effective_date": "2020-01-01"}]}')
    assert _client(conn).get_json("SPLITS", symbol="IBM")["data"]

    _answer(monkeypatch, '{"Information": "premium endpoint"}')
    with pytest.raises(av.PremiumOnly):
        _client(conn).get_json("SPLITS", symbol="IBM")


def test_the_api_key_never_reaches_an_error_message(tmp_path, monkeypatch):
    """Ingest errors are stored in `ingest_runs.error` and logged. A request URL
    carries the key as a query parameter."""
    conn = _db(tmp_path)

    def boom(url, params=None, timeout=None):
        raise av.requests.RequestException(
            "GET https://www.alphavantage.co/query?function=X&apikey=TESTKEY failed")

    monkeypatch.setattr(av.requests, "get", boom)
    with pytest.raises(av.AlphaVantageError) as excinfo:
        _client(conn).get_csv("TIME_SERIES_DAILY", symbol="IBM")
    assert "TESTKEY" not in str(excinfo.value)
    assert "apikey=***" in str(excinfo.value)


# ── The budget ────────────────────────────────────────────────────────────────

def test_the_budget_is_durable_across_clients(tmp_path, monkeypatch):
    """An in-memory counter double-spends when the nightly job and a hand-run
    ingest land on the same day. At 25 requests a day that is the difference
    between progress and none."""
    conn = _db(tmp_path)
    _answer(monkeypatch, "timestamp,open,high,low,close,volume\n")
    _client(conn, budget=3).get_csv("LISTING_STATUS")
    _client(conn, budget=3).get_csv("LISTING_STATUS")
    assert _client(conn, budget=3).spent == 2
    assert _client(conn, budget=3).remaining == 1


def test_an_exhausted_budget_stops_before_the_request_goes_out(tmp_path, monkeypatch):
    conn = _db(tmp_path)
    calls = _answer(monkeypatch, "timestamp,open,high,low,close,volume\n")
    client = _client(conn, budget=1)
    client.get_csv("LISTING_STATUS")
    with pytest.raises(av.BudgetExhausted):
        client.get_csv("LISTING_STATUS")
    assert len(calls) == 1


def test_a_reserve_keeps_headroom_for_the_other_jobs(tmp_path, monkeypatch):
    """Three jobs share one key. Without a reserve the price backfill spends the
    whole day's allowance before the listing and benchmark jobs ever run."""
    conn = _db(tmp_path)
    _answer(monkeypatch, "timestamp,open,high,low,close,volume\n")
    greedy = _client(conn, budget=10, reserve=7)
    assert greedy.remaining == 3
    for _ in range(3):
        greedy.get_csv("TIME_SERIES_DAILY", symbol="IBM")
    with pytest.raises(av.BudgetExhausted):
        greedy.get_csv("TIME_SERIES_DAILY", symbol="IBM")
    # The reserved requests are still there for a client that does not reserve.
    assert _client(conn, budget=10).remaining == 7


def test_a_paid_key_has_no_local_ceiling(tmp_path, monkeypatch):
    """budget=0 is how a premium key is configured; the same code path runs."""
    conn = _db(tmp_path)
    _answer(monkeypatch, "timestamp,open,high,low,close,volume\n")
    client = _client(conn, budget=0)
    for _ in range(50):
        client.get_csv("TIME_SERIES_DAILY", symbol="IBM")
    assert client.remaining == -1
    assert client.spent == 50


def test_the_charge_lands_even_when_the_request_fails(tmp_path, monkeypatch):
    """A timed-out attempt consumed the vendor's allowance as far as anyone here
    can tell, and a budget that undercounts is one that gets the key throttled."""
    conn = _db(tmp_path)

    def boom(url, params=None, timeout=None):
        raise av.requests.RequestException("timeout")

    monkeypatch.setattr(av.requests, "get", boom)
    with pytest.raises(av.AlphaVantageError):
        _client(conn, budget=5).get_csv("TIME_SERIES_DAILY", symbol="IBM")
    assert us_storage.calls_spent(conn, av.SOURCE, DAY) == 1


def test_the_tier_is_configuration_not_code(monkeypatch):
    monkeypatch.setenv("ALPHAVANTAGE_RATE_PER_MIN", "75")
    monkeypatch.setenv("ALPHAVANTAGE_DAILY_BUDGET", "0")
    assert av.rate_per_min() == 75.0
    assert av.daily_budget() == 0
    monkeypatch.delenv("ALPHAVANTAGE_RATE_PER_MIN")
    monkeypatch.delenv("ALPHAVANTAGE_DAILY_BUDGET")
    assert av.rate_per_min() == 5.0
    assert av.daily_budget() == 25


def test_pacing_waits_between_requests(tmp_path, monkeypatch):
    conn = _db(tmp_path)
    _answer(monkeypatch, "timestamp,open,high,low,close,volume\n")
    slept = []
    monkeypatch.setattr(av.time, "sleep", slept.append)
    client = av.Client(conn, today=DAY, budget=0, per_min=60)  # one per second
    client.get_csv("LISTING_STATUS")
    client.get_csv("LISTING_STATUS")
    assert slept and 0 < slept[-1] <= 1.0
