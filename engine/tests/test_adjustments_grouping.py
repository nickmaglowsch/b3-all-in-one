"""The last two places adjustments.py scaled with the size of the whole price table.

`filter_fatcot_redundant_splits` was fixed in test_adjustments_scaling.py; its two
neighbours were not. Both `compute_split_adjustment_factors` and
`compute_dividend_adjustment_factors` walked the ISINs that have an event and, for
each one, built a full-length boolean mask over the *whole* price table to find
that ISIN's rows — and a second one over the event table. `compute_split_*` then
wrote its four OHLC columns back through that mask, four more full-length passes
per ISIN.

On the real B3 plane that is thousands of ISINs against millions of price rows:
the per-ISIN arithmetic is already O(n log m) and cheap, and every bit of the cost
was in re-scanning the table to find the rows. One `groupby(...).indices` gives
every ISIN's row positions in a single pass, which is the same lookup done once.

Neither function may change what it computes, so equivalence comes first — the
dividend half had no direct tests at all, so the characterisation here is written
against the behaviour as it stands — and the scaling assertion second.

Following test_adjustments_scaling.py, the scaling guard counts whole-frame work
rather than timing it: a wall-clock ratio turns a 6%-slow CI runner into a red
`main`, while "how many times was the whole table scanned" is exact, runner-
independent, and fails by construction the moment a per-ISIN scan comes back.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from b3_pipeline import config
from b3_pipeline.adjustments import (
    _MAX_CUMULATIVE_FACTOR,
    _MIN_CUMULATIVE_FACTOR,
    compute_dividend_adjustment_factors,
    compute_split_adjustment_factors,
)


# ── builders ─────────────────────────────────────────────────────────────────

def _prices(rows, *, index=None):
    """rows: (isin, date, open, high, low, close)."""
    df = pd.DataFrame(
        [{"isin_code": i, "ticker": i[:4] + "3", "date": d,
          "open": float(o), "high": float(h), "low": float(lo), "close": float(c),
          "volume": 1_000_000, "quotation_factor": 1}
         for i, d, o, h, lo, c in rows],
        columns=["isin_code", "ticker", "date", "open", "high", "low", "close",
                 "volume", "quotation_factor"],
    )
    if index is not None:
        df.index = pd.Index(index)
    return df


def _splits(rows):
    """rows: (isin, ex_date, split_factor)."""
    return pd.DataFrame(rows, columns=["isin_code", "ex_date", "split_factor"])


def _actions(rows):
    """rows: (isin, event_date, event_type, value)."""
    return pd.DataFrame(
        rows, columns=["isin_code", "event_date", "event_type", "value"])


def _split_adjusted(prices):
    """`compute_dividend_adjustment_factors` reads split_adj_close, so its input
    is always the output of the split pass. With no splits that is a copy."""
    return compute_split_adjustment_factors(prices, _splits([]))


# ── compute_split_adjustment_factors: what it computes ───────────────────────

def test_split_factor_lands_on_the_rows_before_the_ex_date_only():
    prices = _prices([
        ("BRAAAAAAAAA0", "2020-01-14", 100, 100, 100, 100),
        ("BRAAAAAAAAA0", "2020-01-15", 50, 50, 50, 50),
        ("BRAAAAAAAAA0", "2020-01-16", 52, 52, 52, 52),
    ])
    out = compute_split_adjustment_factors(
        prices, _splits([("BRAAAAAAAAA0", "2020-01-15", 0.5)]))
    assert list(out["split_adj_close"]) == [50.0, 50.0, 52.0]
    assert list(out["split_adj_open"]) == [50.0, 50.0, 52.0]
    assert list(out["split_adj_high"]) == [50.0, 50.0, 52.0]
    assert list(out["split_adj_low"]) == [50.0, 50.0, 52.0]


def test_each_isin_gets_only_its_own_splits():
    """The whole point of the grouping: a leaky group would re-price a company
    against another company's corporate action."""
    prices = _prices([
        ("BRAAAAAAAAA0", "2020-01-14", 100, 100, 100, 100),
        ("BRBBBBBBBBB1", "2020-01-14", 100, 100, 100, 100),
        ("BRCCCCCCCCC2", "2020-01-14", 100, 100, 100, 100),
    ])
    splits = _splits([
        ("BRAAAAAAAAA0", "2020-01-15", 0.5),
        ("BRBBBBBBBBB1", "2020-01-15", 0.25),
    ])
    out = compute_split_adjustment_factors(prices, splits)
    got = dict(zip(out["isin_code"], out["split_adj_close"]))
    assert got == {"BRAAAAAAAAA0": 50.0, "BRBBBBBBBBB1": 25.0,
                   "BRCCCCCCCCC2": 100.0}


