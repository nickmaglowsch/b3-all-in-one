"""The two places adjustments.py scaled with the size of the whole price table.

filter_fatcot_redundant_splits re-filtered and re-sorted the entire prices frame
once per B3 stock action — O(actions x rows) on a table that is the largest thing
the pipeline holds. And both it and detect_splits_from_prices normalised the date
column with a per-row Python .apply().

Neither is allowed to change what it decides, so equivalence comes first and the
scaling assertion second.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from b3_pipeline import adjustments, config
from b3_pipeline.adjustments import (
    _normalize_date,
    _normalize_date_series,
    filter_fatcot_redundant_splits,
)


# ── _normalize_date_series must equal _normalize_date, value for value ───────

@pytest.mark.parametrize("values", [
    ["2020-01-15", "2020-06-30", "1999-12-31"],
    ["2020-01-15T10:30:00", "2020-01-15T23:59:59Z", "2020-03-01T00:00:00+00:00"],
    [dt.date(2020, 1, 15), dt.date(2021, 7, 1)],
    [dt.datetime(2020, 1, 15, 10, 30), dt.datetime(2020, 2, 1, 0, 0)],
    [pd.Timestamp("2020-01-15"), pd.Timestamp("2020-02-01 12:00")],
    ["2020-01-15", None, "2020-02-01"],
    [None, None],
    [np.nan, "2020-01-15"],
    ["not-a-date", "2020-01-15"],
    ["", "2020-01-15"],
    [],
])
def test_normalize_date_series_matches_the_scalar_version(values):
    s = pd.Series(values, dtype=object)
    expected = pd.Series([_normalize_date(v) for v in values], dtype=object)
    got = _normalize_date_series(s)
    assert list(got) == list(expected), f"{list(got)} != {list(expected)}"


def test_normalize_date_series_keeps_the_index():
    s = pd.Series(["2020-01-15", "2020-02-01"], index=[7, 9], dtype=object)
    assert list(_normalize_date_series(s).index) == [7, 9]


def test_normalize_date_series_handles_a_datetime64_column():
    s = pd.Series(pd.to_datetime(["2020-01-15", "2020-02-01", None]))
    assert list(_normalize_date_series(s)) == [dt.date(2020, 1, 15), dt.date(2020, 2, 1), None]


def test_iso_string_with_a_timezone_keeps_its_wall_clock_date():
    # The scalar version had two string branches (fromisoformat for anything with
    # a "T", strptime on the first ten characters otherwise) that always agreed,
    # because a timezone suffix never shifts the wall-clock date. The vectorised
    # version relies on that, so pin it.
    assert _normalize_date("2020-01-15T23:59:59+00:00") == dt.date(2020, 1, 15)
    assert list(_normalize_date_series(pd.Series(["2020-01-15T23:59:59+00:00"]))) == [
        dt.date(2020, 1, 15)]


# ── filter_fatcot_redundant_splits ───────────────────────────────────────────

def _prices(rows):
    return pd.DataFrame(
        [{"isin_code": i, "date": d, "quotation_factor": f, "close": 10.0}
         for i, d, f in rows]
    )


def _actions(rows):
    return pd.DataFrame(
        [{"isin_code": i, "ex_date": d, "factor": f, "action_type": a, "source": s}
         for i, d, f, a, s in rows]
    )


def test_redundant_split_is_still_filtered():
    prices = _prices([
        ("BRPETRACNOR9", "2020-01-14", 1000),
        ("BRPETRACNOR9", "2020-01-15", 1),
    ])
    actions = _actions([
        ("BRPETRACNOR9", "2020-01-15", 1000.0, config.EVENT_TYPE_STOCK_SPLIT, "B3"),
    ])
    assert filter_fatcot_redundant_splits(actions, prices).empty


def test_a_real_split_with_no_fatcot_transition_survives():
    prices = _prices([
        ("BRPETRACNOR9", "2020-01-14", 1),
        ("BRPETRACNOR9", "2020-01-15", 1),
    ])
    actions = _actions([
        ("BRPETRACNOR9", "2020-01-15", 2.0, config.EVENT_TYPE_STOCK_SPLIT, "B3"),
    ])
    assert len(filter_fatcot_redundant_splits(actions, prices)) == 1


def test_detected_splits_are_never_filtered():
    prices = _prices([
        ("BRPETRACNOR9", "2020-01-14", 1000),
        ("BRPETRACNOR9", "2020-01-15", 1),
    ])
    actions = _actions([
        ("BRPETRACNOR9", "2020-01-15", 1000.0, config.EVENT_TYPE_STOCK_SPLIT, "DETECTED"),
    ])
    assert len(filter_fatcot_redundant_splits(actions, prices)) == 1


def test_one_isins_transition_cannot_filter_another_isins_action():
    """The scan was per-ISIN before and must stay per-ISIN: grouping is the whole
    point of the change, and a leaky group would drop real corporate actions."""
    prices = _prices([
        ("BRAAAAAAAAA0", "2020-01-14", 1000),
        ("BRAAAAAAAAA0", "2020-01-15", 1),
        ("BRBBBBBBBBB1", "2020-01-14", 1),
        ("BRBBBBBBBBB1", "2020-01-15", 1),
    ])
    actions = _actions([
        ("BRBBBBBBBBB1", "2020-01-15", 1000.0, config.EVENT_TYPE_STOCK_SPLIT, "B3"),
    ])
    assert len(filter_fatcot_redundant_splits(actions, prices)) == 1


def test_transition_outside_the_date_window_does_not_filter():
    prices = _prices([
        ("BRPETRACNOR9", "2020-01-01", 1000),
        ("BRPETRACNOR9", "2020-01-02", 1),
        ("BRPETRACNOR9", "2020-01-15", 1),
    ])
    actions = _actions([
        ("BRPETRACNOR9", "2020-01-15", 1000.0, config.EVENT_TYPE_STOCK_SPLIT, "B3"),
    ])
    assert len(filter_fatcot_redundant_splits(actions, prices)) == 1


def test_reverse_split_uses_the_inverse_ratio():
    # fatcot 1 -> 1000 is a ratio of 1/1000; a REVERSE_SPLIT with factor 0.001
    # expects 1/0.001 = 1000. Match, so it is redundant.
    prices = _prices([
        ("BRPETRACNOR9", "2020-01-14", 1),
        ("BRPETRACNOR9", "2020-01-15", 1000),
    ])
    actions = _actions([
        ("BRPETRACNOR9", "2020-01-15", 1000.0, config.EVENT_TYPE_REVERSE_SPLIT, "B3"),
    ])
    # ratio 1/1000 = 0.001 vs expected 1/1000 = 0.001 -> filtered
    assert filter_fatcot_redundant_splits(actions, prices).empty


def test_unparseable_ex_date_is_skipped():
    prices = _prices([
        ("BRPETRACNOR9", "2020-01-14", 1000),
        ("BRPETRACNOR9", "2020-01-15", 1),
    ])
    actions = _actions([
        ("BRPETRACNOR9", "nonsense", 1000.0, config.EVENT_TYPE_STOCK_SPLIT, "B3"),
    ])
    assert len(filter_fatcot_redundant_splits(actions, prices)) == 1


def test_isin_absent_from_prices_is_skipped():
    prices = _prices([
        ("BRAAAAAAAAA0", "2020-01-14", 1000),
        ("BRAAAAAAAAA0", "2020-01-15", 1),
    ])
    actions = _actions([
        ("BRZZZZZZZZZ9", "2020-01-15", 1000.0, config.EVENT_TYPE_STOCK_SPLIT, "B3"),
    ])
    assert len(filter_fatcot_redundant_splits(actions, prices)) == 1


def test_caller_frame_is_not_mutated():
    prices = _prices([
        ("BRPETRACNOR9", "2020-01-14", 1000),
        ("BRPETRACNOR9", "2020-01-15", 1),
    ])
    before = list(prices["date"])
    filter_fatcot_redundant_splits(
        _actions([("BRPETRACNOR9", "2020-01-15", 1000.0,
                   config.EVENT_TYPE_STOCK_SPLIT, "B3")]),
        prices,
    )
    assert list(prices["date"]) == before


def test_whole_frame_work_does_not_scale_with_the_action_count(monkeypatch):
    """The regression guard: the price frame is normalised, grouped and sorted
    once, not once per B3 action.

    This was a wall-clock ratio (`large < small * 4`) until a CI runner came in
    6% slow, the ratio landed at 4.24x, and a red `main` silently stopped every
    deploy. The property being guarded is structural, not temporal: the
    whole-frame work happens the same number of times whatever the action count.
    Counting it is exact, immune to runner speed, and a re-scan per action fails
    it by construction rather than by being slow enough to notice.
    """
    n_rows, n_isin = 6_000, 300
    isins = [f"BR{i:09d}" for i in range(n_isin)]
    dates = pd.date_range("2015-01-01", periods=n_rows // n_isin, freq="B")
    prices = pd.DataFrame({
        "isin_code": np.repeat(isins, len(dates)),
        "date": np.tile([d.strftime("%Y-%m-%d") for d in dates], n_isin),
        "quotation_factor": 1,
        "close": 10.0,
    })

    # _normalize_date_series is the whole-column normalisation; sort_values is the
    # per-group ordering. _normalize_date (scalar, per action) is deliberately not
    # counted — that one is allowed to scale with the actions.
    calls: dict[str, int] = {}
    real_normalize = adjustments._normalize_date_series
    real_sort = pd.DataFrame.sort_values

    def counting_normalize(series):
        calls["normalize"] = calls.get("normalize", 0) + 1
        return real_normalize(series)

    def counting_sort(self, *a, **kw):
        calls["sort"] = calls.get("sort", 0) + 1
        return real_sort(self, *a, **kw)

    monkeypatch.setattr(adjustments, "_normalize_date_series", counting_normalize)
    monkeypatch.setattr(pd.DataFrame, "sort_values", counting_sort)

    def work_for(k):
        calls.clear()
        actions = _actions([
            (isins[i % n_isin], "2018-06-01", 2.0, config.EVENT_TYPE_STOCK_SPLIT, "B3")
            for i in range(k)
        ])
        filter_fatcot_redundant_splits(actions, prices)
        return dict(calls)

    few, many = work_for(20), work_for(200)
    assert few == many, (
        f"10x the actions changed the whole-frame work: {few} -> {many}"
        " — looks O(actions x rows) again")
    # Pin the absolute numbers too, so a refactor that stops doing the work at all
    # cannot pass by making both sides equally zero.
    assert few == {"normalize": 1, "sort": n_isin}, few
