"""Deleting backtest runs: one row, or a cohort of old ones.

The bench only ever grew — every submission, including the ones that died in the
engine seconds later, stayed on the list forever with a metrics blob on the row
and a directory of artifacts behind it. These are the two verbs that clear it,
and what each of them must refuse:

  · a run another tenant owns is a 404, not a 403 — deleting must not be a way to
    learn which ids exist;
  · a run the worker still holds (`pending`/`running`) is a 409 — the engine is
    writing into that artifacts directory right now, and the quota counts exactly
    those rows, so deleting one would also be a way past admission control;
  · a bare `DELETE /backtests` with no criteria is a 422, because "clear the
    bench" and "delete every backtest I have ever run" are different requests
    that look identical at the URL.

The artifacts assertions are the point of the file as much as the row counts: a
sweep that leaves curves.csv on disk has not reclaimed anything, and a failed run
has a directory the engine created *before* it failed — with no `artifacts_path`
on the row to find it by.
"""

import datetime
import uuid
from functools import partial
from pathlib import Path

import pytest

from platform_app.billing import SELLABLE as ALL_MODULES
from tests.conftest import auth

pytestmark = pytest.mark.asyncio(loop_scope="session")

_auth = partial(auth, modules=ALL_MODULES)


async def _me(client, headers) -> uuid.UUID:
    return uuid.UUID((await client.get("/me", headers=headers)).json()["id"])


async def _seed(
    tenant_id: uuid.UUID,
    status: str = "done",
    *,
    age_days: int = 0,
    strategy: str = "MomSharpe",
) -> uuid.UUID:
    """One run row, aged by rewriting created_at (server_default=now() otherwise)."""
    from platform_app.db import async_session_maker
    from platform_app.models import BacktestRun

    async with async_session_maker() as session:
        run = BacktestRun(
            tenant_id=tenant_id, strategy=strategy, status=status, params={})
        session.add(run)
        await session.commit()
        run.created_at = datetime.datetime.now(datetime.UTC) - datetime.timedelta(
            days=age_days)
        await session.commit()
        return run.id


async def _ids(client, headers) -> list[str]:
    return [r["id"] for r in (await client.get("/backtests", headers=headers)).json()]


def _artifacts(run_id) -> Path:
    from platform_app.routes_backtest_retention import artifacts_dir

    return artifacts_dir(run_id)


def _write_artifacts(run_id) -> Path:
    """What the engine leaves behind. Written by hand rather than by running a
    backtest: this suite is about the rows and the files, not the engine."""
    d = _artifacts(run_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "curves.csv").write_text("date,pretax\n2026-01-02,1.0\n")
    return d


async def test_deleting_a_run_takes_its_artifacts_with_it(client):
    headers = await _auth(client, "retention-one@example.com")
    tid = await _me(client, headers)
    rid = await _seed(tid)
    d = _write_artifacts(rid)

    r = await client.delete(f"/backtests/{rid}", headers=headers)
    assert r.status_code == 204, r.text
    assert str(rid) not in await _ids(client, headers)
    assert not d.exists()
    # Gone for good, not merely hidden from the listing.
    assert (await client.get(f"/backtests/{rid}", headers=headers)).status_code == 404


async def test_a_failed_runs_artifacts_are_found_by_id_not_by_the_column(client):
    """`artifacts_path` is only written on the success path, but the engine
    creates the directory and writes curves.csv before anything can fail."""
    headers = await _auth(client, "retention-failed@example.com")
    tid = await _me(client, headers)
    rid = await _seed(tid, "error")
    d = _write_artifacts(rid)

    assert (await client.delete(f"/backtests/{rid}", headers=headers)).status_code == 204
    assert not d.exists()


async def test_deleting_a_run_with_no_artifacts_on_disk_still_succeeds(client):
    headers = await _auth(client, "retention-nodir@example.com")
    tid = await _me(client, headers)
    rid = await _seed(tid, "error")

    assert not _artifacts(rid).exists()
    assert (await client.delete(f"/backtests/{rid}", headers=headers)).status_code == 204


