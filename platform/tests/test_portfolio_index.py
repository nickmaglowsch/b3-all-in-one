"""GET /portfolio/index — the tenant's book as a daily TWR index vs IBOV/CDI
(spec/03 "Benchmarking").

The exact-value assertions live in the pure-math tests below: build_index takes
plain dicts, so every chaining rule is checked without a database. The endpoint
test then proves the wiring — tenant tables in, market plane priced, benchmarks
degraded honestly (conftest sets ENGINE_BENCH_OFFLINE=1). Run isolated:

    PLATFORM_TEST_DB=platform_test_pidx uv run pytest tests/test_portfolio_index.py
"""
from __future__ import annotations

import uuid
from datetime import date

import pytest

from platform_app.portfolio_index import (
    EXCLUDED_MARKETS, PRIMARY_ETF_MARKET, build_index, canonical,
    max_drawdown_pct, rebase, summarize,
)

D1, D2, D3 = date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)


# ── Pure math: no DB, no fixtures ─────────────────────────────────────────────

def test_price_move_is_the_whole_return():
    """100 shares bought at 10, price to 11 → index 110. The base day is 100
    even though the buy lands on it (buys are start-of-day inflows)."""
    out = build_index(
        [D1, D2],
        {"X": {D1: 10.0, D2: 11.0}},
        [(D1, "X", 100.0)],
        [(D1, "buy", 1000.0)],
    )
    assert out["index"] == [100.0, 110.0]
    assert out["nav"] == [1000.0, 1100.0]


def test_deposit_moves_nav_but_not_the_index():
    """The property that makes this a *time-weighted* return: doubling the
    position at the market price on D3 leaves the index flat at 110."""
    out = build_index(
        [D1, D2, D3],
        {"X": {D1: 10.0, D2: 11.0, D3: 11.0}},
        [(D1, "X", 100.0), (D3, "X", 100.0)],
        [(D1, "buy", 1000.0), (D3, "buy", 1100.0)],
    )
    assert out["index"] == [100.0, 110.0, 110.0]
    assert out["nav"][-1] == 2200.0          # NAV doubles...
    assert out["index"][-1] == 110.0         # ...the index does not


def test_income_counts_as_return_on_the_day_it_lands():
    """A R$50 dividend on a flat R$1000 book is +5%, because it left the book."""
    out = build_index(
        [D1, D2],
        {"X": {D1: 10.0, D2: 10.0}},
        [(D1, "X", 100.0)],
        [(D1, "buy", 1000.0), (D2, "income", 50.0)],
    )
    assert out["index"] == [100.0, 105.0]


def test_split_is_continuous():
    """2:1 split: qty doubles the same day the raw close halves, so the index
    is flat. This is why the index values at raw `close`, not `adj_close`."""
    out = build_index(
        [D1, D2],
        {"X": {D1: 10.0, D2: 5.0}},
        [(D1, "X", 100.0), (D2, "X", 100.0)],
        [(D1, "buy", 1000.0)],
    )
    assert out["index"] == [100.0, 100.0]
    assert out["nav"] == [1000.0, 1000.0]


def test_holdings_before_the_window_open_the_book():
    """Pre-window buys become opening holdings; their cash is NOT re-counted as
    a flow (that would swamp day one). Day one has no prior capital, so it is
    flat at 100 and the return starts on D3."""
    out = build_index(
        [D2, D3],
        {"X": {D2: 10.0, D3: 12.0}},
        [(D1, "X", 100.0)],
        [(D1, "buy", 1000.0)],
    )
    assert out["nav"] == [1000.0, 1200.0]
    assert out["index"] == [100.0, 120.0]


def test_sell_is_return_not_a_loss():
    """Selling the whole book at the mark drops NAV to zero without moving the
    index — the proceeds are an end-of-day outflow, not a -100% day."""
    out = build_index(
        [D1, D2],
        {"X": {D1: 10.0, D2: 10.0}},
        [(D1, "X", 100.0), (D2, "X", -100.0)],
        [(D1, "buy", 1000.0), (D2, "sell", 1000.0)],
    )
    assert out["nav"] == [1000.0, 0.0]
    assert out["index"] == [100.0, 100.0]


def test_off_grid_flows_roll_forward():
    """A trade dated on a day the plane has no session still counts — it snaps
    to the next trading day instead of vanishing."""
    saturday = date(2024, 1, 6)      # between D3 and D4
    d4 = date(2024, 1, 8)
    out = build_index(
        [D3, d4],
        {"X": {D3: 10.0, d4: 10.0}},
        [(D3, "X", 100.0), (saturday, "X", 100.0)],
        [(D3, "buy", 1000.0), (saturday, "buy", 1000.0)],
    )
    assert out["nav"] == [1000.0, 2000.0]
    assert out["index"] == [100.0, 100.0]    # bought at the mark → no return


