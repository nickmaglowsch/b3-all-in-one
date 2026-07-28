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
    EXCLUDED_MARKETS, PRIMARY_ETF_MARKET, RENDA_FIXA_MARKET, accrue_rf,
    build_index, canonical, is_excluded, max_drawdown_pct, rebase,
    select_tickers, summarize, years_between,
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


def test_canonical_leaves_a_renda_fixa_contract_code_ending_in_f_intact():
    """Renda fixa contracts share the "Renda Fixa" label with the listed ETFs,
    so the market cannot scope this strip — the round-lot shape has to. taxcore
    strips at a different pipeline stage, so a truncation here would leave the
    two layers disagreeing about one holding."""
    assert canonical("LCI - LCI24H9RKF") == "LCI - LCI24H9RKF"
    assert canonical("CDB - CDB24C7HQ2L") == "CDB - CDB24C7HQ2L"


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


def test_renda_fixa_splits_by_ticker_shape_not_by_label():
    """B3 files listed fixed-income ETFs and real OTC contracts under one market
    label. Excluding the label threw out 11% of a real book along with the CDBs —
    and disagreed with taxcore, which has always kept the ETFs."""
    assert not is_excluded("LFTB11", RENDA_FIXA_MARKET)      # listed ETF
    assert not is_excluded("IMAB11", RENDA_FIXA_MARKET)
    assert is_excluded("CDB123456789ABC", RENDA_FIXA_MARKET)  # OTC contract code
    assert is_excluded("", RENDA_FIXA_MARKET)
    assert is_excluded("WINJ20", "Futuro")                    # still no position
    assert not is_excluded("PETR4", "Mercado à Vista")


def test_a_contract_joins_the_book_only_once_it_can_be_priced():
    priced = frozenset({"CDB - CDB24C7HQ2L"})
    assert not is_excluded("CDB - CDB24C7HQ2L", RENDA_FIXA_MARKET, priced)
    assert is_excluded("LCI - LCI24H9RKV3", RENDA_FIXA_MARKET, priced)
    # Whatever it can price, a Futuro trade is still not an overnight position.
    assert is_excluded("WINJ20", "Futuro", frozenset({"WINJ20"}))


def test_carrying_an_unpriced_contract_would_book_its_aplicacao_as_a_loss():
    """Why an unpriced contract is kept out of the book rather than carried at 0.

    On a book that holds nothing else, build_index's zero-numerator guard hides
    it. On a real mixed book it does not: the equities keep the numerator
    positive, so the chain divides a NAV the contract never joined by a
    denominator its aplicação did — and the purchase reads as an immediate,
    permanent loss of that money.
    """
    out = build_index(
        [D1, D2], {}, [(D1, "CDB - X", 10_000.0)], [(D1, "buy", 10_000.0)])
    assert out["unpriced"] == ["CDB - X"]
    assert out["nav"] == [0.0, 0.0]
    assert out["index"] == [100.0, 100.0]  # only because build_index holds flat

    # The same contract beside R$100k of equity, both bought on D1, price flat.
    mixed = build_index(
        [D1, D2],
        {"PETR4": {D1: 10.0, D2: 10.0}},
        [(D1, "PETR4", 10_000.0), (D1, "CDB - X", 10_000.0)],
        [(D1, "buy", 100_000.0), (D1, "buy", 10_000.0)],
    )
    assert mixed["unpriced"] == ["CDB - X"]
    # Nothing moved, and the book is down 9.09% for good — 10k/110k.
    assert mixed["index"][-1] == pytest.approx(90.91, abs=0.01)


# ── accrue_rf: a contract's own marks imply its rate ──────────────────────────

def test_accrue_rf_lands_exactly_on_both_of_b3s_marks():
    """A CDB bought at PU 0.01 and redeemed at 0.0121 is worth exactly the
    aplicação on the first day and exactly the resgate on the last — those are
    reported values, not model output, so the path must not round them away."""
    grid = [date(2024, 1, d) for d in range(2, 12)]
    out = accrue_rf(grid, {"CDB - X": [(grid[0], 0.01), (grid[-1], 0.0121)]})
    series = out["CDB - X"]
    assert series[grid[0]] == pytest.approx(0.01)
    assert series[grid[-1]] == pytest.approx(0.0121)
    assert len(series) == len(grid)
    assert all(series[a] < series[b] for a, b in zip(grid, grid[1:]))


