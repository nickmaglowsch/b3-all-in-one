"""load_all_fundamentals must scan fundamentals_pit and prices once, not per metric.

It used to call load_fundamentals_pit in a loop over eight metrics, and each of
those ran its own full fundamentals_pit query *and* its own
`SELECT DISTINCT date FROM prices` — sixteen scans of two large tables to build
one dict, on every backtest that needs fundamentals.

These tests pin both halves: the batched frames must equal the per-metric ones
exactly, and the number of round-trips must stay bounded.
"""
from __future__ import annotations

import sqlite3

import pandas as pd
import pytest

from b3_pipeline import storage

METRICS = ["revenue", "net_income", "ebitda", "total_assets",
           "equity", "net_debt", "shares_outstanding"]


@pytest.fixture
def seeded_db(tmp_path):
    """Prices Jan 2023 - Dec 2024 plus a few filings per company, including a
    restatement (two versions on one filing_date) and a metric that is NULL for
    one company — the two cases the per-metric WHERE clause used to handle."""
    db_path = str(tmp_path / "fund.sqlite")
    conn = sqlite3.connect(db_path)
    storage.init_db(conn)

    for dt in pd.date_range("2023-01-01", "2024-12-31", freq="B"):
        for tk in ("PETR3", "VALE3", "ITUB4"):
            conn.execute(
                "INSERT OR REPLACE INTO prices (ticker, isin_code, date, open, high,"
                " low, close, volume) VALUES (?, 'UNKNOWN', ?, 30, 30, 30, 30, 1000)",
                (tk, dt.strftime("%Y-%m-%d")),
            )

    rows = [
        # filing_id, ticker, period_end, filing_date, version, revenue, net_income, ebitda
        ("f1", "PETR", "2022-12-31", "2023-01-31", 1, 1000.0, 100.0, 300.0),
        ("f2", "PETR", "2023-06-30", "2023-08-15", 1, 2000.0, 200.0, 600.0),
        # restatement: same filing_date, higher version wins
        ("f3", "PETR", "2023-06-30", "2023-08-15", 2, 2100.0, 210.0, 610.0),
        ("f4", "VALE", "2022-12-31", "2023-02-10", 1, 5000.0, None, 900.0),
        ("f5", "VALE", "2023-09-30", "2023-11-01", 1, 5500.0, 550.0, None),
        # filed after `end` in some windows — must be invisible then
        ("f6", "ITUB", "2024-09-30", "2024-11-20", 1, 7000.0, 700.0, 1200.0),
    ]
    for fid, tk, pe, fd, ver, rev, ni, ebitda in rows:
        conn.execute(
            "INSERT INTO fundamentals_pit (filing_id, cnpj, ticker, period_end,"
            " filing_date, filing_version, doc_type, revenue, net_income, ebitda,"
            " total_assets, equity, net_debt, shares_outstanding)"
            " VALUES (?, ?, ?, ?, ?, ?, 'ITR', ?, ?, ?, ?, ?, ?, ?)",
            (fid, "0" * 14, tk, pe, fd, ver, rev, ni, ebitda,
             None, 42.0, None, 1_000_000.0),
        )
    conn.commit()
    conn.close()
    return db_path


def test_batched_frames_equal_the_per_metric_ones(seeded_db):
    from backtests.core.data import load_all_fundamentals, load_fundamentals_pit

    batched = load_all_fundamentals(seeded_db, "2023-01-01", "2024-12-31", freq="ME")
    for metric in METRICS:
        one = load_fundamentals_pit(
            seeded_db, metric, "2023-01-01", "2024-12-31", freq="ME")
        pd.testing.assert_frame_equal(batched[metric], one, obj=f"metric={metric}")


def test_batched_frames_equal_per_metric_with_a_narrow_window(seeded_db):
    """`end` gates which filings are visible at all, so the window has to be
    applied to the shared query exactly as the per-metric one applied it."""
    from backtests.core.data import load_all_fundamentals, load_fundamentals_pit

    batched = load_all_fundamentals(seeded_db, "2023-01-01", "2023-09-30", freq="ME")
    for metric in METRICS:
        one = load_fundamentals_pit(
            seeded_db, metric, "2023-01-01", "2023-09-30", freq="ME")
        pd.testing.assert_frame_equal(batched[metric], one, obj=f"metric={metric}")