def test_a_zeroed_position_cannot_annihilate_the_index():
    """A qty_event that empties a position with no matching sell is a data gap,
    not a -100% day. Without the guard the index multiplies by zero and can never
    recover — every later day would read 0 no matter what the book did."""
    d4 = date(2024, 1, 5)
    out = build_index(
        [D1, D2, D3, d4],
        {"X": {D1: 10.0, D2: 10.0, D3: 10.0, d4: 10.0}},
        # 100 shares in, all 100 vanish on D3, 100 bought back on D4.
        [(D1, "X", 100.0), (D3, "X", -100.0), (d4, "X", 100.0)],
        [(D1, "buy", 1000.0), (d4, "buy", 1000.0)],
    )
    assert out["nav"] == [1000.0, 1000.0, 0.0, 1000.0]
    assert out["index"] == [100.0, 100.0, 100.0, 100.0]   # flat, never zeroed


def test_unpriced_tickers_are_reported_not_swallowed():
    out = build_index(
        [D1], {"X": {D1: 10.0}},
        [(D1, "X", 100.0), (D1, "NOPE11", 50.0)],
        [(D1, "buy", 1000.0)],
    )
    assert out["unpriced"] == ["NOPE11"]


def test_fractional_trades_fold_onto_the_round_lot():
    """PETR4F is PETR4 at a smaller lot, and the plane only carries the round lot.
    Left raw, every fractional trade lands on a ticker that can never be priced:
    the shares vanish from NAV and the ticker is disclosed as unpriced. This is
    what put half a real book in the "sem cotação na base" list."""
    deltas = [(D1, canonical(t), q) for t, q in (("PETR4", 100.0), ("PETR4F", 7.0))]
    out = build_index([D1], {"PETR4": {D1: 10.0}}, deltas, [(D1, "buy", 1070.0)])
    assert out["unpriced"] == []
    assert out["nav"] == [1070.0]      # all 107 shares valued, not just the 100


def test_canonical_only_strips_the_fractional_suffix():
    assert canonical("VALE3F") == "VALE3"
    assert canonical("SAPR11F") == "SAPR11"
    assert canonical("PETR4") == "PETR4"
    assert canonical("BTLG11") == "BTLG11"   # no round-lot B3 code ends in F


def test_etf_subscription_is_the_buy_not_a_duplicate():
    """Subscribe 50 HASH11 in the primary market (booked as HASH11H), sell them
    on the exchange months later. Drop the subscription and the sell is an orphan:
    the index carries -50 shares it never bought and marks them at every close,
    subtracting a position that does not exist from the NAV forever after."""
    d1, d2 = date(2021, 4, 22), date(2021, 11, 29)
    subscribe = (d1, canonical("HASH11H", PRIMARY_ETF_MARKET), 50.0)
    sell = (d2, canonical("HASH11", "Mercado à Vista"), -50.0)
    out = build_index(
        [d1, d2], {"HASH11": {d1: 47.0, d2: 59.9}},
        [subscribe, sell], [(d1, "buy", 2351.0), (d2, "sell", 2995.0)],
    )
    assert out["nav"] == [2350.0, 0.0]     # held then flat — never short
    assert out["index"][-1] > out["index"][0]   # sold above the mark: a gain


def test_primary_etf_market_is_no_longer_excluded():
    """The subscription is a real acquisition of the ETF, so it belongs in the
    index rather than in the disclosed-exclusions list."""
    assert PRIMARY_ETF_MARKET not in EXCLUDED_MARKETS
    assert canonical("HASH11H", PRIMARY_ETF_MARKET) == "HASH11"
    # ...and the H strip cannot reach a spot ticker, which is why it is scoped.
    assert canonical("HASH11H") == "HASH11H"


def test_empty_grid_is_not_a_crash():
    assert build_index([], {}, [], []) == {
        "dates": [], "index": [], "nav": [], "unpriced": []}


def test_rebase_and_drawdown():
    assert rebase([200.0, 220.0, 180.0]) == [100.0, 110.0, 90.0]
    assert rebase([0.0, 0.0]) == [0.0, 0.0]          # no first point → untouched
    # Peak 110 → trough 90 is -18.18%, not -10% off the start.
    assert max_drawdown_pct([100.0, 110.0, 90.0]) == -18.18
    assert max_drawdown_pct([100.0, 110.0]) == 0.0   # never below peak


def test_summarize_withholds_annualised_return_under_a_year():
    short = summarize([100.0, 110.0], n_days=60)
    assert short["total_return_pct"] == 10.0
    assert short["ann_return_pct"] is None           # 60 days is not a CAGR

    full = summarize([100.0, 121.0], n_days=504)     # 2 years, +21%
    assert full["ann_return_pct"] == pytest.approx(10.0, abs=0.05)


