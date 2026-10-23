"""A user portfolio of ticker sleeves runs exactly like the built-in it copies.

The acceptance check for ticker sleeves in user portfolios: a user saves
DIVO11.SA / CASH_CDI / IVVB11.SA and the backtest must match the built-in
"DIVO11 / CDI / IVVB11 (thirds)" — same blend engine, same CDI parking before
IVVB11 lists, same tax engine. This runs both through scripts/run_backtest_json.py,
the entry point the platform shells, with the user spec materialized the way the
platform does it (JSON in a .yaml under ENGINE_USER_SPECS_DIR) and in the shape
platform/strategy_specs._compile_portfolio emits — platform/tests/test_mcp.py
pins that same dict from the other side.

Synthetic prices in a tmp_path SQLite and a synthetic CDI, so the numbers here are
not the real 2013-2026 ones; what is asserted is that the two paths agree.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from b3_pipeline import storage

ROOT = Path(__file__).resolve().parents[1]
BUILTIN = "DIVO11 / CDI / IVVB11 (thirds)"
START, END = "2012-01-01", "2018-12-31"
IVVB_LISTS = "2014-05-02"

# What the platform compiles {"ticker": "DIVO11.SA", "target_pct": p} /
# {"strategy_ref": "CASH_CDI", ...} / {"ticker": "IVVB11.SA", ...} into.
def user_spec(name: str, divo: float, cdi: float, ivvb: float) -> dict:
    return {"name": name, "kind": "fixed_weight", "description": "",
            "rebalance": "QE", "park_in_cdi_until_live": True,
            "sleeves": [{"ticker": "DIVO11.SA", "weight": divo / 100.0},
                        {"asset": "CDI", "weight": cdi / 100.0},
                        {"ticker": "IVVB11.SA", "weight": ivvb / 100.0}]}


# The CDI comes from the BCB in a real run. Swapped for a deterministic daily
# series inside the child before the runner imports anything else.
_RUNNER = """
import sys
sys.path.insert(0, {root!r})
import pandas as pd
import backtests.core.data as data
import backtests.core.shared_data as sd

def cdi(start, end):
    idx = pd.bdate_range(start, end)
    return pd.Series(0.0004 + 0.0001 * (idx.month % 3), index=idx, name="CDI")

data.download_cdi_daily = sd.download_cdi_daily = cdi
from scripts import run_backtest_json
sys.argv = ["run_backtest_json.py", *sys.argv[1:]]
run_backtest_json.main()
"""


@pytest.fixture(scope="module")
def plane(tmp_path_factory) -> str:
    db = str(tmp_path_factory.mktemp("plane") / "b3.sqlite")
    conn = sqlite3.connect(db)
    storage.init_db(conn)
    rng = np.random.default_rng(42)
    days = pd.bdate_range(START, END)
    for ticker, first, drift, vol in (("DIVO11", START, 0.0006, 0.011),
                                      ("IVVB11", IVVB_LISTS, 0.0007, 0.010)):
        d = days[days >= first]
        px = 50.0 * np.exp(np.cumsum(rng.normal(drift, vol, len(d))))
        for day, p in zip(d, px):
            conn.execute(
                "INSERT INTO prices (ticker, isin_code, date, open, high, low, close,"
                " adj_close, split_adj_close, volume) VALUES"
                " (?, 'UNKNOWN', ?, ?, ?, ?, ?, ?, ?, 5e6)",
                (ticker, day.strftime("%Y-%m-%d"), p, p, p, p, p, p))
    ibov = 60000.0 * np.exp(np.cumsum(rng.normal(0.0003, 0.012, len(days))))
    storage.upsert_external_prices(conn, pd.DataFrame({
        "ticker": "$IBOV", "isin_code": "UNKNOWN", "date": days, "open": np.nan,
        "high": np.nan, "low": np.nan, "close": ibov, "volume": 0.0,
        "source": "B3_INDEX"}))
    conn.commit()
    conn.close()
    return db


def _run(plane: str, tmp_path: Path, strategy: str, specs: dict | None = None):
    out = tmp_path / "out"
    env = {k: v for k, v in os.environ.items() if k != "ENGINE_BENCH_OFFLINE"}
    env.update(ENGINE_DATABASE_URL=plane, ENGINE_BACKTEST_MEM_MB="0")
    if specs:
        user_dir = tmp_path / "user_specs"
        user_dir.mkdir()
        for i, spec in enumerate(specs.values()):
            (user_dir / f"u{i}.yaml").write_text(json.dumps(spec))
        env["ENGINE_USER_SPECS_DIR"] = str(user_dir)
    runner = tmp_path / "runner.py"
    runner.write_text(_RUNNER.format(root=str(ROOT)))
    cfg = {"start": START, "end": END}
    proc = subprocess.run([sys.executable, str(runner), strategy, str(out), json.dumps(cfg)],
                          capture_output=True, text=True, cwd=str(ROOT), env=env)
    assert proc.returncode == 0, proc.stderr[-3000:]
    metrics = json.loads((out / "metrics.json").read_text())
    return metrics, pd.read_csv(out / "curves.csv")


BASES = ("pretax", "aftertax")


def _headline(m: dict, basis: str) -> dict:
    return {k: m[basis][k] for k in ("Ann. Return (%)", "Max Drawdown (%)", "Sharpe")}


@pytest.fixture(scope="module")
def builtin(plane, tmp_path_factory):
    return _run(plane, tmp_path_factory.mktemp("builtin"), BUILTIN)


def test_user_thirds_is_the_builtin_to_the_last_digit(plane, builtin, tmp_path):
    b_m, b_c = builtin
    third = 100.0 / 3.0
    spec = user_spec("Minha DIVO/CDI/IVVB", third, third, third)
    u_m, u_c = _run(plane, tmp_path, spec["name"], {"u": spec})

    # Pre-tax and after-tax curves, tax paid and loss carry, row for row. Weights
    # of 0.33333333333333337 against the built-in's 0.3333333333333333 are the
    # only difference, hence a tolerance at float precision rather than ==.
    assert list(u_c["date"]) == list(b_c["date"])
    for col in ("pretax", "aftertax", "tax_paid", "loss_carryforward", "turnover"):
        np.testing.assert_allclose(u_c[col], b_c[col], rtol=1e-12, atol=1e-9, err_msg=col)
    for basis in BASES:
        assert _headline(u_m, basis) == pytest.approx(_headline(b_m, basis), rel=1e-9)
    assert u_m["market"] == b_m["market"] == "B3"   # one tax rule, by construction

    # The window opens before IVVB11 lists, so both ran their CDI parking — and
    # both were taxed, so the after-tax side compared above is not trivially equal.
    assert b_c["date"].iloc[0] < IVVB_LISTS
    assert b_c["tax_paid"].sum() > 0


def test_the_34_33_33_portfolio_matches_within_rounding(plane, builtin, tmp_path):
    """The request's own acceptance spec: whole percentages, so close, not equal."""
    b_m, _ = builtin
    spec = user_spec("DIVO 34 / CDI 33 / IVVB 33", 34, 33, 33)
    u_m, _ = _run(plane, tmp_path, spec["name"], {"u": spec})
    for basis in BASES:
        b, u = _headline(b_m, basis), _headline(u_m, basis)
        assert u["Ann. Return (%)"] == pytest.approx(b["Ann. Return (%)"], abs=0.15)
        assert u["Max Drawdown (%)"] == pytest.approx(b["Max Drawdown (%)"], abs=0.5)
        assert u["Sharpe"] == pytest.approx(b["Sharpe"], abs=0.02)
