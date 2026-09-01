"""The scheduled market-data ingestion tasks.

Split out of `tasks` on size: nine periodic jobs with the docstrings this
codebase writes do not fit alongside the import and backtest tasks under the
400-line gate. Nothing else moved — `app` still lives in `tasks`, and this
module only decorates.

**It has to be imported for the schedule to exist.** procrastinate registers a
periodic when the decorator runs, and only a worker listening on the queue
defers it, so `worker.py` imports this module by name before opening the app. A
module nobody imports is a cron that silently never fires.
"""

import asyncio
import datetime

from platform_app import enginerun
from platform_app.config import settings
from platform_app.db import async_session_maker
from platform_app.models import IngestRun
from platform_app.tasks import app

# Runs in-process here rather than under host cron or a GitHub Action: the prod
# Postgres publishes no ports, the image already carries b3_pipeline and every dep
# it imports, and procrastinate (already a dependency) gives cron, retries and a
# queryable job table for free.
#
# `queue="ingest"`: a full backfill is hours long and the default worker runs two
# slots. Left on the default queue it would starve imports and backtests, so the
# compose worker excludes this queue and a separate one drains it.
#
# `queueing_lock`: at most one pending run per kind. A nightly firing while the
# previous one still runs (or while someone ingests by hand) would have two
# processes writing the same COTAHIST file and the same price rows.

async def _record_ingest(kind: str, args: list[str]) -> dict:
    """Run an engine ingestion module, bracketed by an ingest_runs row."""
    async with async_session_maker() as session:
        run = IngestRun(kind=kind, status="running")
        session.add(run)
        await session.commit()
        run_id = run.id

    proc = await asyncio.to_thread(enginerun.run_module, args)

    async with async_session_maker() as session:
        row = await session.get(IngestRun, run_id)
        row.finished_at = datetime.datetime.now(datetime.timezone.utc)
        if proc.returncode == 0:
            row.status = "done"
        else:
            row.status = "error"
            row.error = (proc.stderr or proc.stdout)[-4000:]
        await session.commit()
        return {"kind": kind, "status": row.status, "run_id": str(run_id)}


@app.periodic(cron=settings.ingest_prices_cron)
@app.task(name="ingest_prices", queue="ingest", queueing_lock="ingest_prices")
async def ingest_prices(timestamp: int = 0) -> dict:
    # timestamp defaults so the job can also be deferred by hand
    # (`ingest_prices.defer_async()`) — the periodic scheduler passes it, an
    # operator re-running a failed refresh or kicking off the initial backfill
    # has nothing to pass. Without the default that call raises inside the worker
    # before _record_ingest can write a row, so the failure is invisible in
    # ingest_runs and only shows up in procrastinate_jobs.
    """Refresh the price plane + corporate actions from B3.

    --force-download is what makes this worth scheduling: B3 rewrites the current
    year's COTAHIST archive daily, and without it the pipeline reuses the local
    copy and never advances (see b3_pipeline.main).

    Corporate actions are deliberately NOT skipped. `corporate_actions` was empty
    for the whole life of this database — the step had only ever been run with
    --skip-corporate-actions — which left dividend total-return adjustment missing
    and only detector-inferred splits in place.
    """
    return await _record_ingest("prices", [
        "b3_pipeline.main",
        "--force-download",
        "--start-year", str(settings.ingest_start_year),
    ])


@app.periodic(cron=settings.ingest_fundamentals_cron)
@app.task(name="ingest_fundamentals", queue="ingest", queueing_lock="ingest_fundamentals")
async def ingest_fundamentals(timestamp: int = 0) -> dict:  # see ingest_prices re: default
    """Refresh CVM fundamentals (fundamentals_pit), which the f_* signals read."""
    return await _record_ingest("fundamentals", ["b3_pipeline.cvm_main"])


@app.periodic(cron=settings.ingest_instruments_cron)
@app.task(name="ingest_instruments", queue="ingest", queueing_lock="ingest_instruments")
async def ingest_instruments(timestamp: int = 0) -> dict:  # see ingest_prices re: default
    """Rebuild the B3 security master from the price plane.

    This is what switches survivorship correction on for B3: until `instruments`
    has rows, `backtests.core.universe` treats every ticker as always-listed and
    the loaders behave exactly as they did before it existed. Separate from
    ingest_prices, and later in the day, because it reads what that job wrote.
    """
    return await _record_ingest("instruments", ["b3_pipeline.instruments_backfill"])