async def test_a_run_in_flight_is_refused(client):
    """The worker holds that row across a subprocess that runs for minutes, and
    the quota counts exactly these — so this 409 is also admission control."""
    headers = await _auth(client, "retention-inflight@example.com")
    tid = await _me(client, headers)
    for status in ("pending", "running"):
        rid = await _seed(tid, status)
        r = await client.delete(f"/backtests/{rid}", headers=headers)
        assert r.status_code == 409, r.text
        assert status in r.json()["detail"]
        assert str(rid) in await _ids(client, headers)


async def test_another_tenants_run_is_a_404(client):
    headers = await _auth(client, "retention-stranger@example.com")
    stranger = await _seed(uuid.uuid4())

    assert (await client.delete(
        f"/backtests/{stranger}", headers=headers)).status_code == 404
    # Still there — a 404 that deleted the row would be worse than a 403.
    from platform_app.db import async_session_maker
    from platform_app.models import BacktestRun

    async with async_session_maker() as session:
        assert await session.get(BacktestRun, stranger) is not None


async def test_a_junk_id_is_a_404_not_a_500(client):
    headers = await _auth(client, "retention-junk@example.com")
    assert (await client.delete("/backtests/not-a-uuid", headers=headers)).status_code == 404


async def test_sweeping_by_age_keeps_the_recent_runs(client):
    headers = await _auth(client, "retention-age@example.com")
    tid = await _me(client, headers)
    old = [await _seed(tid, age_days=n) for n in (60, 45, 31)]
    recent = [await _seed(tid, age_days=n) for n in (5, 0)]
    dirs = [_write_artifacts(r) for r in old]

    cutoff = datetime.date.today() - datetime.timedelta(days=30)
    r = await client.delete(f"/backtests?before={cutoff.isoformat()}", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json() == {"deleted": len(old)}

    left = await _ids(client, headers)
    assert sorted(left) == sorted(str(x) for x in recent)
    assert not any(d.exists() for d in dirs)


async def test_sweeping_by_status_clears_an_afternoon_of_failures(client):
    """The listing in the screenshot: eight `Erro` rows and two that finished."""
    headers = await _auth(client, "retention-status@example.com")
    tid = await _me(client, headers)
    errors = [await _seed(tid, "error") for _ in range(8)]
    kept = [await _seed(tid, "done") for _ in range(2)]
    inflight = await _seed(tid, "running")

    r = await client.delete("/backtests?status=error", headers=headers)
    assert r.json() == {"deleted": len(errors)}
    assert sorted(await _ids(client, headers)) == sorted(
        str(x) for x in [*kept, inflight])


async def test_keep_protects_the_newest_runs_whatever_their_status(client):
    """`keep` is counted over the whole book, not over the matched set — so
    "keep 3" means what it says on a bench where the last three all failed."""
    headers = await _auth(client, "retention-keep@example.com")
    tid = await _me(client, headers)
    # Oldest first, so the last three seeded are the three newest.
    old = [await _seed(tid, "error", age_days=n) for n in (40, 39, 38, 37)]
    newest = [await _seed(tid, "error", age_days=n) for n in (2, 1, 0)]

    r = await client.delete("/backtests?status=error&keep=3", headers=headers)
    assert r.json() == {"deleted": len(old)}
    assert sorted(await _ids(client, headers)) == sorted(str(x) for x in newest)


async def test_keep_alone_trims_to_the_last_n(client):
    headers = await _auth(client, "retention-trim@example.com")
    tid = await _me(client, headers)
    for n in range(6, 0, -1):
        await _seed(tid, age_days=n)

    assert (await client.delete("/backtests?keep=2", headers=headers)).json() == {
        "deleted": 4}
    assert len(await _ids(client, headers)) == 2


async def test_a_sweep_never_takes_a_run_in_flight(client):
    headers = await _auth(client, "retention-sweep-inflight@example.com")
    tid = await _me(client, headers)
    inflight = [await _seed(tid, s, age_days=90) for s in ("pending", "running")]
    done = await _seed(tid, "done", age_days=90)

    cutoff = datetime.date.today() - datetime.timedelta(days=1)
    r = await client.delete(f"/backtests?before={cutoff.isoformat()}", headers=headers)
    assert r.json() == {"deleted": 1}
    assert sorted(await _ids(client, headers)) == sorted(str(x) for x in inflight)
    assert str(done) not in await _ids(client, headers)


async def test_a_sweep_stops_at_the_books_edge(client):
    """Another tenant's runs are not swept by yours, however old they are."""
    headers = await _auth(client, "retention-mine@example.com")
    tid = await _me(client, headers)
    mine = await _seed(tid, age_days=90)
    theirs = await _seed(uuid.uuid4(), age_days=90)

    cutoff = datetime.date.today() - datetime.timedelta(days=1)
    r = await client.delete(f"/backtests?before={cutoff.isoformat()}", headers=headers)
    assert r.json() == {"deleted": 1}

    from platform_app.db import async_session_maker
    from platform_app.models import BacktestRun

    async with async_session_maker() as session:
        assert await session.get(BacktestRun, theirs) is not None
        assert await session.get(BacktestRun, mine) is None


async def test_a_sweep_with_no_criteria_is_refused(client):
    headers = await _auth(client, "retention-nocriteria@example.com")
    tid = await _me(client, headers)
    kept = await _seed(tid)

    r = await client.delete("/backtests", headers=headers)
    assert r.status_code == 422, r.text
    assert str(kept) in await _ids(client, headers)


async def test_an_unknown_or_inflight_status_is_refused_by_name(client):
    """`{"deleted": 0}` cannot tell a caller their filter will never match."""
    headers = await _auth(client, "retention-badstatus@example.com")

    r = await client.delete("/backtests?status=errors", headers=headers)
    assert r.status_code == 422, r.text
    assert "errors" in r.json()["detail"]

    r = await client.delete("/backtests?status=running", headers=headers)
    assert r.status_code == 422, r.text
    assert "running" in r.json()["detail"]


async def test_keep_is_bounded(client):
    headers = await _auth(client, "retention-keepbound@example.com")
    from platform_app.routes_backtest_retention import MAX_KEEP

    r = await client.delete(f"/backtests?keep={MAX_KEEP + 1}", headers=headers)
    assert r.status_code == 422, r.text


async def test_a_sweep_that_matches_nothing_is_a_zero(client):
    headers = await _auth(client, "retention-empty@example.com")
    tid = await _me(client, headers)
    recent = await _seed(tid)

    cutoff = datetime.date.today() - datetime.timedelta(days=365)
    r = await client.delete(f"/backtests?before={cutoff.isoformat()}", headers=headers)
    assert r.json() == {"deleted": 0}
    assert str(recent) in await _ids(client, headers)


async def test_deleting_runs_needs_the_bench_module(client):
    """Same gate as every other /backtests route — a route that skipped it would
    let an unentitled account reach the tenant's rows."""
    headers = await auth(client, "retention-gate@example.com", ["carteira"])

    assert (await client.delete("/backtests?keep=1", headers=headers)).status_code == 402
    assert (await client.delete(
        f"/backtests/{uuid.uuid4()}", headers=headers)).status_code == 402


async def test_every_run_route_gives_the_same_404(client):
    """Read, curves and delete answer junk, unknown and foreign ids identically,
    so no route can be used to tell which ids exist elsewhere."""
    headers = await _auth(client, "retention-same404@example.com")
    stranger = await _seed(uuid.uuid4())
    for rid in ("not-a-uuid", str(uuid.uuid4()), str(stranger)):
        for method, path in (("GET", f"/backtests/{rid}"),
                             ("GET", f"/backtests/{rid}/curves"),
                             ("DELETE", f"/backtests/{rid}")):
            r = await client.request(method, path, headers=headers)
            assert (r.status_code, r.json()) == (404, {"detail": "backtest not found"}), (
                method, path)


async def test_get_owned_scopes_by_tenant():
    from fastapi import HTTPException

    from platform_app.db import async_session_maker
    from platform_app.models import BacktestRun
    from platform_app.tenancy import get_owned

    owner = uuid.uuid4()
    rid = await _seed(owner)
    async with async_session_maker() as session:
        row = await get_owned(session, BacktestRun, str(rid), owner, "nope")
        assert row.id == rid
        for raw, tid in ((str(rid), uuid.uuid4()), ("junk", owner),
                         (str(uuid.uuid4()), owner)):
            with pytest.raises(HTTPException) as exc:
                await get_owned(session, BacktestRun, raw, tid, "nope")
            assert (exc.value.status_code, exc.value.detail) == (404, "nope")
