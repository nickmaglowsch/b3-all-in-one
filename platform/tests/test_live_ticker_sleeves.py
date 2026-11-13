"""A live sleeve that holds one asset directly, on any market (/estrategia):
GET /market/assets finds it, PUT /portfolio/sleeves stores it priced, and
GET /portfolio/target + POST /portfolio/rebalance-plans value and trade it in
reais off its own plane.

Deterministic: a throwaway Postgres engine DB carrying a few B3 codes (a stock,
an ETF, a unit, a FII, a BDR, a renda-fixa ETF only in external_prices), US
symbols and a crypto pair, read by both the platform (asyncpg) and the engine's
advise_weights subprocess — the two must price a sleeve alike.
"""
from __future__ import annotations

import os
import subprocess
import uuid
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import pytest
import pytest_asyncio

from platform_app import enginerun
from platform_app.config import settings
from platform_app.db import async_session_maker, close_engine_pool
from platform_app.models import Position, Trade
from tests.conftest import TEST_DB, auth

pytestmark = pytest.mark.asyncio(loop_scope="session")

# Named after the test DB, so parallel runs (PLATFORM_TEST_DB) never share a plane.
PLANE_DB = f"{TEST_DB}_ticker_plane"

# Run in the engine venv: the engine's own schema, then a handful of rows.
_BOOTSTRAP = r"""
import datetime
from b3_pipeline import storage
day = lambda n: (datetime.date.today() - datetime.timedelta(days=n)).isoformat()
conn = storage.get_connection()
storage.init_db(conn, rebuild=True)
rows = lambda sql, rs: storage.executemany(conn, sql, rs)
rows("INSERT INTO prices (ticker, isin_code, date, close, adj_close, volume)"
     " VALUES (?, 'X', ?, ?, ?, 1e6)",
     [(t, day(d), px, px) for t, px in (("PETR4", 40.0), ("IVVB11", 350.0),
                                         ("TAEE11", 35.0), ("HGLG11", 160.0),
                                         ("AAPL34", 61.5))
      for d in (3000, 2)] + [("OLDX3", day(3000), 9.0, 9.0), ("OLDX3", day(400), 10.0, 10.0)])
rows("INSERT INTO external_prices (ticker, isin_code, date, close, volume, source)"
     " VALUES (?, 'X', ?, ?, 0, 'T')", [("B5P211", day(2), 100.0), ("$IMAB5", day(2), 9.0)])
rows("INSERT INTO market_prices (market, symbol, date, close, close_brl, fx_rate, source)"
     " VALUES (?, ?, ?, ?, ?, ?, 'T')",
     [("US", "SPY", day(2), 610.0, 3294.0, 5.4), ("US", "AAPL", day(2), 230.0, 1242.0, 5.4),
      ("US", "QQQ", day(2), 520.0, None, None),
      ("CRYPTO", "BTCUSDT", day(1), 100000.0, 540000.0, 5.4)])
rows("INSERT INTO instruments (market, symbol, currency, kind, name, delisting_date,"
     " is_test_issue) VALUES (?, ?, ?, ?, ?, ?, ?)",
     [("B3", t, "BRL", "EQUITY", None, None, 0)
      for t in ("PETR4", "IVVB11", "TAEE11", "HGLG11", "AAPL34", "OLDX3")]
     + [("US", "SPY", "USD", "ETF", "SPDR S&P 500 ETF Trust", None, 0),
        ("US", "AAPL", "USD", "EQUITY", "Apple Inc. - Common Stock", None, 0),
        ("US", "QQQ", "USD", "ETF", "Invesco QQQ Trust", None, 0),
        ("US", "LEHMQ", "USD", "EQUITY", "Lehman Brothers", day(5000), 0),
        ("US", "ZVZZT", "USD", "EQUITY", "NASDAQ TEST STOCK", None, 1),
        ("US", "BAC$K", "USD", "EQUITY", "Bank of America Preferred K", None, 0),
        ("US", "BACKX", "USD", "EQUITY", "Backx Corp", None, 0),
        ("CRYPTO", "BTCUSDT", "USDT", "SPOT_PAIR", None, None, 0)])
rows("INSERT INTO cvm_companies (cnpj, ticker, company_name, b3_trading_name)"
     " VALUES (?, ?, ?, ?)",
     [("1", "PETR", "PETROLEO BRASILEIRO S.A. PETROBRAS", "PETROBRAS"),
      ("2", "TAEE", "TRANSMISSORA ALIANCA DE ENERGIA ELETRICA S.A.", "TAESA")])
conn.commit()
conn.close()
"""


