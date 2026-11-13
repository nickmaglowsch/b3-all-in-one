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
from tests.conftest import auth

pytestmark = pytest.mark.asyncio(loop_scope="session")

PLANE_DB = "live_ticker_sleeves_engine"

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
    # A test issue, and a symbol no sleeve could hold, are on the plane but not offered.
    assert await _search(client, h, q="ZVZZT") == []
    assert await _search(client, h, q="BAC") == []

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
    assert (btc["ticker"], btc["asset_class"], btc["price_brl"]) == (
        "BTCUSDT", "CRYPTO", 540000.0)
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
     422, "duplicate sleeve in sleeve set: B3:IVVB11"),
    ([{"ticker": "PETR4", "strategy_ref": "CASH_CDI", "target_pct": 100}], 422,
     "exactly one of strategy_ref / ticker"),
    ([{"strategy_ref": "CASH_CDI", "market": "US", "target_pct": 100}], 422,
     "market goes with a ticker"),
    ([{"ticker": "SPY", "market": "NYSE", "target_pct": 100}], 422, "market"),
    ([{"ticker": "SPY", "target_pct": 100}], 422, "not a B3 trading code"),
    ([{"ticker": "^BVSP", "target_pct": 100}], 422, "is an index"),
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


async def test_target_values_every_ticker_sleeve_in_brl(client, plane):
    h = await _user(client, "target")
    await _seed_petr4(client, h)
    assert (await client.put("/portfolio/sleeves", json=MIX, headers=h)).status_code == 200
    r = await client.get("/portfolio/target", headers=h)
    assert r.status_code == 200, r.text
    t = r.json()
    assert t["combined_weights"] == pytest.approx({
        "PETR4": 0.15, "IVVB11": 0.15, "AAPL34": 0.10, "B5P211": 0.10,
        "SPY": 0.20, "BTCUSDT": 0.10})
    assert t["cash_weight_target"] == pytest.approx(0.20)
    assert t["markets"] == {"SPY": "US", "BTCUSDT": "CRYPTO"}
    assert t["nav"] == pytest.approx(4000.0)                  # 100 x R$40
    assert t["current_weights"] == pytest.approx({"PETR4": 1.0})
    drift = {d["ticker"]: d for d in t["drift"]}
    assert drift["SPY"]["current_w"] == 0 and drift["SPY"]["target_w"] == pytest.approx(0.2)
    assert any("SPY (EUA)" in w for w in t["warnings"])


async def test_rebalance_plan_trades_each_market_at_its_own_brl_price(client, plane):
    h = await _user(client, "plan")
    await _seed_petr4(client, h)
    assert (await client.put("/portfolio/sleeves", json=MIX, headers=h)).status_code == 200
    r = await client.post("/portfolio/rebalance-plans", json={"new_cash": 96000}, headers=h)
    assert r.status_code == 200, r.text
    plan = r.json()
    assert plan["nav"] == pytest.approx(100_000.0)
    buys = {b["ticker"]: b for b in plan["trades"]["buys"]}
    assert set(buys) == {"PETR4", "IVVB11", "AAPL34", "B5P211", "SPY", "BTCUSDT"}
    expected = {"PETR4": 40.0, "IVVB11": 350.0, "AAPL34": 61.5, "B5P211": 100.0,
                "SPY": 3294.0, "BTCUSDT": 540000.0}
    assert {t: b["est_price"] for t, b in buys.items()} == expected
    assert buys["SPY"]["qty"] == 6                            # floor(20000 / 3294)
    # A coin is bought in fractions — R$10k of BTC is not zero BTC.
    assert 0 < buys["BTCUSDT"]["qty"] < 1
    assert buys["BTCUSDT"]["qty"] == pytest.approx(10000 / 540000, abs=1e-8)
    assert buys["PETR4"]["qty"] == 275                        # 15k target - 4k held
    # The off-B3 buys say how they are taxed instead of joining the B3 overlay.
    [note] = [n for n in plan["notes"] if "fora da B3" in n]
    assert "SPY (EUA)" in note and "BTCUSDT (cripto)" in note and "14.754" in note
    assert plan["trades"]["sells"] == []


async def test_a_new_position_below_one_unit_is_named_in_the_plan(client, plane):
    h = await _user(client, "plan-small")
    body = [{"ticker": "SPY", "market": "US", "target_pct": 50},
            {"ticker": "PETR4", "target_pct": 50}]
    assert (await client.put("/portfolio/sleeves", json=body, headers=h)).status_code == 200
    r = await client.post("/portfolio/rebalance-plans", json={"new_cash": 1000}, headers=h)
    assert r.status_code == 200, r.text
    plan = r.json()
    assert [b["ticker"] for b in plan["trades"]["buys"]] == ["PETR4"]
    assert any("não chega a uma unidade de: SPY" in n for n in plan["notes"])
    # No B3 history at all: no apuração to overlay, and no 500 for want of one.
    assert plan["tax"] == {}


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
