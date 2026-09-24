"""Split and dividend factors for the US plane, checked against the B3 ones.

The US factors are a second implementation, not shared code — `adjustments.py`
is keyed on `isin_code`, takes B3-shaped frames and is at its size ceiling. A
second implementation of arithmetic this consequential needs a seam, so the
cross-check tests here drive both with the same events and require identical
arrays. If someone fixes a bug in one, these say so.
"""
from __future__ import annotations

import datetime

import numpy as np
import pandas as pd
import pytest
from b3_pipeline import adjustments
from us_pipeline import us_adjust

D = datetime.date


def _bars(rows):
    return pd.DataFrame(rows, columns=["date", "close"])


APPLE = _bars([(D(2020, 8, 27), 500.0), (D(2020, 8, 28), 499.0),
               (D(2020, 8, 31), 124.8), (D(2020, 9, 1), 134.2)])
SPLIT_4_FOR_1 = pd.DataFrame([{"ex_date": D(2020, 8, 31), "ratio": 0.25}])


# ── Splits ────────────────────────────────────────────────────────────────────

def test_a_four_for_one_split_stops_being_a_seventy_five_percent_crash():
    """The bug this exists to prevent. Unadjusted, Apple's 2020 split reads as a
    -75% day, and MARKETS["US"].glitch_down = -0.45 would then mask the name for
    the whole lookback — so the damage would also be silent."""
    out, state = us_adjust.apply(APPLE, SPLIT_4_FOR_1, pd.DataFrame())
    adj = out["split_adj_close"]
    raw_return = APPLE["close"].iloc[2] / APPLE["close"].iloc[1] - 1
    adj_return = adj.iloc[2] / adj.iloc[1] - 1
    assert raw_return == pytest.approx(-0.75, abs=0.01)
    assert adj_return == pytest.approx(0.0, abs=0.01)
    assert state == us_adjust.TOTAL_RETURN


def test_the_ex_date_itself_already_trades_adjusted():
    """side='right', not 'left'. The factor applies strictly before the ex-date;
    applying it on the ex-date would halve an already-halved price."""
    out, _ = us_adjust.apply(APPLE, SPLIT_4_FOR_1, pd.DataFrame())
    assert out["split_adj_close"].iloc[2] == pytest.approx(124.8)
    assert out["split_adj_close"].iloc[1] == pytest.approx(499.0 * 0.25)


def test_split_factors_match_the_b3_implementation():
    """Same events, same arrays. The seam that catches a divergence."""
    events = pd.DataFrame([
        {"ex_date": D(2015, 6, 9), "ratio": 1.0 / 7.0},
        {"ex_date": D(2020, 8, 31), "ratio": 0.25},
        {"ex_date": D(2023, 5, 1), "ratio": 10.0},      # a reverse split
    ])
    dates = pd.Series(pd.date_range("2014-01-01", "2024-01-01", freq="7D").date)
    mine = us_adjust.split_factors(dates, events)

    theirs = adjustments.compute_split_adjustment_factors(
        pd.DataFrame({"date": dates, "isin_code": "USTEST", "open": 1.0,
                      "high": 1.0, "low": 1.0, "close": 1.0}),
        events.rename(columns={"ratio": "split_factor"}).assign(isin_code="USTEST"),
    )["split_adj_close"].to_numpy()

    np.testing.assert_allclose(mine, theirs, rtol=1e-12)


def test_no_splits_on_record_leaves_the_series_alone():
    out, state = us_adjust.apply(APPLE, pd.DataFrame(), pd.DataFrame())
    assert list(out["split_adj_close"]) == list(APPLE["close"])
    assert state == us_adjust.TOTAL_RETURN


# ── Dividends ─────────────────────────────────────────────────────────────────

def test_a_dividend_lifts_the_history_before_its_ex_date():
    bars = _bars([(D(2026, 8, 6), 100.0), (D(2026, 8, 7), 100.0),
                  (D(2026, 8, 8), 99.0)])
    divs = pd.DataFrame([{"ex_date": D(2026, 8, 8), "amount": 1.0}])
    out, state = us_adjust.apply(bars, pd.DataFrame(), divs)
    assert state == us_adjust.TOTAL_RETURN
    # 1 - 1.0/100.0 applied to everything strictly before the ex-date.
    assert out["adj_close"].iloc[0] == pytest.approx(99.0)
    assert out["adj_close"].iloc[2] == pytest.approx(99.0)


def test_a_distribution_bigger_than_the_price_is_skipped_not_applied():
    """A factor near zero crushes all prior history and invents a huge return at
    the ex-date. adjustments.py names a real case: a R$19 payout on a R$5 stock."""
    bars = _bars([(D(2026, 8, 6), 5.0), (D(2026, 8, 7), 5.0), (D(2026, 8, 8), 1.0)])
    divs = pd.DataFrame([{"ex_date": D(2026, 8, 8), "amount": 19.0}])
    out, _ = us_adjust.apply(bars, pd.DataFrame(), divs)
    assert list(out["adj_close"]) == [5.0, 5.0, 1.0]


def test_a_dividend_before_the_first_bar_cannot_be_priced_and_is_skipped():
    divs = pd.DataFrame([{"ex_date": D(2019, 1, 1), "amount": 1.0}])
    out, _ = us_adjust.apply(APPLE, pd.DataFrame(), divs)
    assert list(out["adj_close"]) == list(APPLE["close"])


# ── The receipt ───────────────────────────────────────────────────────────────

def test_refused_endpoints_are_recorded_not_papered_over():
    """`None` is the vendor refusing; an empty frame is the vendor saying "none
    on record". Only the second lets the series be called adjusted."""
    _, state = us_adjust.apply(APPLE, SPLIT_4_FOR_1, None)
    assert state == us_adjust.SPLIT_ONLY
    _, state = us_adjust.apply(APPLE, None, None)
    assert state == us_adjust.UNADJUSTED


def test_without_splits_nothing_is_adjusted_at_all():
    """A dividend factor laid over a series that still has its splits in it is
    not a partial improvement — it is a third wrong number."""
    divs = pd.DataFrame([{"ex_date": D(2020, 9, 1), "amount": 1.0}])
    out, state = us_adjust.apply(APPLE, None, divs)
    assert state == us_adjust.UNADJUSTED
    assert list(out["adj_close"]) == list(APPLE["close"])
    assert list(out["split_adj_close"]) == list(APPLE["close"])


def test_backward_cumulative_is_a_clamped_strict_suffix_product():
    ex = np.array(["2020-01-02", "2020-01-04"], dtype="datetime64[ns]")
    dates = np.array(["2020-01-01", "2020-01-02", "2020-01-03", "2020-01-05"],
                     dtype="datetime64[ns]")
    out = us_adjust._backward_cumulative(ex, np.array([0.5, 0.25]), dates)
    assert out.tolist() == [0.125, 0.25, 0.25, 1.0]
    # Beyond the bound is bad vendor data, so the product is clamped per step.
    big = us_adjust._backward_cumulative(ex, np.array([1e3, 1e3]), dates)
    assert big.tolist() == [100_000.0, 1e3, 1e3, 1.0]
    tiny = us_adjust._backward_cumulative(ex, np.array([1e-3, 1e-3]), dates)
    assert tiny.tolist() == [1e-5, 1e-3, 1e-3, 1.0]