@app.periodic(cron=settings.ingest_crypto_cron)
@app.task(name="ingest_crypto", queue="ingest", queueing_lock="ingest_crypto")
async def ingest_crypto(timestamp: int = 0) -> dict:  # see ingest_prices re: default
    """Refresh the crypto plane from Binance's public archive.

    Enumerates every spot pair the archive has ever held, delisted ones included
    — that enumeration is the survivorship property, so there is no "only the
    live ones" fast path to add here later.
    """
    return await _record_ingest("crypto", ["crypto_pipeline.crypto_main"])


@app.periodic(cron=settings.ingest_us_symbols_cron)
@app.task(name="ingest_us_symbols", queue="ingest",
          queueing_lock="ingest_us_symbols")
async def ingest_us_symbols(timestamp: int = 0) -> dict:  # see ingest_prices
    """Capture today's consolidated US symbol directory.

    This is what makes the US plane survivorship-free going forward: a symbol's
    appearance and disappearance across these daily captures is its listing
    interval, and nobody publishes them historically for free. A day not captured
    is listing history that cannot be recovered — the same reason B3's daily file
    is a daily obligation rather than a backfill.
    """
    return await _record_ingest("us_symbols", ["us_pipeline.symbol_directory"])


@app.periodic(cron=settings.ingest_us_fundamentals_cron)
@app.task(name="ingest_us_fundamentals", queue="ingest",
          queueing_lock="ingest_us_fundamentals")
async def ingest_us_fundamentals(timestamp: int = 0) -> dict:  # see ingest_prices
    """Refresh US point-in-time fundamentals from SEC XBRL.

    Bootstraps itself: an empty plane takes the bulk `companyfacts.zip` pass —
    the one that gets the companies which stopped filing — and a populated one
    walks the CIKs it already has. Deciding from the plane rather than from a
    flag is what stops this reporting success having walked an empty list.

    The archive lands on the engine data volume, so the ~1.5 GB download happens
    on the first run and not again.
    """
    return await _record_ingest("us_fundamentals", ["us_pipeline.us_main"])


@app.periodic(cron=settings.ingest_us_listing_cron)
@app.task(name="ingest_us_listing", queue="ingest",
          queueing_lock="ingest_us_listing")
async def ingest_us_listing(timestamp: int = 0) -> dict:  # see ingest_prices
    """The US listing record, backwards.

    Two requests return every US stock and ETF ever listed, alive or delisted,
    with an IPO date and a delisting date. That is the half the daily directory
    capture cannot reach: it builds forward from the day it started, so a
    company that delisted in 2011 had no interval at all and the point-in-time
    universe treated it as tradable on every date.

    Runs before the directory capture so a same-day disagreement between the two
    is visible in `ingest_runs` in the order it happened.
    """
    return await _record_ingest("us_listing", ["us_pipeline.us_listing"])


@app.periodic(cron=settings.ingest_us_prices_cron)
@app.task(name="ingest_us_prices", queue="ingest",
          queueing_lock="ingest_us_prices")
async def ingest_us_prices(timestamp: int = 0) -> dict:  # see ingest_prices
    """Spend the day's request budget on the US bars still missing.

    Daily, and every day counts: the free tier is a hard 25 requests a day
    against ~11k symbols, so this is a slice of a long job rather than a refresh.
    The cursor is what makes the slices add up — see `us_pipeline.us_prices`.

    Exhausting the budget is a normal outcome, not a failure, and the run says so
    in its summary rather than in its exit code.
    """
    return await _record_ingest("us_prices", ["us_pipeline.us_prices"])


@app.periodic(cron=settings.ingest_spx_cron)
@app.task(name="ingest_spx", queue="ingest", queueing_lock="ingest_spx")
async def ingest_spx(timestamp: int = 0) -> dict:  # see ingest_prices
    """The US benchmark, as a BRL total-return series under `$SPX`.

    Three requests. Kept out of the price job because it must not queue behind an
    eleven-thousand-symbol backfill: a bench with no ruler is what this fixes.
    """
    return await _record_ingest("spx", ["us_pipeline.spx_series"])
