"""The 2026-11-02 prices-ingest OOM and the stock_actions state it left.

- step 9 boxed every cell of the 3.5M-row price table at once (+2.3 GB);
- a `--start-year 2005` job parsed every COTAHIST zip left in the volume;
- a stale DETECTED row next to a restored B3 split applied the split twice, and
  the detector re-detected jumps whose B3 date had no bar on every run.
"""
from __future__ import annotations

import inspect
import tracemalloc

import numpy as np
import pandas as pd
import pytest

from b3_pipeline import adjustments, config, parser, storage

ISIN = "BRA1APBDR001"
NO_DIVIDENDS = pd.DataFrame(columns=["isin_code", "event_date", "event_type", "value"])


def _seed(conn) -> pd.DatetimeIndex:
    # 2:1 split; B3's date (dates[8]) and the day after have no bar, the jump
    # shows on dates[10] — the A1AP34 2020-10-21 / 2020-10-23 shape.
    dates = pd.bdate_range("2020-10-12", periods=20)
    keep = [i for i in range(20) if i not in (8, 9)]
    close = [20.0 if i < 10 else 10.0 for i in keep]
    storage.upsert_prices(conn, pd.DataFrame({
        "ticker": "A1AP34", "isin_code": ISIN,
        "date": dates[keep].strftime("%Y-%m-%d"),
        "open": close, "high": close, "low": close, "close": close,
        "volume": 1000, "quotation_factor": 1,
    }))
    return dates


def _row(date, source, factor=2.0):
    return {"isin_code": ISIN, "ex_date": date.strftime("%Y-%m-%d"),
            "action_type": config.EVENT_TYPE_STOCK_SPLIT, "factor": factor,
            "source": source}


def _steps_7_to_9(conn) -> int:
    """main.run_pipeline steps 7-9, as wired there."""
    prices = storage.get_all_prices(conn)
    actions = storage.get_all_stock_actions(conn)
    detected = adjustments.detect_splits_from_prices(prices, actions)
    actions = pd.concat([actions, detected], ignore_index=True)
    adjusted, _, validated = adjustments.compute_all_adjustments(
        prices, NO_DIVIDENDS, actions)
    storage.replace_stock_actions(conn, validated)
    storage.update_adjusted_columns(conn, adjusted)
    return len(detected)


def test_stale_detected_row_beside_b3_split_is_dropped_and_rerun_is_stable(mem_conn):
    dates = _seed(mem_conn)
    storage.upsert_stock_actions(mem_conn, pd.DataFrame(
        [_row(dates[8], "B3"), _row(dates[10], "DETECTED")]))

    for _ in range(3):
        assert _steps_7_to_9(mem_conn) == 0  # nothing re-detected, ever
        assert [tuple(r) for r in mem_conn.execute(
            "SELECT ex_date, factor, source FROM stock_actions")] == [
            (dates[8].strftime("%Y-%m-%d"), 2.0, "B3")]
        adj = [r[0] for r in mem_conn.execute(
            "SELECT split_adj_close FROM prices ORDER BY date")]
        assert adj == pytest.approx([10.0] * 18)  # applied once, not squared


def test_detected_row_far_from_or_unlike_the_b3_split_survives(mem_conn):
    dates = _seed(mem_conn)
    far = dates[8] + pd.Timedelta(days=30)
    actions = pd.DataFrame([_row(dates[8], "B3"), _row(far, "DETECTED"),
                            _row(dates[10], "DETECTED", factor=10.0)])
    out = adjustments.validate_split_actions_against_prices(
        actions, storage.get_all_prices(mem_conn))
    assert sorted(out["source"]) == ["B3", "DETECTED", "DETECTED"]


def _frame(n: int) -> pd.DataFrame:
    vals = np.arange(n, dtype=float)
    return pd.DataFrame({
        "ticker": "PETR4", "isin_code": [None, "BRPETRACNPR6"] * (n // 2),
        "date": pd.Timestamp("2020-01-01") + pd.to_timedelta(np.arange(n) % 9000, "D"),
        "split_adj_open": vals, "split_adj_high": vals, "split_adj_low": vals,
        "split_adj_close": vals, "adj_close": vals, "volume": vals,
    })


def test_update_rows_are_streamed_not_materialised():
    df = _frame(200_000)
    rows = storage._adjusted_records(df, batch=10_000)
    assert inspect.isgenerator(rows)

    tracemalloc.start()
    for _ in rows:
        pass
    _, streamed = tracemalloc.get_traced_memory()
    tracemalloc.reset_peak()
    whole = list(storage._adjusted_records(df, batch=len(df)))
    _, materialised = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert streamed < materialised / 4

    # same rows as the old one-shot conversion
    assert whole[1] == [1.0] * 6 + ["PETR4", "BRPETRACNPR6", "2020-01-02"]
    assert whole[0][7] == "UNKNOWN"
    assert list(storage._adjusted_records(df.head(5), batch=2)) == whole[:5]


def test_update_rows_fill_missing_columns_with_null():
    df = pd.DataFrame({"ticker": ["X"], "date": ["2020-01-01"], "adj_close": [1.5]})
    assert list(storage._adjusted_records(df)) == [
        [None, None, None, None, 1.5, None, "X", "UNKNOWN", "2020-01-01"]]


def test_parse_reads_only_the_requested_years(tmp_path, monkeypatch):
    for y in (1994, 2004, 2005, 2026):
        (tmp_path / f"COTAHIST_A{y}.ZIP").touch()
    seen = []
    monkeypatch.setattr(parser, "parse_cotahist_file",
                        lambda p: seen.append(p.name) or pd.DataFrame())
    parser.parse_all_cotahist_files(tmp_path, years=[2005, 2026])
    assert seen == ["COTAHIST_A2005.ZIP", "COTAHIST_A2026.ZIP"]
