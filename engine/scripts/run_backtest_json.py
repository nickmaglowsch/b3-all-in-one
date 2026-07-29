"""Headless JSON backtest runner for the platform bench API.

Usage:
    python scripts/run_backtest_json.py <strategy_name> <out_dir> <config_json>

<config_json> is a JSON string: {"params": {...}, "start": ..., "end": ...,
"freq": ..., "flows": [...]} — all keys optional. Reads the market plane from
Postgres via ENGINE_DATABASE_URL (falls back to the local sqlite DB when unset),
runs the registry strategy through run_simulation, writes metrics.json +
curves.csv into <out_dir>, and prints a one-line JSON summary to stdout.

Two modes, one runner. Without "flows" this is a plain backtest: the strategy's
own window and lump sum. With "flows" — [{"date": "YYYY-MM-DD", "amount": N},
...] — it is a what-if replay of a tenant's real dated cash flows: no lump sum
at all, the window opens at their first trade, and the money arrives on its true
dates as a dated ``contribution`` Series. Flows deploy at the next rebalance (a
documented approximation — a flow between two monthly marks lands on the later
one). Everything downstream of the window is identical, which is why the replay
does not get its own script.

Network: this path never touches yfinance. The IBOV benchmark (^BVSP) is stubbed
with a flat series — the bench reports the strategy's own pre/after-tax metrics,
not an index comparison, so the index is not needed. CDI daily is fetched from
the Brazilian Central Bank (api.bcb.gov.br, reliable) because build_shared_data
requires it; set ENGINE_BENCH_OFFLINE=1 to stub CDI too and run fully offline
(valid for price-only strategies, used by tests + the docker smoke).
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
    """A flat daily series over business days in [start, end] with a real
    DatetimeIndex — enough for build_shared_data's resample/rolling to run
    without a network fetch (pct_change == 0, so it adds no signal)."""
    idx = pd.bdate_range(start=start, end=end)
    return pd.Series(value, index=idx, name=name)


def _install_benchmark_stubs(start: str, end: str, offline: bool) -> None:
    """Replace the yfinance/BCB fetchers in the loaded modules with offline
    stubs. Patches both the data module and the name shared_data imported at
    import time. IBOV is always stubbed (no yfinance); CDI only when offline."""
    import backtests.core.data as data
    import backtests.core.shared_data as sd

    _real_bench = data.download_benchmark

    def _bench(ticker: str, s: str, e: str) -> pd.Series:
        if ticker == "^BVSP" or offline:
            return _flat_series(s, e, ticker, 100.0)
        return _real_bench(ticker, s, e)  # real ETF sleeves in online mode

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


def _capital_and_flows(cfg: dict, params: dict) -> tuple[str, float, object]:
    """``(start, initial_capital, contribution)`` for whichever mode was asked for.

    The only place the replay and the plain backtest differ. Keyed on the
    *presence* of "flows", not its truthiness: a caller that asked for a replay
    and sent an empty list gets an error, never a silent 100k lump-sum backtest
    reported under the same name.
    """
    if "flows" in cfg:
        flows = _build_flows(cfg["flows"] or [])
        start = str(cfg.get("start") or flows.index.min().date().isoformat())
        return start, 0.0, flows
    return (
        str(cfg.get("start") or params.get("start_date", "2005-01-01")),
        float(params.get("initial_capital", 100_000)),
        float(params.get("contribution", 0.0)),
    )


def main() -> None:
    name, out_dir, config_json = sys.argv[1], sys.argv[2], sys.argv[3]
    cfg = json.loads(config_json) if config_json else {}
    req_params = cfg.get("params") or {}

    from backtests.core.strategy_registry import get_registry
    from backtests.core.strategy_base import dedup_target_weights
    from backtests.core.simulation import run_simulation
    from backtests.core.metrics import (
        build_metrics, value_to_ret, strategy_daily_values,
    )

    strat = get_registry().get(name)
    params = strat.get_default_parameters()
    params.update(req_params)

    start, initial_capital, contribution = _capital_and_flows(cfg, params)
    end = _resolve_end(str(cfg.get("end") or params.get("end_date", "today")))
    freq = str(cfg.get("freq") or params.get("rebalance_freq", "ME"))
    needs_funds = bool(getattr(strat, "needs_fundamentals", False))

    offline = os.environ.get("ENGINE_BENCH_OFFLINE") == "1"
    _install_benchmark_stubs(start, end, offline)

    from backtests.core.shared_data import build_shared_data
    # db_path is ignored once ENGINE_DATABASE_URL routes to Postgres; pass the DSN
    # (or the sqlite default) so a bare sqlite run still works.
    db_path = os.environ.get("ENGINE_DATABASE_URL") or str(ROOT / "b3_market_data.sqlite")
    shared = build_shared_data(db_path, start, end, freq, include_fundamentals=needs_funds)

    ret_matrix, tw = strat.generate_signals(shared, params)
    tw = dedup_target_weights(tw, shared["adtv"])
    result = run_simulation(
        returns_matrix=ret_matrix.fillna(0.0),
        target_weights=tw,
        initial_capital=initial_capital,
        tax_rate=float(params.get("tax_rate", 0.15)),
        slippage=float(params.get("slippage", 0.001)),
        monthly_sales_exemption=float(params.get("monthly_sales_exemption", 20_000)),
        contribution=contribution,
        name=name,
    )

    idx = result["pretax_values"].index
    if len(idx) < 2:
        raise SystemExit(f"strategy '{name}' produced no investable periods for {start}..{end}")

    contribs = result["contributions"]
    pt_ret = value_to_ret(result["pretax_values"], contribs)
    at_ret = value_to_ret(result["aftertax_values"], contribs)
    # Zero is fine here even for a replay: reconstruct_daily_values is scale-free
    # and rebases a zero start to 1.0 itself, so the drawdown ratios hold.
    strat_daily = strategy_daily_values(shared, tw, initial_capital)
    ppy = {"ME": 12, "QE": 4, "W-FRI": 52}.get(freq, 12)

    pre = build_metrics(pt_ret, "Pre-Tax", ppy, daily_values=strat_daily)
    aft = build_metrics(at_ret, "After-Tax", ppy, daily_values=strat_daily)

    # Drawdown series on the after-tax NAV path (the "truth" curve).
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
        "initial_capital": initial_capital,
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
