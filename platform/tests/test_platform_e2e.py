"""End-to-end: upload the vendored B3 extracts, run the import job, and assert the
platform path is equivalent to taxcore's golden-tested scripts.

The extracts are synthetic (taxcore/tests/golden/make_fixtures.py) — the counts
below track whatever that generator last produced.

dev-login -> POST /imports (all 16 fixtures) -> run process_imports inline ->
positions match taxcore/data/positions.csv, and POST /irpf/reports year=2025
reproduces the golden IRPF markdown (modulo the date-generated line).
"""
from __future__ import annotations

import csv
import re
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")

TAXCORE = Path(__file__).resolve().parents[2] / "taxcore"
B3_IMPORTS = TAXCORE / "b3_imports"
POSITIONS_CSV = TAXCORE / "data" / "positions.csv"
OVERRIDES_CSV = TAXCORE / "data" / "cost_basis_overrides.csv"
GOLDEN_IRPF = TAXCORE / "tests" / "golden" / "expected" / "irpf-2025.md"

_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _normalize_irpf(text: str) -> str:
    return re.sub(r"(?m)^\*\*Date generated\*\*:.*$", "**Date generated**: <DATE>", text)


async def _seed_overrides(tenant_id: uuid.UUID) -> None:
    """Insert the vendored cost-basis overrides for this tenant (in file order), so
    the import reproduces the golden positions (e.g. the XPBR31 cisão buy)."""
    from platform_app.b3run import to_date, to_float
    from platform_app.db import async_session_maker
    from platform_app.models import CostBasisOverride

    lines = [l for l in OVERRIDES_CSV.read_text().splitlines() if not l.startswith("#")]
    rows = list(csv.DictReader(lines))
    async with async_session_maker() as session:
        for r in rows:
            session.add(CostBasisOverride(
                tenant_id=tenant_id, date=to_date(r["date"]), ticker=r["ticker"],
                kind=r["kind"], qty=to_float(r["qty"]), price=to_float(r["price"]),
                amount=to_float(r["amount"]), note=r["note"],
            ))
        await session.commit()


async def test_an_uploaded_filename_cannot_escape_the_tenant_directory(client):
    """The client picks the filename, and LGPD erasure rmtree's the tenant's upload
    directory — so an extract written outside it would outlive the account it
    belongs to. Everything downstream (the IRPF workspace copy) rides on this too."""
    from platform_app.config import settings

    token = (await client.post("/auth/dev-login")).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    tid = (await client.get("/me", headers=headers)).json()["id"]

    uploads = Path(settings.uploads_dir)
    escaped = uploads / "negociacao-escaped.xlsx"
    escaped.unlink(missing_ok=True)

    r = await client.post(
        "/imports",
        files=[("files", ("../negociacao-escaped.xlsx", b"not really xlsx", _XLSX))],
        headers=headers,
    )
    assert r.status_code == 200, r.text

    assert not escaped.exists(), "upload escaped the tenant directory"
    assert (uploads / tid / "negociacao-escaped.xlsx").exists()
    # The stored name is the sanitized one, so the erasure sweep can find it.
    assert r.json()[0]["filename"] == "negociacao-escaped.xlsx"

    # Leave the dev tenant's import list as the golden test below expects it.
    from sqlalchemy import delete

    from platform_app.db import async_session_maker
    from platform_app.models import Import

    async with async_session_maker() as session:
        await session.execute(delete(Import).where(Import.tenant_id == uuid.UUID(tid)))
        await session.commit()
    (uploads / tid / "negociacao-escaped.xlsx").unlink(missing_ok=True)


