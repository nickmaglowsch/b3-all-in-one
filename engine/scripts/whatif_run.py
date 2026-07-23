"""Headless what-if runner: replay real dated cash flows through a strategy.

Usage:
    python scripts/whatif_run.py <strategy_name> <out_dir> <config_json>

<config_json> is a JSON string: {"flows": [{"date": "YYYY-MM-DD", "amount": N},
...], "start": ..., "end": ..., "freq": ..., "params": {...}} — only "flows" is
required. Reads the market plane from Postgres via ENGINE_DATABASE_URL (falls
back to the local sqlite DB when unset), runs the registry strategy through
run_simulation with the flows fed in as a dated ``contribution`` Series
(initial_capital 0 — a pure replay of the tenant's own money), writes
metrics.json + curves.csv into <out_dir>, and prints a one-line JSON summary to
stdout.

Mirrors run_backtest_json.py: same offline stub support (ENGINE_BENCH_OFFLINE=1
stubs CDI + IBOV so a price-only strategy runs fully offline), same artifact
contract. Flows deploy at the next rebalance (documented approximation — a flow
between two monthly marks lands on the later one).
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _flat_series(start: str, end: str, name: str, value: float) -> pd.Series:
    idx = pd.bdate_range(start=start, end=end)
    return pd.Series(value, index=idx, name=name)


def _install_benchmark_stubs(start: str, end: str, offline: bool) -> None:
    """Replace the yfinance/BCB fetchers with offline stubs — IBOV always, CDI
    only when offline. Identical to run_backtest_json's stubbing."""
    import backtests.core.data as data
    import backtests.core.shared_data as sd

    _real_bench = data.download_benchmark

    def _bench(ticker: str, s: str, e: str) -> pd.Series:
        if ticker == "^BVSP" or offline:
            return _flat_series(s, e, ticker, 100.0)
        return _real_bench(ticker, s, e)

    data.download_benchmark = _bench
    sd.download_benchmark = _bench

    if offline:
        def _cdi(s: str, e: str) -> pd.Series:
            return _flat_series(s, e, "CDI", 0.0)
        data.download_cdi_daily = _cdi
        sd.download_cdi_daily = _cdi


def _resolve_end(end: str) -> str:
    return datetime.today().strftime("%Y-%m-%d") if end == "today" else end


def _build_flows(raw: list[dict]) -> pd.Series:
    """[{date, amount}, ...] -> a date-indexed Series, same-date flows summed."""
    if not raw:
        raise SystemExit("whatif requires a non-empty 'flows' list")
    s = pd.Series(
        [float(f["amount"]) for f in raw],
        index=pd.DatetimeIndex([pd.Timestamp(f["date"]) for f in raw]),
    )
    return s.groupby(level=0).sum().sort_index()


def main() -> None:
    name, out_dir, config_json = sys.argv[1], sys.argv[2], sys.argv[3]
    cfg = json.loads(config_json) if config_json else {}
    req_params = cfg.get("params") or {}

    flows = _build_flows(cfg.get("flows") or [])

    from backtests.core.strategy_registry import get_registry
    from backtests.core.strategy_base import dedup_target_weights
    from backtests.core.simulation import run_simulation
    from backtests.core.metrics import (
        build_metrics, value_to_ret, strategy_daily_values,
    )

    strat = get_registry().get(name)
    params = strat.get_default_parameters()
    params.update(req_params)

    # Default window: from the tenant's first flow (first trade) to today.
    start = str(cfg.get("start") or flows.index.min().date().isoformat())
    end = _resolve_end(str(cfg.get("end") or "today"))
    freq = str(cfg.get("freq") or params.get("rebalance_freq", "ME"))
    needs_funds = bool(getattr(strat, "needs_fundamentals", False))

    offline = os.environ.get("ENGINE_BENCH_OFFLINE") == "1"
    _install_benchmark_stubs(start, end, offline)

    from backtests.core.shared_data import build_shared_data
    db_path = os.environ.get("ENGINE_DATABASE_URL") or str(ROOT / "b3_market_data.sqlite")
    shared = build_shared_data(db_path, start, end, freq, include_fundamentals=needs_funds)

    ret_matrix, tw = strat.generate_signals(shared, params)
    tw = dedup_target_weights(tw, shared["adtv"])
    result = run_simulation(
        returns_matrix=ret_matrix.fillna(0.0),
        target_weights=tw,
        initial_capital=0.0,               # pure replay: all capital comes from flows
        tax_rate=float(params.get("tax_rate", 0.15)),
        slippage=float(params.get("slippage", 0.001)),
        monthly_sales_exemption=float(params.get("monthly_sales_exemption", 20_000)),
        contribution=flows,
        name=name,
    )

    idx = result["pretax_values"].index
    if len(idx) < 2:
        raise SystemExit(f"strategy '{name}' produced no investable periods for {start}..{end}")

    contribs = result["contributions"]
    pt_ret = value_to_ret(result["pretax_values"], contribs)
    at_ret = value_to_ret(result["aftertax_values"], contribs)
    strat_daily = strategy_daily_values(shared, tw, initial_capital=1.0)
    ppy = {"ME": 12, "QE": 4, "W-FRI": 52}.get(freq, 12)

    pre = build_metrics(pt_ret, "Pre-Tax", ppy, daily_values=strat_daily)
    aft = build_metrics(at_ret, "After-Tax", ppy, daily_values=strat_daily)

    at_vals = result["aftertax_values"]
    at_dd = at_vals / at_vals.cummax() - 1.0

    # ── Artifacts ─────────────────────────────────────────────────────────────
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    curves = pd.DataFrame({
        "date": [d.date().isoformat() for d in idx],
        "pretax": result["pretax_values"].to_numpy(),
        "aftertax": at_vals.to_numpy(),
        "drawdown": at_dd.to_numpy(),
        "turnover": result["turnover"].to_numpy(),
        "contribution": contribs.to_numpy(),
        "tax_paid": result["tax_paid"].to_numpy(),
        "loss_carryforward": result["loss_carryforward"].to_numpy(),
    })
    curves.to_csv(out / "curves.csv", index=False)

    metrics = {
        "strategy": name,
        "start": start,
        "end": end,
        "freq": freq,
        "periods": int(len(idx)),
        "initial_capital": 0.0,
        "final_pretax_nav": float(result["pretax_values"].iloc[-1]),
        "final_aftertax_nav": float(at_vals.iloc[-1]),
        "total_tax_paid": float(result["tax_paid"].sum()),
        "final_loss_carryforward": float(result["loss_carryforward"].iloc[-1]),
        "total_contributions": float(contribs.sum()),
        "pretax": pre,
        "aftertax": aft,
    }
    (out / "metrics.json").write_text(json.dumps(metrics, indent=2))

    summary = {"status": "done", "artifacts": ["metrics.json", "curves.csv"], **metrics}
    json.dump(summary, sys.stdout)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
