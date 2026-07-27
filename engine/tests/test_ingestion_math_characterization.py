"""Pins the numbers the ingestion math produces, independent of implementation.

Split detection and split adjustment decide what every historical price in the
database is worth. They used to have two implementations — a Python one and a
Rust extension that was tried first and silently fell back — and no shipped path
ever reached the Rust half: the image excluded the crate outright, CI installs
from requirements-bench.txt, and every production call resolves the interpreter
to engine/.venv (3.12), which never had it.

(A locally-built cotahist_rs could still be importable under an ambient
interpreter, so "the parity tests always skipped" was too strong a claim — on a
dev box that ran `maturin develop` under a different python, they ran. That
changes nothing about which code shipped, which is what these tests pin.)

Removing the unused half is only safe if the surviving numbers are provably
unchanged. These assertions are written against the *results*, not the code
path, so they held before the removal and hold after it. Hand-computed
expectations, not golden files: a golden file re-recorded after a regression
just enshrines the regression.
"""
from __future__ import annotations

import datetime
import sqlite3

import numpy as np
import pandas as pd
import pytest

from b3_pipeline import storage
from b3_pipeline.adjustments import (
    compute_split_adjustment_factors,
    detect_splits_from_prices,
)
from backtests.core.data import load_b3_data

NO_EXISTING = pd.DataFrame(columns=["isin_code", "ex_date", "action_type", "factor"])


def _splits(rows: list[tuple]) -> pd.DataFrame:
    """The adjustment API's splits frame. `split_factor` is old_shares/new_shares,
    so a 2:1 split is 0.5 and pre-split prices are multiplied by it."""
    return pd.DataFrame(rows, columns=["isin_code", "ex_date", "split_factor"])


def _series(isin: str, start: str, closes: list[float]) -> pd.DataFrame:
    dates = pd.bdate_range(start, periods=len(closes))
    return pd.DataFrame({
        "isin_code": isin,
        "ticker": "TEST4",
        "date": [d.date() for d in dates],
        "open": closes,
        "high": [c * 1.01 for c in closes],
        "low": [c * 0.99 for c in closes],
        "close": closes,
        "volume": [1_000_000.0] * len(closes),
    })


class TestSplitDetection:
    def test_a_clean_two_for_one_halving_is_detected_with_factor_two(self):
        # 20 -> 10 overnight is the textbook 2:1 split.
        prices = _series("BRTESTACNOR1", "2024-01-01", [20.0] * 5 + [10.0] * 5)

        found = detect_splits_from_prices(prices, NO_EXISTING)

        assert len(found) == 1, f"expected exactly one split, got:\n{found}"
        row = found.iloc[0]
        assert row["isin_code"] == "BRTESTACNOR1"
        assert row["factor"] == pytest.approx(2.0)
        assert row["ex_date"] == datetime.date(2024, 1, 8)

    def test_a_ten_for_one_is_detected_at_factor_ten(self):
        prices = _series("BRTESTACNOR2", "2024-03-01", [100.0] * 4 + [10.0] * 4)

        found = detect_splits_from_prices(prices, NO_EXISTING)

        assert len(found) == 1
        assert found.iloc[0]["factor"] == pytest.approx(10.0)

    def test_a_reverse_split_is_detected_as_a_fractional_factor(self):
        # 1:10 grouping — price jumps 10x.
        prices = _series("BRTESTACNOR3", "2024-05-01", [2.0] * 4 + [20.0] * 4)

        found = detect_splits_from_prices(prices, NO_EXISTING)

        assert len(found) == 1
        assert found.iloc[0]["factor"] == pytest.approx(0.1)

    def test_ordinary_volatility_is_not_a_split(self):
        """The detector must not fire on a bad day, or every crash becomes a
        corporate action and every historical price is silently rescaled."""
        prices = _series(
            "BRTESTACNOR4", "2024-07-01",
            [20.0, 19.1, 20.4, 18.7, 19.9, 21.2, 20.1, 19.4],
        )

        assert detect_splits_from_prices(prices, NO_EXISTING).empty

    def test_an_already_recorded_split_is_not_reported_again(self):
        """B3 is the authority; re-detecting what it already told us would
        double-apply the factor."""
        prices = _series("BRTESTACNOR5", "2024-09-02", [30.0] * 4 + [10.0] * 4)
        existing = pd.DataFrame([{
            "isin_code": "BRTESTACNOR5",
            "ex_date": datetime.date(2024, 9, 6),
            "action_type": "SPLIT",
            "factor": 3.0,
        }])

        assert detect_splits_from_prices(prices, existing).empty

    def test_empty_input_returns_the_empty_contract_not_a_crash(self):
        out = detect_splits_from_prices(
            pd.DataFrame(columns=["isin_code", "date", "close"]), NO_EXISTING
        )
        assert out.empty
        assert list(out.columns) == [
            "isin_code", "ex_date", "action_type", "factor", "source",
        ]


