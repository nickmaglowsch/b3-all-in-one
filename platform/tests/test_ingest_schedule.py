"""The scheduled market-data refresh.

These lock down the wiring that decides whether the automation actually runs and
whether a failure is noticeable — the two things whose absence left this database
seven months stale with nothing reporting it.
"""
import datetime

import pytest
from platform_app import enginerun, tasks, tasks_ingest
from platform_app.config import settings
from platform_app.db import async_session_maker
from platform_app.models import IngestRun


def test_the_claimed_listing_interval_is_captured_before_the_observed_one():
    """`us_listing` writes listing_date/delisting_date from a vendor claim;
    `us_symbols` writes first_seen/last_seen from what the plane watched. When
    the two disagree on a day, the order they ran in is the only way to read
    `ingest_runs` and tell which saw what."""
    listing_hour = int(settings.ingest_us_listing_cron.split()[1])
    symbols_hour = int(settings.ingest_us_symbols_cron.split()[1])
    assert listing_hour < symbols_hour


def test_periodic_tasks_are_registered_on_the_ingest_queue():
    """A task is only deferred by a worker listening on its queue, and only ever
    fires at all if the periodic registration took."""
    registry = tasks.app.periodic_registry.periodic_tasks
    by_name = {name: pt for (name, _pid), pt in registry.items()}

    assert set(by_name) == {"ingest_prices", "ingest_fundamentals",
                            "ingest_instruments", "ingest_crypto",
                            "ingest_us_fundamentals", "ingest_us_symbols",
                            "ingest_us_listing", "ingest_us_prices",
                            "ingest_us_daily", "ingest_spx"}
    assert by_name["ingest_prices"].cron == settings.ingest_prices_cron
    assert by_name["ingest_fundamentals"].cron == settings.ingest_fundamentals_cron
    assert by_name["ingest_instruments"].cron == settings.ingest_instruments_cron
    assert by_name["ingest_crypto"].cron == settings.ingest_crypto_cron
    assert (by_name["ingest_us_fundamentals"].cron
            == settings.ingest_us_fundamentals_cron)
    assert by_name["ingest_us_symbols"].cron == settings.ingest_us_symbols_cron
    assert by_name["ingest_us_listing"].cron == settings.ingest_us_listing_cron
    assert by_name["ingest_us_prices"].cron == settings.ingest_us_prices_cron
    assert (by_name["ingest_us_daily"].cron
            == settings.ingest_us_daily_cron)
    assert by_name["ingest_spx"].cron == settings.ingest_spx_cron
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


def test_the_us_daily_refresh_runs_after_the_session_it_asks_for_has_settled():
    """The free tier refuses a session that has not closed, by name. New York
    midnight is 04:00 UTC on daylight time and 05:00 on standard, and the vendor
    needs a while past that to settle the day — so a refresh scheduled before
    ~06:00 UTC would spend its request being told to come back later, every day,
    and the plane would never advance. Encoded because the failure is silent:
    `NotSettled` is handled, so the job would go on reporting success while
    writing nothing."""
    daily_hour = int(settings.ingest_us_daily_cron.split()[1])
    assert daily_hour >= 6


@pytest.mark.parametrize("task", ["ingest_prices", "ingest_fundamentals",
                                 "ingest_instruments", "ingest_crypto",
                                 "ingest_us_fundamentals", "ingest_us_symbols",
                                 "ingest_us_listing", "ingest_us_prices",
                                 "ingest_spx", "ingest_us_daily",
                                 "ingest_wiki_backfill"])
def test_ingest_tasks_are_callable_without_the_periodic_timestamp(task):
    """The periodic scheduler injects `timestamp`; a manual defer_async() does not.
    Without a default the worker raises before the ingest_runs row is written, so
    an operator re-running a failed refresh gets an invisible failure. Caught by
    actually deferring the job, not by a unit test — hence this one."""
    import inspect

    sig = inspect.signature(getattr(tasks_ingest, task).func)
    assert sig.parameters["timestamp"].default is not inspect.Parameter.empty


def test_the_wiki_backfill_is_deliberately_not_periodic():
    """Every other ingest here has a cron because its source keeps publishing.
    WIKI was frozen in April 2018, so a schedule would re-read the same 15.4M
    rows forever without ever adding a bar. It is deferred by hand."""
    registry = tasks.app.periodic_registry.periodic_tasks
    assert "ingest_wiki_backfill" not in {
        pt.task.name for pt in registry.values()}
    assert tasks_ingest.ingest_wiki_backfill.queue == "ingest"


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

    await tasks_ingest.ingest_prices.func(timestamp=0)

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

    # Raised, not returned: a task that returns is `succeeded` in
    # procrastinate_jobs, so the failure lived only in a column nothing reads.
    with pytest.raises(tasks_ingest.IngestFailed, match="503") as failed:
        await tasks_ingest.ingest_prices.func(timestamp=0)

    async with async_session_maker() as session:
        row = await session.get(IngestRun, failed.value.run_id)
        assert row.kind == "prices"
        assert row.status == "error"
        assert "503" in row.error
        assert row.finished_at is not None