def test_splits_compound_backwards_as_a_suffix_product():
    prices = _prices([
        ("BRAAAAAAAAA0", "2020-01-01", 100, 100, 100, 100),
        ("BRAAAAAAAAA0", "2020-02-01", 50, 50, 50, 50),
        ("BRAAAAAAAAA0", "2020-03-01", 25, 25, 25, 25),
    ])
    splits = _splits([
        ("BRAAAAAAAAA0", "2020-02-01", 0.5),
        ("BRAAAAAAAAA0", "2020-03-01", 0.5),
    ])
    out = compute_split_adjustment_factors(prices, splits)
    assert list(out["split_adj_close"]) == [25.0, 25.0, 25.0]


def test_the_cumulative_factor_is_clamped_at_both_ends():
    prices = _prices([("BRAAAAAAAAA0", "2020-01-01", 1, 1, 1, 1)])
    huge = compute_split_adjustment_factors(
        prices, _splits([("BRAAAAAAAAA0", "2020-06-01", 1e9)]))
    assert huge["split_adj_close"].iloc[0] == _MAX_CUMULATIVE_FACTOR
    tiny = compute_split_adjustment_factors(
        prices, _splits([("BRAAAAAAAAA0", "2020-06-01", 1e-9)]))
    assert tiny["split_adj_close"].iloc[0] == _MIN_CUMULATIVE_FACTOR


def test_a_split_for_an_isin_with_no_prices_is_ignored():
    prices = _prices([("BRAAAAAAAAA0", "2020-01-01", 100, 100, 100, 100)])
    out = compute_split_adjustment_factors(
        prices, _splits([("BRZZZZZZZZZ9", "2020-06-01", 0.5)]))
    assert list(out["split_adj_close"]) == [100.0]


def test_a_split_with_no_ex_date_is_dropped_not_applied():
    prices = _prices([("BRAAAAAAAAA0", "2020-01-01", 100, 100, 100, 100)])
    out = compute_split_adjustment_factors(
        prices, _splits([("BRAAAAAAAAA0", None, 0.5)]))
    assert list(out["split_adj_close"]) == [100.0]


def test_rows_are_matched_by_position_not_by_index_label():
    """The price frame comes off a chunked read whose index is whatever the
    reader left; a shuffled, non-monotonic index must not shuffle the prices."""
    prices = _prices([
        ("BRAAAAAAAAA0", "2020-01-14", 100, 100, 100, 100),
        ("BRBBBBBBBBB1", "2020-01-14", 200, 200, 200, 200),
        ("BRAAAAAAAAA0", "2020-01-16", 52, 52, 52, 52),
    ], index=[9, 3, 7])
    out = compute_split_adjustment_factors(
        prices, _splits([("BRAAAAAAAAA0", "2020-01-15", 0.5)]))
    assert list(out.index) == [9, 3, 7]
    assert list(out["split_adj_close"]) == [50.0, 200.0, 52.0]


def test_unsorted_price_rows_still_get_their_own_factor():
    """`prices` arrives in whatever order the query returned; the split pass
    never sorted it and must not start depending on the order."""
    prices = _prices([
        ("BRAAAAAAAAA0", "2020-01-16", 52, 52, 52, 52),
        ("BRAAAAAAAAA0", "2020-01-14", 100, 100, 100, 100),
        ("BRAAAAAAAAA0", "2020-01-15", 50, 50, 50, 50),
    ])
    out = compute_split_adjustment_factors(
        prices, _splits([("BRAAAAAAAAA0", "2020-01-15", 0.5)]))
    assert list(out["split_adj_close"]) == [52.0, 50.0, 50.0]


