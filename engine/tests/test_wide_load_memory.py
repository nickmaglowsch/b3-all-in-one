"""The price load, which is the largest thing a backtest does.

The load is streamed: `stream_market_panels` walks the universe a few hundred
names at a time, and the bundle reduces each batch to the rebalance grid before
the next arrives, so the market's daily bars are never all resident. That only
works because every daily reduction in this repository is per ticker — and it is
only *correct* if a batch is exactly its slice of the whole-market frame.

That invariant is what most of this file tests, along with the pieces it rests
on: `_pivot_wide` scattering a value column at a time, `load_market_panels`
reading only the panels and symbols it is asked for, and the session index that
makes one batch's frame line up with another's.
"""
from __future__ import annotations

import sqlite3

import numpy as np
import pandas as pd
import pytest

from b3_pipeline import storage
from backtests.core import markets
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


# ── streaming: a batch is its slice of the market ────────────────────────────

def test_streamed_batches_concatenate_to_the_whole_market_load(plane):
    """The invariant the architecture rests on. If a batch were anything but its
    own columns of the whole-market frame — a different fill, a shorter index, a
    different order — then reducing batch by batch would quietly produce a
    different backtest from reducing the market."""
    whole = loader.load_market_panels(plane, "2020-01-01", "2020-01-31", "B3",
                                      quiet=True)
    parts: dict = {}
    for batch in loader.stream_market_panels(plane, "2020-01-01", "2020-01-31",
                                             "B3", size=1):
        for name, frame in batch.items():
            parts.setdefault(name, []).append(frame)

    assert len(parts["adj_close"]) == 2, "one batch per name, so this streamed"
    for name, frames in parts.items():
        got = pd.concat(frames, axis=1)
        pd.testing.assert_frame_equal(got, whole[name])


def test_a_batch_carries_the_markets_sessions_not_its_own(plane):
    """A subset only trades on some of the market's days. Reindexing to the
    market's calendar *before* the forward fill is what makes the fill bridge a
    gap the way it would have in the whole-market frame — and what makes two
    batches line up column-wise at all."""
    conn = sqlite3.connect(plane)
    conn.execute("DELETE FROM prices WHERE ticker = 'VALE3' AND date = ?",
                 ("2020-01-03",))
    conn.commit()
    conn.close()

    batches = list(loader.stream_market_panels(plane, "2020-01-01", "2020-01-31",
                                               "B3", size=1))
    assert [list(b["adj_close"].columns) for b in batches] == [["PETR4"], ["VALE3"]]
    for b in batches:
        assert list(b["adj_close"].index.strftime("%Y-%m-%d")) == DAYS
    # VALE3 did not print on the 3rd; the fill carries the 2nd across it exactly
    # as the whole-market frame does.
    vale = batches[1]["adj_close"]["VALE3"]
    assert vale.loc["2020-01-03"] == vale.loc["2020-01-02"]


def test_the_axes_are_the_market_and_respect_the_window(plane):
    assert loader.market_symbols(plane, "2020-01-01", "2020-01-31", "B3") == [
        "PETR4", "VALE3"]
    assert list(loader.market_sessions(plane, "2020-01-01", "2020-01-31", "B3")
                .strftime("%Y-%m-%d")) == DAYS
    assert loader.market_symbols(plane, "2019-01-01", "2019-12-31", "B3") == []


def test_the_shape_filter_reaches_the_symbol_scan(plane):
    """`market_symbols` decides the batches, so a name the panels would drop must
    not reach one — it would come back as a batch of nothing."""
    conn = sqlite3.connect(plane)
    conn.execute(
        "INSERT INTO prices (ticker, isin_code, date, open, high, low, close,"
        " adj_close, split_adj_high, split_adj_low, split_adj_close, volume)"
        " VALUES ('PETR34', 'UNKNOWN', '2020-01-02', 1,1,1,1,1,1,1,1,1)")
    conn.commit()
    conn.close()
    assert "PETR34" not in loader.market_symbols(
        plane, "2020-01-01", "2020-01-31", "B3"), "a BDR is not a standard lot"


def test_symbols_are_bound_as_values_never_interpolated(plane):
    """A symbol comes off the plane. The one place a market's own data must never
    reach is its query text."""
    spec = markets.get("B3")
    fragment, params = loader._symbol_filter(spec, ["PETR4", "VALE3"])
    assert "PETR4" not in fragment and "VALE3" not in fragment
    assert sorted(params.values()) == ["PETR4", "VALE3"]
    assert loader._symbol_filter(spec, None) == ("", {})
    assert loader._symbol_filter(spec, [])[0].strip() == "AND 1 = 0"


def test_asking_for_no_symbols_yields_an_empty_frame_not_a_syntax_error(plane):
    """`IN ()` is a syntax error in SQLite and a footgun in Postgres."""
    got = loader.load_market_panels(plane, "2020-01-01", "2020-01-31", "B3",
                                    panels=("adj_close",), symbols=[], quiet=True)
    assert got["adj_close"].empty


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
