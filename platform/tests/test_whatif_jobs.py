"""POST /whatif is a queued job: validated and queued at once, replayed by the
worker (tasks_whatif), polled at GET /whatif/{id}.

The replay itself (the engine) is faked: what is pinned here is the row's life —
the 202, the job, the result or error the poll returns, the account's in-flight
cap, whose runs a book may read, and the boot sweep of runs a dead worker left.
"""
from __future__ import annotations

import datetime
import uuid

import pytest
from sqlalchemy import delete, select

from platform_app import routes_whatif, tasks, tasks_whatif, worker
from platform_app.db import async_session_maker
from platform_app.models import Trade
from platform_app.whatif_models import WhatIfRun
from tests.conftest import auth

pytestmark = pytest.mark.asyncio(loop_scope="session")
BUILTIN = {"name": "Fake", "needs_fundamentals": False}


@pytest.fixture(autouse=True)
def _engine(monkeypatch):
    async def gallery(_session, _tid):
        return [BUILTIN]

    monkeypatch.setattr(routes_whatif, "_gallery", gallery)

    def fake_run(sid, config, tid, extra_env=None):
        assert config["flows"] and config["freq"] == "ME"
        return {"summary": {"final_aftertax_nav": 123.45,
                            "aftertax": {"Ann. Return (%)": 5.0, "Sharpe": 0.5,
                                         "Max Drawdown (%)": -10.0}},
                "curve": [{"date": "2024-01-31", "aftertax": 123.45}]}

    monkeypatch.setattr(routes_whatif, "_run_whatif", fake_run)

    async def no_prices(_tickers):
        return []

    monkeypatch.setattr(routes_whatif, "_latest_engine_prices", no_prices)


async def _book(client, email: str) -> tuple[dict, uuid.UUID]:
    h = await auth(client, email, modules=["mercado"])
    tid = uuid.UUID((await client.get("/me", headers=h)).json()["tenant_id"])
    async with async_session_maker() as s:
        s.add(Trade(tenant_id=tid, date=datetime.date(2024, 1, 2), side="Compra",
                    market="Mercado à Vista", ticker="PETR4", qty=10, price=30.0,
                    amount=300.0, signed_qty=10, source_file="seed"))
        await s.commit()
    return h, tid


async def _post(client, h):
    return await client.post("/whatif", json={"strategy_ids": ["Fake"]}, headers=h)


async def test_post_queues_and_the_poll_returns_the_result(client):
    h, tid = await _book(client, f"wi-ok-{uuid.uuid4()}@example.com")
    r = await _post(client, h)
    assert r.status_code == 202, r.text
    run = r.json()
    assert run["status"] == "pending" and run["result"] is None
    assert run["strategy_ids"] == ["Fake"]
    async with tasks.app.open_async():     # the job is really in the queue
        jobs = await tasks.app.connector.execute_query_all_async(
            "SELECT 1 FROM procrastinate_jobs WHERE task_name = 'run_whatif'"
            " AND args->>'run_id' = %(id)s", id=run["id"])
    assert jobs

    assert (await tasks_whatif.run_whatif.func(run_id=run["id"]))["status"] == "done"
    polled = (await client.get(f"/whatif/{run['id']}", headers=h)).json()
    assert polled["status"] == "done" and polled["finished_at"]
    result = polled["result"]
    assert result["total_invested"] == 300.0
    assert result["scenarios"][0]["strategy"] == "Fake"
    assert result["scenarios"][0]["final_value"] == 123.45
    # A second job for the same run stands down rather than replaying it again.
    assert (await tasks_whatif.run_whatif.func(run_id=run["id"]))["status"] == "skipped"


async def test_an_engine_failure_ends_in_error_with_a_reference(client, monkeypatch):
    h, _ = await _book(client, f"wi-err-{uuid.uuid4()}@example.com")

    def boom(*_a, **_k):
        raise RuntimeError("Traceback ... /opt/engine/secret/path.py")

    monkeypatch.setattr(routes_whatif, "_run_whatif", boom)
    run = (await _post(client, h)).json()
    await tasks_whatif.run_whatif.func(run_id=run["id"])
    polled = (await client.get(f"/whatif/{run['id']}", headers=h)).json()
    assert polled["status"] == "error"
    assert "whatif (Fake) falhou (ref" in polled["error"]
    assert "/opt/engine" not in polled["error"]


async def test_validation_still_answers_synchronously(client):
    h = await auth(client, f"wi-empty-{uuid.uuid4()}@example.com", modules=["mercado"])
    r = await _post(client, h)                      # no trades at all
    assert r.status_code == 422
    r = await client.post("/whatif", json={"strategy_ids": ["Nope"]}, headers=h)
    assert r.status_code == 404


async def test_an_account_may_have_two_in_flight(client):
    h, _ = await _book(client, f"wi-quota-{uuid.uuid4()}@example.com")
    assert [(await _post(client, h)).status_code for _ in range(2)] == [202, 202]
    r = await _post(client, h)
    assert r.status_code == 429
    assert r.json()["detail"]["error"] == "whatif_quota"


async def test_another_book_cannot_read_a_run(client):
    h, _ = await _book(client, f"wi-own-{uuid.uuid4()}@example.com")
    run = (await _post(client, h)).json()
    other = await auth(client, f"wi-other-{uuid.uuid4()}@example.com", modules=["mercado"])
    assert (await client.get(f"/whatif/{run['id']}", headers=other)).status_code == 404
    assert (await client.get("/whatif/not-a-uuid", headers=h)).status_code == 404


async def test_only_the_newest_finished_runs_are_kept(client, monkeypatch):
    monkeypatch.setattr(routes_whatif, "KEEP_RUNS", 2)
    h, tid = await _book(client, f"wi-keep-{uuid.uuid4()}@example.com")
    for _ in range(4):
        run = (await _post(client, h)).json()
        await tasks_whatif.run_whatif.func(run_id=run["id"])
    async with async_session_maker() as s:
        n = len((await s.scalars(select(WhatIfRun).where(WhatIfRun.tenant_id == tid))).all())
    assert n <= 3   # the two kept, plus the one queued after the last trim


async def test_the_default_worker_closes_whatifs_a_dead_worker_left(monkeypatch):
    tid = uuid.uuid4()
    an_hour_ago = datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=1)
    async with async_session_maker() as s:
        await s.execute(delete(WhatIfRun).where(WhatIfRun.status.in_(("pending", "running"))))
        rows = {"running": WhatIfRun(tenant_id=tid, status="running", params={}),
                "lost": WhatIfRun(tenant_id=tid, status="pending", params={},
                                  created_at=an_hour_ago),
                "fresh": WhatIfRun(tenant_id=tid, status="pending", params={})}
        s.add_all(rows.values())
        await s.commit()
        ids = {k: r.id for k, r in rows.items()}

    async def no_jobs(**_kwargs):
        return None

    monkeypatch.setattr(worker.app, "run_worker_async", no_jobs)
    monkeypatch.delenv("WORKER_QUEUES", raising=False)
    await worker._run()
    async with async_session_maker() as s:
        got = {k: await s.get(WhatIfRun, v) for k, v in ids.items()}
        for r in got.values():
            await s.refresh(r)
    assert got["running"].status == "error" and "Interrompida" in got["running"].error
    assert got["lost"].status == "error" and "Perdida" in got["lost"].error
    assert got["fresh"].status == "pending"      # inside the grace period