def test_the_caller_frame_is_not_mutated():
    prices = _prices([("BRAAAAAAAAA0", "2020-01-01", 100, 100, 100, 100)])
    before = list(prices.columns)
    compute_split_adjustment_factors(
        prices, _splits([("BRAAAAAAAAA0", "2020-06-01", 0.5)]))
    assert list(prices.columns) == before
    assert "_date_ts" not in prices.columns


def test_the_scratch_date_column_never_leaves_the_function():
    prices = _prices([("BRAAAAAAAAA0", "2020-01-01", 100, 100, 100, 100)])
    out = compute_split_adjustment_factors(
        prices, _splits([("BRAAAAAAAAA0", "2020-06-01", 0.5)]))
    assert "_date_ts" not in out.columns


# ── compute_dividend_adjustment_factors: what it computes ────────────────────

def test_a_dividend_scales_every_row_strictly_before_the_ex_date():
    prices = _split_adjusted(_prices([
        ("BRAAAAAAAAA0", "2020-01-14", 100, 100, 100, 100),
        ("BRAAAAAAAAA0", "2020-01-15", 99, 99, 99, 99),
        ("BRAAAAAAAAA0", "2020-01-16", 99, 99, 99, 99),
    ]))
    actions = _actions([
        ("BRAAAAAAAAA0", "2020-01-15", config.EVENT_TYPE_CASH_DIVIDEND, 1.0)])
    out = compute_dividend_adjustment_factors(prices, actions)
    # prev_close is the 14th's 100; factor = 1 - 1/100 = 0.99.
    assert list(out["adj_close"]) == pytest.approx([99.0, 99.0, 99.0])


def test_jcp_and_cash_dividends_are_both_applied():
    for kind in (config.EVENT_TYPE_JCP, "Dividendo", "JCP", "Rend. Tributado"):
        prices = _split_adjusted(_prices([
            ("BRAAAAAAAAA0", "2020-01-14", 100, 100, 100, 100),
            ("BRAAAAAAAAA0", "2020-01-15", 99, 99, 99, 99),
        ]))
        out = compute_dividend_adjustment_factors(
            prices, _actions([("BRAAAAAAAAA0", "2020-01-15", kind, 1.0)]))
        assert out["adj_close"].iloc[0] == pytest.approx(99.0), kind


def test_a_non_dividend_event_does_not_move_adj_close():
    prices = _split_adjusted(_prices([
        ("BRAAAAAAAAA0", "2020-01-14", 100, 100, 100, 100),
        ("BRAAAAAAAAA0", "2020-01-15", 50, 50, 50, 50),
    ]))
    out = compute_dividend_adjustment_factors(
        prices,
        _actions([("BRAAAAAAAAA0", "2020-01-15", config.EVENT_TYPE_STOCK_SPLIT, 2.0)]))
    assert list(out["adj_close"]) == [100.0, 50.0]


def test_dividends_compound_backwards():
    prices = _split_adjusted(_prices([
        ("BRAAAAAAAAA0", "2020-01-01", 100, 100, 100, 100),
        ("BRAAAAAAAAA0", "2020-02-01", 100, 100, 100, 100),
        ("BRAAAAAAAAA0", "2020-03-01", 100, 100, 100, 100),
    ]))
    actions = _actions([
        ("BRAAAAAAAAA0", "2020-02-01", config.EVENT_TYPE_CASH_DIVIDEND, 1.0),
        ("BRAAAAAAAAA0", "2020-03-01", config.EVENT_TYPE_CASH_DIVIDEND, 1.0),
    ])
    out = compute_dividend_adjustment_factors(prices, actions)
    assert list(out["adj_close"]) == pytest.approx([98.01, 99.0, 100.0])


def test_one_isins_dividend_never_touches_another():
    prices = _split_adjusted(_prices([
        ("BRAAAAAAAAA0", "2020-01-14", 100, 100, 100, 100),
        ("BRBBBBBBBBB1", "2020-01-14", 100, 100, 100, 100),
    ]))
    out = compute_dividend_adjustment_factors(
        prices,
        _actions([("BRAAAAAAAAA0", "2020-01-15",
                   config.EVENT_TYPE_CASH_DIVIDEND, 1.0)]))
    got = dict(zip(out["isin_code"], out["adj_close"]))
    assert got["BRAAAAAAAAA0"] == pytest.approx(99.0)
    assert got["BRBBBBBBBBB1"] == 100.0


