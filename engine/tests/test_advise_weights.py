"""Subprocess tests for scripts/advise_weights.py against the local Postgres data.

Auto-skipped unless ENGINE_DATABASE_URL points at Postgres (same guard as
test_postgres_storage.py). Runs the advise-only path fully offline
(ENGINE_BENCH_OFFLINE=1) with the price-only MomSharpe strategy, then checks the
one-line JSON contract: weights sum ~1, as_of is a 2025 date, and (with a seed
book) an integer-share trade list that conserves cash and never oversells.

Run:
    ENGINE_DATABASE_URL=postgresql://postgres:devpassword@localhost:5432/b3 \
        pytest tests/test_advise_weights.py -v
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from b3_pipeline import storage

pytestmark = pytest.mark.skipif(
    not storage.using_postgres(),
    reason="ENGINE_DATABASE_URL is not a postgresql:// DSN",
)

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "advise_weights.py"
STRATEGY = "MomSharpe"  # price-only, no fundamentals needed


def _run_argv(*args: str) -> dict:
    """Run advise_weights offline and parse the last JSON line of stdout (the
    build_shared_data progress prints precede it — same contract the platform
    consumes via reversed(splitlines()))."""
    env = {**os.environ, "ENGINE_BENCH_OFFLINE": "1"}
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True, text=True, env=env, cwd=str(ROOT),
    )
    assert proc.returncode == 0, proc.stderr
    for line in reversed(proc.stdout.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            return json.loads(line)
    raise AssertionError(f"no JSON line in stdout:\n{proc.stdout}")


def _run(*args: str) -> dict:
    return _run_argv(STRATEGY, *args)


def test_weights_only():
    out = _run()
    assert out["strategy"] == STRATEGY
    assert out["as_of"].startswith("2025"), out["as_of"]
    w = out["weights"]
    assert w, "expected non-empty weights"
    assert all(v != 0 for v in w.values())
    assert sum(w.values()) == pytest.approx(1.0, abs=1e-6)
    assert "skipped_rows_note" in out
    assert "trades" not in out   # no --positions given


def test_trade_list_consistency():
    # Seed a book of two names that MomSharpe won't hold (large caps vs its
    # small-cap momentum picks): both get fully sold, all NAV redeploys to the
    # advised names.
    positions = {"PETR4": {"qty": 100, "avg_cost": 30.0},
                 "VALE3": {"qty": 50, "avg_cost": 60.0}}
    cash = 40_000.0
    out = _run("--positions", json.dumps(positions), "--nav-cash", str(cash))
    t = out["trades"]

    sells = {s["ticker"]: s for s in t["sells"]}
    buys = t["buys"]

    # integer share quantities everywhere
    for leg in list(sells.values()) + buys:
        assert isinstance(leg["qty"], int) and leg["qty"] > 0
        assert leg["est_brl"] == pytest.approx(leg["qty"] * leg["est_price"])

    # no sell exceeds the held quantity, and the fully-out-of-target names are
    # liquidated entirely (target 0 -> sell all held).
    for tk, held in {"PETR4": 100, "VALE3": 50}.items():
        assert tk in sells
        assert sells[tk]["qty"] <= held
        assert sells[tk]["qty"] == held

    # Money conservation: sell+buy BRL balance around the rebalance diff. With a
    # book fully outside target, Σbuys == NAV == cash + Σsells before rounding;
    # integer flooring/ceiling keeps the residual inside one lot per traded name.
    sum_sell = sum(s["est_brl"] for s in t["sells"])
    sum_buy = sum(b["est_brl"] for b in buys)
    residual = cash + sum_sell - sum_buy
    lot_envelope = sum(leg["est_price"] for leg in list(sells.values()) + buys)
    assert abs(residual) <= lot_envelope

    assert t["unpriced_skipped"] == []
    assert t["nav"] == pytest.approx(sum_buy + residual)  # NAV = deployed + leftover cash


def test_weights_mode():
    # Fixed weights instead of a strategy: 50% PETR4 / 30% VALE3 / 20% CASH_CDI.
    # Signals are skipped; the two priced tickers are bought, 20% stays as cash.
    weights = {"PETR4": 0.5, "VALE3": 0.3, "CASH_CDI": 0.2}
    positions = {"PETR4": {"qty": 100, "avg_cost": 30.0}}
    cash = 50_000.0
    out = _run_argv("--weights", json.dumps(weights),
                    "--positions", json.dumps(positions), "--nav-cash", str(cash))
    assert out["strategy"] is None
    assert out["as_of"].startswith("2025"), out["as_of"]
    assert out["weights"] == weights  # echoed verbatim, CASH_CDI included

    t = out["trades"]
    # CASH_CDI is never traded and never counted as an unpriced ticker.
    assert "CASH_CDI" not in t["unpriced_skipped"]
    traded = {leg["ticker"] for leg in t["sells"] + t["buys"]}
    assert "CASH_CDI" not in traded
    assert t["unpriced_skipped"] == []

    for leg in t["sells"] + t["buys"]:
        assert isinstance(leg["qty"], int) and leg["qty"] > 0
        assert leg["est_brl"] == pytest.approx(leg["qty"] * leg["est_price"])

    # 20% of NAV should be left uninvested (CASH_CDI sleeve), within one lot per leg.
    nav = t["nav"]
    sum_sell = sum(s["est_brl"] for s in t["sells"])
    sum_buy = sum(b["est_brl"] for b in t["buys"])
    residual = cash + sum_sell - sum_buy
    lot_envelope = sum(leg["est_price"] for leg in t["sells"] + t["buys"])
    assert abs(residual - 0.2 * nav) <= lot_envelope
