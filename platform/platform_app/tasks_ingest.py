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


# Deliberately not `@app.periodic` any more, for the reason `ingest_wiki_backfill`
# never was: the source stopped answering. Stooq began requiring an API key in
# 2026, issued only through a browser CAPTCHA, so the per-symbol walk this task
# runs now 404s on every symbol — a nightly run that could only ever record an
# error. The history it was meant to fetch is loaded from Stooq's bulk `d_us_txt`
# dump by hand (no machine can fetch it past the vendor's proof-of-work), and
# `ingest_us_daily` is what keeps that history current. Kept deferrable rather
# than deleted: if a key is ever issued, restoring the schedule is one line.
@app.task(name="ingest_us_backfill", queue="ingest",
          queueing_lock="ingest_us_backfill")
async def ingest_us_backfill(timestamp: int = 0) -> dict:  # see ingest_prices
    """The US price history, from one bulk file, off the metered budget.

    This is the job that makes a free key workable. `ingest_us_prices` buys
    three requests per symbol against a 25-a-day ceiling and ~11k symbols — five
    years to a first pass — so it is the top-up, not the backfill, and this is
    the backfill. No API key, no account, and it reaches the present, which is
    the one thing `ingest_wiki_backfill` cannot do.

    Daily, and resumable, because the vendor serves one symbol per request under
    an anonymous daily hit ceiling: a run ends when the ceiling is spent, keeps
    what it wrote, and the next one picks up from the cursor. A throttled stop
    is reported in the summary rather than in the exit code — the day's
    allowance running out is what the day's allowance does. A first pass over
    the universe therefore takes several days, the same way `ingest_crypto`'s
    takes hours: a free source charges in wall-clock instead of money.

    It also *replaces* WIKI's rows for every symbol it can serve, so that no
    symbol carries a raw close and a split-adjusted one in the same column. See
    `us_pipeline.us_backfill` on why that is a delete and not an upsert.
    """
    return await _record_ingest("us_backfill", ["us_pipeline.us_backfill"])


@app.periodic(cron=settings.ingest_us_daily_cron)
@app.task(name="ingest_us_daily", queue="ingest",
          queueing_lock="ingest_us_daily")
async def ingest_us_daily(timestamp: int = 0) -> dict:  # see ingest_prices
    """Keep the US plane current: re-adjust for splits, then append the session.

    The counterpart to a history that arrives as a file. The bulk dump is loaded
    by hand and is correct on the day it is loaded; this is what stops it going
    stale, in one request per session against a free tier that allows five a
    minute.

    Two steps and the order is the point. A split-adjusted vendor re-scales its
    own history when a split executes and the stored history does not move, so
    appending the new bar first would splice a quarter-price session onto a
    full-price one and fabricate a -75% return — the artefact `glitch_down` is
    built to mask, which is what makes it dangerous. `us_daily` re-scales the
    old rows first, once per split ever, keyed on the vendor's split id.

    A session that has not closed is not a failure: the free tier refuses it by
    name, the run stops there and reports it, and the next run picks it up.
    """
    return await _record_ingest("us_daily", ["us_pipeline.us_daily"])


# Deliberately not `@app.periodic`. Every other ingest here has a cron because
# its source keeps publishing; WIKI was frozen in April 2018 and never will
# again, so a schedule would re-read the same 15.4M rows forever. Deferred by
# hand, once, and re-run only to repair.
@app.task(name="ingest_wiki_backfill", queue="ingest",
          queueing_lock="ingest_wiki_backfill")
async def ingest_wiki_backfill(timestamp: int = 0) -> dict:  # see ingest_prices
    """Fifty-six years of US history from the WIKI bulk export.

    One free key, one request: 15.4M bars over 3,199 tickers from 1962, with the
    corporate actions in the rows — so the bars land `total_return` rather than
    the `split_only` a split-adjusted feed can give.

    It fills 1962..2018-03-27 and nothing after. `ingest_us_backfill` is what
    covers the gap and, for every symbol it can serve, takes the symbol over;
    what stays here is the tail this file alone has, the names that died inside
    WIKI's maintenance window. Worth running first on an empty plane anyway: it
    is one request and it is fast.
    """
    return await _record_ingest("wiki_backfill", ["us_pipeline.wiki_backfill"])


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
