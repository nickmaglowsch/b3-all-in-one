"""Behaviour tests for MaxEntropy (Gibbs/Boltzmann allocation).

The claims worth guarding are the ones the docstring makes: the ensemble is a
continuous dial between equal weight and minimum-variance concentration, the
entropy floor removes names with a persistent direction, and neither the
exponential nor the flat-price edge case can produce NaN weights.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from backtests.core.strategy_registry import get_registry
from backtests.strategies.max_entropy import (
    MaxEntropyStrategy, binary_entropy, boltzmann_weights, directional_entropy,
    effective_positions, realised_energy,
)


# ── The two entropies ─────────────────────────────────────────────────────────

def test_binary_entropy_peaks_at_a_fair_coin_and_vanishes_at_certainty():
    h = binary_entropy(pd.Series([0.5, 0.9, 0.1, 1.0, 0.0, np.nan]))
    assert np.isclose(h.iloc[0], 1.0)                    # fair coin = 1 bit
    assert np.isclose(h.iloc[1], h.iloc[2])              # symmetric in p
    assert 0.4 < h.iloc[1] < 0.5                         # H(0.9) ≈ 0.469
    assert h.iloc[3] == 0.0 and h.iloc[4] == 0.0         # certainty = 0 bits, not NaN
    assert np.isnan(h.iloc[5])                           # warmup stays unmeasured


def test_directional_entropy_separates_a_coin_flip_from_a_trend():
    days = pd.bdate_range("2021-01-01", periods=200)
    alt = np.where(np.arange(len(days)) % 2 == 0, 0.01, -0.01)   # up half the days
    px = pd.DataFrame({
        "COIN": 100 * np.cumprod(1 + alt),
        "TREND": 100 * np.cumprod(1 + np.full(len(days), 0.002)),  # up every day
    }, index=days)

    h = directional_entropy(px, 60).iloc[-1]
    assert np.isclose(h["COIN"], 1.0, atol=0.02)   # ~50/50 -> a full bit
    assert h["TREND"] == 0.0                       # never a down day -> no entropy


def test_entropy_is_blind_to_the_size_of_the_moves():
    """The two measures are independent by construction: same sign process, 10x
    the amplitude. Entropy must not move; energy must."""
    days = pd.bdate_range("2021-01-01", periods=200)
    sign = np.where(np.arange(len(days)) % 2 == 0, 1.0, -1.0)
    px = pd.DataFrame({
        "CALM": 100 * np.cumprod(1 + 0.002 * sign),
        "WILD": 100 * np.cumprod(1 + 0.020 * sign),
    }, index=days)

    h = directional_entropy(px, 60).iloc[-1]
    e = realised_energy(px, 60).iloc[-1]
    assert np.isclose(h["CALM"], h["WILD"])
    assert e["WILD"] > 50 * e["CALM"]


# ── The ensemble ──────────────────────────────────────────────────────────────

def _ladder(n: int = 10) -> pd.Series:
    """`n` names on an energy ladder: 10%..55% annualised vol, as variances."""
    return pd.Series(np.linspace(0.10, 0.55, n) ** 2,
                     index=[f"T{i:02d}" for i in range(n)])


def test_temperature_dials_from_equal_weight_to_the_coldest_name():
    e = _ladder()
    hot = boltzmann_weights(e, temperature=1000.0, cap=1.0)
    warm = boltzmann_weights(e, temperature=1.0, cap=1.0)
    cold = boltzmann_weights(e, temperature=0.02, cap=1.0)

    for w in (hot, warm, cold):
        assert np.isclose(w.sum(), 1.0)
        assert w.notna().all()

    # T -> inf is the equal-weight limit; T -> 0 collapses onto the ground state.
    assert np.allclose(hot.to_numpy(), 0.1, atol=1e-3)
    assert cold.idxmax() == "T00" and cold["T00"] > 0.99
    # ...and the dial is monotone in between, read as effective positions.
    assert (effective_positions(cold) < effective_positions(warm)
            < effective_positions(hot))
    assert np.isclose(effective_positions(hot), 10.0, atol=0.05)


def test_weights_decrease_with_energy_and_are_not_merely_inverse_vol():
    e = _ladder()
    w = boltzmann_weights(e, temperature=1.0, cap=1.0)
    assert (w.diff().dropna() < 0).all()          # monotone in the ladder

    # exp(-σ²/T) decays faster than 1/σ: on the same ladder the ensemble spreads
    # calmest-to-wildest wider than inverse-vol, and unlike inverse-vol how much
    # wider is a dial rather than whatever the data happened to hand you.
    inv = (1 / np.sqrt(e)) / (1 / np.sqrt(e)).sum()

    def spread(x):
        return x.iloc[0] / x.iloc[-1]

    assert spread(w) > spread(inv)
    assert spread(boltzmann_weights(e, 0.3, 1.0)) > 10 * spread(w)


def test_cold_temperatures_do_not_overflow_to_nan():
    """exp(-E/T) at T=1e-6 overflows to inf unless the exponents are shifted."""
    w = boltzmann_weights(_ladder(), temperature=1e-6, cap=1.0)
    assert w.notna().all() and np.isclose(w.sum(), 1.0)


def test_degenerate_energies_fall_back_to_equal_weight():
    flat = pd.Series(0.0, index=list("ABCD"))
    w = boltzmann_weights(flat, temperature=1.0, cap=1.0)
    assert np.allclose(w.to_numpy(), 0.25)


def test_the_cap_binds_and_the_book_still_sums_to_one():
    """A cold ensemble wants ~100% in one name; 10 names at a 15% ceiling is
    reachable (1.5 >= 1), so every name must end at or under it."""
    w = boltzmann_weights(_ladder(), temperature=0.05, cap=0.15)
    assert (w <= 0.15 + 1e-9).all()
    assert np.isclose(w.sum(), 1.0)


def test_an_unreachable_cap_falls_back_to_equal_weight():
    """6 names at a 15% ceiling cannot sum to 1. Equal weight is the flattest
    book that exists — and, unlike clipping each name at the cap, it stays fully
    invested instead of parking the remainder in nothing."""
    w = boltzmann_weights(_ladder(6), temperature=0.5, cap=0.15)
    assert np.isclose(w.sum(), 1.0)
    assert np.allclose(w.to_numpy(), 1 / 6)


def test_effective_positions_counts_an_equal_weight_book():
    assert np.isclose(effective_positions(pd.Series([0.25] * 4)), 4.0)
    assert np.isclose(effective_positions(pd.Series([1.0])), 1.0)


# ── End to end ────────────────────────────────────────────────────────────────

def _shared(n_names: int = 12, seed: int = 3) -> dict:
    """A vol ladder in daily prices, plus one name in a relentless uptrend."""
    rng = np.random.default_rng(seed)
    days = pd.bdate_range("2018-01-01", periods=600)
    cols = [f"T{i:02d}" for i in range(n_names)] + ["TREND"]
    vols = np.linspace(0.005, 0.035, n_names)
    px = pd.DataFrame(
        {c: 100 * np.cumprod(1 + rng.normal(0, v, len(days)))
         for c, v in zip(cols, vols)},
        index=days)
    px["TREND"] = 100 * np.cumprod(1 + np.abs(rng.normal(0.002, 0.0005, len(days))))

    ret = px.resample("ME").last().pct_change()
    idx = ret.index
    return {
        "ret": ret, "adj_close": px,
        "adtv": pd.DataFrame(5e6, index=idx, columns=cols),
        "raw_close": pd.DataFrame(50.0, index=idx, columns=cols),
        "has_glitch": pd.DataFrame(0.0, index=idx, columns=cols),
    }


def test_the_book_tilts_toward_the_calmest_names_and_stays_fully_invested():
    shared = _shared()
    _, tw = MaxEntropyStrategy().generate_signals(shared, {"top_n": 8})

    active = tw[(tw != 0).any(axis=1)]
    assert len(active) > 0
    for _, row in active.iterrows():
        assert np.isclose(row.sum(), 1.0)
        assert (row <= 0.15 + 1e-9).all()          # the default cap holds

    row = active.iloc[-1]
    held = row[row > 0]
    # T00 is the coldest name in the ladder; the ensemble must weight it most.
    assert held.idxmax() == "T00"
    assert "T11" not in held.index                 # the wildest never gets in


def test_a_name_with_no_down_days_is_excluded_by_the_equilibrium_floor():
    shared = _shared()
    _, tw = MaxEntropyStrategy().generate_signals(shared, {"top_n": 13})
    assert (tw["TREND"] == 0.0).all()

    # ...and it is the entropy floor doing it, not the variance ranking: drop the
    # floor to zero and the same run holds it.
    _, loose = MaxEntropyStrategy().generate_signals(
        shared, {"top_n": 13, "min_entropy": 0.0})
    assert (loose["TREND"] > 0).any()


def test_temperature_changes_the_concentration_of_a_real_book():
    shared = _shared()
    _, hot = MaxEntropyStrategy().generate_signals(
        shared, {"top_n": 8, "temperature": 5.0, "weight_cap": 1.0})
    _, cold = MaxEntropyStrategy().generate_signals(
        shared, {"top_n": 8, "temperature": 0.1, "weight_cap": 1.0})

    h, c = hot.iloc[-1], cold.iloc[-1]
    assert effective_positions(c[c > 0]) < effective_positions(h[h > 0])


def test_weights_never_read_the_row_they_are_traded_on():
    """Row i must be built from data at i-1: blanking every price from the last
    rebalance date onward cannot change any earlier weight."""
    shared = _shared()
    _, full = MaxEntropyStrategy().generate_signals(shared, {"top_n": 8})

    cut = shared["ret"].index[-1]
    truncated = {**shared, "adj_close": shared["adj_close"].loc[:cut]}
    _, partial = MaxEntropyStrategy().generate_signals(truncated, {"top_n": 8})

    pd.testing.assert_frame_equal(full.iloc[:-1], partial.iloc[:-1])


def test_registered_in_the_gallery():
    assert "MaxEntropy" in get_registry().names()
