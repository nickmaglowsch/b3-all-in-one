"""DARF ledger — the monthly apuração view and the alert on an unpaid month.

spec/03: "port of `darf_payments.csv` as CRUD + monthly apuração view; alert when
a month's computed tax is due and unrecorded."

The apuração half is computed, never stored: it comes from taxcore's
``irpf_2026 --json``, the same function that renders the report's Renda Variável
tables. Only the *payment* is a row in this database, which is why the ledger can
never disagree with the report and why there is no stored tax state to corrupt.

    PLATFORM_TEST_DB=platform_test_darf uv run pytest tests/test_darf.py
"""
from __future__ import annotations

import csv
import datetime
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")

DATA = Path(__file__).resolve().parents[2] / "taxcore" / "data"

# The golden book's one taxable ação month: R$ 25.970 gross (over the isenção),
# R$ 5.034,57 realised, no carry → 15% = R$ 755,19. Same figures as the row that
# seeded taxcore/data/darf_payments.csv.
ABRIL = {"apuracao": "2025-04", "classe": "AÇÃO", "ir": 755.19}
# 31/05/2025 is a Saturday, so the último dia útil is Friday the 30th.
ABRIL_VENCIMENTO = "2025-05-30"


def _rows(name: str) -> list[dict]:
    return list(csv.DictReader((DATA / name).read_text().splitlines()))


async def _seed(client) -> dict:
    """dev-login, ensure the canonical trades + cash_events exist, empty the ledger.

    The apuração reads only those two tables (no positions, no uploaded extracts),
    so this is the whole fixture.
    """
    token = (await client.post("/auth/dev-login")).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    tid = uuid.UUID((await client.get("/me", headers=headers)).json()["id"])

    from sqlalchemy import func, select

    from platform_app.b3run import to_date, to_float
    from platform_app.db import async_session_maker
    from platform_app.models import CashEvent, Trade

    async with async_session_maker() as s:
        n = await s.scalar(select(func.count()).select_from(Trade)
                           .where(Trade.tenant_id == tid))
        if not n:  # standalone run: no import step ran, seed the canonical CSVs
            for r in _rows("trades.csv"):
                s.add(Trade(
                    tenant_id=tid, date=to_date(r["date"]), side=r["side"],
                    market=r["market"], maturity=r["maturity"], broker=r["broker"],
                    ticker=r["ticker"], qty=to_float(r["qty"]), price=to_float(r["price"]),
                    amount=to_float(r["amount"]), source_file=r["__source_file"],
                    signed_qty=to_float(r["signed_qty"])))
            for r in _rows("cash_events.csv"):
                s.add(CashEvent(
                    tenant_id=tid, date=to_date(r["date"]), ticker=r["ticker"],
                    event_type=r["event_type"], event_category=r["event_category"],
                    is_amortization=r["is_amortization"].strip().lower() == "true",
                    qty=to_float(r["qty"]), price=to_float(r["price"]),
                    amount=to_float(r["amount"]), broker=r["broker"],
                    source_file=r["__source_file"]))
            await s.commit()

    # Shared dev tenant: start every test from an empty ledger.
    assert (await client.put("/irpf/darf?year=2025", json=[],
                             headers=headers)).status_code == 200
    return headers


def _abril(payload: dict) -> dict:
    return next(m for m in payload["meses"]
                if m["apuracao"] == ABRIL["apuracao"] and m["classe"] == ABRIL["classe"])


# ── vencimento (pure) ────────────────────────────────────────────────────────

async def test_vencimento_is_the_last_business_day_of_the_following_month():
    from platform_app.routes import _darf_vencimento

    d = datetime.date
    # 31/05/2025 = Saturday → Friday the 30th.
    assert _darf_vencimento(d(2025, 4, 1)) == d(2025, 5, 30)
    # 31/08/2025 = Sunday → Friday the 29th.
    assert _darf_vencimento(d(2025, 7, 1)) == d(2025, 8, 29)
    # 31/07/2025 = Thursday → itself.
    assert _darf_vencimento(d(2025, 6, 1)) == d(2025, 7, 31)


async def test_a_december_apuracao_rolls_into_the_next_year():
    from platform_app.routes import _darf_vencimento

    # 31/01/2026 = Saturday → Friday the 30th, in 2026.
    assert _darf_vencimento(datetime.date(2025, 12, 1)) == datetime.date(2026, 1, 30)


# ── apuração view ────────────────────────────────────────────────────────────

async def test_a_taxable_month_is_listed_with_its_computed_ir_and_due_date(client):
    headers = await _seed(client)
    r = await client.get("/irpf/darf?year=2025", headers=headers)
    assert r.status_code == 200, r.text
    payload = r.json()

    assert payload["year"] == 2025
    assert payload["codigo"] == "6015"
    abril = _abril(payload)
    assert abril["ir_devido"] == pytest.approx(ABRIL["ir"], abs=0.01)
    assert abril["aliquota"] == 0.15
    assert abril["vencimento"] == ABRIL_VENCIMENTO
    assert abril["pagamento"] is None


async def test_months_that_owe_nothing_are_not_in_the_ledger(client):
    """The full twelve-month grid is the report's job. The ledger lists what has
    to be paid — a quiet month here would be a row nobody can act on and, worse,
    a row that looks like an unpaid one."""
    headers = await _seed(client)
    payload = (await client.get("/irpf/darf?year=2025", headers=headers)).json()

    assert payload["meses"], "expected at least the April ação DARF"
    assert all(m["ir_devido"] > 0 for m in payload["meses"])
    assert ("2025-01", "AÇÃO") not in {(m["apuracao"], m["classe"]) for m in payload["meses"]}


