"""End-to-end: upload the real B3 extracts, run the import job, and assert the
platform path is equivalent to taxcore's golden-tested scripts.

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
    """Insert the real cost-basis overrides for this tenant (in file order), so the
    import reproduces the golden positions (e.g. the XPBR31 cisão buy)."""
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
    assert result["positions"] == 45

    # imports flipped to done
    imports = (await client.get("/imports", headers=headers)).json()
    assert len(imports) == 16
    assert {i["status"] for i in imports} == {"done"}

    # positions equal taxcore/data/positions.csv (ticker/qty/avg_cost, float-tolerant)
    api_pos = {p["ticker"]: p for p in (await client.get("/portfolio/positions", headers=headers)).json()}
    expected = {r["ticker"]: r for r in csv.DictReader(POSITIONS_CSV.read_text().splitlines())}
    assert len(api_pos) == len(expected) == 45
    for ticker, exp in expected.items():
        assert ticker in api_pos, f"missing {ticker}"
        assert abs(api_pos[ticker]["qty"] - float(exp["qty"])) < 1e-6, ticker
        assert abs(api_pos[ticker]["avg_cost"] - float(exp["avg_cost"])) < 1e-4, ticker

    # trades endpoint returns newest-first, paginated
    trades = (await client.get("/portfolio/trades?limit=5", headers=headers)).json()
    assert trades["total"] == 1576
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


async def test_tlh_plans_after_import(client):
    """Runs after the import test above (same module, same session DB) so the dev
    tenant already has its 45 positions + trades. Prices come from the vendored
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
