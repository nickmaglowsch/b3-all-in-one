"""
A split the validator corrects must be applied once, however many pipeline runs
follow.

B3 reports a 2:1 split as a 1-for-40 REVERSE_SPLIT. The validator turns it into
STOCK_SPLIT 2.0 / B3_CORRECTED and replace_stock_actions stores that. The next
run's fetch upserts the original REVERSE_SPLIT row again; since action_type is
part of the stock_actions key it lands beside the correction instead of over
it, and the validator used to correct it a second time, emitting two 0.5
factors on the same date. update_adjusted_columns then halved every bar before
the split, and replace_stock_actions collapsed the duplicate back into one row,
so the table looked clean while the cycle repeated on every run.

This drives the stock-action half of b3_pipeline.main's steps 6-9 against a
real (SQLite) plane, three runs in a row.
"""
from __future__ import annotations

import pandas as pd
import pytest

from b3_pipeline import adjustments, config, storage

ISIN = "BRVIVTACNOR0"


def _seed_prices(conn) -> pd.Timestamp:
    dates = pd.bdate_range("2020-01-01", periods=20)
    close = [20.0] * 10 + [10.0] * 10
    storage.upsert_prices(conn, pd.DataFrame({
        "ticker": "VIVT3", "isin_code": ISIN,
        "date": dates.strftime("%Y-%m-%d"),
        "open": close, "high": close, "low": close, "close": close,
        "volume": 1000, "quotation_factor": 1,
    }))
    return dates[10]


def _b3_fetch(ex_date: pd.Timestamp) -> pd.DataFrame:
    return pd.DataFrame([{
        "isin_code": ISIN, "ex_date": ex_date.strftime("%Y-%m-%d"),
        "action_type": config.EVENT_TYPE_REVERSE_SPLIT, "factor": 0.025,
        "source": "B3",
    }])


def _run(conn, fetched: pd.DataFrame | None) -> None:
    if fetched is not None:
        storage.upsert_stock_actions(conn, fetched)
    prices = storage.get_all_prices(conn)
    actions = storage.get_all_stock_actions(conn)
    no_dividends = pd.DataFrame(columns=["isin_code", "event_date", "event_type", "value"])
    adjusted, _, validated = adjustments.compute_all_adjustments(
        prices, no_dividends, actions)
    storage.replace_stock_actions(conn, validated)
    storage.update_adjusted_columns(conn, adjusted)


def _split_adj_close(conn) -> list[float]:
    return [r[0] for r in conn.execute(
        "SELECT split_adj_close FROM prices WHERE isin_code = ? ORDER BY date",
        (ISIN,))]


def _actions(conn) -> list[tuple]:
    return [tuple(r) for r in conn.execute(
        "SELECT ex_date, action_type, factor, source FROM stock_actions")]


@pytest.mark.parametrize("fetch_every_run", [True, False])
def test_corrected_split_is_applied_once_across_runs(mem_conn, fetch_every_run):
    ex_date = _seed_prices(mem_conn)
    fetched = _b3_fetch(ex_date)

    _run(mem_conn, fetched)
    first_prices, first_actions = _split_adj_close(mem_conn), _actions(mem_conn)
    assert first_prices == pytest.approx([10.0] * 20)
    assert first_actions == [(
        ex_date.strftime("%Y-%m-%d"), config.EVENT_TYPE_STOCK_SPLIT, 2.0,
        "B3_CORRECTED",
    )]

    for _ in range(2):
        _run(mem_conn, fetched if fetch_every_run else None)
        assert _split_adj_close(mem_conn) == pytest.approx(first_prices)
        assert _actions(mem_conn) == first_actions
