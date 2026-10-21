"""A backtest always finishes: whatever goes wrong ends in `error`, never in a row
stuck `running`.

A stuck row is worse than a failed one. Nothing ever finishes it, the retention
routes refuse to delete an in-flight run, and BACKTEST_QUOTA counts it forever —
three of them and the whole account (every client book of an empresarial firm)
gets a permanent 429. The task used to handle exactly two failures, a non-zero
exit and unparseable stdout; an exception anywhere else, a NaN in the metrics
that JSONB refuses at commit, or a params dict too big for argv (E2BIG at exec)
all left the row where it was.
"""
import asyncio
import datetime
import json
import subprocess
import uuid

import pytest

from tests.conftest import auth

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _seed_run(tenant_id=None, **cols) -> uuid.UUID:
    from platform_app.db import async_session_maker
    from platform_app.models import BacktestRun

    run = BacktestRun(tenant_id=tenant_id or uuid.uuid4(), strategy="MomSharpe",
                      status=cols.pop("status", "pending"), params=cols.pop("params", {}),
                      **cols)
    async with async_session_maker() as session:
        session.add(run)
        await session.commit()
    return run.id


async def _row(rid):
    from platform_app.db import async_session_maker
    from platform_app.models import BacktestRun

    async with async_session_maker() as session:
        return await session.get(BacktestRun, rid)


def _fake_engine(seen: dict, *, rc=0, stdout='{"cagr": 0.1}', raises=None):
    def fake(script, args, extra_env=None, stdin=None):
        seen.update(script=script, args=args, stdin=stdin)
        if raises:
            raise raises
        return subprocess.CompletedProcess(args, rc, stdout=stdout, stderr="")
    return fake


async def test_an_exception_mid_run_ends_in_error_not_running(monkeypatch):
    from platform_app import enginerun
    from platform_app.tasks import run_backtest

    rid = await _seed_run()
    monkeypatch.setattr(enginerun, "run_script", _fake_engine({}, raises=RuntimeError("x")))
    result = await run_backtest(run_id=str(rid))

    row = await _row(rid)
    assert (result["status"], row.status) == ("error", "error")
    assert row.finished_at is not None
    # An arbitrary exception's text is SQL and parameters: logged under a ref,
    # and only the ref is stored for the tenant to read.
    assert row.error.startswith("backtest falhou (ref ")


async def test_non_finite_metrics_are_stored_as_null(monkeypatch):
    """Postgres JSONB rejects NaN, so storing the engine's summary verbatim made
    the final commit the thing that failed — after the run had succeeded."""
    from platform_app import enginerun
    from platform_app.tasks import run_backtest

    rid = await _seed_run()
    monkeypatch.setattr(enginerun, "run_script", _fake_engine(
        {}, stdout='progress\n{"sharpe": NaN, "cagr": 0.1, "dd": [-Infinity, 0.5]}\n'))
    assert (await run_backtest(run_id=str(rid)))["status"] == "done"
    row = await _row(rid)
    assert row.status == "done"
    assert row.metrics == {"sharpe": None, "cagr": 0.1, "dd": [None, 0.5]}


async def test_params_reach_the_engine_on_stdin(monkeypatch):
    from platform_app import enginerun
    from platform_app.tasks import run_backtest

    params = {"params": {"note": "x" * 200_000}, "freq": "ME"}
    rid = await _seed_run(params=params)
    seen: dict = {}
    monkeypatch.setattr(enginerun, "run_script", _fake_engine(seen))
    await run_backtest(run_id=str(rid))
    assert seen["script"] == "run_backtest_json.py"
    assert seen["args"][-1] == "-"
    assert json.loads(seen["stdin"]) == params


async def test_an_exec_the_kernel_refuses_is_a_return_code_not_an_exception(tmp_path):
    """One argv element over MAX_ARG_STRLEN (128 KB) fails the exec with E2BIG.
    That used to escape subprocs.run as OSError, past every caller's
    returncode branch."""
    from platform_app import subprocs

    proc = subprocs.run(["true", "x" * 200_000], cwd=tmp_path, env={"PATH": "/usr/bin:/bin"},
                        timeout_s=10, label="probe")
    assert proc.returncode == subprocs.EXEC_FAILED_EXIT
    assert proc.stderr.startswith("probe could not start")