@pytest.mark.asyncio(loop_scope="session")
async def test_a_run_that_survived_a_failed_step_says_which(monkeypatch, capsys):
    """b3_pipeline lets the benchmark and Tesouro steps fail alone and exits 0.
    Those lines were thrown away with the rest of a successful run's output, so
    the benchmarks went stale behind a row reading `done`."""
    out = ("2026-10-21 06:00:01 - INFO - Step 5c2/9: Fetching Ibovespa index series...\n"
           "2026-10-21 06:00:02 - ERROR - Ibovespa series fetch failed, continuing: 503\n"
           "2026-10-21 06:00:03 - INFO - Step 6/9: ...\n")

    def partial(args, timeout_s=None):
        import subprocess
        return subprocess.CompletedProcess(args, 0, stdout=out, stderr="")

    monkeypatch.setattr(enginerun, "run_module", partial)

    result = await tasks_ingest.ingest_prices.func(timestamp=0)

    assert result["status"] == "done"
    async with async_session_maker() as session:
        row = await session.get(IngestRun, __import__("uuid").UUID(result["run_id"]))
    assert row.error == ("2026-10-21 06:00:02 - ERROR - Ibovespa series fetch "
                         "failed, continuing: 503")
    assert "Ibovespa series fetch failed" in capsys.readouterr().err


@pytest.mark.asyncio(loop_scope="session")
async def test_market_status_reports_staleness(client, monkeypatch):
    """/market/status must say the plane is behind. Positions are marked from it,
    so 'stale' is the difference between a stated fact and a silent lie."""
    from platform_app import routes_market_status as routes

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
    from platform_app import routes_market_status as routes

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


INGEST_MODULES = {
    "prices": ["b3_pipeline.main", "--force-download",
               "--start-year", str(settings.ingest_start_year)],
    "fundamentals": ["b3_pipeline.cvm_main"],
    "instruments": ["b3_pipeline.instruments_backfill"],
    "crypto": ["crypto_pipeline.crypto_main"],
    "us_symbols": ["us_pipeline.symbol_directory"],
    "us_fundamentals": ["us_pipeline.us_main"],
    "us_listing": ["us_pipeline.us_listing"],
    "us_daily": ["us_pipeline.us_daily"],
    "wiki_backfill": ["us_pipeline.wiki_backfill"],
    "us_prices": ["us_pipeline.us_prices"],
    "spx": ["us_pipeline.spx_series"],
}


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("kind", sorted(INGEST_MODULES))
async def test_each_ingest_task_is_wired_to_its_module(monkeypatch, kind):
    """Name, queue, one-pending-run lock, and the exact module argv each kind
    runs, recorded under its own kind in ingest_runs."""
    import subprocess

    task = getattr(tasks_ingest, f"ingest_{kind}")
    assert task.name == f"ingest_{kind}"
    assert task.queue == "ingest"
    assert task.queueing_lock == f"ingest_{kind}"

    seen = []
    monkeypatch.setattr(enginerun, "run_module", lambda args, timeout_s=None: (
        seen.append(args) or subprocess.CompletedProcess(args, 0, stdout="", stderr="")))
    result = await task.func(timestamp=0)
    assert seen == [INGEST_MODULES[kind]]
    assert (result["kind"], result["status"]) == (kind, "done")


@pytest.mark.asyncio(loop_scope="session")
async def test_an_expired_market_status_is_served_while_one_refresh_runs(client, monkeypatch):
    """The aggregate behind /market/status is seconds on a full plane. An
    expired entry must answer at once and recompute in the background — once,
    however many tabs poll meanwhile — never inline on a request."""
    import asyncio

    from platform_app import routes_market_status as routes

    today = datetime.datetime.now(datetime.UTC).date().isoformat()
    expired = datetime.datetime.now(datetime.UTC) - routes._MARKET_STATUS_TTL * 2
    routes._market_status_cache = (expired, await _fake_status("2020-01-02"))
    calls, gate = [], asyncio.Event()

    async def slow():
        calls.append(1)
        await gate.wait()
        return await _fake_status(today)

    monkeypatch.setattr(routes, "_market_status_uncached", slow)
    token = (await client.post("/auth/dev-login")).json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}
    for _ in range(3):
        r = await asyncio.wait_for(client.get("/market/status", headers=h), 5)
        assert r.json()["prices"]["max_date"] == "2020-01-02"   # the stale one
    await asyncio.sleep(0)
    assert calls == [1]
    gate.set()
    await routes._market_status_refresh
    r = await client.get("/market/status", headers=h)
    assert r.json()["prices"]["max_date"] == today
    routes._market_status_cache = None