def test_accrue_rf_compounds_rather_than_interpolating_straight():
    """Interest compounds, so the midpoint of a 16x run is 4x, not 8.5x. A
    straight line would understate every day in the first half of a contract."""
    grid = [date(2024, 1, d) for d in range(2, 7)]
    out = accrue_rf(grid, {"CDB - X": [(grid[0], 1.0), (grid[4], 16.0)]})
    assert out["CDB - X"][grid[2]] == pytest.approx(4.0)


def test_accrue_rf_segments_between_every_mark():
    """A second aplicação is another real mark, not noise: the contract accrued
    at one rate before it and may accrue at another after."""
    grid = [date(2024, 1, d) for d in range(2, 7)]
    marks = [(grid[0], 1.0), (grid[2], 2.0), (grid[4], 2.0)]
    series = accrue_rf(grid, {"CDB - X": marks})["CDB - X"]
    assert series[grid[1]] == pytest.approx(2 ** 0.5)   # doubling over 2 days
    assert series[grid[3]] == pytest.approx(2.0)        # flat over the second


def test_accrue_rf_skips_a_contract_with_nothing_to_interpolate():
    """One mark implies no rate. A contract still held has only its aplicação,
    and inventing a curve from it is exactly what this must not do."""
    grid = [date(2024, 1, 2), date(2024, 1, 3)]
    assert accrue_rf(grid, {"CDB - X": [(grid[0], 0.01)]}) == {}
    assert accrue_rf(grid, {"CDB - X": [(grid[0], 0.0), (grid[1], 0.0)]}) == {}
    assert accrue_rf([], {"CDB - X": [(date(2024, 1, 2), 0.01)]}) == {}


def test_marks_starting_mid_window_do_not_fabricate_a_return():
    """A ticker the plane only starts covering after the book already held it used
    to be worth 0 until its first mark, then jump to full value in one session.
    NAV steps up with no matching buy flow, so the chain books the step as profit.
    Carried flat at its first known close instead: wrong by the pre-coverage drift,
    which is bounded and disclosable, rather than by a whole invented rally."""
    d4 = date(2024, 1, 5)
    out = build_index(
        [D1, D2, D3, d4],
        {"LATE": {D3: 10.0, d4: 10.0}},     # bought on D1, first mark on D3
        [(D1, "LATE", 100.0)], [(D1, "buy", 1000.0)],
    )
    assert out["nav"] == [1000.0] * 4
    assert out["index"] == [100.0] * 4       # flat, not a +100% day on D3
    assert out["unpriced"] == []


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
    short = summarize([100.0, 110.0], years=0.25)
    assert short["total_return_pct"] == 10.0
    assert short["ann_return_pct"] is None           # a quarter is not a CAGR

    full = summarize([100.0, 121.0], years=2.0)      # 2 years, +21%
    assert full["ann_return_pct"] == pytest.approx(10.0, abs=0.05)


def test_picking_a_calendar_year_off_the_filter_gets_a_cagr():
    """The window a date filter makes trivial to select, and the one the old
    sessions/252 proxy refused: B3 runs ~250 sessions a year, so twelve real
    months landed at 0.99 "years" and were captioned "período < 1 ano"."""
    for year, leap in ((2025, False), (2024, True)):
        span = years_between(date(year, 1, 1), date(year, 12, 31))
        assert 0.99 <= span <= 1.005, (year, leap, span)
        assert summarize([100.0, 110.0], span)["ann_return_pct"] is not None


def test_a_short_window_is_still_refused():
    """The guard exists to stop a quarter being annualised into a headline."""
    q = years_between(date(2025, 1, 1), date(2025, 3, 31))
    assert summarize([100.0, 110.0], q)["ann_return_pct"] is None
    # Eleven months is short enough to still be honest about it.
    assert summarize([100.0, 110.0], years_between(
        date(2025, 1, 1), date(2025, 11, 30)))["ann_return_pct"] is None


# ── Selecting a subset of the book ────────────────────────────────────────────
# "Which of my assets is helping and which is hurting" is answered by rebuilding
# the index over a subset, so the cash of the dropped assets has to go with them.

