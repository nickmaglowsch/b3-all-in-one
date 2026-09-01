"""The coverage report: what the SEC ingest actually reached.

Offline, over a synthetic plane. This also satisfies the orphan gate in
`scripts/quality_gates.py` — a module under `engine/scripts` that nothing
imports fails the build, and a report nobody drives is a report nobody trusts.
"""
from __future__ import annotations

import sqlite3

import pandas as pd
import pytest
from b3_pipeline import market_storage, storage, us_storage

from scripts import us_coverage


def _plane(tmp_path, facts, instruments, pit=()):
    conn = sqlite3.connect(str(tmp_path / "cov.sqlite"))
    storage.init_db(conn)
    us_storage.upsert_fundamentals_pit_us(conn, pd.DataFrame(facts))
    market_storage.upsert_instruments(conn, pd.DataFrame(instruments))
    if pit:
        us_storage.upsert_ticker_pit(conn, pd.DataFrame(pit))
    return conn


def _fact(cik, ticker, accn, **metrics):
    return {"fact_id": f"{accn}:2023-12-31", "cik": cik, "ticker": ticker,
            "period_end": "2023-12-31", "filing_date": "2024-02-01",
            "accn": accn, "form": "10-K", **metrics}


def _instr(symbol, cik, **kw):
    return {"market": "US", "symbol": symbol, "currency": "USD", "cik": cik, **kw}


def test_fill_rate_is_per_metric_over_the_whole_plane(tmp_path):
    conn = _plane(tmp_path, [
        _fact("1", "AAA", "a", revenue=1.0, net_income=1.0, ebitda=1.0),
        _fact("2", "BBB", "b", revenue=2.0, net_income=2.0),
    ], [_instr("AAA", "1"), _instr("BBB", "2")])
    rates = us_coverage.measure(conn)["fill_rate"]
    assert rates["revenue"] == pytest.approx(1.0)
    assert rates["ebitda"] == pytest.approx(0.5)
    assert rates["net_debt"] == pytest.approx(0.0)


def test_the_cik_placeholders_are_counted_because_they_are_the_open_gap(tmp_path):
    """This number is the size of what is left of #38: filers whose facts are on
    the plane but cannot join any price series."""
    conn = _plane(tmp_path, [
        _fact("1", "AAA", "a", revenue=1.0),
        _fact("2", None, "b", revenue=1.0),
    ], [_instr("AAA", "1"), _instr("CIK0000000002", "2")])
    fallback = us_coverage.measure(conn)["cik_fallback"]
    assert fallback["count"] == 1
    assert fallback["pct"] == pytest.approx(0.5)


def test_an_empty_plane_reports_zeroes_rather_than_dividing_by_none(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "cov.sqlite"))
    storage.init_db(conn)
    report = us_coverage.measure(conn)
    assert report["rows"] == 0
    assert report["fill_rate"]["revenue"] == 0.0
    assert report["cik_fallback"]["pct"] == 0.0


# ── The findings that are decisions, not statistics ───────────────────────────

def test_a_metric_that_should_be_universal_and_is_not_is_called_out(tmp_path):
    """A low revenue fill rate is a missing tag in sec_parser's preference list,
    not a fact about the market — and it is invisible unless something says so."""
    conn = _plane(tmp_path, [
        _fact("1", "AAA", "a", revenue=1.0, net_income=1.0, total_assets=1.0,
              equity=1.0),
        _fact("2", "BBB", "b", net_income=1.0, total_assets=1.0, equity=1.0),
    ], [_instr("AAA", "1"), _instr("BBB", "2")])
    found = us_coverage.warnings(us_coverage.measure(conn))
    assert any("revenue" in w and "tag preference" in w for w in found)


def test_a_plane_with_no_delistings_at_all_is_called_out(tmp_path):
    """Zero here after a real backfill means the plane is dropping its dead
    somewhere upstream, and a survivorship-free backtest is impossible."""
    conn = _plane(tmp_path, [_fact("1", "AAA", "a", revenue=1.0)],
                  [_instr("AAA", "1")])
    assert any("delisting date" in w
               for w in us_coverage.warnings(us_coverage.measure(conn)))


def test_a_healthy_plane_reports_nothing_to_act_on(tmp_path):
    conn = _plane(tmp_path, [
        _fact("1", "AAA", "a", revenue=1.0, net_income=1.0, total_assets=1.0,
              equity=1.0)],
        [_instr("AAA", "1", delisting_date="2020-01-01")])
    assert us_coverage.warnings(us_coverage.measure(conn)) == []


def test_it_renders_without_blowing_up_on_an_empty_plane(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "cov.sqlite"))
    storage.init_db(conn)
    assert "fill rate by metric" in us_coverage.render(us_coverage.measure(conn))