async def test_import_positions_and_irpf_equivalent_to_golden(client):
    token = (await client.post("/auth/dev-login")).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    tid = (await client.get("/me", headers=headers)).json()["id"]

    await _seed_overrides(uuid.UUID(tid))

    xlsx = sorted(B3_IMPORTS.glob("*.xlsx"))
    assert len(xlsx) == 16
    files = [("files", (p.name, p.read_bytes(), _XLSX)) for p in xlsx]
    r = await client.post("/imports", files=files, headers=headers)
    assert r.status_code == 200, r.text
    assert len(r.json()) == 16

    # Run the job inline (the deferred one is never picked up in tests).
    from platform_app.tasks import process_imports

    result = await process_imports(tenant_id=tid)
    assert result["status"] == "done", result
    assert result["positions"] == 25

    # imports flipped to done
    imports = (await client.get("/imports", headers=headers)).json()
    assert len(imports) == 16
    assert {i["status"] for i in imports} == {"done"}

    # positions equal taxcore/data/positions.csv (ticker/qty/avg_cost, float-tolerant)
    api_pos = {p["ticker"]: p for p in (await client.get("/portfolio/positions", headers=headers)).json()}
    expected = {r["ticker"]: r for r in csv.DictReader(POSITIONS_CSV.read_text().splitlines())}
    assert len(api_pos) == len(expected) == 25
    for ticker, exp in expected.items():
        assert ticker in api_pos, f"missing {ticker}"
        assert abs(api_pos[ticker]["qty"] - float(exp["qty"])) < 1e-6, ticker
        assert abs(api_pos[ticker]["avg_cost"] - float(exp["avg_cost"])) < 1e-4, ticker

    # trades endpoint returns newest-first, paginated
    trades = (await client.get("/portfolio/trades?limit=5", headers=headers)).json()
    assert trades["total"] == 74
    assert len(trades["items"]) == 5
    dates = [t["date"] for t in trades["items"]]
    assert dates == sorted(dates, reverse=True)

    # income aggregates by category + year
    income = (await client.get("/portfolio/income", headers=headers)).json()
    assert any(row["event_category"] == "income" for row in income)

    # IRPF report reproduces the golden markdown (modulo the date line)
    r = await client.post("/irpf/reports", json={"year": 2025}, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["year"] == 2025
    assert _normalize_irpf(r.json()["markdown"]) == _normalize_irpf(GOLDEN_IRPF.read_text())


@pytest.mark.market
async def test_tlh_plans_after_import(client):
    """Runs after the import test above (same module, same session DB) so the dev
    tenant already has its 25 positions + trades. Prices come from the vendored
    taxcore/data snapshots (platform-market-prices-later)."""
    token = (await client.post("/auth/dev-login")).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # No sells → just the current situation, both tax buckets keyed.
    r = await client.post("/tlh/plans", json={"sells": []}, headers=headers)
    assert r.status_code == 200, r.text
    plan = r.json()
    assert set(plan["situation"]) == {"AÇÃO", "FII"}
    assert plan["buckets"] == {}  # nothing proposed, no plan

    # A real profit-taking sell of a held, priced ticker → its bucket shows up.
    r = await client.post(
        "/tlh/plans", json={"sells": [{"ticker": "PETR4", "qty": 100}]}, headers=headers)
    assert r.status_code == 200, r.text
    plan = r.json()
    assert plan["buckets"], "expected the AÇÃO bucket for a PETR4 sell"
    assert any(s["ticker"] == "PETR4" for s in plan["sells"])

    # Absurd qty (far above holdings) → validation error mapped to 422.
    r = await client.post(
        "/tlh/plans", json={"sells": [{"ticker": "PETR4", "qty": 10_000_000}]}, headers=headers)
    assert r.status_code == 422, r.text


@pytest.mark.market
async def test_target_and_rebalance_after_import(client):
    """Runs after the import test (same module/session) so the dev tenant has its 25
    positions. This module sorts before test_sleeves, so no sleeves exist yet."""
    token = (await client.post("/auth/dev-login")).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # No sleeves configured yet → 409.
    assert (await client.get("/portfolio/target", headers=headers)).status_code == 409

    good = [{"strategy_ref": "MomSharpe", "target_pct": 60},
            {"strategy_ref": "CASH_CDI", "target_pct": 40}]
    assert (await client.put("/portfolio/sleeves", json=good, headers=headers)).status_code == 200

    # --- Combined target + drift ---
    tgt = (await client.get("/portfolio/target", headers=headers)).json()
    cw = tgt["combined_weights"]
    assert cw, "expected non-empty combined weights"
    assert tgt["cash_weight_target"] == pytest.approx(0.4)
    # Full allocation (investable + cash) sums to 1; MomSharpe fills the 60% sleeve.
    assert sum(cw.values()) + tgt["cash_weight_target"] == pytest.approx(1.0, abs=1e-6)
    assert sum(cw.values()) == pytest.approx(0.6, abs=1e-6)
    assert tgt["nav"] > 0
    for d in tgt["drift"]:
        assert d["drift_w"] == pytest.approx(d["target_w"] - d["current_w"])
        assert d["drift_brl"] == pytest.approx((d["target_w"] - d["current_w"]) * tgt["nav"])

    # --- Rebalance plan: integer trades, tax overlay, exclude honored ---
    positions = {p["ticker"] for p in
                 (await client.get("/portfolio/positions", headers=headers)).json()}
    assert "PETR4" in positions  # the ticker we'll exclude is really held

    plan = (await client.post(
        "/portfolio/rebalance-plans",
        json={"new_cash": 0, "exclude": ["PETR4"]}, headers=headers)).json()
    sells, buys = plan["trades"]["sells"], plan["trades"]["buys"]

    for leg in sells + buys:
        assert isinstance(leg["qty"], int) and leg["qty"] > 0
    # never sell what isn't held
    for s in sells:
        assert s["ticker"] in positions, s["ticker"]
    # exclude honored: PETR4 in neither leg
    assert all(leg["ticker"] != "PETR4" for leg in sells + buys)
    # tax section present with both class buckets in the current situation
    assert set(plan["tax"]["situation"]) == {"AÇÃO", "FII"}
    assert isinstance(plan["notes"], list)
    assert plan["nav"] > 0
