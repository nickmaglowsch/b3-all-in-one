"""advise_weights --weights with ticker sleeves of every market (a live sleeve can
hold PETR4, a BDR, a renda-fixa ETF, SPY or BTCUSDT directly).

Runs on a temporary SQLite plane built by storage.init_db, which carries the
same `prices`, `external_prices` and `market_prices` tables as Postgres.
"""
from __future__ import annotations

import argparse
import json
import sqlite3

import pandas as pd
import pytest

from b3_pipeline import storage
from scripts import advise_weights as aw
from scripts import weights_pricing as wp


@pytest.fixture
def plane(tmp_path, monkeypatch) -> str:
    db = str(tmp_path / "plane.sqlite")
    conn = sqlite3.connect(db)
    storage.init_db(conn)
    conn.executemany(
        "INSERT INTO prices (ticker, isin_code, date, close, adj_close, volume)"
        " VALUES (?, 'X', ?, ?, ?, 1e6)",
        [("PETR4", "2026-10-29", 39.0, 39.0), ("PETR4", "2026-10-30", 40.0, 40.0),
         # A BDR: outside the standard-lot universe the strategies trade.
         ("AAPL34", "2026-10-29", 60.0, 60.0), ("AAPL34", "2026-10-30", 61.5, 61.5)])
    conn.executemany(
        "INSERT INTO external_prices (ticker, isin_code, date, close, volume, source)"
        " VALUES (?, 'X', ?, ?, 0, 'T')",
        [("LFTB11", "2026-10-30", 110.0), ("$IMAB", "2026-10-30", 11919.9)])
    conn.executemany(
        "INSERT INTO market_prices (market, symbol, date, close, close_brl, fx_rate, source)"
        " VALUES (?, ?, ?, ?, ?, ?, 'T')",
        [("US", "SPY", "2026-10-29", 600.0, 3240.0, 5.4),
         ("US", "SPY", "2026-10-30", 610.0, 3294.0, 5.4),
         # The newest bar had no PTAX yet: no BRL close, so the previous one counts.
         ("US", "SPY", "2026-10-31", 612.0, None, None),
         ("CRYPTO", "BTCUSDT", "2026-10-30", 100_000.0, 540_000.0, 5.4),
         # Same symbol on another market is another instrument.
         ("CRYPTO", "SPY", "2026-10-30", 1.0, 5.4, 5.4)])
    conn.commit()
    conn.close()
    monkeypatch.setenv("ENGINE_DATABASE_URL", db)
    return db


END = "2026-12-31"


def test_b3_codes_outside_the_lot_universe_are_priced_by_name(plane):
    assert wp.b3_ticker_closes(plane, ["AAPL34", "LFTB11", "XPTO34"], END) == {
        "AAPL34": 61.5, "LFTB11": 110.0}
    # A stored index is never a price, even asked for by name.
    assert wp.b3_ticker_closes(plane, ["$IMAB"], END) == {}


def test_us_and_crypto_are_priced_in_reais_from_their_own_plane(plane):
    got = wp.market_closes_brl(plane, {"SPY": "US", "BTCUSDT": "CRYPTO", "QQQ": "US"}, END)
    assert got == {"SPY": 3294.0, "BTCUSDT": 540_000.0}
    # The end date bounds it, as for B3.
    assert wp.market_closes_brl(plane, {"SPY": "US"}, "2026-10-29") == {"SPY": 3240.0}


def test_sleeve_closes_routes_each_ticker_to_its_market(plane):
    got = wp.sleeve_closes(plane, ["AAPL34", "SPY", "BTCUSDT"],
                           {"SPY": "US", "BTCUSDT": "CRYPTO"}, END)
    assert got == {"AAPL34": 61.5, "SPY": 3294.0, "BTCUSDT": 540_000.0}


def _advise(weights: dict, markets: dict, nav_cash: float, positions=None) -> dict:
    return aw._advise_from_weights(argparse.Namespace(
        weights=json.dumps(weights), markets=json.dumps(markets),
        positions=json.dumps(positions or {}), nav_cash=nav_cash))


def test_a_target_of_every_market_trades_in_brl(plane):
    out = _advise({"PETR4": 0.2, "AAPL34": 0.2, "SPY": 0.2, "BTCUSDT": 0.2, "CASH_CDI": 0.2},
                  {"SPY": "US", "BTCUSDT": "CRYPTO"}, nav_cash=100_000.0)
    trades = out["trades"]
    buys = {b["ticker"]: b for b in trades["buys"]}
    assert trades["unpriced_skipped"] == []
    assert buys["PETR4"]["qty"] == 500 and buys["PETR4"]["est_price"] == 40.0
    assert buys["AAPL34"]["qty"] == 325                     # floor(20000 / 61.5)
    assert buys["SPY"]["qty"] == 6 and buys["SPY"]["est_price"] == 3294.0
    # A coin trades in fractions: R$20k of a R$540k BTC is not zero BTC.
    assert buys["BTCUSDT"]["qty"] == pytest.approx(0.03703703)
    assert buys["BTCUSDT"]["est_brl"] <= 20_000.0
    assert "CASH_CDI" not in buys
    assert sum(b["est_brl"] for b in trades["buys"]) <= 100_000.0


def test_without_its_market_an_off_b3_ticker_is_reported_unpriced(plane):
    """Never priced as some B3 code by accident, and never dropped silently."""
    out = _advise({"SPY": 1.0}, {}, nav_cash=10_000.0)
    assert out["trades"]["unpriced_skipped"] == ["SPY"]
    assert out["trades"]["buys"] == []


def test_a_new_position_smaller_than_one_unit_is_named(plane):
    out = _advise({"SPY": 0.5, "PETR4": 0.5}, {"SPY": "US"}, nav_cash=1_000.0)
    trades = out["trades"]
    assert trades["below_one_lot"] == ["SPY"]               # R$500 < one SPY
    assert [b["ticker"] for b in trades["buys"]] == ["PETR4"]


def test_a_top_up_below_one_share_is_rounding_not_a_lost_sleeve():
    """A held name a few reais under target is the ordinary residue of whole
    shares, not a position the plan could not open."""
    out = aw._build_trades({"PETR4": {"qty": 100, "avg_cost": 30.0}}, 10.0,
                           pd.Series({"PETR4": 1.0}), {"PETR4": 40.0})
    assert out["buys"] == [] and out["below_one_lot"] == []


def test_a_fractional_sell_never_exceeds_the_holding():
    out = aw._build_trades({"BTCUSDT": {"qty": 0.05, "avg_cost": 400_000.0}}, 0.0,
                           pd.Series({"BTCUSDT": 0.5}), {"BTCUSDT": 540_000.0},
                           fractional=frozenset({"BTCUSDT"}))
    [sell] = out["sells"]
    assert sell["qty"] == pytest.approx(0.025) and sell["qty"] <= 0.05