def test_selecting_a_ticker_drops_its_neighbours_cash_too():
    """A buy of an asset that is not in the selection is not an inflow to the
    selected book. Left in, it inflates the denominator of the chain on that day
    and the subset reads as a loss it never had."""
    qty = [(D1, "X", 100.0), (D1, "Y", 50.0)]
    flows = [(D1, "buy", 1000.0, "X"), (D1, "buy", 500.0, "Y"),
             (D2, "income", 30.0, "Y")]

    kept_qty, kept_flows = select_tickers(qty, flows, {"X"})

    assert kept_qty == [(D1, "X", 100.0)]
    # Flows come back as the 3-tuples build_index takes — the ticker's only job
    # was to decide whether the flow survives.
    assert kept_flows == [(D1, "buy", 1000.0)]


def test_no_selection_keeps_the_whole_book():
    qty = [(D1, "X", 100.0)]
    flows = [(D1, "buy", 1000.0, "X"), (D2, "income", 30.0, None)]

    kept_qty, kept_flows = select_tickers(qty, flows, None)

    assert kept_qty == qty
    assert kept_flows == [(D1, "buy", 1000.0), (D2, "income", 30.0)]


def test_a_selection_drops_cash_that_names_no_ticker():
    """An untickered cash event cannot be attributed to a selected asset, so a
    filtered book must not claim it. Counting it would credit the subset with
    return earned by holdings the user just excluded."""
    _, kept = select_tickers([], [(D1, "income", 30.0, None)], {"X"})
    assert kept == []


def test_a_selected_subset_is_an_index_of_only_that_subset():
    """End to end over the pure math, and the whole point of the feature: X
    doubles while Y halves, so the book reads +25% and neither number is on the
    chart. Selecting one at a time is what shows the +100% and the -50%."""
    closes = {"X": {D1: 10.0, D2: 20.0}, "Y": {D1: 10.0, D2: 5.0}}
    qty = [(D1, "X", 100.0), (D1, "Y", 100.0)]
    flows = [(D1, "buy", 1000.0, "X"), (D1, "buy", 1000.0, "Y")]

    def index_of(keep):
        return build_index([D1, D2], closes, *select_tickers(qty, flows, keep))["index"]

    assert index_of(None) == [100.0, 125.0]
    assert index_of({"X"}) == [100.0, 200.0]
    assert index_of({"Y"}) == [100.0, 50.0]


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


async def _seed_renda_fixa(tenant_id: uuid.UUID) -> None:
    """Three OTC contracts on top of `_seed_book`, one of each outcome.

    Redeemed (two marks, chartable), still held (one mark), and frozen by a
    custody withdrawal that never settled. Denominated the way B3 denominates
    CDBs: units of R$0.01, so the per-unit prices are small and the quantities
    large — which is also what proves nothing here assumes a share-like PU.
    """
    from platform_app.db import async_session_maker
    from platform_app.models import CashEvent, Trade

    rf = [
        # Redeemed: 25,000 in at PU 0.01, 30,250 back at 0.0121 — a real 21%.
        ("2023-02-01", "Compra", "CDB - REDEEMED", 2_500_000, 0.01, 25_000.0, 2_500_000),
        ("2023-11-01", "Venda", "CDB - REDEEMED", 2_500_000, 0.0121, 30_250.0, -2_500_000),
        # Still held: no resgate, so no rate is implied and none is invented.
        ("2023-03-01", "Compra", "CDB - OPEN", 1_800_000, 0.01, 18_000.0, 1_800_000),
        # Frozen: two marks, but the second never paid — see event below.
        ("2023-04-01", "Compra", "CDB - FROZEN", 950_000, 0.01, 9_500.0, 950_000),
        ("2023-10-02", "Venda", "CDB - FROZEN", 950_000, 0.011, 10_450.0, -950_000),
    ]
    async with async_session_maker() as session:
        for d, side, tk, qty, px, amt, sq in rf:
            session.add(Trade(
                tenant_id=tenant_id, date=date.fromisoformat(d), side=side,
                market=RENDA_FIXA_MARKET, ticker=tk, qty=qty, price=px,
                amount=amt, signed_qty=sq, source_file="seed"))
        session.add(CashEvent(
            tenant_id=tenant_id, date=date(2023, 10, 2), ticker="CDB - FROZEN",
            event_type="RETIRADA DE CUSTÓDIA", event_category="frozen",
            is_amortization=False, qty=950_000, price=0, amount=0,
            source_file="seed"))
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
@pytest.mark.market
async def test_portfolio_index_filters_to_the_selected_tickers(client):
    """`?tickers=` rebuilds the index over a subset of the book — the feature
    that answers "which of these is helping and which is hurting"."""
    token = (await client.post("/auth/dev-login")).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    tid = (await client.get("/me", headers=headers)).json()["id"]
    await _seed_book(uuid.UUID(tid))

    whole = (await client.get("/portfolio/index", headers=headers)).json()
    # Every ticker the index can carry, so the UI can offer the filter at all.
    # WINM23 is excluded upstream (Futuro) and is not on the menu.
    assert whole["tickers"] == ["PETR4", "VALE3"]

    r = await client.get("/portfolio/index?tickers=PETR4", headers=headers)
    assert r.status_code == 200, r.text
    petr = r.json()

    assert petr["tickers"] == ["PETR4"]        # only the selection is charted...
    assert petr["universe"] == ["PETR4", "VALE3"]   # ...the menu stays whole
    # A different book is a different line. Identical curves would mean the
    # filter was accepted and ignored.
    assert [p["portfolio"] for p in petr["series"]] != [
        p["portfolio"] for p in whole["series"]]

    # The window is pinned to the whole book, so toggling an asset changes the
    # lines and never the x-axis — otherwise the two curves are not comparable.
    assert petr["start"] == whole["start"] and petr["end"] == whole["end"]
    assert len(petr["series"]) == len(whole["series"])
    assert petr["coverage"]["first_trade"] == whole["coverage"]["first_trade"]

    # VALE3 is bought two months into the window; before that the subset holds
    # nothing, so it is flat at the base rather than undefined.
    vale = (await client.get(
        "/portfolio/index?tickers=VALE3", headers=headers)).json()
    assert vale["series"][0]["portfolio"] == 100.0
    assert vale["nav"] > 0