class TestSplitAdjustment:
    def test_pre_split_prices_are_divided_and_post_split_left_alone(self):
        """Backward-cumulative adjustment: history is restated into today's
        share count, so only rows *before* the ex-date move."""
        prices = _series("BRTESTACNOR6", "2024-01-01", [20.0] * 5 + [10.0] * 5)
        splits = _splits([("BRTESTACNOR6", datetime.date(2024, 1, 8), 0.5)])

        out = compute_split_adjustment_factors(prices, splits)

        adj = out["split_adj_close"].tolist()
        assert adj[:5] == pytest.approx([10.0] * 5), "pre-split history not halved"
        assert adj[5:] == pytest.approx([10.0] * 5), "post-split prices were touched"

    def test_ohlc_all_move_together(self):
        prices = _series("BRTESTACNOR7", "2024-01-01", [20.0] * 5 + [10.0] * 5)
        splits = _splits([("BRTESTACNOR7", datetime.date(2024, 1, 8), 0.5)])

        out = compute_split_adjustment_factors(prices, splits)

        first = out.iloc[0]
        assert first["split_adj_open"] == pytest.approx(10.0)
        assert first["split_adj_high"] == pytest.approx(20.0 * 1.01 / 2)
        assert first["split_adj_low"] == pytest.approx(20.0 * 0.99 / 2)
        assert first["split_adj_close"] == pytest.approx(10.0)

    def test_two_splits_compound_backwards(self):
        """A 2:1 then a 5:1 means the earliest history is divided by 10."""
        prices = _series("BRTESTACNOR8", "2024-01-01", [100.0] * 12)
        splits = _splits([
            ("BRTESTACNOR8", datetime.date(2024, 1, 5), 0.5),   # 2:1
            ("BRTESTACNOR8", datetime.date(2024, 1, 11), 0.2),  # 5:1
        ])

        out = compute_split_adjustment_factors(prices, splits)
        by_date = dict(zip(out["date"], out["split_adj_close"]))

        assert by_date[datetime.date(2024, 1, 1)] == pytest.approx(10.0)   # /2 then /5
        assert by_date[datetime.date(2024, 1, 8)] == pytest.approx(20.0)   # /5 only
        assert by_date[datetime.date(2024, 1, 12)] == pytest.approx(100.0)  # untouched

    def test_no_splits_leaves_every_price_untouched(self):
        prices = _series("BRTESTACNOR9", "2024-01-01", [7.5, 7.6, 7.4, 7.7])

        out = compute_split_adjustment_factors(prices, _splits([]))

        assert out["split_adj_close"].tolist() == pytest.approx([7.5, 7.6, 7.4, 7.7])

    def test_one_companys_split_never_touches_another(self):
        a = _series("BRAAAAACNOR1", "2024-01-01", [20.0] * 6)
        b = _series("BRBBBBBCNOR1", "2024-01-01", [50.0] * 6)
        prices = pd.concat([a, b], ignore_index=True)
        splits = _splits([("BRAAAAACNOR1", datetime.date(2024, 1, 4), 0.5)])

        out = compute_split_adjustment_factors(prices, splits)
        other = out[out["isin_code"] == "BRBBBBBCNOR1"]["split_adj_close"]

        assert other.tolist() == pytest.approx([50.0] * 6), "B was rescaled by A's split"


class TestWidePivot:
    """The pivot inside load_b3_data — the third function whose Rust branch went.

    Drives the real loader over a temp SQLite DB (same fixture shape as
    test_load_b3_data_pivot.py). An earlier version of this test re-implemented
    the pivot inline and asserted on that, which tested pandas rather than
    data.py: deleting the .ffill() from load_b3_data left it green.
    """

    @staticmethod
    def _db_with(tmp_path, rows) -> str:
        db_path = str(tmp_path / "pivot.sqlite")
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA foreign_keys = OFF")
        storage.init_db(conn)
        for ticker, date_str, close, volume in rows:
            conn.execute(
                """INSERT INTO prices (ticker, isin_code, date, open, high, low,
                                       close, adj_close, split_adj_high,
                                       split_adj_low, split_adj_close, volume)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (ticker, "UNKNOWN", date_str, close, close, close, close,
                 close, close, close, close, volume),
            )
        conn.commit()
        conn.close()
        return db_path

    def test_pivot_forward_fills_prices_but_never_volume(self, tmp_path):
        # BBBB4 does not trade on the 2nd — the gap is the whole point.
        db_path = self._db_with(tmp_path, [
            ("AAAA3", "2024-01-01", 10.0, 100_000),
            ("BBBB4", "2024-01-01", 20.0, 200_000),
            ("AAAA3", "2024-01-02", 11.0, 110_000),
            ("AAAA3", "2024-01-03", 12.0, 120_000),
            ("BBBB4", "2024-01-03", 22.0, 220_000),
        ])

        adj_close, close_px, fin_vol = load_b3_data(db_path, "2024-01-01", "2024-01-31")

        gap = pd.Timestamp("2024-01-02")
        assert adj_close.loc[gap, "BBBB4"] == pytest.approx(20.0), (
            "a price must carry across a non-trading day, or every gap reads as a "
            "crash to the return series"
        )
        assert close_px.loc[gap, "BBBB4"] == pytest.approx(20.0)
        assert np.isnan(fin_vol.loc[gap, "BBBB4"]), (
            "a non-trading day must stay NaN in volume, or ADTV averages are wrong "
            "and illiquid names pass the liquidity filter"
        )

    def test_traded_days_keep_their_own_values(self, tmp_path):
        """The ffill must not overwrite a real observation."""
        db_path = self._db_with(tmp_path, [
            ("AAAA3", "2024-01-01", 10.0, 100_000),
            ("AAAA3", "2024-01-02", 11.0, 110_000),
            ("AAAA3", "2024-01-03", 12.0, 120_000),
        ])

        adj_close, _, fin_vol = load_b3_data(db_path, "2024-01-01", "2024-01-31")

        assert adj_close["AAAA3"].tolist() == pytest.approx([10.0, 11.0, 12.0])
        # volume is stored in reais already; the loader passes it through
        assert fin_vol["AAAA3"].notna().all()
