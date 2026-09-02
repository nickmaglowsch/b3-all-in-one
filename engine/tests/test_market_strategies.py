"""Behaviour of the US and crypto specs, on synthetic data.

`test_market_specs.py` asserts the whole gallery loads and validates; this one
drives the four specs that have logic beyond "rank a named factor" and pins what
they actually select. Same shape as `test_ported_specs.py` — synthetic frames, no
DB, no network.

The mirrors (USLowVol, USMomSharpe, USMultiFactor, CryptoLowVol,
CryptoMomSharpe, CryptoMultiFactor) are deliberately not re-tested here: they are
their B3 originals with a `market:` key, and `test_ported_specs.py` already pins
those expressions. What is new and therefore tested is the fundamentals composite
built from the raw PIT frames, the benchmark-gated books, and the ADTV book.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from backtests.core.config_strategy import RankAndHold

SPECS = Path(__file__).resolve().parent.parent / "backtests" / "strategies" / "specs"


def _spec(fname: str) -> dict:
    return yaml.safe_load((SPECS / fname).read_text())


def _grid(n: int, freq: str = "ME") -> pd.DatetimeIndex:
    return pd.date_range("2018-01-31", periods=n, freq=freq)


def _base(idx, cols, ret, price=50.0, adtv=5e6) -> dict:
    """A fully-eligible universe: liquid, above every price floor, no glitches."""
    return {
        "ret": ret,
        "log_ret": np.log1p(ret),
        "adtv": pd.DataFrame(adtv, index=idx, columns=cols),
        "raw_close": pd.DataFrame(price, index=idx, columns=cols),
        "has_glitch": pd.DataFrame(0.0, index=idx, columns=cols),
    }


def _held(row: pd.Series) -> list:
    return sorted(row[row > 0].index)


# ── USQualityValue ────────────────────────────────────────────────────────────
# Value leg = f_equity / (raw_close * f_shares_outstanding); quality leg =
# f_net_income / f_revenue. Both duration-invariant — see signals.yaml.

def _quality_value_shared(n_names: int = 10):
    cols = [f"US{i:02d}" for i in range(n_names)]
    idx = _grid(4)
    ret = pd.DataFrame(0.0, index=idx, columns=cols)
    shared = _base(idx, cols, ret)
    shared["f_shares_outstanding"] = pd.DataFrame(1.0, index=idx, columns=cols)
    # Equity ascending: US09 is the cheapest name at a flat price of 50.
    shared["f_equity"] = pd.DataFrame(
        np.tile(np.arange(1, n_names + 1) * 10.0, (len(idx), 1)),
        index=idx, columns=cols)
    shared["f_revenue"] = pd.DataFrame(100.0, index=idx, columns=cols)
    # Margin ascending too, so both legs agree and the top of the composite is
    # unambiguous.
    shared["f_net_income"] = pd.DataFrame(
        np.tile(np.arange(1, n_names + 1) * 1.0, (len(idx), 1)),
        index=idx, columns=cols)
    return shared, cols, idx


def test_us_quality_value_holds_the_cheap_and_profitable_end():
    shared, cols, _ = _quality_value_shared()
    _, tw = RankAndHold(_spec("us_quality_value.yaml")).generate_signals(shared, {})

    row = tw.iloc[-1]
    # top_pct 0.20 of 10 names -> 2, but min_hold defaults to min_names (5)
    assert _held(row) == ["US05", "US06", "US07", "US08", "US09"]
    assert np.allclose(row[_held(row)].to_numpy(), 0.2)


def test_us_quality_value_still_invests_when_one_leg_is_missing():
    """`nan_add`, not `+`. The two legs read different tables and a filer can
    report equity and shares without a usable revenue line; a plain sum would NaN
    out every such name — the bug ValueQuality's own comment records."""
    shared, cols, _ = _quality_value_shared()
    shared["f_revenue"] = shared["f_revenue"].astype(float)
    shared["f_revenue"].iloc[:, :] = np.nan          # the quality leg is gone

    _, tw = RankAndHold(_spec("us_quality_value.yaml")).generate_signals(shared, {})

    row = tw.iloc[-1]
    assert row.sum() == pytest.approx(1.0), "a missing leg must not empty the book"
    # Ranked on the surviving value leg alone: the highest-equity names.
    assert _held(row) == ["US05", "US06", "US07", "US08", "US09"]


def test_us_quality_value_declares_the_fundamentals_it_reads():
    """`needs_fundamentals` is what makes the runner load the PIT frames at all;
    without it the expression evaluates against a bundle that has no f_* keys."""
    assert RankAndHold(_spec("us_quality_value.yaml")).needs_fundamentals
    assert RankAndHold(_spec("us_profitability.yaml")).needs_fundamentals


# ── The benchmark-gated books (USTrendRegime / CryptoBTCRegime) ────────────────

@pytest.mark.parametrize("spec_file", ["us_trend_regime.yaml",
                                       "crypto_btc_regime.yaml"])