async def test_an_unpaid_month_past_its_due_date_raises_the_alert(client):
    """The whole point of the feature: ano-base 2025 DARFs came due in 2025/2026,
    so with nothing recorded every one of them is atrasada."""
    headers = await _seed(client)
    payload = (await client.get("/irpf/darf?year=2025", headers=headers)).json()

    assert _abril(payload)["status"] == "atrasado"
    assert payload["atrasados"] == len(payload["meses"])
    assert payload["total_pago"] == 0.0
    assert payload["total_devido"] > 0


async def test_recording_the_payment_clears_the_alert(client):
    headers = await _seed(client)
    saved = await client.put("/irpf/darf?year=2025", json=[{
        "apuracao": ABRIL["apuracao"], "classe": ABRIL["classe"],
        "valor_principal": ABRIL["ir"], "data_pagamento": ABRIL_VENCIMENTO,
        "obs": "pago no vencimento",
    }], headers=headers)
    assert saved.status_code == 200, saved.text

    payload = (await client.get("/irpf/darf?year=2025", headers=headers)).json()
    abril = _abril(payload)
    assert abril["status"] == "pago"
    assert abril["pagamento"]["valor_principal"] == pytest.approx(ABRIL["ir"])
    assert abril["pagamento"]["data_pagamento"] == ABRIL_VENCIMENTO
    assert abril["pagamento"]["obs"] == "pago no vencimento"
    assert payload["total_pago"] == pytest.approx(ABRIL["ir"])
    assert payload["atrasados"] == len(payload["meses"]) - 1


async def test_a_recorded_payment_for_a_month_that_owes_nothing_is_still_shown(client):
    """A payment the user typed is never hidden, even where the engine now
    computes zero — that disagreement is exactly what they need to see."""
    headers = await _seed(client)
    await client.put("/irpf/darf?year=2025", json=[{
        "apuracao": "2025-01", "classe": "AÇÃO", "valor_principal": 10.0,
        "data_pagamento": "2025-02-28", "obs": "",
    }], headers=headers)

    payload = (await client.get("/irpf/darf?year=2025", headers=headers)).json()
    jan = next(m for m in payload["meses"]
               if m["apuracao"] == "2025-01" and m["classe"] == "AÇÃO")
    assert jan["ir_devido"] == 0.0
    assert jan["status"] == "pago"


# ── CRUD ─────────────────────────────────────────────────────────────────────

async def test_the_put_replaces_only_the_requested_year(client):
    headers = await _seed(client)
    await client.put("/irpf/darf?year=2024", json=[{
        "apuracao": "2024-03", "classe": "FII", "valor_principal": 40.0,
        "data_pagamento": None, "obs": "",
    }], headers=headers)
    await client.put("/irpf/darf?year=2025", json=[{
        "apuracao": "2025-04", "classe": "AÇÃO", "valor_principal": 755.19,
        "data_pagamento": None, "obs": "",
    }], headers=headers)

    from sqlalchemy import select

    from platform_app.db import async_session_maker
    from platform_app.models import DarfPayment

    tid = uuid.UUID((await client.get("/me", headers=headers)).json()["id"])
    async with async_session_maker() as s:
        rows = (await s.scalars(select(DarfPayment)
                                .where(DarfPayment.tenant_id == tid))).all()
    assert {(r.apuracao.year, r.classe) for r in rows} == {(2024, "FII"), (2025, "AÇÃO")}

    # And an empty PUT clears that year alone.
    await client.put("/irpf/darf?year=2025", json=[], headers=headers)
    async with async_session_maker() as s:
        rows = (await s.scalars(select(DarfPayment)
                                .where(DarfPayment.tenant_id == tid))).all()
    assert {r.apuracao.year for r in rows} == {2024}
    await client.put("/irpf/darf?year=2024", json=[], headers=headers)


@pytest.mark.parametrize("bad, why", [
    ({"apuracao": "2024-04", "classe": "AÇÃO", "valor_principal": 1.0}, "wrong year"),
    ({"apuracao": "2025-13", "classe": "AÇÃO", "valor_principal": 1.0}, "month 13"),
    ({"apuracao": "abril", "classe": "AÇÃO", "valor_principal": 1.0}, "not a month"),
    ({"apuracao": "2025-04", "classe": "CRIPTO", "valor_principal": 1.0}, "unknown class"),
    ({"apuracao": "2025-04", "classe": "AÇÃO", "valor_principal": -1.0}, "negative"),
])
async def test_the_put_rejects_a_row_it_cannot_mean(client, bad, why):
    headers = await _seed(client)
    r = await client.put("/irpf/darf?year=2025", json=[bad], headers=headers)
    assert r.status_code == 422, f"{why}: {r.status_code} {r.text}"


async def test_the_same_month_and_class_cannot_be_paid_twice(client):
    headers = await _seed(client)
    r = await client.put("/irpf/darf?year=2025", json=[
        {"apuracao": "2025-04", "classe": "AÇÃO", "valor_principal": 1.0},
        {"apuracao": "2025-04", "classe": "AÇÃO", "valor_principal": 2.0},
    ], headers=headers)
    assert r.status_code == 422, r.text


async def test_the_ledger_is_tenant_scoped(client):
    """Another tenant's DARFs are another tenant's. The router is mounted behind
    needs_investidor, so this checks the row filter, not the auth."""
    headers = await _seed(client)
    await client.put("/irpf/darf?year=2025", json=[{
        "apuracao": "2025-04", "classe": "AÇÃO", "valor_principal": 755.19,
    }], headers=headers)

    from platform_app.db import async_session_maker
    from platform_app.routes import _darf_payments

    async with async_session_maker() as s:
        mine = await _darf_payments(s, uuid.UUID(
            (await client.get("/me", headers=headers)).json()["id"]), 2025)
        theirs = await _darf_payments(s, uuid.uuid4(), 2025)
    assert mine and not theirs