@_asyncio
@pytest.mark.market
async def test_selecting_nothing_is_422_not_an_empty_chart(client):
    """A selection that matches no ticker in the book has no index to draw. A
    200 with a flat line would read as "these assets went nowhere"."""
    token = (await client.post("/auth/dev-login")).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    tid = (await client.get("/me", headers=headers)).json()["id"]
    await _seed_book(uuid.UUID(tid))

    r = await client.get("/portfolio/index?tickers=NOPE11", headers=headers)
    assert r.status_code == 422
    assert "selecionado" in r.json()["detail"] or "select" in r.json()["detail"]


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


@_asyncio
@pytest.mark.market
async def test_renda_fixa_is_charted_from_its_own_marks(client):
    """The end-to-end wiring for OTC paper: contract rows in the tenant's tables,
    a per-unit path interpolated from B3's own aplicação and resgate prices, and
    every contract that has no such path named with a reason instead."""
    token = (await client.post("/auth/dev-login")).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    tid = uuid.UUID((await client.get("/me", headers=headers)).json()["id"])
    await _seed_book(tid)
    await _seed_renda_fixa(tid)

    body = (await client.get("/portfolio/index", headers=headers)).json()

    # Only the redeemed contract is chartable, and it is a first-class holding
    # in the filter menu alongside the equities.
    assert body["universe"] == ["CDB - REDEEMED", "PETR4", "VALE3"]
    # Priced, so it is never reported as merely missing a quote.
    assert "CDB - REDEEMED" not in body["excluded"]["unpriced_tickers"]

    reasons = {r["ticker"]: r["reason"] for r in body["excluded"]["renda_fixa"]}
    assert set(reasons) == {"CDB - OPEN", "CDB - FROZEN"}
    assert "resgate" in reasons["CDB - OPEN"]
    assert "custódia" in reasons["CDB - FROZEN"]
    # Named one by one, so "Renda Fixa" never appears as a bare market label.
    assert "Renda Fixa" not in body["excluded"]["markets"]

    # Isolating the contract reproduces exactly the return its two marks imply:
    # 25,000 in, 30,250 back. Nothing else touches that line, so 21.00% is the
    # whole assertion — the accrual is anchored, not modelled.
    only = (await client.get(
        "/portfolio/index?tickers=CDB - REDEEMED", headers=headers)).json()
    assert only["tickers"] == ["CDB - REDEEMED"]
    assert only["metrics"]["portfolio"]["total_return_pct"] == pytest.approx(
        21.00, abs=0.01)
    # It compounds, so it never gives ground on the way there.
    curve = [p["portfolio"] for p in only["series"]]
    assert curve == sorted(curve)


# ── Open contracts: the indexer the tenant entered, over the BCB levels ───────

