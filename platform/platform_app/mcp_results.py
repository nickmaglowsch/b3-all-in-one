"""Backtest results in the shape a model can compare (spec/09).

The bench's run rows carry the engine's whole metrics payload, which is built
for a tear sheet; a model comparing thirty variants wants five numbers per run,
pre- and after-tax, in one table. Pure functions over the dicts the routes
already return — no database, no engine — so they are tested on literals.
"""
from __future__ import annotations


class ToolError(Exception):
    """A failure the model should read and act on (bad spec, unknown run...)."""


# Short keys a model can sort by, onto the engine's display labels.
METRICS = {
    "ann_return": "Ann. Return (%)",
    "volatility": "Ann. Volatility (%)",
    "sharpe": "Sharpe",
    "max_drawdown": "Max Drawdown (%)",
    "calmar": "Calmar",
}
# Which direction is "better". Drawdown is negative, so closest to zero is the
# largest; volatility is the one where less wins.
_DESCENDING = {"ann_return": True, "sharpe": True, "calmar": True,
               "max_drawdown": True, "volatility": False}
BASES = ("aftertax", "pretax")


def _basis(metrics: dict, basis: str) -> dict:
    block = metrics.get(basis) or {}
    return {k: block.get(label) for k, label in METRICS.items()}


def summarize(run: dict) -> dict:
    """One run row (routes._run_dict) as the handful of fields worth comparing."""
    m, p = run.get("metrics") or {}, run.get("params") or {}
    out = {"run_id": run["id"], "strategy": run["strategy"], "status": run["status"],
           "created_at": run.get("created_at")}
    if run.get("error"):
        out["error"] = run["error"][-600:]
    if m:
        out["window"] = {k: m.get(k) for k in ("start", "end", "freq", "market", "periods")}
        out.update({b: _basis(m, b) for b in BASES})
        out["total_tax_paid"] = m.get("total_tax_paid")
        out["final_aftertax_nav"] = m.get("final_aftertax_nav")
        out["data_warnings"] = m.get("data_warnings") or []
    else:
        out["window"] = {k: p.get(k) for k in ("start", "end", "freq")}
    if p.get("params"):
        out["params"] = p["params"]
    return out


def _bounded(value, lo: int, hi: int, name: str) -> int:
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        raise ToolError(f"{name} must be an integer")


def leaderboard(rows: list[dict], *, strategy=None, status=None, sort_by="created_at",
                basis="aftertax", limit=20) -> list[dict]:
    """Summaries filtered and ranked best-first by ``sort_by`` on ``basis``.
    Runs without that metric (pending, failed) sort last, newest first."""
    if sort_by != "created_at" and sort_by not in METRICS:
        raise ToolError(f"sort_by must be created_at or one of {sorted(METRICS)}")
    if basis not in BASES:
        raise ToolError(f"basis must be one of {list(BASES)}")
    picked = [r for r in rows if (not strategy or r["strategy"] == strategy)
              and (not status or r["status"] == status)]
    if sort_by != "created_at":
        sign = -1 if _DESCENDING[sort_by] else 1
        scored = [r for r in picked if (r.get(basis) or {}).get(sort_by) is not None]
        rest = [r for r in picked if (r.get(basis) or {}).get(sort_by) is None]
        picked = sorted(scored, key=lambda r: sign * r[basis][sort_by]) + rest
    return picked[:_bounded(limit, 1, 100, "limit")]


def _sample(n: int, k: int) -> list[int]:
    """Up to ``k`` indices spread over ``range(n)``, always keeping the last."""
    if n <= k:
        return list(range(n))
    return sorted({round(i * (n - 1) / (k - 1)) for i in range(k)})


def _yearly(dates: list[str], values: list | None, flows: list | None) -> dict:
    """Calendar-year return (%) of a NAV path, net of the money that came in
    during the year — a contribution is not performance."""
    if not values:
        return {}
    out, start = {}, 0
    for i, d in enumerate(dates):
        if i + 1 < len(dates) and dates[i + 1][:4] == d[:4]:
            continue
        added = sum((flows[j] or 0.0) for j in range(start + 1, i + 1)) if flows else 0.0
        v0, v1 = values[start], values[i]
        out[d[:4]] = round((v1 - v0 - added) / v0 * 100, 2) if v0 and v1 is not None else None
        start = i
    return out


def curve_digest(series: dict, max_points=60) -> dict:
    """The equity curves, downsampled, plus calendar-year returns — enough to see
    whether a result is one lucky year or a steady edge."""
    dates = series.get("date") or []
    idx = _sample(len(dates), _bounded(max_points, 2, 500, "max_points"))
    cols = [c for c in ("pretax", "aftertax", "drawdown") if c in series]
    return {
        "periods": len(dates),
        "curve": {"date": [dates[i] for i in idx],
                  **{c: [series[c][i] for i in idx] for c in cols}},
        "yearly_returns_pct": {b: _yearly(dates, series.get(b), series.get("contribution"))
                               for b in BASES},
    }
