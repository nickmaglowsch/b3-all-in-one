"""Portfolio settings (spec/05 Features 3 & 4): asset-class overrides + rebalance
behavior, and their effect on GET /portfolio/target and POST /portfolio/rebalance-plans.

Standalone-safe: seeds the dev tenant's positions from taxcore/data/positions.csv when
the shared session DB doesn't already have them (the full suite imports them first).
"""
from __future__ import annotations

import csv
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")

DATA = Path(__file__).resolve().parents[2] / "taxcore" / "data"
SLEEVES = [{"strategy_ref": "MomSharpe", "target_pct": 60},
           {"strategy_ref": "CASH_CDI", "target_pct": 40}]
DEFAULT_REBAL = {"buy_only_laggards": False, "sell_only_for_harvest": False, "cadence": "ME"}


def _rows(name: str) -> list[dict]:
    return list(csv.DictReader((DATA / name).read_text().splitlines()))


async def _seed(client) -> tuple[dict, str]:
    """dev-login, ensure positions + sleeves exist, reset settings to a clean baseline.
    Returns (auth headers, tenant id). Standalone runs seed the canonical positions,
    trades and cash_events directly (the TLH overlay's build_timeline needs the last
    two); the full suite imports them first, so we detect and skip."""
    token = (await client.post("/auth/dev-login")).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    tid = uuid.UUID((await client.get("/me", headers=headers)).json()["id"])

    from sqlalchemy import func, select
    from platform_app.b3run import to_date, to_float
    from platform_app.db import async_session_maker
    from platform_app.models import CashEvent, Position, Trade

    async with async_session_maker() as s:
        n = await s.scalar(select(func.count()).select_from(Position)
                           .where(Position.tenant_id == tid))
        if not n:  # standalone run: no import step ran, seed the canonical CSVs
            for r in _rows("positions.csv"):
                s.add(Position(
                    tenant_id=tid, ticker=r["ticker"], qty=to_float(r["qty"]),
                    avg_cost=to_float(r["avg_cost"]), total_cost=to_float(r["total_cost"]),
                    market=r["market"]))
            for r in _rows("trades.csv"):
                s.add(Trade(
                    tenant_id=tid, date=to_date(r["date"]), side=r["side"], market=r["market"],
                    maturity=r["maturity"], broker=r["broker"], ticker=r["ticker"],
                    qty=to_float(r["qty"]), price=to_float(r["price"]), amount=to_float(r["amount"]),
                    source_file=r["__source_file"], signed_qty=to_float(r["signed_qty"])))
            for r in _rows("cash_events.csv"):
                s.add(CashEvent(
                    tenant_id=tid, date=to_date(r["date"]), ticker=r["ticker"],
                    event_type=r["event_type"], event_category=r["event_category"],
                    is_amortization=r["is_amortization"].strip().lower() == "true",
                    qty=to_float(r["qty"]), price=to_float(r["price"]), amount=to_float(r["amount"]),
                    broker=r["broker"], source_file=r["__source_file"]))
            await s.commit()

    assert (await client.put("/portfolio/sleeves", json=SLEEVES, headers=headers)).status_code == 200
    # clean settings baseline so tests are order-independent (shared dev tenant)
    await client.put("/portfolio/settings/asset-overrides", json=[], headers=headers)
    await client.put("/portfolio/settings/rebalance", json=DEFAULT_REBAL, headers=headers)
    return headers, tid