def test_a_null_metric_does_not_borrow_a_neighbours_row(seeded_db):
    """VALE's net_income is NULL on its 2023-02-10 filing and its ebitda is NULL
    on the 2023-11-01 one. Each metric carries its own last non-null filing —
    that is what the per-metric `AND {metric} IS NOT NULL` bought, and dropping
    it for one shared query must not lose it."""
    from backtests.core.data import load_all_fundamentals

    f = load_all_fundamentals(seeded_db, "2023-01-01", "2024-12-31", freq="ME")
    ni, ebitda = f["net_income"], f["ebitda"]
    # net_income first appears only with the November filing.
    assert ni.loc[: pd.Timestamp("2023-10-31"), "VALE"].isna().all()
    assert ni.loc[pd.Timestamp("2023-11-30"), "VALE"] == 550.0
    # ebitda is NULL in November, so it keeps February's 900 rather than blanking.
    assert ebitda.loc[pd.Timestamp("2023-11-30"), "VALE"] == 900.0


def test_restatement_takes_the_highest_version_on_a_shared_filing_date(seeded_db):
    from backtests.core.data import load_all_fundamentals

    f = load_all_fundamentals(seeded_db, "2023-01-01", "2024-12-31", freq="ME")
    assert f["revenue"].loc[pd.Timestamp("2023-08-31"), "PETR"] == 2100.0


def test_load_all_fundamentals_does_not_scan_per_metric(seeded_db, monkeypatch):
    """The regression this whole change is about: one pass over fundamentals_pit
    and one over prices, however many metrics are requested."""
    from backtests.core import data as bt_data

    seen: list[str] = []
    real = bt_data.storage.read_sql

    def counting(sql, conn, *a, **kw):
        seen.append(" ".join(sql.split()))
        return real(sql, conn, *a, **kw)

    monkeypatch.setattr(bt_data.storage, "read_sql", counting)
    bt_data.load_all_fundamentals(seeded_db, "2023-01-01", "2024-12-31", freq="ME")

    pit = [s for s in seen if "fundamentals_pit" in s]
    prices = [s for s in seen if "FROM prices" in s]
    assert len(pit) == 1, f"expected 1 fundamentals_pit scan, got {len(pit)}:\n" + "\n".join(pit)
    assert len(prices) == 1, f"expected 1 prices scan, got {len(prices)}:\n" + "\n".join(prices)


def test_rebalance_grid_query_does_not_enumerate_every_date(seeded_db, monkeypatch):
    """_get_rebalance_dates only ever used min and max, but asked for every
    distinct date in the table to get them."""
    from backtests.core import data as bt_data

    seen: list[str] = []
    real = bt_data.storage.read_sql

    def counting(sql, conn, *a, **kw):
        seen.append(" ".join(sql.split()).upper())
        return real(sql, conn, *a, **kw)

    monkeypatch.setattr(bt_data.storage, "read_sql", counting)
    bt_data._get_rebalance_dates(seeded_db, "2023-01-01", "2024-12-31", "ME")

    assert seen and "DISTINCT" not in seen[0], seen[0]
    assert "MIN(" in seen[0] and "MAX(" in seen[0], seen[0]


def test_rebalance_grid_is_unchanged(seeded_db):
    from backtests.core.data import _get_rebalance_dates

    grid = _get_rebalance_dates(seeded_db, "2023-01-01", "2024-12-31", "ME")
    # First business day 2023-01-02 .. last 2024-12-31 -> month ends between.
    assert grid[0] == pd.Timestamp("2023-01-31")
    assert grid[-1] == pd.Timestamp("2024-12-31")
    assert len(grid) == 24


def test_rebalance_grid_falls_back_when_there_are_no_prices(tmp_path):
    from backtests.core.data import _get_rebalance_dates

    db_path = str(tmp_path / "empty.sqlite")
    conn = sqlite3.connect(db_path)
    storage.init_db(conn)
    conn.commit()
    conn.close()

    grid = _get_rebalance_dates(db_path, "2023-01-01", "2023-06-30", "ME")
    assert list(grid) == list(pd.date_range("2023-01-01", "2023-06-30", freq="ME"))
