"""Strategy-sleeve weights live in Postgres and are refreshed off the request.

A strategy sleeve's weights are a full market-plane build (10-20 s). The route
must compute one inline only when nothing is stored for it; an answer from an
older day or plane is served while the ingest worker refreshes it; and the
answer survives the process (a deploy) — see platform_app/sleeve_weights.py.
"""
from __future__ import annotations

import datetime
import uuid

import pytest
import pytest_asyncio

from platform_app import routes, sleeve_weights, tasks_sleeves
from platform_app.db import async_session_maker
from platform_app.models import PortfolioSleeve, RebalanceSettings, UserStrategy
from tests.conftest import reset_sleeve_weights

pytestmark = pytest.mark.asyncio(loop_scope="session")
TODAY = datetime.date.today()


@pytest_asyncio.fixture(loop_scope="session", autouse=True)
async def _clean(monkeypatch):
    await reset_sleeve_weights()
    refreshes: list = []
    monkeypatch.setattr(sleeve_weights, "refresh_soon", lambda *a: refreshes.append(a))
    yield refreshes
    await reset_sleeve_weights()


def counting(calls: list, weights: dict | None = None):
    async def advise(strategy, freq, extra_env=None):
        calls.append((strategy, freq))
        return {"weights": weights or {f"{strategy}3": 1.0}}
    return advise


async def _book(*refs: str) -> uuid.UUID:
    tid = uuid.uuid4()
    async with async_session_maker() as s:
        for ref in refs:
            s.add(PortfolioSleeve(tenant_id=tid, strategy_ref=ref, target_pct=100 / len(refs)))
        await s.commit()
    return tid


async def _target(tid, freq="ME"):
    async with async_session_maker() as s:
        combined, *_ = await routes._combined_target(s, tid, freq)
    return combined


async def _store(fam, day, payload, tid=None):
    async with async_session_maker() as s:
        await sleeve_weights.store(s, fam, tid, day, await sleeve_weights.plane_date(), payload)


async def test_an_absent_family_is_computed_once_and_survives_a_restart(monkeypatch, _clean):
    calls: list = []
    monkeypatch.setattr(routes, "_advise_strategy", counting(calls))
    tid = await _book("A")
    assert await _target(tid) == {"A3": 1.0}
    sleeve_weights.clear_memory()             # a deploy: the process is new
    assert await _target(await _book("A")) == {"A3": 1.0}   # another book, same strategy
    assert calls == [("A", "ME")]
    assert _clean == []                         # nothing was stale


async def test_a_stale_answer_is_served_and_a_refresh_requested(monkeypatch, _clean):
    calls: list = []
    monkeypatch.setattr(routes, "_advise_strategy", counting(calls))
    await _store(sleeve_weights.family("A", "ME"), TODAY - datetime.timedelta(days=1),
                 {"weights": {"OLD3": 1.0}})
    assert await _target(await _book("A")) == {"OLD3": 1.0}
    assert calls == []
    assert len(_clean) == 1


async def test_each_cadence_is_its_own_answer(monkeypatch, _clean):
    calls: list = []
    monkeypatch.setattr(routes, "_advise_strategy", counting(calls))
    tid = await _book("A")
    await _target(tid, "ME")
    await _target(tid, "QE")
    assert calls == [("A", "ME"), ("A", "QE")]


async def test_an_edited_user_strategy_is_never_served_its_old_weights(monkeypatch, _clean):
    calls: list = []
    monkeypatch.setattr(routes, "_advise_strategy", counting(calls))
    tid = await _book("Mine")
    async with async_session_maker() as s:
        row = UserStrategy(tenant_id=tid, name="Mine", kind="rank_and_hold", spec={})
        s.add(row)
        await s.commit()
    await _target(tid)
    async with async_session_maker() as s:
        row = await s.get(UserStrategy, row.id)
        row.spec = {"edited": True}
        row.updated_at = row.updated_at + datetime.timedelta(seconds=1)
        await s.commit()
    await _target(tid)
    assert calls == [("Mine", "ME"), ("Mine", "ME")]
    async with async_session_maker() as s:   # and its rows are the tenant's (LGPD)
        from sqlalchemy import select
        owners = set((await s.scalars(select(sleeve_weights.SleeveWeights.tenant_id))).all())
    assert owners == {tid}


async def test_needs_names_every_strategy_sleeve_at_every_cadence_in_use():
    a, b = uuid.uuid4(), uuid.uuid4()

    class Row:
        def __init__(self, name):
            self.name, self.updated_at = name, datetime.datetime(2026, 1, 1)

    mine = Row("Mine")
    got = tasks_sleeves.needs(
        [(a, "LowVol"), (a, "CASH_CDI"), (b, "LowVol"), (b, "Mine")],
        {b: "QE"}, {b: [mine]})
    assert {(n.ref, n.freq, n.tid) for n in got.values()} == {
        ("LowVol", "ME", None), ("LowVol", "QE", None),
        ("Mine", "ME", b), ("Mine", "QE", b)}
    assert sleeve_weights.family("LowVol", "ME") in got     # one entry for both books


async def test_warm_refreshes_what_is_stale_and_survives_a_failure(monkeypatch):
    a = await _book("Fresh", "Stale", "Broken")
    async with async_session_maker() as s:
        s.add(RebalanceSettings(tenant_id=a, cadence="ME"))
        await s.commit()
    await _store(sleeve_weights.family("Fresh", "ME"), TODAY, {"weights": {"F3": 1.0}})
    await _store(sleeve_weights.family("Stale", "ME"), TODAY - datetime.timedelta(days=2),
                 {"weights": {"OLD3": 1.0}})
    seen: list = []

    def fake(need):
        seen.append(need.ref)
        if need.ref == "Broken":
            raise RuntimeError("needs fundamentals")
        return {"weights": {f"{need.ref}3": 1.0}}

    monkeypatch.setattr(tasks_sleeves, "_advise", fake)
    out = await tasks_sleeves.warm()
    # Other tests' books share this database, so only ours are asserted on.
    assert "Fresh" not in seen and {"Stale", "Broken"} <= set(seen)
    assert "Broken" in out["failed"]
    sleeve_weights.clear_memory()
    async with async_session_maker() as s:
        got = await sleeve_weights.lookup(s, [sleeve_weights.family("Stale", "ME")])
    entry = got[sleeve_weights.family("Stale", "ME")]
    assert entry.day == TODAY and entry.payload == {"weights": {"Stale3": 1.0}}