def test_a_dividend_before_the_first_bar_has_no_previous_close_and_is_skipped():
    prices = _split_adjusted(_prices([
        ("BRAAAAAAAAA0", "2020-01-14", 100, 100, 100, 100)]))
    out = compute_dividend_adjustment_factors(
        prices,
        _actions([("BRAAAAAAAAA0", "2019-01-01",
                   config.EVENT_TYPE_CASH_DIVIDEND, 1.0)]))
    assert list(out["adj_close"]) == [100.0]


def test_a_payout_at_or_above_ninety_percent_of_the_price_is_refused():
    """BRPR3's R$19 payout on a R$5 stock: a multiplicative factor cannot carry
    it, so the event is dropped and price history is kept."""
    prices = _split_adjusted(_prices([
        ("BRAAAAAAAAA0", "2020-01-14", 5, 5, 5, 5),
        ("BRAAAAAAAAA0", "2020-01-15", 5, 5, 5, 5),
    ]))
    out = compute_dividend_adjustment_factors(
        prices,
        _actions([("BRAAAAAAAAA0", "2020-01-15",
                   config.EVENT_TYPE_CASH_DIVIDEND, 19.0)]))
    assert list(out["adj_close"]) == [5.0, 5.0]


@pytest.mark.parametrize("amount", [0.0, -1.0])
def test_a_non_positive_payout_is_skipped(amount):
    prices = _split_adjusted(_prices([
        ("BRAAAAAAAAA0", "2020-01-14", 100, 100, 100, 100),
        ("BRAAAAAAAAA0", "2020-01-15", 100, 100, 100, 100),
    ]))
    out = compute_dividend_adjustment_factors(
        prices,
        _actions([("BRAAAAAAAAA0", "2020-01-15",
                   config.EVENT_TYPE_CASH_DIVIDEND, amount)]))
    assert list(out["adj_close"]) == [100.0, 100.0]


def test_a_dividend_against_a_non_positive_previous_close_is_skipped():
    prices = _split_adjusted(_prices([
        ("BRAAAAAAAAA0", "2020-01-14", 0, 0, 0, 0),
        ("BRAAAAAAAAA0", "2020-01-15", 100, 100, 100, 100),
    ]))
    out = compute_dividend_adjustment_factors(
        prices,
        _actions([("BRAAAAAAAAA0", "2020-01-15",
                   config.EVENT_TYPE_CASH_DIVIDEND, 1.0)]))
    assert list(out["adj_close"]) == [0.0, 100.0]


def test_an_undated_dividend_is_dropped():
    prices = _split_adjusted(_prices([
        ("BRAAAAAAAAA0", "2020-01-14", 100, 100, 100, 100),
        ("BRAAAAAAAAA0", "2020-01-15", 100, 100, 100, 100),
    ]))
    out = compute_dividend_adjustment_factors(
        prices,
        _actions([("BRAAAAAAAAA0", None, config.EVENT_TYPE_CASH_DIVIDEND, 1.0)]))
    assert list(out["adj_close"]) == [100.0, 100.0]


def test_the_previous_close_is_the_last_bar_before_the_ex_date_however_rows_arrive():
    """The dividend pass sorts each ISIN's bars by date before taking the
    previous close, because `prices` is not ordered. Shuffle them to pin it."""
    rows = [
        ("BRAAAAAAAAA0", "2020-01-16", 99, 99, 99, 99),
        ("BRAAAAAAAAA0", "2020-01-14", 100, 100, 100, 100),
        ("BRAAAAAAAAA0", "2020-01-15", 99, 99, 99, 99),
    ]
    out = compute_dividend_adjustment_factors(
        _split_adjusted(_prices(rows)),
        _actions([("BRAAAAAAAAA0", "2020-01-15",
                   config.EVENT_TYPE_CASH_DIVIDEND, 1.0)]))
    # prev_close is the 14th's 100 -> 0.99 on the 14th alone.
    assert list(out["adj_close"]) == pytest.approx([99.0, 99.0, 99.0])