def _swap_db(url: str, dbname: str) -> str:
    p = urlsplit(url)
    return urlunsplit((p.scheme, p.netloc, "/" + dbname, p.query, p.fragment))


async def _admin(dsn: str, *sql: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        for stmt in sql:
            await conn.execute(stmt)
    finally:
        await conn.close()


@pytest_asyncio.fixture(loop_scope="session", scope="module")
async def plane():
    base = settings.engine_database_url
    admin, dsn = _swap_db(base, "postgres"), _swap_db(base, PLANE_DB)
    await _admin(admin, f"DROP DATABASE IF EXISTS {PLANE_DB} WITH (FORCE)",
                 f"CREATE DATABASE {PLANE_DB}")
    proc = subprocess.run(
        [*enginerun._engine_python(), "-c", _BOOTSTRAP], cwd=enginerun.ENGINE,
        env={**os.environ, "ENGINE_DATABASE_URL": dsn, "PYTHONWARNINGS": "ignore"},
        capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    settings.engine_database_url = dsn
    try:
        yield dsn
    finally:
        settings.engine_database_url = base
        # The pool holds live connections to the plane; close it before the drop.
        await close_engine_pool()
        await _admin(admin, f"DROP DATABASE IF EXISTS {PLANE_DB} WITH (FORCE)")


async def _user(client, name: str) -> dict:
    return await auth(client, f"{name}-{uuid.uuid4().hex[:8]}@example.com",
                      modules=["bench", "mercado"])


async def _search(client, h, **params) -> list[dict]:
    r = await client.get("/market/assets", params=params, headers=h)
    assert r.status_code == 200, r.text
    return r.json()["results"]


# ── GET /market/assets ────────────────────────────────────────────────────────

async def test_search_finds_assets_of_every_market_by_ticker_or_name(client, plane):
    h = await _user(client, "search")
    [petr] = [r for r in await _search(client, h, q="petr") if r["ticker"] == "PETR4"]
    assert petr == {"ticker": "PETR4", "market": "B3", "name": "PETROBRAS",
                    "asset_class": "STOCK", "price_brl": 40.0,
                    "price_date": petr["price_date"]}
    # A test issue, and a symbol no sleeve could hold, are on the plane but not
    # offered — and filtered before the limit: BAC$K ranks above BACKX, and the
    # one-result page is still BACKX, not empty.
    assert await _search(client, h, q="ZVZZT") == []
    assert [r["ticker"] for r in await _search(client, h, q="BAC")] == ["BACKX"]
    assert [r["ticker"] for r in await _search(client, h, q="BAC", limit=1)] == ["BACKX"]
    # A B3 code is found by its company's name too, through its CVM root.
    assert [r["ticker"] for r in await _search(client, h, q="petrobras")] == ["PETR4"]

    apple = await _search(client, h, q="apple")
    assert [(r["ticker"], r["market"], r["asset_class"]) for r in apple] == [
        ("AAPL", "US", "STOCK")]
    # Exact ticker first; the BDR of the same company is a B3 code of its own.
    aapl = await _search(client, h, q="aapl")
    assert [(r["ticker"], r["market"]) for r in aapl] == [("AAPL", "US"), ("AAPL34", "B3")]
    assert aapl[1]["asset_class"] == "BDR"

    [spy] = await _search(client, h, q="SPY")
    assert (spy["market"], spy["asset_class"], spy["price_brl"]) == ("US", "ETF", 3294.0)
    [btc] = await _search(client, h, q="btc", market="CRYPTO")
    assert (btc["ticker"], btc["name"], btc["asset_class"], btc["price_brl"]) == (
        "BTCUSDT", "Bitcoin", "CRYPTO", 540000.0)
    # A major coin is found by its name too.
    assert [r["ticker"] for r in await _search(client, h, q="bitcoin")] == ["BTCUSDT"]
    # A renda-fixa ETF lives only in external_prices — still found, never the index.
    assert [(r["ticker"], r["asset_class"]) for r in await _search(client, h, q="B5P")] == [
        ("B5P211", "ETF_RF")]
    assert await _search(client, h, q="$IMAB") == []


async def test_search_tells_units_funds_and_unpriced_apart(client, plane):
    h = await _user(client, "search-classes")
    by = {r["ticker"]: r for r in await _search(client, h, q="TAEE")}
    by |= {r["ticker"]: r for r in await _search(client, h, q="HGLG")}
    assert by["TAEE11"]["asset_class"] == "UNIT"   # *11 of a CVM-listed company
    assert by["HGLG11"]["asset_class"] == "FUND"   # *11 of no company: FII or ETF
    [qqq] = await _search(client, h, q="QQQ")
    assert qqq["price_brl"] is None and qqq["price_date"] is None
    # Delisted names are not offered; the market filter and the limit hold.
    assert await _search(client, h, q="LEHMQ") == []
    assert await _search(client, h, q="SPY", market="CRYPTO") == []
    assert len(await _search(client, h, q="A", limit=1)) == 1
    # LIKE wildcards are matched literally, not as "everything".
    assert await _search(client, h, q="%") == []


async def test_search_needs_a_query_a_login_and_a_readable_plane(client, plane, monkeypatch):
    h = await _user(client, "search-guards")
    assert (await client.get("/market/assets", params={"q": "PETR"})).status_code == 401
    for bad in ({"q": ""}, {"q": "  "}, {"q": "PETR", "market": "NYSE"},
                {"q": "PETR", "limit": 0}, {"q": "PETR", "limit": 51}):
        assert (await client.get("/market/assets", params=bad, headers=h)).status_code == 422
    monkeypatch.setattr(settings, "engine_database_url",
                        _swap_db(plane, "no_such_plane_db"))
    r = await client.get("/market/assets", params={"q": "PETR"}, headers=h)
    assert r.status_code == 503 and "indisponível" in r.json()["detail"]


# ── PUT/GET /portfolio/sleeves ────────────────────────────────────────────────

MIX = [{"ticker": "PETR4", "target_pct": 15},
       {"ticker": "ivvb11.sa", "target_pct": 15},          # spelled as the built-ins do
       {"ticker": "AAPL34", "market": "B3", "target_pct": 10},
       {"ticker": "B5P211", "target_pct": 10},
       {"ticker": "spy", "market": "US", "target_pct": 20},
       {"ticker": "btc", "market": "CRYPTO", "target_pct": 10},
       {"strategy_ref": "CASH_CDI", "target_pct": 20}]
STORED = [{"strategy_ref": "CASH_CDI", "target_pct": 20.0},
          {"ticker": "AAPL34", "market": "B3", "target_pct": 10.0},
          {"ticker": "B5P211", "market": "B3", "target_pct": 10.0},
          {"ticker": "IVVB11", "market": "B3", "target_pct": 15.0},
          {"ticker": "PETR4", "market": "B3", "target_pct": 15.0},
          {"ticker": "BTCUSDT", "market": "CRYPTO", "target_pct": 10.0},
          {"ticker": "SPY", "market": "US", "target_pct": 20.0}]


async def test_ticker_sleeves_of_every_market_are_stored_canonical(client, plane):
    h = await _user(client, "sleeves")
    r = await client.put("/portfolio/sleeves", json=MIX, headers=h)
    assert r.status_code == 200, r.text
    saved = r.json()
    assert {(s.get("ticker"), s.get("market")) for s in saved["sleeves"]} == {
        (s.get("ticker"), s.get("market")) for s in STORED}
    # What a US or crypto sleeve cannot see is said on save.
    [note] = [w for w in saved["warnings"] if "extratos da B3" in w]
    assert "SPY (EUA)" in note and "BTCUSDT (cripto)" in note
    assert (await client.get("/portfolio/sleeves", headers=h)).json() == STORED


@pytest.mark.parametrize("body, status, needle", [
    ([{"ticker": "XPTO3", "target_pct": 100}], 422, "sem cotação no plano de preços: XPTO3"),
    ([{"ticker": "QQQ", "market": "US", "target_pct": 100}], 422, "QQQ (EUA)"),
    ([{"ticker": "IVVB11", "target_pct": 50}, {"ticker": "ivvb11.sa", "target_pct": 50}],
     422, "sleeve repetido no conjunto: B3:IVVB11"),
    ([{"ticker": "PETR4", "strategy_ref": "CASH_CDI", "target_pct": 100}], 422,
     "exatamente um de strategy_ref / ticker"),
    ([{"strategy_ref": "CASH_CDI", "market": "US", "target_pct": 100}], 422,
     "market só acompanha um sleeve de ticker"),
    ([{"ticker": "SPY", "market": "NYSE", "target_pct": 100}], 422, "market"),
    ([{"ticker": "SPY", "target_pct": 100}], 422, "não é um código da B3"),
    ([{"ticker": "^BVSP", "target_pct": 100}], 422, "é um índice"),
    # One symbol on two markets would be one weight priced on one plane.
    # (A US symbol may have a B3 code's shape; a crypto pair never does.)
    ([{"ticker": "AAPL34", "target_pct": 50},
      {"ticker": "AAPL34", "market": "US", "target_pct": 50}], 422,
     "AAPL34 aparece em dois mercados (B3 e EUA)"),
])
async def test_a_ticker_sleeve_the_plane_cannot_price_is_refused(
        client, plane, body, status, needle):
    h = await _user(client, "sleeves-bad")
    ok = [{"ticker": "PETR4", "target_pct": 100}]
    assert (await client.put("/portfolio/sleeves", json=ok, headers=h)).status_code == 200
    r = await client.put("/portfolio/sleeves", json=body, headers=h)
    assert r.status_code == status and needle in r.text, r.text
    # A refused save leaves the stored set as it was.
    assert (await client.get("/portfolio/sleeves", headers=h)).json() == [
        {"ticker": "PETR4", "market": "B3", "target_pct": 100.0}]


@pytest.mark.parametrize("row", [
    {"ticker": "PETR4", "market": None},          # a ticker without its market
    {"ticker": "PETR4", "market": "NYSE"},        # a market no plane has
    {"strategy_ref": "CASH_CDI", "market": "B3"},  # a market without a ticker
    {"strategy_ref": "CASH_CDI", "ticker": "PETR4", "market": "B3"},
    {},                                            # neither
])
async def test_the_table_itself_holds_a_sleeve_to_one_source(row):
    """The CHECK behind the API's validation: a row written by anything else is
    still exactly one of strategy_ref / ticker, a ticker with a known market."""
    from sqlalchemy.exc import IntegrityError

    from platform_app.models import PortfolioSleeve
    async with async_session_maker() as s:
        s.add(PortfolioSleeve(tenant_id=uuid.uuid4(), target_pct=100, **row))
        with pytest.raises(IntegrityError, match="ck_sleeves_one_source"):
            await s.commit()


async def test_a_stale_close_is_saved_with_a_warning(client, plane):
    h = await _user(client, "sleeves-stale")
    r = await client.put("/portfolio/sleeves", headers=h,
                         json=[{"ticker": "OLDX3", "target_pct": 100}])
    assert r.status_code == 200, r.text
    assert any("OLDX3: última cotação em" in w for w in r.json()["warnings"])


async def test_an_unreadable_plane_refuses_rather_than_stores_unchecked(
        client, plane, monkeypatch):
    h = await _user(client, "sleeves-down")
    monkeypatch.setattr(settings, "engine_database_url",
                        _swap_db(plane, "no_such_plane_db"))
    r = await client.put("/portfolio/sleeves", headers=h,
                         json=[{"ticker": "PETR4", "target_pct": 100}])
    assert r.status_code == 503
    assert (await client.get("/portfolio/sleeves", headers=h)).json() == []


# ── target and rebalance plan ─────────────────────────────────────────────────

async def _seed_petr4(client, h) -> None:
    """100 PETR4 bought at R$30 — the only thing the B3 book holds."""
    tid = uuid.UUID((await client.get("/me", headers=h)).json()["id"])
    import datetime
    async with async_session_maker() as s:
        s.add(Position(tenant_id=tid, ticker="PETR4", qty=100.0, avg_cost=30.0,
                       total_cost=3000.0, market="Mercado à Vista"))
        s.add(Trade(tenant_id=tid, date=datetime.date(2024, 1, 10), side="Compra",
                    market="Mercado à Vista", maturity=None, broker="X", ticker="PETR4",
                    qty=100.0, price=30.0, amount=3000.0, source_file="t.xlsx",
                    signed_qty=100.0))
        await s.commit()


async def test_target_compares_the_b3_book_and_lists_off_b3_sleeves_outside(client, plane):
    h = await _user(client, "target")
    await _seed_petr4(client, h)
    assert (await client.put("/portfolio/sleeves", json=MIX, headers=h)).status_code == 200
    r = await client.get("/portfolio/target", headers=h)
    assert r.status_code == 200, r.text
    t = r.json()
    assert t["combined_weights"] == pytest.approx({
        "PETR4": 0.15, "IVVB11": 0.15, "AAPL34": 0.10, "B5P211": 0.10,
        "SPY": 0.20, "BTCUSDT": 0.10})
    assert t["markets"] == {"SPY": "US", "BTCUSDT": "CRYPTO"}
    assert t["nav"] == pytest.approx(4000.0)                  # 100 x R$40
    assert t["current_weights"] == pytest.approx({"PETR4": 1.0})
    # The B3 book against its own 70% of the target, scaled to the book.
    assert t["cash_weight_target"] == pytest.approx(0.20 / 0.70)
    drift = {d["ticker"]: d for d in t["drift"]}
    assert drift["PETR4"]["target_w"] == pytest.approx(0.15 / 0.70)
    # Off B3: its weight in the whole target, and no position — unknown, not zero.
    assert drift["SPY"] == {"ticker": "SPY", "market": "US", "target_w": pytest.approx(0.2),
                            "current_w": None, "drift_w": None, "drift_brl": None,
                            "outside_b3": True}
    assert any("Fora da B3 no alvo: BTCUSDT (cripto) 10%, SPY (EUA) 20%" in w
               for w in t["warnings"])


async def test_rebalance_plan_trades_each_market_at_its_own_brl_price(client, plane):
    h = await _user(client, "plan")
    await _seed_petr4(client, h)
    assert (await client.put("/portfolio/sleeves", json=MIX, headers=h)).status_code == 200
    r = await client.post("/portfolio/rebalance-plans", json={"new_cash": 96000}, headers=h)
    assert r.status_code == 200, r.text
    plan = r.json()
    assert plan["nav"] == pytest.approx(100_000.0)
    buys = {b["ticker"]: b for b in plan["trades"]["buys"]}
    assert {t: b["est_price"] for t, b in buys.items()} == {
        "PETR4": 40.0, "IVVB11": 350.0, "AAPL34": 61.5, "B5P211": 100.0,
        "SPY": 3294.0, "BTCUSDT": 540000.0}
    # 30% of the aporte (R$28.8k) goes abroad, by target weight: SPY 2/3, BTC 1/3.
    assert buys["SPY"]["qty"] == 5                            # floor(19200 / 3294)
    assert buys["BTCUSDT"]["qty"] == pytest.approx(9600 / 540000, abs=1e-8)
    assert buys["SPY"]["est_brl"] + buys["BTCUSDT"]["est_brl"] <= 28_800
    # The rest rebalances the B3 book: R$71.2k, 15/70 of it in PETR4.
    assert buys["PETR4"]["qty"] == 281                        # (15257 - 4000) / 40
    assert plan["trades"]["sells"] == []
    # How they are funded comes first; how they are taxed is said too.
    assert "entram só com aporte" in plan["notes"][0] and "R$ 28.800,00" in plan["notes"][0]
    [tax] = [n for n in plan["notes"] if "14.754" in n]
    assert "SPY (EUA)" in tax and "BTCUSDT (cripto)" in tax


async def _rebalance(client, h, *, buy_only: bool) -> None:
    r = await client.put("/portfolio/settings/rebalance", headers=h, json={
        "buy_only_laggards": buy_only, "sell_only_for_harvest": False, "cadence": "ME"})
    assert r.status_code == 200, r.text


async def _plan(client, h, **body) -> dict:
    r = await client.post("/portfolio/rebalance-plans", json=body, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.parametrize("buy_only", [False, True])
async def test_off_b3_sleeves_are_bought_with_the_aporte_never_with_b3_sales(
        client, plane, buy_only):
    """Counted as zero, SPY used to be re-bought in full every plan — and, without
    buy_only_laggards, paid for by selling PETR4: a taxable sale every cycle."""
    h = await _user(client, f"plan-funding-{buy_only}")
    await _seed_petr4(client, h)                              # R$4k, all PETR4
    body = [{"ticker": "PETR4", "target_pct": 70},
            {"ticker": "SPY", "market": "US", "target_pct": 30}]
    assert (await client.put("/portfolio/sleeves", json=body, headers=h)).status_code == 200
    await _rebalance(client, h, buy_only=buy_only)

    plan = await _plan(client, h, new_cash=0)
    # PETR4 is the whole B3 book's target: nothing to sell, nothing to buy.
    assert plan["trades"] == {"sells": [], "buys": []}
    assert "Sem aporte, nenhuma compra deles" in plan["notes"][0]

    plan = await _plan(client, h, new_cash=20000)
    buys = {b["ticker"]: b for b in plan["trades"]["buys"]}
    assert plan["trades"]["sells"] == []
    assert buys["SPY"]["qty"] == 1                            # floor(6000 / 3294)
    assert buys["SPY"]["est_brl"] <= 0.30 * 20000
    assert buys["PETR4"]["qty"] == 350                        # the B3 share, R$14k


async def test_with_no_b3_sleeve_the_b3_book_is_left_alone(client, plane):
    h = await _user(client, "plan-all-abroad")
    await _seed_petr4(client, h)
    body = [{"ticker": "SPY", "market": "US", "target_pct": 100}]
    assert (await client.put("/portfolio/sleeves", json=body, headers=h)).status_code == 200
    plan = await _plan(client, h, new_cash=0)
    assert plan["trades"] == {"sells": [], "buys": []}       # PETR4 is not sold
    plan = await _plan(client, h, new_cash=10000)
    assert plan["trades"]["sells"] == []
    assert [(b["ticker"], b["qty"]) for b in plan["trades"]["buys"]] == [("SPY", 3)]


async def _sleeves(client, h, body) -> None:
    r = await client.put("/portfolio/sleeves", json=body, headers=h)
    assert r.status_code == 200, r.text


_SPY = {"ticker": "SPY", "market": "US"}


@pytest.mark.parametrize("body", [
    [{**_SPY, "target_pct": 99.995}],                        # a save within ±0.01
    [{**_SPY, "target_pct": 50.004}, {"ticker": "BTCUSDT", "market": "CRYPTO",
                                      "target_pct": 49.99}],
    [{**_SPY, "target_pct": 90}, {"strategy_ref": "CASH_CDI", "target_pct": 10}],
    [{**_SPY, "target_pct": 99.99}, {"strategy_ref": "CASH_CDI", "target_pct": 0.01}],
])
async def test_no_b3_asset_sleeve_keeps_the_b3_book_as_it_is(client, plane, body):
    """No sleeve holds a B3 asset: nothing in B3 is sold — not through float
    rounding of a near-100 total, and not because CASH_CDI read as "all cash"."""
    h = await _user(client, "plan-hold")
    await _seed_petr4(client, h)
    await _sleeves(client, h, body)
    plan = await _plan(client, h, new_cash=0)
    assert plan["trades"] == {"sells": [], "buys": []}
    assert any("a carteira da B3 fica como está" in n for n in plan["notes"])
    t = (await client.get("/portfolio/target", headers=h)).json()
    drift = {d["ticker"]: d for d in t["drift"]}
    assert drift["PETR4"]["drift_w"] == 0
    assert t["cash_weight_target"] == t["cash_weight_current"]
    # With an aporte, only the off-B3 share buys, and nothing in B3 trades.
    plan = await _plan(client, h, new_cash=10000)
    assert plan["trades"]["sells"] == []
    assert {b["ticker"] for b in plan["trades"]["buys"]} <= {"SPY", "BTCUSDT"}
    assert sum(b["est_brl"] for b in plan["trades"]["buys"]) <= 10000


@pytest.mark.parametrize("with_off, without_off", [
    # CASH_CDI is the B3 book's cash: rebalanced against, as without SPY.
    ([{"ticker": "PETR4", "target_pct": 35}, {"strategy_ref": "CASH_CDI", "target_pct": 35},
      {**_SPY, "target_pct": 30}],
     [{"ticker": "PETR4", "target_pct": 50}, {"strategy_ref": "CASH_CDI", "target_pct": 50}]),
    # A B3 book off its own target: the same sells and buys, SPY or not.
    ([{"ticker": "PETR4", "target_pct": 35}, {"ticker": "IVVB11", "target_pct": 35},
      {**_SPY, "target_pct": 30}],
     [{"ticker": "PETR4", "target_pct": 50}, {"ticker": "IVVB11", "target_pct": 50}]),
])
async def test_an_off_b3_sleeve_changes_no_b3_trade(client, plane, with_off, without_off):
    plans = []
    for i, body in enumerate((with_off, without_off)):
        h = await _user(client, f"plan-same-{i}")
        await _seed_petr4(client, h)
        await _sleeves(client, h, body)
        plans.append((await _plan(client, h, new_cash=0))["trades"])
    assert plans[0]["sells"], "the B3 book should be off its target"
    assert plans[0] == plans[1]


async def test_a_strategy_sleeve_next_to_an_off_b3_one(client, plane, monkeypatch):
    """The B3 book's share is the strategy's sleeve, 70% — what it advises
    inside it is rebalanced; what it leaves in cash stays cash."""
    from platform_app import routes
    advised = {"weights": {"PETR4": 1.0}}

    async def fake_advise(strategy, freq, extra_env=None):
        return advised

    monkeypatch.setattr(routes, "_advise_strategy", fake_advise)
    monkeypatch.setattr(routes, "_SLEEVE_WEIGHTS_CACHE", {})
    h = await _user(client, "plan-strategy")
    await _seed_petr4(client, h)
    await _sleeves(client, h, [{"strategy_ref": "MomSharpe", "target_pct": 70},
                               {**_SPY, "target_pct": 30}])
    assert (await _plan(client, h, new_cash=0))["trades"] == {"sells": [], "buys": []}
    # Half of the strategy's book in cash: half of PETR4 goes, never more.
    advised = {"weights": {"PETR4": 0.5}}
    monkeypatch.setattr(routes, "_SLEEVE_WEIGHTS_CACHE", {})
    plan = await _plan(client, h, new_cash=0)
    assert [(s["ticker"], s["qty"]) for s in plan["trades"]["sells"]] == [("PETR4", 50)]


async def test_a_held_b3_book_keeps_its_cash_cdi_holdings_too(client, plane):
    h = await _user(client, "plan-hold-cdi")
    await _seed_petr4(client, h)
    tid = uuid.UUID((await client.get("/me", headers=h)).json()["id"])
    async with async_session_maker() as s:
        s.add(Position(tenant_id=tid, ticker="B5P211", qty=10.0, avg_cost=95.0,
                       total_cost=950.0, market="Mercado à Vista"))
        await s.commit()
    r = await client.put("/portfolio/settings/asset-overrides", headers=h,
                         json=[{"ticker": "B5P211", "treatment": "cash_cdi"}])
    assert r.status_code == 200, r.text
    await _sleeves(client, h, [{**_SPY, "target_pct": 100}])
    plan = await _plan(client, h, new_cash=0)
    assert plan["trades"] == {"sells": [], "buys": []}


async def test_an_excluded_off_b3_sleeve_keeps_its_share_as_cash(client, plane):
    h = await _user(client, "plan-exclude-off")
    await _seed_petr4(client, h)
    await _sleeves(client, h, [{"ticker": "PETR4", "target_pct": 70},
                               {**_SPY, "target_pct": 20},
                               {"ticker": "BTCUSDT", "market": "CRYPTO", "target_pct": 10}])
    plan = await _plan(client, h, new_cash=10000, exclude=["SPY"])
    buys = {b["ticker"]: b for b in plan["trades"]["buys"]}
    assert "SPY" not in buys and buys["BTCUSDT"]["est_brl"] <= 1000
    assert "R$ 1.000,00 vão para BTCUSDT (cripto)" in plan["notes"][0]
    assert "A parte de SPY (EUA) (R$ 2.000,00)" in plan["notes"][0]
    assert "fica em caixa desta vez" in plan["notes"][0]
    # The whole aporte is still in the plan's NAV: the B3 book + R$10k.
    assert plan["nav"] == pytest.approx(14_000)


async def test_an_aporte_is_never_negative(client, plane):
    h = await _user(client, "plan-negative")
    r = await client.post("/portfolio/rebalance-plans", json={"new_cash": -100}, headers=h)
    assert r.status_code == 422


async def test_a_new_position_below_one_unit_is_named_in_the_plan(client, plane):
    h = await _user(client, "plan-small")
    body = [{"ticker": "SPY", "market": "US", "target_pct": 50},
            {"ticker": "PETR4", "target_pct": 50}]
    assert (await client.put("/portfolio/sleeves", json=body, headers=h)).status_code == 200
    plan = await _plan(client, h, new_cash=1000)
    assert [b["ticker"] for b in plan["trades"]["buys"]] == ["PETR4"]
    assert any("não chega a uma unidade de: SPY" in n for n in plan["notes"])
    # No B3 history at all: no apuração to overlay, and no 500 for want of one.
    assert plan["tax"] == {}


async def test_buy_only_laggards_names_what_the_aporte_ran_out_before(client, plane):
    h = await _user(client, "plan-buy-only")
    await _seed_petr4(client, h)                              # R$4k of PETR4
    body = [{"ticker": "BTCUSDT", "market": "CRYPTO", "target_pct": 60},
            {"ticker": "IVVB11", "target_pct": 20}, {"ticker": "AAPL34", "target_pct": 10},
            {"ticker": "PETR4", "target_pct": 10}]
    assert (await client.put("/portfolio/sleeves", json=body, headers=h)).status_code == 200
    await _rebalance(client, h, buy_only=True)

    plan = await _plan(client, h, new_cash=1000)
    assert plan["trades"]["sells"] == []                      # pure top-up
    buys = {b["ticker"]: b for b in plan["trades"]["buys"]}
    # 60% of the aporte buys the coin, in fractions of it.
    assert buys["BTCUSDT"]["qty"] == pytest.approx(600 / 540000, abs=1e-8)
    # The B3 R$400 buys one IVVB11; the R$50 left buys no AAPL34 — said, not dropped.
    assert buys["IVVB11"]["qty"] == 1 and "AAPL34" not in buys
    assert any("o aporte acabou antes de: AAPL34" in n for n in plan["notes"])
    assert not any("não chega a uma unidade" in n for n in plan["notes"])

    # Excluded (or pinned), the coin is not bought; its share stays cash and the
    # B3 book's share is untouched.
    for key in ("exclude", "pins"):
        plan = await _plan(client, h, new_cash=1000, **{key: ["BTCUSDT"]})
        buys = {b["ticker"]: b for b in plan["trades"]["buys"]}
        assert "BTCUSDT" not in buys and buys["IVVB11"]["qty"] == 1


# ── saved carteiras: B3 tickers activate; US / crypto are refused ─────────────

async def test_promoting_a_carteira_with_b3_tickers_carries_them_live(client, plane):
    h = await _user(client, "promote")
    r = await client.post("/strategies/user", headers=h, json={
        "kind": "fixed_weight", "name": "ETFs", "rebalance": "QE",
        "sleeves": [{"ticker": "IVVB11.SA", "target_pct": 50},
                    {"strategy_ref": "CASH_CDI", "target_pct": 50}]})
    assert r.status_code == 200, r.text
    promo = await client.post(f"/strategies/user/{r.json()['id']}/promote", headers=h)
    assert promo.status_code == 200, promo.text
    assert (await client.get("/portfolio/sleeves", headers=h)).json() == [
        {"strategy_ref": "CASH_CDI", "target_pct": 50.0},
        {"ticker": "IVVB11", "market": "B3", "target_pct": 50.0}]


@pytest.mark.parametrize("ticker, market", [("SPY", "US"), ("BTCUSDT", "CRYPTO")])
async def test_a_saved_carteira_refuses_an_off_b3_ticker_in_portuguese(
        client, ticker, market):
    h = await auth(client, "carteira-off-b3@example.com", modules=["bench"])
    r = await client.post("/strategies/user", headers=h, json={
        "kind": "fixed_weight", "name": "Global", "rebalance": "QE",
        "sleeves": [{"ticker": ticker, "market": market, "target_pct": 50},
                    {"strategy_ref": "CASH_CDI", "target_pct": 50}]})
    assert r.status_code == 422
    assert "não pode entrar numa carteira salva" in r.text and ticker in r.text


async def test_a_saved_carteira_does_not_suggest_market_for_a_us_ticker(client):
    """The live sleeves' hint ("leva `market`: US ou CRYPTO") would only lead to
    the refusal above; a carteira's message says where such an asset can go."""
    h = await auth(client, "carteira-hint@example.com", modules=["bench"])
    r = await client.post("/strategies/user", headers=h, json={
        "kind": "fixed_weight", "name": "Hint", "rebalance": "QE",
        "sleeves": [{"ticker": "SPY", "target_pct": 100}]})
    assert r.status_code == 422
    assert "não entram numa carteira salva" in r.text and "leva `market`" not in r.text
