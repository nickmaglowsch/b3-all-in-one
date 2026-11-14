"""scripts/advise_weights.py without a Postgres plane.

`test_advise_weights.py` drives the same script end to end, but it is skipped
unless ENGINE_DATABASE_URL points at Postgres — which CI never does, so the
advisor's arithmetic ships unmeasured. The parts that decide what a user is told
to buy and sell are pure or SQLite-shaped, and that is what this covers: share
rounding, NAV, the unpriced fallbacks, and the exit code the platform reads.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from b3_pipeline import storage
from scripts import advise_weights as aw
from scripts import weights_pricing

ROOT = Path(__file__).resolve().parents[1]


def target(**weights) -> pd.Series:
    return pd.Series(weights, dtype=float)


def book(**positions) -> dict:
    """ticker=(qty, avg_cost) -> the positions payload the platform sends."""
    return {t: {"qty": q, "avg_cost": c} for t, (q, c) in positions.items()}


# --------------------------------------------------------------------------
# _latest_prices — one number per ticker out of the built plane
# --------------------------------------------------------------------------

def test_no_plane_prices_nothing_rather_than_raising():
    assert aw._latest_prices(None) == {}
    assert aw._latest_prices(pd.DataFrame()) == {}


def test_the_last_known_close_carries_forward_for_a_name_that_stopped_trading():
    raw = pd.DataFrame(
        {"PETR4": [30.0, 31.0, 32.0], "OLDX3": [10.0, None, None]},
        index=pd.to_datetime(["2025-04-30", "2025-05-31", "2025-06-30"]))
    assert aw._latest_prices(raw) == {"PETR4": 32.0, "OLDX3": 10.0}


def test_a_sleeve_with_no_share_price_is_left_out_entirely():
    """CASH_CDI and friends are NAV, not something you can buy shares of. A zero
    would make them look free and let the trade builder buy infinity of them."""
    raw = pd.DataFrame({"PETR4": [30.0], "CDI_ASSET": [0.0], "NOQUOTE3": [None]},
                       index=pd.to_datetime(["2025-06-30"]))
    assert aw._latest_prices(raw) == {"PETR4": 30.0}


# --------------------------------------------------------------------------
# _build_trades — BRL diffs to whole shares
# --------------------------------------------------------------------------

def test_nav_counts_the_book_at_market_plus_the_uninvested_cash():
    out = aw._build_trades(book(PETR4=(100, 20.0)), 5_000.0,
                           target(PETR4=1.0), {"PETR4": 30.0})
    assert out["nav"] == pytest.approx(100 * 30.0 + 5_000.0)
    assert out["positions_valued"] == {"PETR4": pytest.approx(3_000.0)}


def test_a_buy_rounds_down_so_the_order_never_costs_more_than_the_cash_behind_it():
    """10_000 of cash at 30.00 is 333.33 shares. 334 would be an order the
    account cannot pay for."""
    out = aw._build_trades({}, 10_000.0, target(PETR4=1.0), {"PETR4": 30.0})
    assert out["buys"] == [{"ticker": "PETR4", "qty": 333, "est_price": 30.0,
                            "est_brl": pytest.approx(9_990.0)}]
    assert out["sells"] == []


def test_a_sell_rounds_up_so_the_cash_it_has_to_raise_is_actually_raised():
    """Trimming to 50% of a 4_000 NAV means raising 1_000, which is 33.3 shares at
    30.00. Rounding down leaves the buy leg underfunded; rounding up overshoots by
    at most one share, which is the cheap side of the trade-off."""
    out = aw._build_trades(book(PETR4=(100, 20.0)), 1_000.0,
                           target(PETR4=0.5, VALE3=0.5),
                           {"PETR4": 30.0, "VALE3": 55.0})
    assert [(s["ticker"], s["qty"]) for s in out["sells"]] == [("PETR4", 34)]


def test_a_full_exit_sells_the_position_and_not_one_share_more():
    """The rounding-up rule is capped at what is held — an advisory that shorts
    the user is not an advisory they can execute."""
    out = aw._build_trades(book(PETR4=(7, 20.0)), 0.0,
                           target(VALE3=1.0), {"PETR4": 30.0, "VALE3": 55.0})
    assert [s["qty"] for s in out["sells"]] == [7]


def test_a_buy_too_small_for_one_share_is_dropped_instead_of_ordered_as_zero():
    out = aw._build_trades({}, 100.0, target(PETR4=1.0), {"PETR4": 30_000.0})
    assert out["buys"] == []


def test_an_unpriced_holding_is_valued_at_cost_and_named_as_untradeable():
    """Valuing it at zero would understate NAV and silently shrink every target;
    trading it at an invented price is worse. It counts, and it is listed."""
    out = aw._build_trades(book(PETR4=(100, 20.0), XPTO3=(50, 10.0)), 0.0,
                           target(PETR4=1.0), {"PETR4": 30.0})
    assert out["nav"] == pytest.approx(100 * 30.0 + 50 * 10.0)
    assert out["positions_valued"]["XPTO3"] == pytest.approx(500.0)
    assert out["unpriced_skipped"] == ["XPTO3"]
    assert all(t["ticker"] != "XPTO3" for t in out["sells"] + out["buys"])


def test_a_target_weight_of_zero_is_not_a_ticker_in_play():
    out = aw._build_trades({}, 10_000.0, target(PETR4=1.0, VALE3=0.0),
                           {"PETR4": 30.0, "VALE3": 55.0})
    assert [b["ticker"] for b in out["buys"]] == ["PETR4"]


# --------------------------------------------------------------------------
# weights_pricing.latest_plane_close — the --weights valuation, off the plane
# --------------------------------------------------------------------------

def plane(tmp_path, rows) -> str:
    """A SQLite market plane holding (ticker, date, close) rows."""
    db = tmp_path / "plane.sqlite"
    conn = sqlite3.connect(str(db))
    storage.init_db(conn)
    storage.upsert_prices(conn, pd.DataFrame(
        [{"ticker": t, "date": d, "open": c, "high": c, "low": c, "close": c,
          "volume": 1_000_000, "quotation_factor": 1} for t, d, c in rows]))
    conn.commit()
    conn.close()
    return str(db)


def test_an_empty_plane_reports_no_as_of_rather_than_a_made_up_one(tmp_path):
    db = plane(tmp_path, [])
    assert weights_pricing.latest_plane_close(db, "2005-01-01", "2030-01-01") == (None, {})


def test_each_ticker_is_valued_at_its_own_most_recent_close(tmp_path):
    db = plane(tmp_path, [
        ("PETR4", "2025-06-10", 30.0), ("PETR4", "2025-06-20", 32.0),
        ("VALE3", "2025-03-05", 55.0),      # stopped trading in March
    ])
    as_of, prices = weights_pricing.latest_plane_close(db, "2005-01-01", "2030-01-01")
    assert prices == {"PETR4": 32.0, "VALE3": 55.0}
    # as_of is the month-end label of the plane's last bucket, matching the
    # resampled frame this query replaces.
    assert as_of == pd.Timestamp("2025-06-30")


def test_an_end_date_makes_a_past_valuation_reproducible(tmp_path):
    db = plane(tmp_path, [("PETR4", "2025-06-10", 30.0), ("PETR4", "2025-06-20", 32.0)])
    as_of, prices = weights_pricing.latest_plane_close(db, "2005-01-01", "2025-06-15")
    assert prices == {"PETR4": 30.0} and as_of == pd.Timestamp("2025-06-30")


def test_naming_the_tickers_prices_them_exactly_as_the_whole_plane_does(tmp_path):
    """The narrowed probe is a speed-up, not a second answer: same as_of, same
    close per name, and a name outside the universe (odd lot) or unknown to
    the plane is simply absent, as it is from the full read."""
    db = plane(tmp_path, [
        ("PETR4", "2025-06-10", 30.0), ("PETR4", "2025-06-20", 32.0),
        ("VALE3", "2025-03-05", 55.0), ("PETR4F", "2025-06-20", 31.9),
        ("ITUB4", "2025-06-25", 40.0),
    ])
    full_as_of, full = weights_pricing.latest_plane_close(db, "2005-01-01", "2025-06-22")
    as_of, some = weights_pricing.latest_plane_close(
        db, "2005-01-01", "2025-06-22", ["VALE3", "PETR4", "PETR4F", "NOPE3"])
    assert as_of == full_as_of
    assert some == {"PETR4": 32.0, "VALE3": 55.0} == {
        t: full[t] for t in ("PETR4", "VALE3")}
    assert weights_pricing.latest_plane_close(db, "2005-01-01", "2030-01-01", [])[1] == {}


def test_odd_lots_and_options_are_not_part_of_the_tradeable_universe(tmp_path):
    """The predicate is a character-for-character copy of load_b3_data's; if it
    drifts, the advisor starts pricing names no backtest ever saw."""
    db = plane(tmp_path, [
        ("PETR4", "2025-06-20", 32.0),      # standard lot
        ("HGLG11", "2025-06-20", 150.0),    # unit/FII, also standard
        ("PETR4F", "2025-06-20", 31.9),     # fracionário
        ("PETRA300", "2025-06-20", 1.2),    # option series
    ])
    _, prices = weights_pricing.latest_plane_close(db, "2005-01-01", "2030-01-01")
    assert set(prices) == {"PETR4", "HGLG11"}


# --------------------------------------------------------------------------
# The CLI contract the platform depends on
# --------------------------------------------------------------------------

def run_cli(*args) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "scripts.advise_weights", *args],
                          cwd=ROOT, capture_output=True, text=True)


@pytest.mark.parametrize("args", [
    (),                                     # neither
    ("MomSharpe", "--weights", "{}"),       # both
])
def test_strategy_and_weights_are_mutually_exclusive(args):
    r = run_cli(*args)
    assert r.returncode == 2
    assert "exactly one" in r.stderr


def test_missing_fundamentals_gets_its_own_exit_code_and_a_parseable_reason(capsys):
    """platform_app/enginerun.py keys on exit 3 to turn this into a clean 422
    instead of a generic engine failure, so both halves are contract."""
    with pytest.raises(SystemExit) as e:
        aw._exit_needs_fundamentals("QMV")
    assert e.value.code == aw.NEEDS_FUNDAMENTALS_EXIT == 3
    err = json.loads(capsys.readouterr().err)
    assert err["error"] == "needs_fundamentals" and err["strategy"] == "QMV"


def test_an_empty_fundamentals_table_is_reported_as_empty(tmp_path):
    db = plane(tmp_path, [("PETR4", "2025-06-20", 32.0)])
    assert aw._fundamentals_empty(db) is True


def test_a_database_that_cannot_be_read_is_not_reported_as_missing_fundamentals(tmp_path):
    """Guessing 'needs fundamentals' from an unrelated DB error would send the
    user off to ingest data that is already there."""
    broken = tmp_path / "not-a-database.sqlite"
    broken.write_text("definitely not sqlite")
    assert aw._fundamentals_empty(str(broken)) is False
