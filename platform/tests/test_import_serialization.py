"""One writer per tenant's book, and never one with a stale snapshot.

Each upload defers its own process_imports, and the worker runs two jobs at a
time, so two quick uploads were two jobs over two snapshots running side by
side. The older one finishing last replaced the book without the newer extract
(which still read `done`); two overlapping delete+inserts kept both copies of
every trade, since under READ COMMITTED the second DELETE cannot see the rows
the first one has just inserted.
"""
import asyncio
import subprocess
import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, select

from platform_app import b3run, tasks
from platform_app.config import settings
from platform_app.db import async_session_maker
from platform_app.models import Import, Position, RendaFixaMark, Trade
from tests.conftest import auth

pytestmark = pytest.mark.asyncio(loop_scope="session")

TRADES = (
    "date,side,market,maturity,broker,ticker,qty,price,amount,__source_file,signed_qty\n"
    "2025-03-10,C,Mercado à Vista,,XP,PETR4,100.0,30.5,3050.0,neg.xlsx,100.0\n"
    "2025-03-11,C,Mercado à Vista,,XP,VALE3,10.0,60.0,600.0,neg.xlsx,10.0\n"
)
EVENTS = ("date,ticker,event_type,event_category,is_amortization,qty,price,amount,"
          "broker,__source_file\n")
POSITIONS = "ticker,qty,avg_cost,total_cost,market\nPETR4,100.0,30.5,3050.0,acoes\n"
MARKS = "ticker,date,price\nCDB - X,2025-03-31,1012.5\n"


@pytest.fixture
def fake_load_b3(monkeypatch, tmp_path):
    """load_b3 succeeds and writes a fixed book, RF marks included."""
    monkeypatch.setattr(b3run.settings, "uploads_dir", str(tmp_path))

    def fake(ws: Path, args: list[str]) -> subprocess.CompletedProcess:
        for name, body in (("trades.csv", TRADES), ("cash_events.csv", EVENTS),
                           ("positions.csv", POSITIONS), ("rf_marks.csv", MARKS)):
            (ws / "data" / name).write_text(body)
        return subprocess.CompletedProcess(args, 0, stdout="{}", stderr="")

    monkeypatch.setattr(b3run, "run_module", fake)


async def _upload(tid: uuid.UUID, tmp_path: Path, name: str) -> uuid.UUID:
    src = tmp_path / f"{uuid.uuid4()}.xlsx"
    src.write_text("bytes")
    async with async_session_maker() as s:
        imp = Import(tenant_id=tid, filename=name, stored_path=str(src), kind="negociacao")
        s.add(imp)
        await s.commit()
    return imp.id


async def _count(model, tid) -> int:
    async with async_session_maker() as s:
        return await s.scalar(
            select(func.count()).select_from(model).where(model.tenant_id == tid))


async def test_a_job_whose_snapshot_went_stale_stands_down(fake_load_b3, tmp_path, monkeypatch):
    tid = uuid.uuid4()
    first = await _upload(tid, tmp_path, "negociacao-1.xlsx")

    real_lock = tasks.xact_lock

    async def upload_lands_mid_run(session, *key):
        # The second upload commits while this job's load_b3 is running — after
        # its snapshot, before its write.
        await _upload(tid, tmp_path, "negociacao-2.xlsx")
        await real_lock(session, *key)

    monkeypatch.setattr(tasks, "xact_lock", upload_lands_mid_run)
    out = await tasks.process_imports.func(str(tid))

    assert out == {"tenant_id": str(tid), "status": "superseded"}
    # Nothing written: the newer upload's own job covers both files.
    assert await _count(Trade, tid) == 0
    async with async_session_maker() as s:
        assert (await s.get(Import, first)).status == "processing"


async def test_overlapping_jobs_leave_one_copy_of_the_book(fake_load_b3, tmp_path):
    tid = uuid.uuid4()
    await _upload(tid, tmp_path, "negociacao-1.xlsx")

    outs = await asyncio.gather(
        tasks.process_imports.func(str(tid)), tasks.process_imports.func(str(tid)))

    assert [o["status"] for o in outs] == ["done", "done"]
    assert await _count(Trade, tid) == 2
    assert await _count(Position, tid) == 1
    # Unserialised, the second writer also tripped uq_rf_marks_tenant_ticker_date.
    assert await _count(RendaFixaMark, tid) == 1


async def test_uploads_while_a_job_waits_share_it(client, tmp_path, monkeypatch):
    """Every POST /imports deferred its own job, each a full rebuild over every
    extract, so a tenant could fill the two worker slots other tenants share.
    The waiting job reads all stored extracts when it starts: one is enough."""
    monkeypatch.setattr(settings, "uploads_dir", str(tmp_path))
    headers = await auth(client, "imports-queueing-lock@example.com")
    for i in range(3):
        r = await client.post("/imports", headers=headers, files=[(
            "files", (f"negociacao-{uuid.uuid4()}.xlsx", b"x",
                      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))])
        assert r.status_code == 200, r.text
    async with async_session_maker() as s:
        tid = await s.scalar(select(Import.tenant_id).where(Import.id == uuid.UUID(r.json()[0]["id"])))
    assert await _count(Import, tid) == 3
    async with tasks.app.open_async():
        jobs = await tasks.app.connector.execute_query_all_async(
            "SELECT lock FROM procrastinate_jobs WHERE task_name = 'process_imports'"
            " AND status = 'todo' AND args->>'tenant_id' = %(t)s", t=str(tid))
    assert jobs == [{"lock": f"imports:{tid}"}]
