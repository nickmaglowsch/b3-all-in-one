"""The job tables' state machines: who may move a row, and from where.

backtest_runs and imports each move pending → running/processing → done|error,
but nothing used to check where a row was before writing where it goes. So a
second job for one run started it again; a run whose row a purge deleted while
the engine ran wrote its NAV curve back to disk after the LGPD erasure had swept
it; maintenance put a backup over a row a worker was still finishing; and a
defer that failed after the commit left a row `pending` with no job to move it.
Each of these runs the real task against the real test database.
"""
import subprocess
import uuid
from pathlib import Path

import psycopg
import pytest
from sqlalchemy import insert
from sqlalchemy.exc import IntegrityError

from platform_app import enginerun, tasks
from platform_app import maintenance_backtests as mb
from platform_app.config import settings
from platform_app.db import async_session_maker
from platform_app.models import BacktestRun, Import, IngestRun
from tests.conftest import PG_NETLOC, TEST_DB, auth

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest.fixture
def artifacts(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "artifacts_dir", str(tmp_path / "artifacts"))
    return tmp_path / "artifacts"


async def _seed_run(status="pending", **cols) -> uuid.UUID:
    run = BacktestRun(tenant_id=uuid.uuid4(), strategy="MomSharpe", status=status,
                      params={}, **cols)
    async with async_session_maker() as session:
        session.add(run)
        await session.commit()
    return run.id


async def _row(model, rid):
    async with async_session_maker() as session:
        return await session.get(model, rid)


def _engine(on_run=None):
    """The engine writes curves.csv first, like the real one, then succeeds.
    ``on_run(out_dir)`` runs in its thread, before it returns."""
    calls = []

    def fake(script, args, extra_env=None, stdin=None):
        out = Path(args[1])
        out.mkdir(parents=True, exist_ok=True)
        (out / "curves.csv").write_text("nav")
        calls.append(out)
        if on_run:
            on_run(out)
        return subprocess.CompletedProcess(args, 0, stdout='{"cagr": 0.1}', stderr="")
    return fake, calls


# ── run_backtest ──────────────────────────────────────────────────────────────

async def test_a_run_that_is_not_pending_is_not_started_again(artifacts, monkeypatch):
    """A second job for the same run — or one for a row already failed as lost
    — stands down without touching the row or the engine."""
    fake, calls = _engine()
    monkeypatch.setattr(enginerun, "run_script", fake)
    for status in ("running", "done", "error"):
        rid = await _seed_run(status, error="kept")
        assert await tasks.run_backtest(run_id=str(rid)) == {
            "run_id": str(rid), "status": "skipped"}
        row = await _row(BacktestRun, rid)
        assert (row.status, row.error, row.finished_at) == (status, "kept", None)
    assert calls == []


async def test_a_run_purged_mid_flight_leaves_no_artifacts(artifacts, monkeypatch):
    """The book is offboarded (or the account erased) while the engine runs:
    purge_tenant_files has already swept ARTIFACTS_DIR/{id}, and the engine then
    writes it again. The final write finds no `running` row and removes it."""
    rid = await _seed_run()

    def purge(_out):
        # The engine runs in a worker thread; the purge is another connection.
        with psycopg.connect(f"postgresql://{PG_NETLOC}/{TEST_DB}", autocommit=True) as c:
            c.execute("DELETE FROM backtest_runs WHERE id = %s", (rid,))

    fake, calls = _engine(on_run=purge)
    monkeypatch.setattr(enginerun, "run_script", fake)

    out = await tasks.run_backtest(run_id=str(rid))

    assert out == {"run_id": str(rid), "status": "abandoned"}
    assert calls and not calls[0].exists()
    assert await _row(BacktestRun, rid) is None


