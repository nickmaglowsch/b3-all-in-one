"""scripts/_common.price_lookup — which snapshot wins, and what happens with none.

The TLH planner prices the whole book off this. A ticker that resolves to the wrong
snapshot's quote is a wrong PnL on a sell order, and a missing quote has to be
absent rather than zero — `find_losers` treats a zero as the biggest loss in the
book.
"""
from __future__ import annotations

import pandas as pd

from scripts import _common


def snapshot(d, name, **quotes):
    d.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"papel": list(quotes), "cotacao": list(quotes.values())}) \
        .to_csv(d / name, index=False)


def prices() -> dict:
    df = _common.price_lookup()
    return dict(zip(df["ticker"], df["price"]))


def point_at(monkeypatch, tmp_path):
    dirs = {k: tmp_path / k for k in ("snapshots", "fii_snapshots", "external_prices")}
    monkeypatch.setattr(_common, "SNAPSHOTS", dirs["snapshots"])
    monkeypatch.setattr(_common, "FII_SNAPSHOTS", dirs["fii_snapshots"])
    monkeypatch.setattr(_common, "EXTERNAL_PRICES", dirs["external_prices"])
    return dirs


def test_no_snapshots_at_all_yields_an_empty_frame_not_a_crash(monkeypatch, tmp_path):
    point_at(monkeypatch, tmp_path)
    df = _common.price_lookup()
    assert df.empty and list(df.columns) == ["ticker", "price"]


def test_the_latest_file_by_name_wins_within_a_directory(monkeypatch, tmp_path):
    """Snapshots are ISO-dated filenames, so 'latest' is the last one sorted."""
    dirs = point_at(monkeypatch, tmp_path)
    snapshot(dirs["snapshots"], "2025-01-02.csv", PETR4=30.0)
    snapshot(dirs["snapshots"], "2025-06-30.csv", PETR4=45.0)
    assert prices()["PETR4"] == 45.0


def test_the_equity_screener_outranks_the_fii_and_external_snapshots(monkeypatch, tmp_path):
    dirs = point_at(monkeypatch, tmp_path)
    snapshot(dirs["snapshots"], "2025-06-30.csv", TAEE11=40.0)
    snapshot(dirs["fii_snapshots"], "2025-06-30.csv", TAEE11=1.0, HGLG11=150.0)
    snapshot(dirs["external_prices"], "2025-06-30.csv", TAEE11=2.0, IVVB11=430.0)
    quoted = prices()
    assert quoted["TAEE11"] == 40.0     # unit, priced by the equity screener
    assert quoted["HGLG11"] == 150.0    # only the FII snapshot has it
    assert quoted["IVVB11"] == 430.0    # only the external one has it


def test_a_snapshot_without_the_expected_columns_is_ignored(monkeypatch, tmp_path):
    """A half-written or reshaped scrape must not take the whole lookup down."""
    dirs = point_at(monkeypatch, tmp_path)
    dirs["snapshots"].mkdir(parents=True)
    pd.DataFrame({"ticker": ["PETR4"], "close": [30.0]}).to_csv(
        dirs["snapshots"] / "2025-06-30.csv", index=False)
    snapshot(dirs["fii_snapshots"], "2025-06-30.csv", HGLG11=150.0)
    assert list(_common.price_lookup()["ticker"]) == ["HGLG11"]
