"""The price load, which is the largest thing a backtest does.

Two changes live under here, and both are supposed to be invisible in the
numbers: `_pivot_wide` scatters one value column at a time instead of reshaping
all six at once (so the long frame and the wide frames are never both whole),
and `load_market_panels` reads only the panels its caller names (so the daily
high and low are not built for a run that never looks at them).

"Invisible" is the thing worth testing, so most of this file is equality against
what `df.pivot` produces — the implementation these replaced.
"""
from __future__ import annotations

import sqlite3

import numpy as np
import pandas as pd
import pytest

from b3_pipeline import storage
from backtests.core import panels as loader


def _long(rows: list[tuple]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["date", "ticker", "a", "b"])


ROWS = [
    ("2020-01-03", "VALE3", 3.0, 30.0),
    ("2020-01-02", "PETR4", 1.0, 10.0),
    ("2020-01-03", "PETR4", 2.0, 20.0),
    ("2020-01-02", "VALE3", 4.0, 40.0),
]


def test_the_scatter_is_what_pivot_produced():
    """Values, dtypes, and the sorted index and columns pivot gives — the whole
    contract of the function this replaced."""
    reference = _long(ROWS)
    reference["date"] = pd.to_datetime(reference["date"])
    wide = reference.pivot(index="date", columns="ticker", values=["a", "b"])

    got = loader._pivot_wide(loader._normalise_long(_long(ROWS)), ["a", "b"])
    for frame, name in zip(got, ["a", "b"]):
        pd.testing.assert_frame_equal(frame, wide[name])


def test_a_gap_comes_back_as_nan_not_as_a_missing_row():
    got, = loader._pivot_wide(
        loader._normalise_long(_long(ROWS[:3])), ["a"])
    assert got.shape == (2, 2)
    assert np.isnan(got.loc["2020-01-02", "VALE3"])


def test_the_long_frame_is_given_back_as_it_is_consumed():
    """The point of `pop`: the long frame shrinks by exactly what the wide frames
    grow, so the two are never both whole. On the US plane that is the difference
    between ~6 GB and ~2.5 GB at the peak."""
    df = loader._normalise_long(_long(ROWS))
    loader._pivot_wide(df, ["a", "b"])
    assert "a" not in df.columns and "b" not in df.columns


def test_a_repeated_date_ticker_pair_is_refused_not_silently_overwritten():
    """`pivot` raises on this; a scatter would keep whichever row landed last and
    hand the backtest a price nobody can trace."""
    with pytest.raises(ValueError, match="duplicate"):
        loader._pivot_wide(
            loader._normalise_long(_long(ROWS + [ROWS[0]])), ["a"])


def test_an_empty_plane_still_returns_one_empty_frame_per_value():
    """A fresh database with no prices is a real state — it is what CI runs
    against."""
    got = loader._pivot_wide(_long([]), ["a", "b"])
    assert len(got) == 2
    assert all(frame.empty for frame in got)


def test_the_symbol_column_is_collapsed_before_the_reshape():
    """One shared object per distinct symbol instead of one per row. On the US
    plane's ~30M bars that column alone was a couple of gigabytes."""
    df = loader._normalise_long(_long(ROWS))
    assert isinstance(df["ticker"].dtype, pd.CategoricalDtype)
    assert pd.api.types.is_datetime64_any_dtype(df["date"])


def test_normalising_twice_is_a_no_op():
    once = loader._normalise_long(_long(ROWS))
    twice = loader._normalise_long(once)
    assert isinstance(twice["ticker"].dtype, pd.CategoricalDtype)


# ── load_market_panels, against a real sqlite plane ──────────────────────────

DAYS = ["2020-01-02", "2020-01-03", "2020-01-06"]


@pytest.fixture
def plane(tmp_path) -> str:
    db = str(tmp_path / "plane.sqlite")
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA foreign_keys = OFF")
    storage.init_db(conn)
    for i, ticker in enumerate(("PETR4", "VALE3")):
        for j, day in enumerate(DAYS):
            px = 10.0 + i * 100 + j
            conn.execute(
                "INSERT INTO prices (ticker, isin_code, date, open, high, low,"
                " close, adj_close, split_adj_high, split_adj_low,"
                " split_adj_close, volume) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (ticker, "UNKNOWN", day, px, px * 1.1, px * 0.9, px, px * 2,
                 px * 1.1, px * 0.9, px, 1_000.0 * (j + 1)))
    conn.commit()
    conn.close()
    return db


def test_naming_the_panels_keeps_the_rest_out_of_the_query(plane):
    panels = loader.load_market_panels(plane, "2020-01-01", "2020-01-31", "B3",
                                     panels=("adj_close", "fin_volume"))
    assert set(panels) == {"adj_close", "fin_volume"}
    assert panels["adj_close"].loc["2020-01-02", "PETR4"] == 20.0
    assert panels["fin_volume"].loc["2020-01-06", "VALE3"] == 3_000.0


def test_the_base_set_leaves_out_exactly_the_high_and_the_low(plane):
    """The two panels whose only consumer is the mean-reversion high-low range."""
    assert set(loader.BASE_PANELS) == set(loader.HLC_PANELS) - {
        "split_adj_high", "split_adj_low"}
    panels = loader.load_market_panels(plane, "2020-01-01", "2020-01-31", "B3",
                                     panels=loader.BASE_PANELS)
    assert set(panels) == set(loader.BASE_PANELS)


def test_asking_for_a_panel_that_does_not_exist_is_an_error(plane):
    with pytest.raises(ValueError, match="Unknown price panel"):
        loader.load_market_panels(plane, "2020-01-01", "2020-01-31", "B3",
                                panels=("adj_close", "vwap"))


def test_the_six_panel_tuple_is_unchanged(plane):
    """`load_market_hlc_data` is the older whole-panel API; every existing caller
    still gets the same six frames in the same order."""
    frames = loader.load_market_hlc_data(plane, "2020-01-01", "2020-01-31", "B3")
    assert len(frames) == 6
    panels = loader.load_market_panels(plane, "2020-01-01", "2020-01-31", "B3")
    for frame, name in zip(frames, loader.HLC_PANELS):
        pd.testing.assert_frame_equal(frame, panels[name])


def test_turnover_is_not_forward_filled_but_prices_are(plane):
    """A day a name did not trade must average in as missing, not as a repeat of
    the last session's turnover — `_ffill_in_place` fills a prefix for this."""
    conn = sqlite3.connect(plane)
    conn.execute("DELETE FROM prices WHERE ticker = 'PETR4' AND date = ?",
                 ("2020-01-03",))
    conn.commit()
    conn.close()

    panels = loader.load_market_panels(plane, "2020-01-01", "2020-01-31", "B3")
    assert panels["adj_close"].loc["2020-01-03", "PETR4"] == 20.0   # filled
    assert np.isnan(panels["fin_volume"].loc["2020-01-03", "PETR4"])
