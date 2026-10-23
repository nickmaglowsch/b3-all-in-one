"""A portfolio's ticker sleeves are checked against the price plane on save.

The platform shells scripts/validate_spec.py when a user saves a portfolio; for
a `ticker` sleeve that is the only thing standing between a typo and a portfolio
that silently holds CDI for its whole history (the blend reads "no price" as
"not listed yet"). These pin both halves: the unknown ticker refused by name, and
the late-listed one let through with its first date as a warning.

A tmp_path SQLite plane, so nothing here needs the real market data.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from b3_pipeline import storage
from backtests.core import markets, sleeve_tickers
from scripts.validate_spec import report, validate

ROOT = Path(__file__).resolve().parents[1]


def _insert(conn, ticker: str, days: list[str], adj: float | None = 10.0) -> None:
    for d in days:
        conn.execute(
            "INSERT INTO prices (ticker, isin_code, date, close, adj_close, volume)"
            " VALUES (?, 'UNKNOWN', ?, 10.0, ?, 1000.0)", (ticker, d, adj))


@pytest.fixture
def plane(tmp_path, monkeypatch) -> str:
    """History from 2005; DIVO11 there from the start, IVVB11 listing in 2014 and
    a ticker whose rows carry no adjusted price at all."""
    db = str(tmp_path / "plane.sqlite")
    conn = sqlite3.connect(db)
    storage.init_db(conn)
    _insert(conn, "PETR4", ["2005-01-03", "2005-01-04"])
    _insert(conn, "DIVO11", ["2005-01-20", "2020-01-02"])
    _insert(conn, "IVVB11", ["2014-05-02", "2020-01-02"])
    _insert(conn, "NOADJ11", ["2015-01-02"], adj=None)
    conn.commit()
    conn.close()
    monkeypatch.setenv("ENGINE_DATABASE_URL", db)
    return db


def _thirds(*tickers: str) -> dict:
    sleeves = [{"ticker": t, "weight": 1 / 3} for t in tickers]
    sleeves += [{"asset": "CDI", "weight": 1 - len(tickers) / 3}]
    return {"name": "U", "kind": "fixed_weight", "rebalance": "QE",
            "park_in_cdi_until_live": True, "sleeves": sleeves}


def test_plane_symbol_is_what_download_benchmark_reads():
    assert markets.plane_symbol("ivvb11.sa") == "IVVB11"
    assert markets.plane_symbol(" DIVO11 ") == "DIVO11"
    assert markets.plane_symbol("^BVSP") == "$IBOV"


def test_known_tickers_listed_from_the_start_pass_quietly(plane):
    assert sleeve_tickers.check(_thirds("DIVO11.SA")) == ([], [])


def test_a_late_listing_is_a_warning_naming_its_first_date(plane):
    errors, warnings = sleeve_tickers.check(_thirds("DIVO11.SA", "IVVB11.SA"))
    assert errors == []
    assert len(warnings) == 1
    assert "IVVB11.SA" in warnings[0] and "2014-05-02" in warnings[0]
    assert "CDI" in warnings[0]


def test_an_unknown_ticker_is_an_error_that_names_it(plane):
    errors, _ = sleeve_tickers.check(_thirds("XPTO11.SA", "DIVO11.SA"))
    assert len(errors) == 1
    assert "unknown ticker 'XPTO11.SA'" in errors[0]


def test_rows_without_an_adjusted_price_do_not_count(plane):
    """download_benchmark drops them, so to the backtest the ticker is unpriced."""
    errors, _ = sleeve_tickers.check(_thirds("NOADJ11.SA"))
    assert errors and "NOADJ11" in errors[0]


@pytest.mark.parametrize("ticker", ["^BVSP", "$CDI", "IBOV"])
def test_an_index_is_refused_as_not_tradable(plane, ticker):
    errors, warnings = sleeve_tickers.check(_thirds(ticker))
    assert len(errors) == 1 and "is an index" in errors[0] and ticker in errors[0]
    assert warnings == []


def test_an_empty_plane_says_so(tmp_path, monkeypatch):
    db = str(tmp_path / "empty.sqlite")
    conn = sqlite3.connect(db)
    storage.init_db(conn)
    conn.close()
    monkeypatch.setenv("ENGINE_DATABASE_URL", db)
    errors, _ = sleeve_tickers.check(_thirds("IVVB11.SA"))
    assert errors and "empty" in errors[0]


def test_an_unreadable_plane_refuses_without_leaking_the_driver_error(
        tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ENGINE_DATABASE_URL", str(tmp_path / "no_tables.sqlite"))
    errors, _ = sleeve_tickers.check(_thirds("IVVB11.SA"))
    assert errors == ["could not check the ticker sleeves: the B3 price plane "
                      "could not be read"]
    assert "no such table" in capsys.readouterr().err   # the detail is logged


def test_specs_without_ticker_sleeves_never_touch_the_plane(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("plane read")
    monkeypatch.setattr(sleeve_tickers, "first_prices", boom)
    blend = {"name": "B", "kind": "fixed_weight", "rebalance": "QE",
             "sleeves": [{"strategy": "MomSharpe", "weight": 0.5},
                         {"asset": "CDI", "weight": 0.5}]}
    assert report(blend)["ok"]


@pytest.mark.parametrize("sleeve", [
    {"ticker": "IVVB11.SA", "strategy": "MomSharpe", "weight": 1.0},   # two kinds
    {"weight": 1.0},                                                   # none
    {"asset": "SELIC", "weight": 1.0},                                 # not CDI
    {"ticker": "IVVB11.SA", "weight": "1"},                            # not numeric
    "IVVB11.SA",                                                       # not a dict
])
def test_malformed_sleeves_are_rejected_without_a_plane(sleeve):
    r = validate({"name": "X", "kind": "fixed_weight", "sleeves": [sleeve]})
    assert not r["ok"] and r["errors"][0].startswith("sleeve 0:")


def test_empty_sleeves_are_rejected():
    r = validate({"name": "X", "kind": "fixed_weight", "sleeves": []})
    assert not r["ok"] and "non-empty" in r["errors"][0]


def test_report_merges_plane_errors_and_warnings(plane):
    ok = report(_thirds("DIVO11.SA", "IVVB11.SA"))
    assert ok["ok"] and len(ok["warnings"]) == 1
    bad = report(_thirds("XPTO11.SA"))
    assert not bad["ok"] and "XPTO11.SA" in bad["errors"][0]


def test_cli_reports_an_unknown_ticker_by_name(plane):
    """The path the platform shells: one JSON line, exit 1, the ticker named."""
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "validate_spec.py"),
         "--spec-json", json.dumps(_thirds("XPTO11.SA", "IVVB11.SA"))],
        capture_output=True, text=True, cwd=str(ROOT),
        env={**os.environ, "ENGINE_DATABASE_URL": plane})
    assert proc.returncode == 1, proc.stderr
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["ok"] is False
    assert any("XPTO11.SA" in e for e in out["errors"])
    assert any("2014-05-02" in w for w in out["warnings"])
