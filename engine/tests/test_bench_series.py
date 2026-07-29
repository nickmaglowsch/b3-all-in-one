"""bench_series serves the portfolio page's IBOV/CDI overlay from the plane.

It used to fetch IBOV from Yahoo and CDI from BCB on every cache miss, inside a
request. Both series are already stored — `$IBOV` and `$CDI` in
`external_prices` (written by b3_pipeline/ibov_series.py and index_series.py) — so the request path
now reads the database and touches nothing external.

The consequence worth testing: `offline` no longer means "we were told not to
fetch", it means "the plane has nothing for this window", which is the only
honest reason to draw no benchmark.
"""
import importlib.util
import sqlite3
from pathlib import Path

import pytest

from b3_pipeline import storage

ROOT = Path(__file__).resolve().parents[1]


def _load_bench_series():
    spec = importlib.util.spec_from_file_location(
        "bench_series", ROOT / "scripts" / "bench_series.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def plane(tmp_path, monkeypatch):
    db_path = str(tmp_path / "plane.sqlite")
    conn = sqlite3.connect(db_path)
    storage.init_db(conn)
    conn.commit()
    conn.close()
    monkeypatch.setenv("ENGINE_DATABASE_URL", db_path)
    return db_path


def _ibov(db_path: str, rows: list[tuple[str, float]]) -> None:
    conn = sqlite3.connect(db_path)
    for date_str, pts in rows:
        conn.execute(
            """INSERT INTO external_prices
                   (ticker, isin_code, date, open, high, low, close, volume, source)
               VALUES ('$IBOV', 'UNKNOWN', ?, NULL, NULL, NULL, ?, 0, 'B3_INDEX')""",
            (date_str, pts),
        )
    conn.commit()
    conn.close()


def _cdi(db_path: str, rows: list[tuple[str, float]]) -> None:
    conn = sqlite3.connect(db_path)
    for date_str, level in rows:
        conn.execute(
            """INSERT INTO external_prices
                   (ticker, isin_code, date, open, high, low, close, volume, source)
               VALUES ('$CDI', 'UNKNOWN', ?, NULL, NULL, NULL, ?, 0, 'BCB_SGS')""",
            (date_str, level),
        )
    conn.commit()
    conn.close()


def test_both_series_come_from_the_plane(plane):
    _ibov(plane, [("2024-01-02", 132696.63), ("2024-01-03", 131000.0)])
    _cdi(plane, [("2024-01-02", 1.5), ("2024-01-03", 1.5004)])

    out = _load_bench_series().series("2024-01-01", "2024-01-31")

    assert out["offline"] is False
    assert out["ibov"] == {"2024-01-02": 132696.63, "2024-01-03": 131000.0}
    assert out["cdi"] == {"2024-01-02": 1.5, "2024-01-03": 1.5004}


def test_empty_plane_reports_offline(plane):
    """No rows is not a flat benchmark — the caller must be able to say so."""
    out = _load_bench_series().series("2024-01-01", "2024-01-31")

    assert out["offline"] is True
    assert out["ibov"] == {} and out["cdi"] == {}


def test_one_series_present_is_not_offline(plane):
    """CDI is written by a different pipeline step than $IBOV; one being late
    must not blank the other."""
    _ibov(plane, [("2024-01-02", 100.0)])

    out = _load_bench_series().series("2024-01-01", "2024-01-31")

    assert out["offline"] is False
    assert out["ibov"] == {"2024-01-02": 100.0}
    assert out["cdi"] == {}