async def test_a_day_old_inflight_row_stops_counting_against_the_quota():
    """Belt to the task's braces: a row a dead worker left behind must not lock
    the account out of the bench for good."""
    from platform_app.db import async_session_maker
    from platform_app.models import User
    from platform_app.routes import inflight_backtests

    uid = uuid.uuid4()
    async with async_session_maker() as session:
        session.add(User(id=uid, email=f"stale-{uid}@example.com", hashed_password="x",
                         is_active=True, is_verified=True, modules=["bench"],
                         plan_status="active"))
        await session.commit()
    old = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=2)
    await _seed_run(uid, status="running", created_at=old)
    await _seed_run(uid, status="pending", created_at=old)
    await _seed_run(uid, status="running")
    async with async_session_maker() as session:
        assert await inflight_backtests(session, await session.get(User, uid)) == 1


@pytest.mark.parametrize("raw", [
    '{"strategy": "MomSharpe", "params": {"rebalance_freq": "s"}}',
    '{"strategy": "MomSharpe", "params": {"initial_capital": NaN}}',
])
async def test_params_cannot_reach_around_the_validated_fields(client, raw):
    """With `freq` omitted the engine reads params["rebalance_freq"], the same
    .resample() the freq enum exists to guard; and NaN is a JSONB write error."""
    headers = await auth(client, "bt-params@example.com", ["bench"])
    r = await client.post("/backtests", content=raw,
                          headers={**headers, "content-type": "application/json"})
    assert r.status_code == 422, r.text


async def test_parallel_submissions_cannot_all_slip_under_the_quota(client, monkeypatch):
    """Count, gallery, insert used to run unlocked, so N parallel POSTs all saw
    `running < limit` and all queued."""
    from platform_app import routes, tasks
    from platform_app.routes import BACKTEST_QUOTA

    async def slow_gallery(session, tid):
        await asyncio.sleep(0.05)
        return [{"name": "MomSharpe"}]

    class _NoDefer:
        async def defer_async(self, **kw):
            return None

    monkeypatch.setattr(routes, "_gallery", slow_gallery)
    monkeypatch.setattr(tasks, "run_backtest", _NoDefer())
    headers = await auth(client, f"bt-race-{uuid.uuid4()}@example.com", ["bench"])
    codes = [r.status_code for r in await asyncio.gather(*(
        client.post("/backtests", headers=headers, json={"strategy": "MomSharpe"})
        for _ in range(BACKTEST_QUOTA["bench"] + 3)))]
    assert sorted(codes) == [200] * BACKTEST_QUOTA["bench"] + [429] * 3


async def test_curves_with_empty_or_infinite_cells_are_nulls_not_a_500(client, tmp_path):
    """pandas writes NaN as "" (a drawdown over a zero start is 0/0), and inf
    is not JSON; float("") raised and the whole chart 500'd."""
    headers = await auth(client, "bt-curves@example.com", ["bench"])
    me = uuid.UUID((await client.get("/me", headers=headers)).json()["id"])
    (tmp_path / "curves.csv").write_text(
        "date,aftertax,drawdown\n2024-01-02,100.0,\n2024-01-03,inf,-0.5\n")
    rid = await _seed_run(me, status="done", artifacts_path=str(tmp_path),
                          error="Traceback\n  File \"/opt/engine/scripts/x.py\", line 1")
    r = await client.get(f"/backtests/{rid}/curves", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["series"] == {"date": ["2024-01-02", "2024-01-03"],
                                  "aftertax": [100.0, None], "drawdown": [None, -0.5]}
    # And the stored stderr goes back without the deploy's directory layout.
    run = (await client.get(f"/backtests/{rid}", headers=headers)).json()
    assert run["error"] == 'Traceback\n  File "x.py", line 1'
