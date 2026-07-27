"""Tests for _detect_and_fix_unrecorded_splits (backtests/core/shared_data.py)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from backtests.core.shared_data import _detect_and_fix_unrecorded_splits


def _frames(prices, vols=None):
    idx = pd.date_range("2020-01-01", periods=len(prices), freq="B")
    px = pd.DataFrame({"X": prices}, index=idx)
    fv = pd.DataFrame({"X": vols}, index=idx) if vols is not None else None
    return px, fv


def test_unrecorded_split_still_fixed():
    # Exact 2:1 drop, calm volume, price stays in the new regime -> a genuine
    # unrecorded split: all prior history divided by 2.
    px, fv = _frames([20.0] * 15 + [10.0] * 15, [1e6] * 30)
    fixed = _detect_and_fix_unrecorded_splits(px.copy(), px, fv)
    assert np.isclose(fixed["X"].iloc[0], 10.0)


def test_real_crash_with_volume_spike_untouched():
    # A genuine -50% crash prints anomalous volume — history must NOT be
    # rewritten (this erased real crashes before).
    vols = [1e6] * 15 + [2e7] + [5e6] * 14
    px, fv = _frames([20.0] * 15 + [10.0] * 15, vols)
    fixed = _detect_and_fix_unrecorded_splits(px.copy(), px, fv)
    assert np.isclose(fixed["X"].iloc[0], 20.0)


def test_one_day_glitch_untouched():
    # A one-day bad print that reverts the next day is not a split (neither is
    # the recovery day a reverse split).
    px, fv = _frames([20.0] * 15 + [10.0] + [20.0] * 14, [1e6] * 30)
    fixed = _detect_and_fix_unrecorded_splits(px.copy(), px, fv)
    assert np.isclose(fixed["X"].iloc[0], 20.0)


# ── Branch coverage, pinned before the scalar loop was vectorised ────────────
# The detector rewrites price history, so "faster" is only acceptable if it is
# also bit-identical. These pin each decision the old per-bar loop made, so the
# array version cannot quietly widen or narrow any of them.


def test_reverse_split_multiplies_prior_history():
    # 1:5 reverse split: price jumps 5x, so prior history is multiplied by 5.
    px, fv = _frames([2.0] * 15 + [10.0] * 15, [1e6] * 30)
    fixed = _detect_and_fix_unrecorded_splits(px.copy(), px, fv)
    assert np.isclose(fixed["X"].iloc[0], 10.0)


def test_two_splits_compound():
    # 2:1 then 3:1. The earliest history carries both: 60 / 2 / 3 = 10.
    px, fv = _frames([60.0] * 10 + [30.0] * 10 + [10.0] * 10, [1e6] * 30)
    fixed = _detect_and_fix_unrecorded_splits(px.copy(), px, fv)
    assert np.isclose(fixed["X"].iloc[0], 10.0)
    assert np.isclose(fixed["X"].iloc[12], 10.0)   # between the two events
    assert np.isclose(fixed["X"].iloc[25], 10.0)   # after both, untouched


def test_ratio_outside_tolerance_untouched():
    # -45% is a big fall but not within 4% of any 1/N — left alone.
    px, fv = _frames([20.0] * 15 + [11.0] * 15, [1e6] * 30)
    fixed = _detect_and_fix_unrecorded_splits(px.copy(), px, fv)
    assert np.isclose(fixed["X"].iloc[0], 20.0)


def test_already_adjusted_series_untouched():
    # adj_close already carries the split but close_px does not: the two ratios
    # disagree, which is the signal that an adjustment WAS applied. No rewrite.
    idx = pd.date_range("2020-01-01", periods=30, freq="B")
    raw = pd.DataFrame({"X": [20.0] * 15 + [10.0] * 15}, index=idx)
    adj = pd.DataFrame({"X": [10.0] * 30}, index=idx)
    fv = pd.DataFrame({"X": [1e6] * 30}, index=idx)
    fixed = _detect_and_fix_unrecorded_splits(adj.copy(), raw, fv)
    assert np.isclose(fixed["X"].iloc[0], 10.0)  # unchanged from the input adj


def test_columns_are_independent():
    idx = pd.date_range("2020-01-01", periods=30, freq="B")
    split = [20.0] * 15 + [10.0] * 15
    flat = [7.0] * 30
    px = pd.DataFrame({"A": split, "B": flat}, index=idx)
    fv = pd.DataFrame({"A": [1e6] * 30, "B": [1e6] * 30}, index=idx)
    fixed = _detect_and_fix_unrecorded_splits(px.copy(), px, fv)
    assert np.isclose(fixed["A"].iloc[0], 10.0)
    assert np.isclose(fixed["B"].iloc[0], 7.0)


def test_nan_gaps_do_not_shift_the_event_date():
    # Holidays leave NaNs; the detector drops them per column, so the event must
    # still land on the first post-split bar and not slide onto a gap.
    prices = [20.0] * 8 + [np.nan] * 2 + [20.0] * 5 + [10.0] * 15
    px, fv = _frames(prices, [1e6] * 30)
    fixed = _detect_and_fix_unrecorded_splits(px.copy(), px, fv)
    assert np.isclose(fixed["X"].iloc[0], 10.0)
    assert np.isclose(fixed["X"].iloc[14], 10.0)   # last pre-split bar, halved
    assert np.isclose(fixed["X"].iloc[15], 10.0)   # first post-split bar, as-is


def test_split_with_no_room_for_the_forward_check():
    # Split on the last bar: `nxt` is empty, so the persistence check is skipped
    # rather than failing the event.
    px, fv = _frames([20.0] * 29 + [10.0], [1e6] * 30)
    fixed = _detect_and_fix_unrecorded_splits(px.copy(), px, fv)
    assert np.isclose(fixed["X"].iloc[0], 10.0)


def test_split_on_the_second_bar_has_no_prior_regime():
    # `prv` is empty at pos=1, so that check is skipped too.
    px, fv = _frames([20.0] + [10.0] * 29, [1e6] * 30)
    fixed = _detect_and_fix_unrecorded_splits(px.copy(), px, fv)
    assert np.isclose(fixed["X"].iloc[0], 10.0)


def test_missing_volume_frame_skips_the_volume_check():
    px, _ = _frames([20.0] * 15 + [10.0] * 15)
    fixed = _detect_and_fix_unrecorded_splits(px.copy(), px, None)
    assert np.isclose(fixed["X"].iloc[0], 10.0)


def test_column_absent_from_volume_frame_skips_the_volume_check():
    idx = pd.date_range("2020-01-01", periods=30, freq="B")
    px = pd.DataFrame({"X": [20.0] * 15 + [10.0] * 15}, index=idx)
    fv = pd.DataFrame({"OTHER": [1e6] * 30}, index=idx)
    fixed = _detect_and_fix_unrecorded_splits(px.copy(), px, fv)
    assert np.isclose(fixed["X"].iloc[0], 10.0)


def test_series_too_short_to_have_a_ratio():
    idx = pd.date_range("2020-01-01", periods=1, freq="B")
    px = pd.DataFrame({"X": [20.0]}, index=idx)
    fixed = _detect_and_fix_unrecorded_splits(px.copy(), px, None)
    assert np.isclose(fixed["X"].iloc[0], 20.0)


def test_all_nan_column_survives():
    idx = pd.date_range("2020-01-01", periods=10, freq="B")
    px = pd.DataFrame({"X": [np.nan] * 10}, index=idx)
    fixed = _detect_and_fix_unrecorded_splits(px.copy(), px, None)
    assert fixed["X"].isna().all()


def test_input_frame_is_not_mutated():
    px, fv = _frames([20.0] * 15 + [10.0] * 15, [1e6] * 30)
    before = px["X"].tolist()
    _detect_and_fix_unrecorded_splits(px, px, fv)
    assert px["X"].tolist() == before


# ── Expanding percentile rank (regime signal, same module) ───────────────────

def _pctrank_reference(v):
    """The expanding().apply() this replaced, kept as the oracle."""
    return v.expanding(min_periods=12).apply(lambda x: (x.iloc[-1] >= x).mean(), raw=False)


def _pctrank_cases():
    rng = np.random.default_rng(7)
    idx = lambda n: pd.date_range("2005-01-31", periods=n, freq="ME")
    cases = {
        "dense": rng.random(60),
        "leading_gaps": np.concatenate([[np.nan] * 5, rng.random(55)]),
        "interior_gaps": None,
        "ties": np.round(rng.random(60), 1),
        "all_nan": np.full(30, np.nan),
        "shorter_than_min_periods": rng.random(5),
    }
    v = rng.random(60)
    v[[7, 20, 33, 41]] = np.nan
    cases["interior_gaps"] = v
    return {k: pd.Series(a, index=idx(len(a))) for k, a in cases.items()}


def test_expanding_pctrank_matches_the_apply_it_replaced():
    from backtests.core.shared_data import _expanding_pctrank

    for name, s in _pctrank_cases().items():
        pd.testing.assert_series_equal(
            _expanding_pctrank(s), _pctrank_reference(s), check_names=False,
            obj=f"pctrank[{name}]")


def test_expanding_pctrank_scores_a_missing_month_as_zero_not_nan():
    # Load-bearing quirk of the original: ibov_calm reads this at <= 0.70, so a
    # NaN here would flip a month from calm to not-calm.
    from backtests.core.shared_data import _expanding_pctrank

    v = np.arange(1.0, 21.0)
    v[15] = np.nan
    s = pd.Series(v, index=pd.date_range("2005-01-31", periods=20, freq="ME"))
    assert _expanding_pctrank(s).iloc[15] == 0.0