async def test_a_finished_run_is_stamped_on_the_database_clock(artifacts, monkeypatch):
    fake, _ = _engine()
    monkeypatch.setattr(enginerun, "run_script", fake)
    rid = await _seed_run()
    assert (await tasks.run_backtest(run_id=str(rid)))["status"] == "done"
    row = await _row(BacktestRun, rid)
    assert row.status == "done" and row.finished_at >= row.created_at
    assert (artifacts / str(rid) / "curves.csv").exists()


# ── a defer that raises after the commit ──────────────────────────────────────

class _Broken:
    async def defer_async(self, **kw):
        raise RuntimeError("queue unreachable")


async def test_a_backtest_whose_defer_fails_ends_in_error(client, monkeypatch):
    from platform_app import routes

    async def gallery(session, tid):
        return [{"name": "MomSharpe"}]

    monkeypatch.setattr(routes, "_gallery", gallery)
    monkeypatch.setattr(tasks, "run_backtest", _Broken())
    headers = await auth(client, f"bt-defer-{uuid.uuid4()}@example.com", ["bench"])

    r = await client.post("/backtests", headers=headers, json={"strategy": "MomSharpe"})

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "error" and body["error"].startswith("backtest falhou (ref ")
    assert body["finished_at"]
    assert (await _row(BacktestRun, uuid.UUID(body["id"]))).status == "error"


async def test_an_upload_whose_defer_fails_ends_in_error(client, tmp_path, monkeypatch):
    async def broken(tid):
        raise RuntimeError("queue unreachable")

    monkeypatch.setattr(settings, "uploads_dir", str(tmp_path))
    monkeypatch.setattr(tasks, "defer_process_imports", broken)
    headers = await auth(client, f"imp-defer-{uuid.uuid4()}@example.com")

    r = await client.post("/imports", headers=headers, files=[(
        "files", ("negociacao.xlsx", b"x",
                  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))])

    assert r.status_code == 200, r.text
    assert [i["status"] for i in r.json()] == ["error"]
    listed = (await client.get("/imports", headers=headers)).json()
    assert listed[0]["status"] == "error"
    assert listed[0]["error"].startswith("importação falhou (ref ")


# ── maintenance's restore ─────────────────────────────────────────────────────

async def test_restore_never_overwrites_a_running_row(artifacts):
    """rerun-backtests gave up on a run (run_timeout) that a worker is still
    finishing. Putting the backup over it would make that worker's final write
    miss — and delete the artifacts just put back. It is left alone, backup
    included, for the next session to settle."""
    rid = await _seed_run("done", metrics={"old": 1})
    d = mb.artifacts_dir(rid)
    d.mkdir(parents=True)
    (d / "curves.csv").write_text("old curves")
    assert await mb._backup(rid)
    async with async_session_maker() as session:
        (await session.get(BacktestRun, rid)).status = "running"
        await session.commit()
    (d / "curves.csv").write_text("being rewritten")

    assert await mb._restore(rid) is False

    assert (await _row(BacktestRun, rid)).status == "running"
    assert (d / "curves.csv").read_text() == "being rewritten"
    assert (mb._backup_dir(rid) / "row.json").exists()

    # Once the worker has finished (here: failed), the restore goes through.
    async with async_session_maker() as session:
        (await session.get(BacktestRun, rid)).status = "error"
        await session.commit()
    assert await mb._restore(rid) is True
    row = await _row(BacktestRun, rid)
    assert (row.status, row.metrics) == ("done", {"old": 1})
    assert (d / "curves.csv").read_text() == "old curves"


# ── the schema ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("model, cols", [
    (BacktestRun, {"tenant_id": uuid.uuid4(), "strategy": "x", "params": {}}),
    (Import, {"tenant_id": uuid.uuid4(), "filename": "x", "stored_path": "x", "kind": "x"}),
    (IngestRun, {"kind": "prices"}),
])
async def test_a_status_outside_the_machine_is_refused(model, cols):
    async with async_session_maker() as session:
        with pytest.raises(IntegrityError, match="_status"):
            await session.execute(insert(model).values(status="finished", **cols))
