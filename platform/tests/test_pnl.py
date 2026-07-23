"""P/L math on /portfolio/positions. Pure — no DB, no fixtures.

    pytest tests/test_pnl.py     (or: python tests/test_pnl.py)
"""
from types import SimpleNamespace

from platform_app.routes import _value_position


def _pos(qty, avg_cost, total_cost):
    return SimpleNamespace(ticker="X", qty=qty, avg_cost=avg_cost,
                           total_cost=total_cost, market="Mercado à Vista")


def test_pnl():
    gain = _value_position(_pos(100, 10.0, 1000.0), 12.5)
    assert gain["market_value"] == 1250.0
    assert gain["pnl"] == 250.0
    assert gain["pnl_pct"] == 0.25

    loss = _value_position(_pos(100, 10.0, 1000.0), 8.0)
    assert loss["pnl"] == -200.0
    assert loss["pnl_pct"] == -0.2

    # No price → nulls, never "value == cost" (that would fake a 0% return).
    unpriced = _value_position(_pos(100, 10.0, 1000.0), None)
    assert unpriced["market_value"] is unpriced["pnl"] is unpriced["pnl_pct"] is None

    # Zero-cost holding (bonificação): value yes, percentage undefined, no ZeroDivision.
    free = _value_position(_pos(10, 0.0, 0.0), 3.0)
    assert free["market_value"] == 30.0 and free["pnl"] == 30.0
    assert free["pnl_pct"] is None

    # Header aggregate: the P/L base is the priced cost, not the whole book.
    book = [_value_position(_pos(100, 10.0, 1000.0), 12.5),
            _value_position(_pos(1, 500.0, 500.0), None)]
    priced = [p for p in book if p["market_value"] is not None]
    mv = sum(p["market_value"] for p in priced)
    base = sum(p["total_cost"] for p in priced)
    assert mv == 1250.0 and base == 1000.0 and (mv - base) / base == 0.25


if __name__ == "__main__":
    test_pnl()
    print("ok")
