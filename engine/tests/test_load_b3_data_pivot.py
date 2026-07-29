"""
Unit tests for the pivot+ffill logic inside load_b3_data in backtests/core/data.py.

Safety-net tests for the Rust rewrite in task-03. These tests must pass
before and after the Rust implementation is introduced.
"""
import sqlite3
from pathlib import Path

import pandas as pd

from b3_pipeline import storage
from backtests.core.data import load_b3_data


# ── Fixture helpers (pattern from test_survivorship_filter.py) ─────────────────

def _setup_db(tmp_path: Path) -> str:
    """Create a temp DB with full schema. Returns db_path string."""
    db_path = str(tmp_path / "test.sqlite")
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = OFF")
    storage.init_db(conn)
    conn.commit()
    conn.close()
    return db_path


def _insert_price(conn, ticker, date_str, close=10.0, adj_close=None, volume=100_000):
    if adj_close is None:
        adj_close = close
    conn.execute(
        """INSERT INTO prices (ticker, isin_code, date, open, high, low, close,
                               adj_close, split_adj_high, split_adj_low, split_adj_close, volume)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (ticker, "UNKNOWN", date_str, close, close, close, close,
         adj_close, close, close, close, volume)
    )
    conn.commit()


# ── Tests ──────────────────────────────────────────────────────────────────────

class TestLoadB3DataPivot:

    def test_returns_three_dataframes(self, tmp_path):
        """load_b3_data returns a 3-tuple of DataFrames."""
        db_path = _setup_db(tmp_path)
        conn = sqlite3.connect(db_path)
        for ticker in ["PETR3", "VALE3"]:
            for date_str in ["2020-01-02", "2020-01-03", "2020-01-06"]:
                _insert_price(conn, ticker, date_str)
        conn.close()

        result = load_b3_data(db_path, "2020-01-01", "2020-01-31")
        assert isinstance(result, tuple)
        assert len(result) == 3
        adj_close, close_px, fin_vol = result
        assert isinstance(adj_close, pd.DataFrame)
        assert isinstance(close_px, pd.DataFrame)
        assert isinstance(fin_vol, pd.DataFrame)

    def test_wide_shape_dates_are_index_tickers_are_columns(self, tmp_path):
        """Output DataFrames have dates as index and tickers as columns."""
        db_path = _setup_db(tmp_path)
        conn = sqlite3.connect(db_path)
        for ticker in ["PETR3", "VALE3"]:
            for date_str in ["2020-01-02", "2020-01-03"]:
                _insert_price(conn, ticker, date_str)
        conn.close()

        adj_close, close_px, fin_vol = load_b3_data(db_path, "2020-01-01", "2020-01-31")

        # Index should contain the two dates as datetime64 (resolution-agnostic:
        # pandas 3 pivots to [us]/[s]; the instants are identical)
        assert pd.Timestamp("2020-01-02") in adj_close.index
        assert pd.Timestamp("2020-01-03") in adj_close.index
        assert pd.api.types.is_datetime64_any_dtype(adj_close.index)

        # Columns should contain both tickers
        assert "PETR3" in adj_close.columns
        assert "VALE3" in adj_close.columns

    def test_fin_vol_in_reais(self, tmp_path):
        """fin_vol equals the DB volume as-is (stored in reais by the parser)."""
        db_path = _setup_db(tmp_path)
        conn = sqlite3.connect(db_path)
        _insert_price(conn, "PETR3", "2020-01-02", volume=100_000)
        conn.close()

        adj_close, close_px, fin_vol = load_b3_data(db_path, "2020-01-01", "2020-01-31")

        assert "PETR3" in fin_vol.columns
        val = fin_vol.loc[pd.Timestamp("2020-01-02"), "PETR3"]
        assert abs(val - 100_000.0) < 1e-9

    def test_forward_fill_closes_gaps(self, tmp_path):
        """adj_close and close_px are forward-filled across date gaps."""
        db_path = _setup_db(tmp_path)
        conn = sqlite3.connect(db_path)
        # PETR3 only has 2020-01-02; VALE3 has both dates so both appear in index
        _insert_price(conn, "PETR3", "2020-01-02", close=100.0, adj_close=100.0)
        _insert_price(conn, "VALE3", "2020-01-02", close=200.0, adj_close=200.0)
        _insert_price(conn, "VALE3", "2020-01-03", close=201.0, adj_close=201.0)
        conn.close()

        adj_close, close_px, fin_vol = load_b3_data(db_path, "2020-01-01", "2020-01-31")

        # PETR3 has no row on 2020-01-03 — should be filled from 2020-01-02
        assert abs(adj_close.loc[pd.Timestamp("2020-01-03"), "PETR3"] - 100.0) < 1e-9
        assert abs(close_px.loc[pd.Timestamp("2020-01-03"), "PETR3"] - 100.0) < 1e-9

    def test_fin_vol_not_forward_filled(self, tmp_path):
        """fin_vol is NOT forward-filled; gaps remain NaN."""
        db_path = _setup_db(tmp_path)
        conn = sqlite3.connect(db_path)
        # PETR3 only on 2020-01-02; VALE3 on both dates so 2020-01-03 appears
        _insert_price(conn, "PETR3", "2020-01-02", volume=100_000)
        _insert_price(conn, "VALE3", "2020-01-02", volume=200_000)
        _insert_price(conn, "VALE3", "2020-01-03", volume=200_000)
        conn.close()

        adj_close, close_px, fin_vol = load_b3_data(db_path, "2020-01-01", "2020-01-31")

        val = fin_vol.loc[pd.Timestamp("2020-01-03"), "PETR3"]
        assert pd.isna(val), f"Expected NaN, got {val}"

    def test_non_standard_tickers_excluded(self, tmp_path):
        """Non-standard tickers (BDRs like PETR34, unknown suffix) are excluded."""
        db_path = _setup_db(tmp_path)
        conn = sqlite3.connect(db_path)
        _insert_price(conn, "PETR3", "2020-01-02")   # standard — 5 chars, suffix 3
        _insert_price(conn, "PETR34", "2020-01-02")  # BDR — 6 chars, suffix not "11"
        _insert_price(conn, "PETRX", "2020-01-02")   # 5 chars, suffix X — non-standard
        conn.close()

        adj_close, close_px, fin_vol = load_b3_data(db_path, "2020-01-01", "2020-01-31")

        assert "PETR3" in adj_close.columns
        assert "PETR34" not in adj_close.columns
        assert "PETRX" not in adj_close.columns

    def test_ticker_filter_accepts_suffix_11(self, tmp_path):
        """Tickers like BOVA11 (6 chars, suffix 11) are included."""
        db_path = _setup_db(tmp_path)
        conn = sqlite3.connect(db_path)
        _insert_price(conn, "BOVA11", "2020-01-02")
        conn.close()

        adj_close, close_px, fin_vol = load_b3_data(db_path, "2020-01-01", "2020-01-31")

        assert "BOVA11" in adj_close.columns

    def test_empty_db_returns_empty_dataframes(self, tmp_path):
        """With no price rows in the date range, all three DataFrames are empty."""
        db_path = _setup_db(tmp_path)
        # No rows inserted

        adj_close, close_px, fin_vol = load_b3_data(db_path, "2020-01-01", "2020-01-31")

        assert adj_close.empty
        assert close_px.empty
        assert fin_vol.empty


# ── One reshape, not three (load_b3_data) or six (load_b3_hlc_data) ──────────
# Both loaders used to call df.pivot() once per value column, reshaping the same
# rows over and over. Collapsing that into a single multi-value pivot has to
# leave every frame — values, dtypes, index and column labels — untouched.

def _pivot_by_hand(df: pd.DataFrame, values: list[str]) -> dict:
    return {v: df.pivot(index="date", columns="ticker", values=v) for v in values}


class TestSingleMultiValuePivot:
    def _frame(self):
        rows = []
        for i, d in enumerate(["2024-01-02", "2024-01-03", "2024-01-04"]):
            for t, base in (("PETR4", 30.0), ("VALE3", 60.0), ("HGLG11", 150.0)):
                rows.append({
                    "date": pd.Timestamp(d), "ticker": t,
                    "adj_close": base + i, "close": base + i * 2,
                    "split_adj_high": base + 3, "split_adj_low": base - 3,
                    "split_adj_close": base, "fin_volume": 1e6 * (i + 1),
                })
        # A hole, so the ffill paths are exercised rather than a dense grid.
        return pd.DataFrame(rows).drop(index=4).reset_index(drop=True)

    def test_multi_value_pivot_matches_per_value_pivots(self):
        df = self._frame()
        cols = ["adj_close", "close", "split_adj_high", "split_adj_low",
                "split_adj_close", "fin_volume"]
        one = df.pivot(index="date", columns="ticker", values=cols)
        by_hand = _pivot_by_hand(df, cols)
        for c in cols:
            pd.testing.assert_frame_equal(one[c], by_hand[c])

    def test_sliced_frames_keep_the_ticker_column_name(self):
        # Downstream code reads .columns as plain ticker labels; a leftover
        # MultiIndex level here would silently break every strategy's join.
        df = self._frame()
        one = df.pivot(index="date", columns="ticker", values=["adj_close", "close"])
        assert one["adj_close"].columns.name == "ticker"
        assert not isinstance(one["adj_close"].columns, pd.MultiIndex)


class TestHlcLoaderShape:
    """load_b3_hlc_data over a seeded DB: the six frames it returns must line up
    on one index and one ticker set, whichever way the reshape is done."""

    def test_hlc_frames_are_mutually_aligned(self, tmp_path):
        from backtests.core.data import load_b3_hlc_data

        db_path = _setup_db(tmp_path)
        conn = sqlite3.connect(db_path)
        for d in ("2024-01-02", "2024-01-03", "2024-01-04"):
            _insert_price(conn, "PETR4", d, close=30.0)
            _insert_price(conn, "VALE3", d, close=60.0)
        conn.close()

        frames = load_b3_hlc_data(db_path, "2024-01-01", "2024-12-31")
        assert len(frames) == 6
        first = frames[0]
        for f in frames[1:]:
            assert list(f.index) == list(first.index)
            assert list(f.columns) == list(first.columns)
            assert f.columns.name == "ticker"
