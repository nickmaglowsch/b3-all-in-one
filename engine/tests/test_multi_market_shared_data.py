"""build_shared_data across markets, on one synthetic plane holding all three.

The load-bearing assertions here are about isolation and calibration: a market's
frames must contain only its own instruments, and the B3-tuned machinery must not
have been applied to the others.
"""
from __future__ import annotations

import sqlite3

import pandas as pd
import pytest

from b3_pipeline import market_storage, storage
from backtests.core import markets
from backtests.core.shared_data import build_shared_data

DATES = pd.date_range("2020-01-01", periods=90, freq="D")


def _b3_rows(conn):
    for d in DATES:
        for t, px in (("PETR4", 20.0), ("VALE3", 60.0)):
            conn.execute(
                "INSERT INTO prices (ticker, isin_code, date, open, high, low,"
                " close, adj_close, split_adj_high, split_adj_low,"
                " split_adj_close, volume) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (t, "UNKNOWN", d.date().isoformat(), px, px, px, px, px, px, px,
                 px, 5e6))
    conn.commit()


def _market_rows(conn, market, symbols, close=100.0):
    rows = []
    for sym in symbols:
        for d in DATES:
            rows.append(dict(market=market, symbol=sym, date=d.date(), open=close,
                             high=close * 1.05, low=close * 0.95, close=close,
                             volume=1e6, adj_close=close, split_adj_close=close,
                             close_brl=close * 5.0, adj_close_brl=close * 5.0,
                             fx_rate=5.0, source="t"))
    market_storage.upsert_market_prices(conn, pd.DataFrame(rows))


@pytest.fixture
def plane(tmp_path, monkeypatch):
    db_path = str(tmp_path / "plane.sqlite")
    conn = sqlite3.connect(db_path)
    storage.init_db(conn)
    _b3_rows(conn)
    _market_rows(conn, "CRYPTO", ["BTCUSDT", "ETHUSDT", "BTCTRY"])
    _market_rows(conn, "US", ["AAPL", "MSFT"])
    conn.close()

    # Offline: CDI and the benchmark come off the network / the plane, and this
    # synthetic plane has neither.
    import backtests.core.shared_data as sd

    flat = pd.Series(100.0, index=DATES)
    monkeypatch.setattr(sd, "download_benchmark", lambda t, s, e, *, market="B3": flat)
    monkeypatch.setattr(sd, "download_cdi_daily",
                        lambda s, e: pd.Series(0.0, index=DATES))
    return db_path


def _build(plane, market):
    return build_shared_data(plane, "2020-01-01", "2020-03-30", "ME", market=market)


def test_each_market_sees_only_its_own_instruments(plane):
    assert set(_build(plane, "B3")["ret"].columns) == {"PETR4", "VALE3"}
    assert set(_build(plane, "US")["ret"].columns) == {"AAPL", "MSFT"}


def test_the_crypto_universe_is_one_quote_asset(plane):
    """Binance lists the same coin against a dozen quote assets. Without the
    shape filter a "universe" would hold BTCUSDT and BTCTRY as two names — the
    same book counted twice, in two currencies."""
    assert set(_build(plane, "CRYPTO")["ret"].columns) == {"BTCUSDT", "ETHUSDT"}


def test_non_b3_prices_arrive_in_brl(plane):
    """The numeraire is the holder's currency for every market, so the frames a
    strategy ranks on are already converted."""
    shared = _build(plane, "CRYPTO")
    assert shared["raw_close"]["BTCUSDT"].dropna().iloc[0] == 500.0   # 100 x 5.0


def test_turnover_is_converted_too(plane):
    """min_adtv is a BRL threshold. Comparing it against a USDT turnover would
    make the liquidity filter 5x too permissive without anything saying so."""
    assert _build(plane, "CRYPTO")["adtv"]["BTCUSDT"].dropna().iloc[0] == 5e6


def test_the_market_is_reported_in_the_bundle(plane):
    assert _build(plane, "CRYPTO")["market"] == "CRYPTO"


def test_the_benchmark_aliases_point_at_the_same_frames(plane):
    """`ibov_*` cannot be renamed — signals.yaml, every inline expr and every
    stored spec read those names — so the neutral names are aliases, not copies.
    """
    shared = _build(plane, "US")
    assert shared["bench_px"] is shared["ibov_px"]
    assert shared["bench_ret"] is shared["ibov_ret"]


def test_the_regime_flags_carry_the_neutral_names_too(plane):
    """The flags are computed off MarketSpec.benchmark, so on this plane
    `ibov_above` already means "the S&P above its 10-month MA". A regime spec
    looks its flag up in this dict by name (config_strategy._eval_regime), so
    without the aliases a US or crypto spec had to write `ibov_above` and say
    something false on the page."""
    shared = _build(plane, "US")
    for neutral, legacy in (("bench_above", "ibov_above"),
                            ("bench_calm", "ibov_calm"),
                            ("bench_uptrend", "ibov_uptrend"),
                            ("bench_vol_monthly", "ibov_vol_monthly"),
                            ("bench_vol_pctrank", "ibov_vol_pctrank")):
        assert shared[neutral] is shared[legacy], neutral


def test_the_glitch_mask_uses_the_market_thresholds(plane):
    """A -45% month is B3's artifact threshold. In crypto it is a normal month,
    and using B3's number would permanently mask most of the universe."""
    crypto = markets.MARKETS["CRYPTO"]
    ret = pd.Series([-0.5, 0.1])
    assert not ((ret < crypto.glitch_down).any())
    assert (ret < markets.MARKETS["B3"].glitch_down).any()


def test_an_unknown_market_is_rejected_at_the_bundle(plane):
    with pytest.raises(ValueError, match="Unknown market"):
        _build(plane, "USA")


def test_b3_is_the_default_and_unchanged(plane):
    default = build_shared_data(plane, "2020-01-01", "2020-03-30", "ME")
    assert set(default["ret"].columns) == {"PETR4", "VALE3"}
    assert default["market"] == "B3"