async def _put_rebal(client, headers, **over):
    body = {**DEFAULT_REBAL, **over}
    r = await client.put("/portfolio/settings/rebalance", json=body, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


async def _plan(client, headers, **body):
    r = await client.post("/portfolio/rebalance-plans", json=body, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


async def _target(client, headers):
    r = await client.get("/portfolio/target", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


# ── settings CRUD ─────────────────────────────────────────────────────────────

async def test_asset_overrides_put_get_validation(client):
    headers, _ = await _seed(client)
    base = "/portfolio/settings/asset-overrides"

    assert (await client.get(base, headers=headers)).json() == []

    # tickers are upper-cased on write
    r = await client.put(base, json=[{"ticker": "lftb11", "treatment": "cash_cdi"}], headers=headers)
    assert r.status_code == 200, r.text
    assert r.json() == [{"ticker": "LFTB11", "treatment": "cash_cdi"}]
    assert (await client.get(base, headers=headers)).json() == [{"ticker": "LFTB11", "treatment": "cash_cdi"}]

    # unknown treatment → 422, and the rejected PUT leaves the stored set intact
    assert (await client.put(base, json=[{"ticker": "PETR4", "treatment": "bond"}],
                             headers=headers)).status_code == 422
    assert (await client.get(base, headers=headers)).json() == [{"ticker": "LFTB11", "treatment": "cash_cdi"}]

    # duplicate ticker → 422
    assert (await client.put(base, json=[{"ticker": "PETR4", "treatment": "cash_cdi"},
                                         {"ticker": "petr4", "treatment": "cash_cdi"}],
                             headers=headers)).status_code == 422

    # empty replace clears the set
    assert (await client.put(base, json=[], headers=headers)).status_code == 200
    assert (await client.get(base, headers=headers)).json() == []


async def test_rebalance_settings_put_get_validation(client):
    headers, _ = await _seed(client)
    base = "/portfolio/settings/rebalance"

    # baseline (reset by _seed)
    assert (await client.get(base, headers=headers)).json() == DEFAULT_REBAL

    # each valid cadence round-trips
    for cad in ("ME", "QE", "W-FRI"):
        r = await client.put(base, json={"buy_only_laggards": True,
                                         "sell_only_for_harvest": True, "cadence": cad}, headers=headers)
        assert r.status_code == 200, r.text
        assert r.json() == {"buy_only_laggards": True, "sell_only_for_harvest": True, "cadence": cad}
        assert (await client.get(base, headers=headers)).json()["cadence"] == cad

    # invalid cadence → 422
    assert (await client.put(base, json={"cadence": "yearly"}, headers=headers)).status_code == 422

    await _put_rebal(client, headers)  # reset


# ── Feature 3: cash/CDI holding folds into cash ───────────────────────────────

@pytest.mark.market
async def test_cash_cdi_folds_into_cash_on_target(client):
    headers, _ = await _seed(client)
    ov = "/portfolio/settings/asset-overrides"

    base = await _target(client, headers)
    assert "LFTB11" in {d["ticker"] for d in base["drift"]}, "LFTB11 should start as an equity drift row"

    await client.put(ov, json=[{"ticker": "LFTB11", "treatment": "cash_cdi"}], headers=headers)
    t = await _target(client, headers)

    assert "LFTB11" not in {d["ticker"] for d in t["drift"]}          # no longer an equity drift/sell
    assert t["cash_weight_current"] > base["cash_weight_current"] + 1e-9  # its value folded into cash
    assert t["nav"] == pytest.approx(base["nav"])                    # still counted in the book

    await client.put(ov, json=[], headers=headers)  # reset


@pytest.mark.market
async def test_cash_cdi_not_sold_in_rebalance(client):
    headers, _ = await _seed(client)
    ov = "/portfolio/settings/asset-overrides"
    await client.put(ov, json=[{"ticker": "LFTB11", "treatment": "cash_cdi"}], headers=headers)

    plan = await _plan(client, headers, new_cash=0)
    legs = plan["trades"]["sells"] + plan["trades"]["buys"]
    assert all(leg["ticker"].upper() != "LFTB11" for leg in legs)  # never traded as equity

    await client.put(ov, json=[], headers=headers)  # reset


# ── Feature 4: rebalance behavior ─────────────────────────────────────────────

@pytest.mark.market
async def test_buy_only_laggards_yields_no_nonharvest_sells(client):
    headers, _ = await _seed(client)

    base = await _plan(client, headers, new_cash=50_000)
    assert base["cadence"] == "ME"
    assert base["trades"]["sells"], "default plan should propose some trims to filter out"

    await _put_rebal(client, headers, buy_only_laggards=True)
    plan = await _plan(client, headers, new_cash=50_000)

    assert plan["settings"]["buy_only_laggards"] is True
    assert plan["trades"]["sells"] == []  # pure top-up: zero non-harvest sells
    spent = sum(b["est_brl"] for b in plan["trades"]["buys"])
    assert spent <= 50_000 + 1e-6        # deploys only the new cash, no phantom proceeds

    await _put_rebal(client, headers)  # reset


@pytest.mark.market
async def test_sell_only_for_harvest_yields_only_loss_sells(client):
    headers, _ = await _seed(client)
    positions = {p["ticker"]: p for p in
                 (await client.get("/portfolio/positions", headers=headers)).json()}

    base = await _plan(client, headers, new_cash=0)
    base_sells = {s["ticker"] for s in base["trades"]["sells"]}

    await _put_rebal(client, headers, sell_only_for_harvest=True)
    plan = await _plan(client, headers, new_cash=0)
    sells = plan["trades"]["sells"]

    # every surviving sell realizes a loss (est_price below preço-médio)…
    for s in sells:
        assert s["est_price"] < positions[s["ticker"]]["avg_cost"], s
    # …and they're a subset of the default trims (only the gainers were dropped)
    assert {s["ticker"] for s in sells} <= base_sells
    assert len(sells) <= len(base["trades"]["sells"])

    await _put_rebal(client, headers)  # reset


# ── Renda fixa contract terms ─────────────────────────────────────────────────

RF_TERMS = [
    {"ticker": "CDB - CDB25F3MPT8", "indexer": "cdi_pct", "rate_pct": 102.0},
    {"ticker": "LCI - LCI24H9RKV3", "indexer": "ipca_plus", "rate_pct": 6.25},
]


async def test_renda_fixa_terms_round_trip_and_replace_wholesale(client):
    headers, _ = await _seed(client)

    r = await client.put("/portfolio/settings/renda-fixa", json=RF_TERMS,
                         headers=headers)
    assert r.status_code == 200, r.text
    got = (await client.get("/portfolio/settings/renda-fixa",
                            headers=headers)).json()
    assert {t["ticker"]: (t["indexer"], t["rate_pct"]) for t in got} == {
        "CDB - CDB25F3MPT8": ("cdi_pct", 102.0),
        "LCI - LCI24H9RKV3": ("ipca_plus", 6.25),
    }

    # PUT replaces the whole set — a contract dropped from the body is gone, not
    # merged, same semantics as the asset overrides above.
    await client.put("/portfolio/settings/renda-fixa", json=RF_TERMS[:1],
                     headers=headers)
    got = (await client.get("/portfolio/settings/renda-fixa",
                            headers=headers)).json()
    assert [t["ticker"] for t in got] == ["CDB - CDB25F3MPT8"]

    await client.put("/portfolio/settings/renda-fixa", json=[], headers=headers)


@pytest.mark.parametrize("bad, why", [
    ([{"ticker": "CDB - X", "indexer": "selic_plus", "rate_pct": 1}], "indexer"),
    ([{"ticker": "  ", "indexer": "pre", "rate_pct": 12}], "ticker"),
    ([{"ticker": "CDB - X", "indexer": "pre", "rate_pct": 12},
      {"ticker": "CDB - X", "indexer": "cdi_pct", "rate_pct": 100}], "duplicate"),
    # A negative rate would draw a falling curve on paper that only ever accrues.
    ([{"ticker": "CDB - X", "indexer": "pre", "rate_pct": -5}], "negative"),
])
async def test_renda_fixa_terms_reject_what_cannot_be_accrued(client, bad, why):
    headers, _ = await _seed(client)
    r = await client.put("/portfolio/settings/renda-fixa", json=bad,
                         headers=headers)
    assert r.status_code == 422, why