# ── Endpoint ──────────────────────────────────────────────────────────────────
# Marked per-test, not module-wide: the math tests above are sync.

_asyncio = pytest.mark.asyncio(loop_scope="session")


async def _seed_book(tenant_id: uuid.UUID) -> None:
    """A clean 2023 book on tickers the market plane prices, plus one Futuro
    trade that must be excluded. Mirrors test_whatif's seed; test_portfolio_
    settings re-seeds itself, so clearing here is safe."""
    from sqlalchemy import delete

    from platform_app.db import async_session_maker
    from platform_app.models import CashEvent, Position, Trade

    async with async_session_maker() as session:
        for model in (Trade, Position, CashEvent):
            await session.execute(delete(model).where(model.tenant_id == tenant_id))
        await session.commit()

    trades = [
        ("2023-01-16", "Compra", "Mercado à Vista", "PETR4", 1000, 25.0, 25_000.0, 1000),
        ("2023-03-15", "Compra", "Mercado à Vista", "VALE3", 500, 70.0, 35_000.0, 500),
        ("2023-09-11", "Venda", "Mercado à Vista", "PETR4", 300, 30.0, 9_000.0, -300),
        # Excluded: a daytrade future has no overnight position to value.
        ("2023-05-02", "Compra", "Futuro", "WINM23", 1, 110_000.0, 110_000.0, 1),
    ]
    async with async_session_maker() as session:
        for d, side, market, tk, qty, px, amt, sq in trades:
            session.add(Trade(
                tenant_id=tenant_id, date=date.fromisoformat(d), side=side,
                market=market, ticker=tk, qty=qty, price=px, amount=amt,
                signed_qty=sq, source_file="seed"))
        session.add(CashEvent(
            tenant_id=tenant_id, date=date(2023, 6, 15), ticker="PETR4",
            event_type="Dividendo", event_category="income", is_amortization=False,
            qty=0, price=0, amount=1_500.0, source_file="seed"))
        await session.commit()


@_asyncio
@pytest.mark.market
async def test_portfolio_index_charts_the_real_book(client):
    token = (await client.post("/auth/dev-login")).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    tid = (await client.get("/me", headers=headers)).json()["id"]
    await _seed_book(uuid.UUID(tid))

    r = await client.get("/portfolio/index", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()

    series = body["series"]
    assert len(series) > 100                       # ~3 years of sessions
    # The index opens at 100; the first plotted point already carries day one's
    # return. Bought 1000 PETR4 at R$25.00 on a session that closed at R$24.04,
    # so the book is down 3.84% before it has held anything overnight — the
    # whole pipeline (tenant tables → plane closes → TWR) pinned to one number.
    assert series[0]["portfolio"] == pytest.approx(96.16, abs=0.01)
    dates = [p["date"] for p in series]
    assert dates == sorted(dates) and len(set(dates)) == len(dates)
    assert all(p["portfolio"] > 0 for p in series)  # a real book never zeroes out
    assert body["start"] == dates[0] and body["end"] == dates[-1]
    assert body["nav"] > 0                          # still holding PETR4 + VALE3

    m = body["metrics"]["portfolio"]
    assert m["total_return_pct"] is not None
    assert m["ann_return_pct"] is not None          # >1y window → CAGR is meaningful
    assert m["max_dd_pct"] <= 0

    # Surfaced, not hidden: the Futuro trade and the offline benchmarks.
    assert "Futuro" in body["excluded"]["markets"]
    assert body["excluded"]["benchmarks"] is True   # ENGINE_BENCH_OFFLINE=1
    assert not body["excluded"]["unpriced_tickers"]  # PETR4/VALE3 are in the plane
    assert "ibov" not in series[0] and "cdi" not in series[0]

    # The plane starts after the first trade only if the book predates it; this
    # seed starts 2023-01-16 and the plane 2023-01-02, so no clamp.
    assert body["coverage"]["first_trade"] == "2023-01-16"
    assert body["coverage"]["clamped"] is False
    assert body["coverage"]["trades_after_plane"] == 0   # seed ends inside the plane


@_asyncio
async def test_portfolio_index_422_without_trades(client):
    from sqlalchemy import delete

    from platform_app.db import async_session_maker
    from platform_app.models import CashEvent, Position, Trade

    token = (await client.post("/auth/dev-login")).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    tid = uuid.UUID((await client.get("/me", headers=headers)).json()["id"])
    async with async_session_maker() as session:
        for model in (Trade, Position, CashEvent):
            await session.execute(delete(model).where(model.tenant_id == tid))
        await session.commit()

    r = await client.get("/portfolio/index", headers=headers)
    assert r.status_code == 422
    assert "import a portfolio" in r.json()["detail"]