def test_a_gated_book_invests_on_the_flag_and_parks_off_it(spec_file):
    spec = _spec(spec_file)
    lookback = spec["signal"]["lookback"]
    n = lookback + spec["warmup_pad"] + 4
    cols = [f"C{i:02d}" for i in range(10)]
    idx = _grid(n)
    ret = pd.DataFrame(
        np.tile(np.arange(10) * 0.01, (n, 1)), index=idx, columns=cols)
    shared = _base(idx, cols, ret)
    shared["mf_composite"] = pd.DataFrame(
        np.tile(np.arange(10) * 1.0, (n, 1)), index=idx, columns=cols)
    shared["cdi_monthly"] = pd.Series(0.008, index=idx)
    # The gate reads row i-1, so flip it two rows before the row asserted below.
    flag = pd.Series(True, index=idx)
    flag.iloc[-3:] = False
    shared["bench_above"] = flag

    _, tw = RankAndHold(spec).generate_signals(shared, {})

    on = tw.iloc[-4]
    assert on["CDI_ASSET"] == 0.0
    assert _held(on) == ["C05", "C06", "C07", "C08", "C09"]
    off = tw.iloc[-1]
    assert off["CDI_ASSET"] == pytest.approx(1.0)
    assert off.drop("CDI_ASSET").sum() == 0.0


@pytest.mark.parametrize("spec_file", ["us_trend_regime.yaml",
                                       "crypto_btc_regime.yaml"])
def test_a_gated_book_does_not_add_a_column_named_ibov_to_a_non_b3_matrix(spec_file):
    """`add_ibov: false`. Nothing weights that sleeve, and a US or crypto returns
    matrix carrying a stream literally named IBOV is a lie in every artifact
    downstream of it."""
    spec = _spec(spec_file)
    n = spec["signal"]["lookback"] + spec["warmup_pad"] + 2
    cols = [f"C{i:02d}" for i in range(10)]
    idx = _grid(n)
    shared = _base(idx, cols, pd.DataFrame(0.01, index=idx, columns=cols))
    shared["mf_composite"] = pd.DataFrame(
        np.tile(np.arange(10) * 1.0, (n, 1)), index=idx, columns=cols)
    shared["cdi_monthly"] = pd.Series(0.008, index=idx)
    shared["bench_above"] = pd.Series(True, index=idx)

    r, tw = RankAndHold(spec).generate_signals(shared, {})

    assert "IBOV" not in r.columns
    assert "IBOV" not in tw.columns
    assert "CDI_ASSET" in r.columns          # the cash sleeve is still there


# ── CryptoTopLiquid ───────────────────────────────────────────────────────────

def test_crypto_top_liquid_holds_the_deepest_ten_weighted_by_turnover():
    cols = [f"C{i:02d}USDT" for i in range(15)]
    idx = _grid(3)
    shared = _base(idx, cols, pd.DataFrame(0.0, index=idx, columns=cols))
    # Ascending turnover, all above the 1M floor.
    shared["adtv"] = pd.DataFrame(
        np.tile(np.arange(1, 16) * 2e6, (len(idx), 1)), index=idx, columns=cols)

    _, tw = RankAndHold(_spec("crypto_top_liquid.yaml")).generate_signals(shared, {})

    row = tw.iloc[-1]
    assert _held(row) == [f"C{i:02d}USDT" for i in range(5, 15)]
    assert row.sum() == pytest.approx(1.0)
    # weighting: adtv -> proportional to the prior row's turnover, so the deepest
    # name carries the most and the ordering is strictly increasing.
    held = [f"C{i:02d}USDT" for i in range(5, 15)]
    w = row[held].to_numpy()
    assert np.all(np.diff(w) > 0)
    assert w[-1] / w[0] == pytest.approx(15 / 6)


def test_crypto_top_liquid_respects_the_liquidity_floor():
    """The floor is the whole content of a ranking whose factor *is* liquidity:
    without it the book is "the deepest of whatever is left", including a pair
    that printed once."""
    cols = [f"C{i:02d}USDT" for i in range(15)]
    idx = _grid(3)
    shared = _base(idx, cols, pd.DataFrame(0.0, index=idx, columns=cols))
    shared["adtv"] = pd.DataFrame(
        np.tile(np.arange(1, 16) * 2e6, (len(idx), 1)), index=idx, columns=cols)
    shared["adtv"].iloc[:, 10:] = 1e3        # the five deepest go illiquid

    _, tw = RankAndHold(_spec("crypto_top_liquid.yaml")).generate_signals(shared, {})

    row = tw.iloc[-1]
    assert _held(row) == [f"C{i:02d}USDT" for i in range(10)]


# ── CryptoMeanRevWeekly ───────────────────────────────────────────────────────

def test_crypto_mean_rev_weekly_buys_losers_inside_its_own_band():
    cols = [f"C{i:02d}USDT" for i in range(10)]
    idx = _grid(4, freq="W-FRI")
    ret = pd.DataFrame(0.0, index=idx, columns=cols)
    #        C00     C01    C02    C03    C04    C05   C06   C07   C08  C09
    ret.iloc[1] = [-0.85, -0.60, -0.50, -0.40, -0.30, -0.10, 0.10, 0.5, 1.0, 2.5]
    shared = _base(idx, cols, ret, price=1.0)

    _, tw = RankAndHold(_spec("crypto_mean_rev_weekly.yaml")).generate_signals(
        shared, {})

    row = tw.iloc[2]                          # warmup = lookback(1) + pad(1)
    # C00 is the biggest loser but -85% is outside the strategy's own band, and
    # C09's +250% is outside the other side.
    assert _held(row) == [f"C{i:02d}USDT" for i in range(1, 6)]
    assert row["C00USDT"] == 0.0
    assert row["C09USDT"] == 0.0
