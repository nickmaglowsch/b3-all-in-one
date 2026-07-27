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


def _run_raw(*args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "ENGINE_BENCH_OFFLINE": "1"}
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True, text=True, env=env, cwd=str(ROOT),
    )


def _parse(proc: subprocess.CompletedProcess) -> dict:
    assert proc.returncode == 0, proc.stderr
    for line in reversed(proc.stdout.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            return json.loads(line)
    raise AssertionError(f"no JSON line in stdout:\n{proc.stdout}")


def _run_argv(*args: str) -> dict:
    """Run advise_weights offline and parse the last JSON line of stdout (the
    build_shared_data progress prints precede it — same contract the platform
    consumes via reversed(splitlines()))."""
    return _parse(_run_raw(*args))


def _run(*args: str) -> dict:
    return _run_argv(STRATEGY, *args)


def _plane_max_date() -> str:
    """Last date the market plane carries. The as_of assertions below key on this
    rather than a hardcoded year: the plane is reloaded on its own schedule, and
    pinning "2025" made these tests fail the moment it advanced into 2026 —
    reporting a stale fixture as a broken advisor."""
    with storage.get_connection(None) as conn:
        return str(storage.read_sql("SELECT MAX(date) AS d FROM prices", conn).iloc[0]["d"])[:10]


def test_weights_only():
    out = _run()
    assert out["strategy"] == STRATEGY
    # as_of is the resample("ME") bucket *label*, so it is the last calendar day
    # of the plane's final month and can sit a few days past the last close.
    # Compare months, not the raw date.
    assert out["as_of"][:7] <= _plane_max_date()[:7], out["as_of"]
    w = out["weights"]
    assert w, "expected non-empty weights"
    assert all(v != 0 for v in w.values())
    assert sum(w.values()) == pytest.approx(1.0, abs=1e-6)
    assert "skipped_rows_note" in out
    assert "trades" not in out   # no --positions given


def test_trade_list_consistency():
    # Seed a book of two large caps and rebalance into whatever MomSharpe advises.
    held_book = {"PETR4": 100, "VALE3": 50}
    positions = {t: {"qty": q, "avg_cost": 30.0} for t, q in held_book.items()}
    cash = 40_000.0
    out = _run("--positions", json.dumps(positions), "--nav-cash", str(cash))
    t = out["trades"]

    sells = {s["ticker"]: s for s in t["sells"]}
    buys = t["buys"]

    # integer share quantities everywhere
    for leg in list(sells.values()) + buys:
        assert isinstance(leg["qty"], int) and leg["qty"] > 0
        assert leg["est_brl"] == pytest.approx(leg["qty"] * leg["est_price"])

    # Never oversell, and a held name the strategy does not want at all is
    # liquidated in full. Which names those are depends on what the plane
    # currently makes MomSharpe pick, so it is read off the advised weights
    # rather than assumed — the old fixture hardcoded "it holds neither" and
    # started failing the moment the plane moved.
    advised = out["weights"]
    for tk, held in held_book.items():
        assert tk in sells or tk in advised, f"{tk} neither sold nor targeted"
        if tk in sells:
            assert sells[tk]["qty"] <= held
        if tk not in advised:
            assert sells[tk]["qty"] == held, f"{tk} has no target but was not liquidated"

    # Money conservation: sell+buy BRL balance around the rebalance diff. With a
    # book fully outside target, Σbuys == NAV == cash + Σsells before rounding;
    # integer flooring/ceiling keeps the residual inside one lot per traded name.
    sum_sell = sum(s["est_brl"] for s in t["sells"])
    sum_buy = sum(b["est_brl"] for b in buys)
    residual = cash + sum_sell - sum_buy
    lot_envelope = sum(leg["est_price"] for leg in list(sells.values()) + buys)
    assert abs(residual) <= lot_envelope

    assert t["unpriced_skipped"] == []
    # NAV is the book marked to market plus uninvested cash. Stating it via
    # positions_valued holds whether or not the strategy happens to keep one of
    # the seeded names; the old form (cash + Σsells) only held when the whole
    # book was liquidated, which is a fact about today's plane, not the contract.
    assert t["nav"] == pytest.approx(sum(t["positions_valued"].values()) + cash)


def test_weights_mode():
    # Fixed weights instead of a strategy: 50% PETR4 / 30% VALE3 / 20% CASH_CDI.
    # Signals are skipped; the two priced tickers are bought, 20% stays as cash.
    weights = {"PETR4": 0.5, "VALE3": 0.3, "CASH_CDI": 0.2}
    positions = {"PETR4": {"qty": 100, "avg_cost": 30.0}}
    cash = 50_000.0
    out = _run_argv("--weights", json.dumps(weights),
                    "--positions", json.dumps(positions), "--nav-cash", str(cash))
    assert out["strategy"] is None
    assert out["as_of"][:7] == _plane_max_date()[:7], out["as_of"]
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


# ── --weights is a valuation, not a backtest ─────────────────────────────────
# This mode has no signals: it prices a handful of tickers at the plane's latest
# close. It used to get there by building the entire market plane — 20 years of
# HLC for every listed ticker, the split detector, MA200, ATR, the mean-reversion
# feature set — and then reading one row out of it. Both platform surfaces that
# value a book (GET /portfolio/target, POST /portfolio/rebalance-plans) sit on
# this path and neither caches it, so the waste was paid per page view.

# The progress lines build_shared_data prints on its way through the plane. Their
# absence is what "did not build the plane" looks like from outside the process.
_PLANE_BUILD_MARKERS = ("Loading B3 HLC data", "Loading B3 data", "Pivoting")


def test_weights_mode_does_not_build_the_market_plane():
    proc = _run_raw("--weights", json.dumps({"PETR4": 1.0}))
    assert proc.returncode == 0, proc.stderr
    for marker in _PLANE_BUILD_MARKERS:
        assert marker not in proc.stdout, (
            f"--weights still builds the full plane (saw {marker!r} in stdout)")


def test_weights_mode_prices_match_the_full_plane():
    """Equivalence, not just speed: the latest-close query has to return exactly
    what raw_close.ffill().iloc[-1] returned, or every rebalance plan silently
    reprices. Compares against build_shared_data itself over the same window."""
    from datetime import datetime

    from backtests.core.shared_data import build_shared_data

    end = datetime.today().strftime("%Y-%m-%d")
    db = os.environ["ENGINE_DATABASE_URL"]
    shared = build_shared_data(db, "2005-01-01", end, "ME", include_fundamentals=False)
    raw_close = shared["raw_close"]
    expected_last = raw_close.ffill().iloc[-1]

    # Value a book of names the plane definitely carries.
    tickers = [t for t in ("PETR4", "VALE3", "ITUB4", "BBAS3", "HGLG11")
               if t in raw_close.columns]
    assert tickers, "market plane carries none of the probe tickers"
    positions = {t: {"qty": 10, "avg_cost": 1.0} for t in tickers}
    out = _run_argv("--weights", json.dumps({t: 1.0 / len(tickers) for t in tickers}),
                    "--positions", json.dumps(positions), "--nav-cash", "0")

    # as_of must still be the month-end label resample("ME") produced.
    assert out["as_of"] == raw_close.index[-1].date().isoformat()

    # positions_valued = qty x latest close, so it exposes the price per ticker.
    for t in tickers:
        got = out["trades"]["positions_valued"][t] / 10
        assert got == pytest.approx(float(expected_last[t]), rel=1e-9), t
