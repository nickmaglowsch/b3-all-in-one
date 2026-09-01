"""The parsers, driven from real vendor captures when there are any.

Skips when a file is absent, which is the normal state of a fresh checkout —
see `tests/fixtures/us/README.md`. The point is that the day someone runs
`scripts/capture_us_fixtures.py` and commits what it wrote, these start checking
the documented shape against the real one with no further work, and a vendor
change breaks a test instead of a production run.

`test_av_parsers.py` is the synthetic half and always runs.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from us_pipeline import av_parsers as p

from scripts import capture_us_fixtures

FIXTURES = Path(__file__).parent / "fixtures" / "us"


def _text(name: str) -> str:
    path = FIXTURES / name
    if not path.exists():
        pytest.skip(f"{name} not captured — run scripts/capture_us_fixtures.py "
                    "on a machine with egress and a key")
    return path.read_text()


def _json(name: str) -> dict:
    return json.loads(_text(name))


# ── The shapes ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", ["listing_status_active.csv",
                                  "listing_status_delisted.csv"])
def test_the_real_listing_file_parses(name):
    out = p.parse_listing_status(_text(name))
    assert not out.empty
    assert out["symbol"].notna().all()
    assert set(out["kind"]) <= {"EQUITY", "ETF"}


def test_the_real_delisted_file_carries_delisting_dates():
    """If these came back empty the backward universe would be listing dates
    only — which is not what closes the survivorship hole."""
    out = p.parse_listing_status(_text("listing_status_delisted.csv"))
    assert out["delisting_date"].notna().any()


@pytest.mark.parametrize("name", ["time_series_daily_IBM.csv",
                                  "time_series_daily_SPY.csv"])
def test_the_real_daily_file_parses(name):
    out = p.parse_time_series_daily(_text(name), "IBM")
    assert not out.empty
    assert out["date"].is_monotonic_increasing
    assert (out["close"] > 0).all()
    assert (out["high"] >= out["low"]).all()


def test_a_delisted_symbol_either_has_history_or_says_it_does_not():
    """The question the free path turns on. A capture that exists and parses
    means the plane can price its dead; the README says what it means if the
    file never appears."""
    out = p.parse_time_series_daily(_text("time_series_daily_DELISTED.csv"),
                                    "DELISTED")
    assert not out.empty, (
        "the vendor answered for a delisted symbol but with no rows — US prices "
        "are survivorship-biased even though the universe is not, and that has "
        "to be said in /market/status rather than discovered by a user")


@pytest.mark.parametrize("name", ["splits_AAPL.json", "splits_SPY.json"])
def test_the_real_splits_payload_parses(name):
    out = p.parse_splits(_json(name), "AAPL")
    assert (out["ratio"] > 0).all()


def test_apples_2020_split_comes_back_as_a_quarter():
    """The orientation check. The vendor reports new-over-old; this repository
    uses old-over-new, and getting it backwards turns a 4:1 into a +300% day
    that no glitch filter would catch."""
    out = p.parse_splits(_json("splits_AAPL.json"), "AAPL")
    aug2020 = out[out["ex_date"].astype(str) == "2020-08-31"]
    if aug2020.empty:
        pytest.skip("the captured window does not reach 2020-08-31")
    assert aug2020["ratio"].iloc[0] == pytest.approx(0.25)


@pytest.mark.parametrize("name", ["dividends_AAPL.json", "dividends_SPY.json"])
def test_the_real_dividends_payload_parses(name):
    out = p.parse_dividends(_json(name), "AAPL")
    assert (out["amount"] > 0).all()
    assert out["ex_date"].notna().all()


# ── The refusal envelope, which is the one thing documentation could not settle ─

def test_the_captured_refusal_is_classified_the_way_the_client_assumes():
    """`_classify` treats "Error Message" as permanent and "Note"/"Information"
    as retryable. If a *bad key* arrives as "Information" the client retries it
    forever, spending a budget it will never get anything for."""
    text = _text("refusal.txt")
    assert text.split("\n")[0] in {"RateLimited", "PremiumOnly", "BadRequest",
                                   "AlphaVantageError"}, (
        "the captured refusal did not classify into a known exception — "
        "us_pipeline/alphavantage._classify needs updating against it")


# ── The capture script itself ─────────────────────────────────────────────────

def test_the_capture_script_covers_every_shape_a_parser_reads():
    """Also what keeps `scripts/quality_gates.py::orphans` happy: a module under
    engine/scripts that nothing imports fails the build."""
    names = {n for n, *_ in capture_us_fixtures.CSV_TARGETS}
    names |= {n for n, *_ in capture_us_fixtures.JSON_TARGETS}
    assert "listing_status_delisted.csv" in names
    assert "time_series_daily_DELISTED.csv" in names
    assert any(n.startswith("splits_") for n in names)
    assert any(n.startswith("dividends_") for n in names)


def test_the_capture_script_truncates_to_a_reviewable_size():
    """Fixtures pin shape, not content, and a million-row file is not reviewable
    in a diff."""
    body = "h\n" + "\n".join(str(i) for i in range(1000))
    assert len(capture_us_fixtures._truncate(body, 10).splitlines()) == 11