def _levels(grid, daily_rate):
    """A $CDI-shaped cumulative level series over `grid`, seeded at 1 + i0."""
    out, running = {}, 1.0
    for d in grid:
        running *= 1.0 + daily_rate
        out[d] = running
    return out


def test_an_open_contract_accrues_from_its_aplicacao_under_its_terms():
    """One mark and no resgate used to mean no line at all. With the indexer
    entered, the curve starts at the reported aplicação price — the anchor is
    still a real number, only the slope is a model."""
    grid = [date(2024, 1, d) for d in range(2, 7)]
    out = accrue_rf(
        grid, {"CDB - X": [(grid[0], 0.01)]},
        terms={"CDB - X": ("cdi_pct", 100.0)},
        levels={"$CDI": _levels(grid, 0.001)},
    )
    series = out["CDB - X"]
    assert series[grid[0]] == pytest.approx(0.01)         # anchored, not modelled
    assert series[grid[-1]] == pytest.approx(0.01 * 1.001 ** 4)


def test_a_percentage_of_cdi_compounds_the_rate_not_the_level():
    """50% do CDI over a doubling is not a 50% gain. Scaling the accumulated
    level would say it is; scaling the daily rate — which is what the contract
    actually pays — does not."""
    grid = [date(2024, 1, d) for d in range(2, 7)]
    levels = _levels(grid, 0.10)                          # 10% a day, 4 steps on
    out = accrue_rf(
        grid, {"CDB - X": [(grid[0], 1.0)]},
        terms={"CDB - X": ("cdi_pct", 50.0)}, levels={"$CDI": levels},
    )
    assert out["CDB - X"][grid[-1]] == pytest.approx(1.05 ** 4)
    naive = 1.0 + 0.5 * (levels[grid[-1]] / levels[grid[0]] - 1.0)
    assert out["CDB - X"][grid[-1]] != pytest.approx(naive)


def test_prefixado_needs_no_series_at_all():
    grid = [date(2024, 1, d) for d in range(2, 7)]
    out = accrue_rf(
        grid, {"CDB - X": [(grid[0], 1.0)]},
        terms={"CDB - X": ("pre", 12.0)}, levels={},
    )
    daily = 1.12 ** (1 / 252)
    assert out["CDB - X"][grid[-1]] == pytest.approx(daily ** 4)


def test_cdi_plus_a_spread_adds_the_spread_on_top_of_the_index():
    grid = [date(2024, 1, d) for d in range(2, 7)]
    out = accrue_rf(
        grid, {"CDB - X": [(grid[0], 1.0)]},
        terms={"CDB - X": ("cdi_plus", 2.0)},
        levels={"$CDI": _levels(grid, 0.001)},
    )
    expected = 1.001 ** 4 * 1.02 ** (4 / 252)
    assert out["CDB - X"][grid[-1]] == pytest.approx(expected)


def test_terms_extend_a_redeemed_contract_without_disturbing_its_marks():
    """A partial resgate leaves the rest running. The segment between the two
    real marks stays exact; the accrual only takes over past the last one."""
    grid = [date(2024, 1, d) for d in range(2, 9)]
    out = accrue_rf(
        grid, {"CDB - X": [(grid[0], 1.0), (grid[3], 2.0)]},
        terms={"CDB - X": ("pre", 12.0)},
        levels={},
    )
    series = out["CDB - X"]
    assert series[grid[0]] == pytest.approx(1.0)          # both reported marks
    assert series[grid[3]] == pytest.approx(2.0)          # survive untouched
    assert series[grid[-1]] == pytest.approx(2.0 * 1.12 ** (3 / 252))


def test_an_indexed_contract_with_no_series_draws_nothing():
    """A flat line would read as "this paid nothing" for a year. Better absent
    and disclosed — which is what the route turns this empty result into."""
    grid = [date(2024, 1, d) for d in range(2, 7)]
    assert accrue_rf(
        grid, {"CDB - X": [(grid[0], 1.0)]},
        terms={"CDB - X": ("ipca_plus", 6.0)}, levels={}) == {}
    # Same when the series exists but starts after the contract does.
    late = {"$IPCA": {date(2024, 6, 1): 1.0}}
    assert accrue_rf(
        grid, {"CDB - X": [(grid[0], 1.0)]},
        terms={"CDB - X": ("ipca_plus", 6.0)}, levels=late) == {}


