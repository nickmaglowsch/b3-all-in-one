"""The scheduled market-data refresh.

These lock down the wiring that decides whether the automation actually runs and
whether a failure is noticeable — the two things whose absence left this database
seven months stale with nothing reporting it.
"""
import datetime

import pytest
from sqlalchemy import select

from platform_app import enginerun, tasks
from platform_app.config import settings
from platform_app.db import async_session_maker
from platform_app.models import IngestRun


def test_periodic_tasks_are_registered_on_the_ingest_queue():
    """A task is only deferred by a worker listening on its queue, and only ever
    fires at all if the periodic registration took."""
    registry = tasks.app.periodic_registry.periodic_tasks
    by_name = {name: pt for (name, _pid), pt in registry.items()}

    assert set(by_name) == {"ingest_prices", "ingest_fundamentals",
                            "ingest_instruments", "ingest_crypto"}
    assert by_name["ingest_prices"].cron == settings.ingest_prices_cron
    assert by_name["ingest_fundamentals"].cron == settings.ingest_fundamentals_cron
    assert by_name["ingest_instruments"].cron == settings.ingest_instruments_cron
    assert by_name["ingest_crypto"].cron == settings.ingest_crypto_cron
    for pt in by_name.values():
        assert pt.task.queue == "ingest", (
            "ingestion must not share the default queue — a multi-hour run would "
            "sit in front of every user import and backtest"
        )


def test_the_security_master_is_rebuilt_after_the_prices_it_reads():
    """ingest_instruments derives its listing intervals from `prices`. Scheduled
    ahead of the price refresh it would rebuild the master from yesterday's plane
    — and since that master is what switches survivorship correction on for B3, a
    stale one silently truncates every backtest's universe by a day."""
    prices_hour = int(settings.ingest_prices_cron.split()[1])
    instruments_hour = int(settings.ingest_instruments_cron.split()[1])
    assert instruments_hour > prices_hour


@pytest.mark.parametrize("task", ["ingest_prices", "ingest_fundamentals",
                                 "ingest_instruments", "ingest_crypto"])
def test_ingest_tasks_are_callable_without_the_periodic_timestamp(task):
    """The periodic scheduler injects `timestamp`; a manual defer_async() does not.
    Without a default the worker raises before the ingest_runs row is written, so
    an operator re-running a failed refresh gets an invisible failure. Caught by
    actually deferring the job, not by a unit test — hence this one."""
    import inspect

    sig = inspect.signature(getattr(tasks, task).func)
    assert sig.parameters["timestamp"].default is not inspect.Parameter.empty


def test_user_facing_tasks_stay_on_the_default_queue():
    """The default worker is pinned to queues=['default']; anything that drifts
    off it silently stops being processed."""
    assert tasks.process_imports.queue == "default"
    assert tasks.run_backtest.queue == "default"


@pytest.mark.asyncio(loop_scope="session")
async def test_prices_job_forces_the_current_year_download(monkeypatch):
    """Without --force-download the pipeline reuses the local COTAHIST copy and
    the plane never advances — a scheduled run that does nothing, with green logs.
    Corporate actions must not be skipped either: that flag is why
    `corporate_actions` was empty."""
    seen = {}

    def fake_run_module(args, timeout_s=None):
        seen["args"] = args
        import subprocess
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(enginerun, "run_module", fake_run_module)

    await tasks.ingest_prices.func(timestamp=0)

    assert seen["args"][0] == "b3_pipeline.main"
    assert "--force-download" in seen["args"]
    assert "--skip-corporate-actions" not in seen["args"]
    # Bounded, or the nightly re-probes and re-downloads 1994..2004 forever to
    # serve a window no strategy defaults to.
    assert seen["args"][seen["args"].index("--start-year") + 1] == str(
        settings.ingest_start_year
    )


@pytest.mark.asyncio(loop_scope="session")
async def test_a_failed_run_is_recorded_not_swallowed(monkeypatch):
    """The whole point of ingest_runs: a refresh that fails has to leave a trace."""
    def failing(args, timeout_s=None):
        import subprocess
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="B3 returned 503")

    monkeypatch.setattr(enginerun, "run_module", failing)

    result = await tasks.ingest_prices.func(timestamp=0)
    assert result["status"] == "error"

    async with async_session_maker() as session:
        row = (await session.scalars(
            select(IngestRun).where(IngestRun.id == __import__("uuid").UUID(result["run_id"]))
        )).one()
        assert row.kind == "prices"
        assert row.status == "error"
        assert "503" in row.error
        assert row.finished_at is not None


@pytest.mark.asyncio(loop_scope="session")
async def test_market_status_reports_staleness(client, monkeypatch):
    """/market/status must say the plane is behind. Positions are marked from it,
    so 'stale' is the difference between a stated fact and a silent lie."""
    from platform_app import routes

    routes._market_status_cache = None
    # UTC, matching the route's basis — a local date() is a day out either side of
    # midnight UTC and makes this fail depending on when it runs.
    old = datetime.datetime.now(datetime.UTC).date() - datetime.timedelta(days=200)
    monkeypatch.setattr(
        routes, "_market_status_uncached",
        lambda: _fake_status(old.isoformat()),
    )

    token = (await client.post("/auth/dev-login")).json()["access_token"]
    r = await client.get("/market/status", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["stale"] is True
    assert body["prices_age_days"] == 200
    routes._market_status_cache = None


async def _fake_status(max_date: str) -> dict:
    return {
        "prices": {"rows": 1, "tickers": 1, "min_date": "2020-01-01", "max_date": max_date},
        "tables": {},
    }


@pytest.mark.asyncio(loop_scope="session")
async def test_market_status_survives_an_absent_market_plane(client, monkeypatch):
    """A fresh deploy has no `prices` table until the first ingest runs, and
    aggregating a missing table raises. The endpoint that exists to report "there
    is no data yet" must not 500 on exactly that case — the dashboard polls it on
    every page."""
    from platform_app import routes

    routes._market_status_cache = None

    async def boom():
        raise RuntimeError('relation "prices" does not exist')

    monkeypatch.setattr(routes, "_market_status_uncached", boom)

    token = (await client.post("/auth/dev-login")).json()["access_token"]
    r = await client.get("/market/status", headers={"Authorization": f"Bearer {token}"})

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["stale"] is True
    assert body["prices"]["rows"] == 0
    assert body["prices"]["max_date"] is None
    routes._market_status_cache = None
