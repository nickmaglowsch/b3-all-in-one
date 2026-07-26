"""The tax-summary cache's freshness and isolation contract.

Deliberately no market plane and no engine subprocess: these exercise
`_tax_summary_payload` and the upsert against real rows, so they run everywhere
the DB does, unlike the `market`-marked end-to-end test.

The contract is small and entirely about honesty. A cached tax figure is wrong,
not merely old, once an extract lands after it or the month it describes ends —
and one tenant's apuração must never answer for another's book.

    PLATFORM_TEST_DB=platform_test_cache uv run pytest tests/test_tax_summary_cache.py
"""
from __future__ import annotations

import datetime
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import delete

from platform_app.db import async_session_maker
from platform_app.models import Import, TaxSummary
from platform_app.routes import _month_key, _tax_summary_payload

pytestmark = pytest.mark.asyncio(loop_scope="session")

NOW = datetime.datetime.now(datetime.timezone.utc)


def plan(month: str, *, carry: float = 0.0) -> dict:
    return {
        "month": month,
        "isencao_limit": 20_000.0,
        "rates": {"AÇÃO": 0.15, "FII": 0.20},
        "situation": {"AÇÃO": {"gross_mtd": 0.0, "pnl_mtd": 0.0, "carry": carry}},
    }


@pytest_asyncio.fixture(loop_scope="session")
async def session():
    async with async_session_maker() as s:
        yield s
        # These tests write rows keyed by throwaway tenant ids; clear both tables
        # so a later test that counts rows is not reading this one's leftovers.
        await s.execute(delete(TaxSummary))
        await s.execute(delete(Import))
        await s.commit()


async def test_a_fresh_payload_for_this_month_reports_no_staleness(session):
    tid = uuid.uuid4()
    session.add(TaxSummary(tenant_id=tid, payload=plan(_month_key(datetime.date.today())),
                           computed_at=NOW))
    await session.commit()

    out = await _tax_summary_payload(session, tid)
    assert out["stale"] is False
    assert out["stale_reason"] is None
    assert out["computed_at"]


async def test_an_import_after_the_computation_makes_it_stale(session):
    """The figure is derived from trades, so an extract landing afterwards makes
    it wrong. Counted even while still pending — a book mid-import is exactly
    when the old number is least trustworthy."""
    tid = uuid.uuid4()
    session.add(TaxSummary(tenant_id=tid, payload=plan(_month_key(datetime.date.today())),
                           computed_at=NOW - datetime.timedelta(hours=2)))
    session.add(Import(tenant_id=tid, filename="negociacao.xlsx", stored_path="/tmp/x",
                       kind="negociacao", status="pending",
                       created_at=NOW - datetime.timedelta(minutes=5)))
    await session.commit()

    out = await _tax_summary_payload(session, tid)
    assert out["stale"] is True
    assert out["stale_reason"] == "import"


async def test_the_month_rolling_over_makes_it_stale_with_no_import(session):
    """Every figure in `situation` is month-to-date, so a payload from a month
    that has ended is stale with nothing else having happened — the isenção has
    reset and pnl_mtd is back to zero. Nothing in the import history says so."""
    tid = uuid.uuid4()
    session.add(TaxSummary(tenant_id=tid, payload=plan("01/1999"), computed_at=NOW))
    await session.commit()

    out = await _tax_summary_payload(session, tid)
    assert out["stale"] is True
    assert out["stale_reason"] == "month"


async def test_never_computed_is_null_not_an_empty_payload(session):
    """Absence has to read as absence. A zeroed payload here would render as
    "you owe nothing", which is a different claim entirely."""
    out = await _tax_summary_payload(session, uuid.uuid4())
    assert out == {"payload": None, "computed_at": None, "stale": False,
                   "stale_reason": None}


async def test_one_tenants_apuracao_never_answers_for_another(session):
    """The row is keyed by tenant and read by primary key, so there is no path
    from A's request to B's figures — the property the enterprise tier is sold
    on, at the one table that caches money."""
    a, b = uuid.uuid4(), uuid.uuid4()
    month = _month_key(datetime.date.today())
    session.add(TaxSummary(tenant_id=a, payload=plan(month, carry=111.0), computed_at=NOW))
    session.add(TaxSummary(tenant_id=b, payload=plan(month, carry=222.0), computed_at=NOW))
    await session.commit()

    out_a = await _tax_summary_payload(session, a)
    out_b = await _tax_summary_payload(session, b)
    assert out_a["payload"]["situation"]["AÇÃO"]["carry"] == 111.0
    assert out_b["payload"]["situation"]["AÇÃO"]["carry"] == 222.0

    # And B's import does not make A stale: staleness is per-tenant too.
    session.add(Import(tenant_id=b, filename="n.xlsx", stored_path="/tmp/y",
                       kind="negociacao", status="done", created_at=NOW + datetime.timedelta(hours=1)))
    await session.commit()
    assert (await _tax_summary_payload(session, a))["stale"] is False
    assert (await _tax_summary_payload(session, b))["stale_reason"] == "import"