def test_a_stale_bcb_series_draws_nothing_rather_than_a_flat_tail():
    """A series that stops early used to be forward-filled flat over the rest of
    the window — the contract reads as having paid nothing for months, on paper
    that was accruing the whole time. The start was guarded; the end was not."""
    grid = [date(2024, 1, d) for d in range(2, 12)]
    stale = {"$CDI": {grid[0]: 1.0, grid[1]: 1.001, grid[2]: 1.002}}
    assert accrue_rf(
        grid, {"CDB - X": [(grid[0], 1.0)]},
        terms={"CDB - X": ("cdi_pct", 100.0)}, levels=stale) == {}
    # A couple of sessions behind is structural — SGS publishes after the close —
    # and must still draw, or the feature never works on a live plane.
    fresh = {"$CDI": {d: 1.0 + 0.001 * i for i, d in enumerate(grid[:-1])}}
    assert accrue_rf(
        grid, {"CDB - X": [(grid[0], 1.0)]},
        terms={"CDB - X": ("cdi_pct", 100.0)}, levels=fresh) != {}


def test_a_mark_before_the_window_is_not_redrawn_inside_it():
    """The aplicação predates the plane's first close (coverage.clamped). Snapped
    to index 0, the whole pre-window run gets redrawn over the visible sessions —
    a five-year double showing up as a six-session double. Only the part of the
    segment the window can actually show belongs on the chart."""
    grid = [date(2025, 3, d) for d in range(3, 10)]
    out = accrue_rf(
        grid, {"CDB - X": [(date(2020, 1, 2), 1.0), (grid[-1], 2.0)]})
    series = out["CDB - X"]
    assert series[grid[-1]] == pytest.approx(2.0)      # the reported mark holds
    # Almost all the growth happened before the window, so the visible span is
    # nearly flat rather than a doubling.
    assert series[grid[0]] > 1.9
    assert series[grid[-1]] / series[grid[0]] < 1.01


@_asyncio
@pytest.mark.market
async def test_a_contract_the_accrual_refuses_leaves_the_book_entirely(client):
    """The gap `is_excluded` alone cannot close, and the reason the route drops
    undrawn contracts instead of only disclosing them.

    `_book` decides admission before the grid exists, so it can only tell which
    contracts are priceable *in principle*. Whether a BCB series is there to
    price them against is known one step later — and a contract admitted there
    and refused here still carries its aplicação as an inflow against a NAV it
    never joined. That is a permanent loss of the whole purchase, and a caveat
    string does not undo it.

    Seeded with terms naming `$CDI`, which the test plane does not carry.
    """
    from sqlalchemy import delete

    from platform_app.db import async_session_maker
    from platform_app.models import RendaFixaTerms

    token = (await client.post("/auth/dev-login")).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    tid = uuid.UUID((await client.get("/me", headers=headers)).json()["id"])
    await _seed_book(tid)
    await _seed_renda_fixa(tid)

    async with async_session_maker() as session:
        await session.execute(
            delete(RendaFixaTerms).where(RendaFixaTerms.tenant_id == tid))
        session.add(RendaFixaTerms(tenant_id=tid, ticker="CDB - OPEN",
                                   indexer="cdi_pct", rate_pct=100.0))
        await session.commit()

    drawable = (await client.get(
        "/portfolio/index?tickers=PETR4,VALE3,CDB - REDEEMED",
        headers=headers)).json()
    body = (await client.get("/portfolio/index", headers=headers)).json()

    # Admitted by _book (it has terms), refused by accrue_rf (no $CDI on the
    # plane) — so it must be absent from the book, not merely mentioned.
    assert "CDB - OPEN" not in body["universe"]
    assert "CDB - OPEN" not in body["excluded"]["unpriced_tickers"]
    reasons = {r["ticker"] for r in body["excluded"]["renda_fixa"]}
    assert "CDB - OPEN" in reasons

    # The proof it never touched the chain: the whole book returns exactly what
    # its drawable holdings alone return. Before the fix the undrawn contract's
    # aplicação sat in the denominator and dragged this down.
    assert body["universe"] == ["CDB - REDEEMED", "PETR4", "VALE3"]
    assert (body["metrics"]["portfolio"]["total_return_pct"]
            == pytest.approx(
                drawable["metrics"]["portfolio"]["total_return_pct"], abs=0.01))

    async with async_session_maker() as session:
        await session.execute(
            delete(RendaFixaTerms).where(RendaFixaTerms.tenant_id == tid))
        await session.commit()
