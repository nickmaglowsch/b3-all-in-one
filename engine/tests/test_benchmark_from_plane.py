"""Benchmarks come off B3's own plane, not Yahoo.

`download_benchmark` used to scrape Yahoo Finance, which is both a licensing
problem for a paid product and a data-quality one (see the synthetic LFTB11 head
documented in b3_pipeline/external_prices.py). Every series it fetched is already
in the plane: the `.SA` sleeves are ordinary COTAHIST tickers, and the Ibovespa
is tracked by BOVA11, which has traded since 2008.

These tests pin the mapping and the window handling. Nothing here touches the
network — that is the point.
"""
import sqlite3
from pathlib import Path

import pandas as pd

from b3_pipeline import storage
from backtests.core import data as core_data
from backtests.core.data import download_benchmark


def _plane(tmp_path: Path) -> str:
    db_path = str(tmp_path / "plane.sqlite")
    conn = sqlite3.connect(db_path)
    storage.init_db(conn)
    conn.commit()
    conn.close()
    return db_path


def _insert(db_path: str, ticker: str, rows: list[tuple[str, float]]) -> None:
    """rows: [(date, adj_close), ...]. `close` is deliberately different from
    `adj_close` so a test can tell which column the reader picked."""
    conn = sqlite3.connect(db_path)
    for date_str, adj in rows:
        conn.execute(
            """INSERT INTO prices (ticker, isin_code, date, open, high, low,
                                   close, adj_close, split_adj_high,
                                   split_adj_low, split_adj_close, volume)
               VALUES (?, 'UNKNOWN', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (ticker, date_str, adj, adj, adj, adj * 0.5, adj, adj, adj, adj, 1000.0),
        )
    conn.commit()
    conn.close()


def _use(monkeypatch, db_path: str) -> None:
    """Point the default connection at the temp plane (conftest strips the real
    ENGINE_DATABASE_URL, so this is the whole resolution chain)."""
    monkeypatch.setenv("ENGINE_DATABASE_URL", db_path)


def test_bvsp_resolves_to_bova11(tmp_path, monkeypatch):
    """^BVSP is Yahoo's symbol for the Ibovespa; on the plane it is BOVA11."""
    db = _plane(tmp_path)
    _insert(db, "BOVA11", [("2024-01-02", 100.0), ("2024-01-03", 102.0)])
    _use(monkeypatch, db)

    px = download_benchmark("^BVSP", "2024-01-01", "2024-01-31")

    assert isinstance(px, pd.Series)
    assert list(px.index) == [pd.Timestamp("2024-01-02"), pd.Timestamp("2024-01-03")]
    assert px.tolist() == [100.0, 102.0]      # adj_close, not close


def test_sa_suffix_is_stripped(tmp_path, monkeypatch):
    """The ETF sleeves were requested as IVVB11.SA; the plane calls them IVVB11."""
    db = _plane(tmp_path)
    _insert(db, "IVVB11", [("2024-01-02", 250.0)])
    _use(monkeypatch, db)

    assert download_benchmark("IVVB11.SA", "2024-01-01", "2024-01-31").tolist() == [250.0]
    # And the bare ticker works too — callers should not have to know.
    assert download_benchmark("IVVB11", "2024-01-01", "2024-01-31").tolist() == [250.0]


def test_window_is_respected(tmp_path, monkeypatch):
    db = _plane(tmp_path)
    _insert(db, "BOVA11", [("2023-12-29", 90.0), ("2024-01-02", 100.0),
                           ("2024-02-01", 110.0)])
    _use(monkeypatch, db)

    px = download_benchmark("^BVSP", "2024-01-01", "2024-01-31")
    assert px.tolist() == [100.0]


def test_missing_ticker_is_empty_not_an_error(tmp_path, monkeypatch):
    """A plane with no rows for the benchmark degrades to nothing at all.

    Not a flat line: a caller that charts a benchmark has to be able to tell
    "we have no data" from "the index did not move" — same posture bench_series
    takes with its `offline` flag.
    """
    db = _plane(tmp_path)
    _use(monkeypatch, db)

    px = download_benchmark("^BVSP", "2024-01-01", "2024-01-31")
    assert isinstance(px, pd.Series) and px.empty


def test_no_yahoo_left_in_the_engine():
    """The deliverable itself: no yfinance in the product path or its deps."""
    root = Path(core_data.__file__).resolve().parents[2]
    for rel in ("backtests/core/data.py", "scripts/bench_series.py",
                "requirements.txt", "requirements-bench.txt"):
        assert "yfinance" not in (root / rel).read_text(), rel