def test_dividend_rows_are_matched_by_position_not_by_index_label():
    prices = _split_adjusted(_prices([
        ("BRAAAAAAAAA0", "2020-01-14", 100, 100, 100, 100),
        ("BRBBBBBBBBB1", "2020-01-14", 200, 200, 200, 200),
        ("BRAAAAAAAAA0", "2020-01-15", 99, 99, 99, 99),
    ], index=[9, 3, 7]))
    out = compute_dividend_adjustment_factors(
        prices,
        _actions([("BRAAAAAAAAA0", "2020-01-15",
                   config.EVENT_TYPE_CASH_DIVIDEND, 1.0)]))
    assert list(out.index) == [9, 3, 7]
    assert list(out["adj_close"]) == pytest.approx([99.0, 200.0, 99.0])


def test_no_corporate_actions_leaves_adj_close_equal_to_split_adj_close():
    prices = _split_adjusted(_prices([
        ("BRAAAAAAAAA0", "2020-01-14", 100, 100, 100, 100)]))
    out = compute_dividend_adjustment_factors(prices, _actions([]))
    assert list(out["adj_close"]) == list(out["split_adj_close"])


def test_the_dividend_pass_does_not_mutate_its_inputs():
    prices = _split_adjusted(_prices([
        ("BRAAAAAAAAA0", "2020-01-14", 100, 100, 100, 100),
        ("BRAAAAAAAAA0", "2020-01-15", 99, 99, 99, 99),
    ]))
    actions = _actions([
        ("BRAAAAAAAAA0", "2020-01-15", config.EVENT_TYPE_CASH_DIVIDEND, 1.0)])
    price_cols, action_cols = list(prices.columns), list(actions.columns)
    out = compute_dividend_adjustment_factors(prices, actions)
    assert list(prices.columns) == price_cols
    assert list(actions.columns) == action_cols
    assert "_date_ts" not in out.columns
    assert "_event_date_ts" not in out.columns


# ── the scaling guard ────────────────────────────────────────────────────────

def _count_whole_frame_scans(monkeypatch) -> dict:
    """Count the operations whose cost is the size of the *whole* price table.

    A boolean mask over a frame — `frame[mask]`, `frame.loc[mask]`,
    `frame.loc[mask, col] = ...` — touches every row whatever the group being
    selected, which is exactly the regression: doing it once per ISIN is
    O(isins x rows). `groupby` touches every row too, but once for all of them,
    so it is counted separately and pinned to a constant.
    """
    calls: dict[str, int] = {"mask": 0, "groupby": 0}

    def _is_mask(key) -> bool:
        return (isinstance(key, (pd.Series, np.ndarray))
                and getattr(key, "dtype", None) == bool)

    real_getitem = pd.DataFrame.__getitem__
    real_groupby = pd.DataFrame.groupby
    from pandas.core.indexing import _LocIndexer
    real_loc_get, real_loc_set = _LocIndexer.__getitem__, _LocIndexer.__setitem__

    def counting_getitem(self, key):
        if _is_mask(key):
            calls["mask"] += 1
        return real_getitem(self, key)

    def counting_groupby(self, *a, **kw):
        calls["groupby"] += 1
        return real_groupby(self, *a, **kw)

    def counting_loc_get(self, key):
        if _is_mask(key) or (isinstance(key, tuple) and any(_is_mask(k) for k in key)):
            calls["mask"] += 1
        return real_loc_get(self, key)

    def counting_loc_set(self, key, value):
        if _is_mask(key) or (isinstance(key, tuple) and any(_is_mask(k) for k in key)):
            calls["mask"] += 1
        return real_loc_set(self, key, value)

    monkeypatch.setattr(pd.DataFrame, "__getitem__", counting_getitem)
    monkeypatch.setattr(pd.DataFrame, "groupby", counting_groupby)
    monkeypatch.setattr(_LocIndexer, "__getitem__", counting_loc_get)
    monkeypatch.setattr(_LocIndexer, "__setitem__", counting_loc_set)
    return calls


_N_ROWS, _N_ISIN = 6_000, 300
_ISINS = [f"BR{i:09d}" for i in range(_N_ISIN)]


def _big_prices() -> pd.DataFrame:
    dates = pd.date_range("2015-01-01", periods=_N_ROWS // _N_ISIN, freq="B")
    return pd.DataFrame({
        "isin_code": np.repeat(_ISINS, len(dates)),
        "date": np.tile([d.strftime("%Y-%m-%d") for d in dates], _N_ISIN),
        "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.0,
        "split_adj_close": 10.0,
    })


def test_split_pass_does_not_rescan_the_price_table_per_isin(monkeypatch):
    """10x the ISINs with a split must not mean 10x the whole-table work.

    The old form built two full-length boolean masks per ISIN to find its rows
    and its splits, then wrote four OHLC columns back through the first — six
    whole-table passes for every corporate action, on the largest frame the
    pipeline holds.
    """
    prices = _big_prices()
    calls = _count_whole_frame_scans(monkeypatch)

    def work_for(k):
        calls.update(mask=0, groupby=0)
        splits = _splits([(_ISINS[i], "2018-06-01", 0.5) for i in range(k)])
        compute_split_adjustment_factors(prices, splits)
        return dict(calls)

    few, many = work_for(20), work_for(200)
    assert few == many, (
        f"10x the ISINs changed the whole-table work: {few} -> {many}"
        " — looks O(isins x rows) again")
    # Pin the absolute numbers, so a refactor that stops doing the work at all
    # cannot pass by making both sides equally zero.
    assert few == {"mask": 0, "groupby": 2}, few


def test_dividend_pass_does_not_rescan_the_price_table_per_isin(monkeypatch):
    prices = _big_prices()
    calls = _count_whole_frame_scans(monkeypatch)

    def work_for(k):
        calls.update(mask=0, groupby=0)
        actions = _actions([
            (_ISINS[i], "2018-06-01", config.EVENT_TYPE_CASH_DIVIDEND, 0.1)
            for i in range(k)])
        compute_dividend_adjustment_factors(prices, actions)
        return dict(calls)

    few, many = work_for(20), work_for(200)
    assert few == many, (
        f"10x the ISINs changed the whole-table work: {few} -> {many}"
        " — looks O(isins x rows) again")
    # One mask: the isin() filter that picks dividend events out of all actions,
    # which is over the action table and runs once.
    assert few == {"mask": 1, "groupby": 2}, few


def test_neither_pass_builds_a_series_per_dividend_row(monkeypatch):
    """`iterrows` materialises a pandas Series per row — the object churn the
    whole plane's ingest pays for. The per-event loop reads plain arrays now."""
    def refuse(self):
        raise AssertionError("iterrows in the adjustment hot path")

    monkeypatch.setattr(pd.DataFrame, "iterrows", refuse)
    prices = _split_adjusted(_prices([
        ("BRAAAAAAAAA0", "2020-01-14", 100, 100, 100, 100),
        ("BRAAAAAAAAA0", "2020-01-15", 99, 99, 99, 99),
    ]))
    out = compute_dividend_adjustment_factors(
        prices,
        _actions([("BRAAAAAAAAA0", "2020-01-15",
                   config.EVENT_TYPE_CASH_DIVIDEND, 1.0)]))
    assert out["adj_close"].iloc[0] == pytest.approx(99.0)


# ── the two passes together, on a frame with every shape of input ────────────

def test_split_then_dividend_over_a_mixed_plane():
    """A regression net over both functions at once: several ISINs, unsorted
    rows, an ISIN with no events, an event for an ISIN with no bars."""
    prices = _prices([
        ("BRAAAAAAAAA0", "2020-03-02", 50, 51, 49, 50),
        ("BRAAAAAAAAA0", "2020-01-02", 100, 102, 98, 100),
        ("BRAAAAAAAAA0", "2020-02-03", 100, 101, 99, 100),
        ("BRBBBBBBBBB1", "2020-01-02", 20, 21, 19, 20),
        ("BRBBBBBBBBB1", "2020-02-03", 20, 21, 19, 20),
        ("BRCCCCCCCCC2", "2020-01-02", 7, 7, 7, 7),
    ])
    splits = _splits([
        ("BRAAAAAAAAA0", "2020-03-02", 0.5),
        ("BRZZZZZZZZZ9", "2020-03-02", 0.5),   # no bars for this ISIN
    ])
    adj = compute_split_adjustment_factors(prices, splits)
    # A's pre-split bars halve; the ex-date bar and every other ISIN stand.
    assert list(adj["split_adj_close"]) == [50.0, 50.0, 50.0, 20.0, 20.0, 7.0]

    actions = _actions([
        ("BRBBBBBBBBB1", "2020-02-03", config.EVENT_TYPE_CASH_DIVIDEND, 2.0),
        ("BRCCCCCCCCC2", "2020-02-03", config.EVENT_TYPE_STOCK_SPLIT, 2.0),
    ])
    out = compute_dividend_adjustment_factors(adj, actions)
    # B: prev_close 20, factor 0.9, applied to the 2 Jan bar only.
    assert list(out["adj_close"]) == pytest.approx(
        [50.0, 50.0, 50.0, 18.0, 20.0, 7.0])


def test_dividend_dates_that_do_not_parse_are_dropped_not_crashed():
    prices = _split_adjusted(_prices([
        ("BRAAAAAAAAA0", "2020-01-14", 100, 100, 100, 100),
        ("BRAAAAAAAAA0", "2020-01-15", 99, 99, 99, 99),
    ]))
    out = compute_dividend_adjustment_factors(
        prices,
        _actions([("BRAAAAAAAAA0", "nonsense",
                   config.EVENT_TYPE_CASH_DIVIDEND, 1.0)]))
    assert list(out["adj_close"]) == [100.0, 99.0]


def test_date_objects_and_strings_are_both_accepted():
    prices = _prices([
        ("BRAAAAAAAAA0", dt.date(2020, 1, 14), 100, 100, 100, 100),
        ("BRAAAAAAAAA0", dt.date(2020, 1, 15), 50, 50, 50, 50),
    ])
    out = compute_split_adjustment_factors(
        prices, _splits([("BRAAAAAAAAA0", dt.date(2020, 1, 15), 0.5)]))
    assert list(out["split_adj_close"]) == [50.0, 50.0]


def test_the_raw_ohlc_columns_are_not_aliased_by_the_adjusted_ones():
    """The split pass now writes into numpy arrays taken off the frame. If one of
    those arrays were a *view* of `open`, adjusting would silently rewrite the raw
    price the dividend pass then divides by."""
    prices = _prices([
        ("BRAAAAAAAAA0", "2020-01-14", 100, 101, 99, 100),
        ("BRAAAAAAAAA0", "2020-01-16", 50, 51, 49, 50),
    ])
    out = compute_split_adjustment_factors(
        prices, _splits([("BRAAAAAAAAA0", "2020-01-15", 0.5)]))
    assert list(out["open"]) == [100.0, 50.0]
    assert list(out["high"]) == [101.0, 51.0]
    assert list(out["low"]) == [99.0, 49.0]
    assert list(out["close"]) == [100.0, 50.0]
    assert list(out["split_adj_open"]) == [50.0, 50.0]


def test_the_dividend_suffix_product_is_bit_for_bit_the_backward_sweep():
    """The sweep the vectorised form replaces multiplied the factors right to
    left, one bar at a time. `cumprod` over the reversed factors does the same
    multiplications in the same order, so this is `==`, not `approx`."""
    n = 12
    days = pd.date_range("2020-01-01", periods=n * 2, freq="D")
    prices = _split_adjusted(_prices([
        ("BRAAAAAAAAA0", d.strftime("%Y-%m-%d"), 100, 100, 100, 100)
        for d in days
    ]))
    # A dividend every other day, each an amount that does not divide evenly.
    amounts = [0.37 + i * 0.11 for i in range(n)]
    actions = _actions([
        ("BRAAAAAAAAA0", days[2 * i + 1].strftime("%Y-%m-%d"),
         config.EVENT_TYPE_CASH_DIVIDEND, a)
        for i, a in enumerate(amounts)
    ])
    out = compute_dividend_adjustment_factors(prices, actions)

    # The reference: the first bar precedes every ex-date, so its cumulative is
    # the product of all twelve factors, accumulated newest-first exactly as the
    # old `last_cumulative *= ...` loop did.
    factors = [1.0 - (a / 100.0) for a in amounts]
    cumulative = 1.0
    for f in reversed(factors):
        cumulative *= f
    assert out["adj_close"].iloc[0] == 100.0 * cumulative
