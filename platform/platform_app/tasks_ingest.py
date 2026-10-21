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
import re
import sys

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

# The pipelines log in two formats ("... - ERROR - ..." in b3_pipeline,
# "... ERROR ..." in us_pipeline); the level word is what they share.
_ERROR_LINE = re.compile(r"\b(ERROR|CRITICAL)\b")


class IngestFailed(RuntimeError):
    """The ingestion module exited non-zero. Raised after the ingest_runs row is
    written, so the job is `failed` in procrastinate_jobs too rather than a
    success whose only trace is a column nothing reads."""

    def __init__(self, kind: str, run_id, detail: str) -> None:
        self.run_id = run_id
        super().__init__(f"ingest {kind} failed (ingest_runs {run_id}):\n{detail}")


async def _record_ingest(kind: str, args: list[str]) -> dict:
    """Run an engine ingestion module, bracketed by an ingest_runs row.

    The module's output is captured, so this is the only place it can surface.
    A non-zero exit is recorded and then raised. A zero exit can still carry
    errors: b3_pipeline lets its third-party steps (CDI/IPCA, Ibovespa, Tesouro,
    external prices) fail alone ("fetch failed, continuing"), and those lines
    were discarded with the rest of a successful run's output — the series went
    stale behind a row saying `done`. They are kept in `error` on the done row, and every outcome is
    printed for `docker compose logs ingest`.
    """
    async with async_session_maker() as session:
        run = IngestRun(kind=kind, status="running")
        session.add(run)
        await session.commit()
        run_id = run.id

    proc = await asyncio.to_thread(enginerun.run_module, args)

    if proc.returncode == 0:
        status = "done"
        output = f"{proc.stdout or ''}\n{proc.stderr or ''}"
        detail = "\n".join(
            ln for ln in output.splitlines() if _ERROR_LINE.search(ln))[-4000:]
    else:
        status = "error"
        detail = (proc.stderr or proc.stdout)[-4000:]
    async with async_session_maker() as session:
        row = await session.get(IngestRun, run_id)
        row.finished_at = datetime.datetime.now(datetime.timezone.utc)
        row.status = status
        row.error = detail or None
        await session.commit()

    print(f"[ingest] {kind}: {status} (exit {proc.returncode}, ingest_runs {run_id})"
          + (f"\n{detail}" if detail else ""), file=sys.stderr, flush=True)
    if proc.returncode != 0:
        raise IngestFailed(kind, run_id, detail)
    return {"kind": kind, "status": status, "run_id": str(run_id)}


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


def _ingest_task(kind: str, module: str, cron: str | None):
    """Register ``ingest_<kind>``, running ``python -m <module>`` on the ingest
    queue with one pending run per kind (see above). Scheduled on ``cron``;
    ``None`` means deferred by hand only. ``timestamp`` defaults for the reason
    given on ingest_prices."""
    name = f"ingest_{kind}"

    async def ingest(timestamp: int = 0) -> dict:
        return await _record_ingest(kind, [module])

    task = app.task(name=name, queue="ingest", queueing_lock=name)(ingest)
    return task if cron is None else app.periodic(cron=cron)(task)


# Refresh CVM fundamentals (fundamentals_pit), which the f_* signals read.
ingest_fundamentals = _ingest_task(
    "fundamentals", "b3_pipeline.cvm_main", settings.ingest_fundamentals_cron)

# Rebuild the B3 security master from the price plane.
#
# This is what switches survivorship correction on for B3: until `instruments`
# has rows, `backtests.core.universe` treats every ticker as always-listed and
# the loaders behave exactly as they did before it existed. Separate from
# ingest_prices, and later in the day, because it reads what that job wrote.
ingest_instruments = _ingest_task(
    "instruments", "b3_pipeline.instruments_backfill", settings.ingest_instruments_cron)

# Refresh the crypto plane from Binance's public archive.
#
# Enumerates every spot pair the archive has ever held, delisted ones included
# — that enumeration is the survivorship property, so there is no "only the
# live ones" fast path to add here later.
ingest_crypto = _ingest_task(
    "crypto", "crypto_pipeline.crypto_main", settings.ingest_crypto_cron)

# Capture today's consolidated US symbol directory.
#
# This is what makes the US plane survivorship-free going forward: a symbol's
# appearance and disappearance across these daily captures is its listing
# interval, and nobody publishes them historically for free. A day not captured
# is listing history that cannot be recovered — the same reason B3's daily file
# is a daily obligation rather than a backfill.
ingest_us_symbols = _ingest_task(
    "us_symbols", "us_pipeline.symbol_directory", settings.ingest_us_symbols_cron)

# Refresh US point-in-time fundamentals from SEC XBRL.
#
# Bootstraps itself: an empty plane takes the bulk `companyfacts.zip` pass —
# the one that gets the companies which stopped filing — and a populated one
# walks the CIKs it already has. Deciding from the plane rather than from a
# flag is what stops this reporting success having walked an empty list.
#
# The archive lands on the engine data volume, so the ~1.5 GB download happens
# on the first run and not again.
ingest_us_fundamentals = _ingest_task(
    "us_fundamentals", "us_pipeline.us_main", settings.ingest_us_fundamentals_cron)

# The US listing record, backwards.
#
# Two requests return every US stock and ETF ever listed, alive or delisted,
# with an IPO date and a delisting date. That is the half the daily directory
# capture cannot reach: it builds forward from the day it started, so a
# company that delisted in 2011 had no interval at all and the point-in-time
# universe treated it as tradable on every date.
#
# Runs before the directory capture so a same-day disagreement between the two
# is visible in `ingest_runs` in the order it happened.
ingest_us_listing = _ingest_task(
    "us_listing", "us_pipeline.us_listing", settings.ingest_us_listing_cron)

# Keep the US plane current: re-adjust for splits, then append the session.
#
# The counterpart to a history that arrives as a file. The bulk dump is loaded
# by hand and is correct on the day it is loaded; this is what stops it going
# stale, in one request per session against a free tier that allows five a
# minute.
#
# Two steps and the order is the point. A split-adjusted vendor re-scales its
# own history when a split executes and the stored history does not move, so
# appending the new bar first would splice a quarter-price session onto a
# full-price one and fabricate a -75% return — the artefact `glitch_down` is
# built to mask, which is what makes it dangerous. `us_daily` re-scales the
# old rows first, once per split ever, keyed on the vendor's split id.
#
# A session that has not closed is not a failure: the free tier refuses it by
# name, the run stops there and reports it, and the next run picks it up.
ingest_us_daily = _ingest_task(
    "us_daily", "us_pipeline.us_daily", settings.ingest_us_daily_cron)

# Fifty-six years of US history from the WIKI bulk export. Deliberately not
# periodic (cron=None). Every other ingest here has a cron because its source
# keeps publishing; WIKI was frozen in April 2018 and never will again, so a
# schedule would re-read the same 15.4M rows forever. Deferred by hand, once,
# and re-run only to repair.
#
# One free key, one request: 15.4M bars over 3,199 tickers from 1962, with the
# corporate actions in the rows — so the bars land `total_return` rather than
# the `split_only` a split-adjusted feed can give.
#
# It fills 1962..2018-03-27 and nothing after. The bulk `d_us_txt` dump covers
# the gap and, for every symbol it can serve, takes the symbol over; what
# stays here is the tail that file alone has — 1,502 names that died inside
# WIKI's maintenance window, which the dump does not carry because it holds
# only what is currently listed. Worth running first on an empty plane anyway:
# it is one request and it is fast.
ingest_wiki_backfill = _ingest_task("wiki_backfill", "us_pipeline.wiki_backfill", None)

# Spend the day's request budget on the US bars still missing.
#
# Daily, and every day counts: the free tier is a hard 25 requests a day
# against ~11k symbols, so this is a slice of a long job rather than a refresh.
# The cursor is what makes the slices add up — see `us_pipeline.us_prices`.
#
# Exhausting the budget is a normal outcome, not a failure, and the run says so
# in its summary rather than in its exit code.
ingest_us_prices = _ingest_task(
    "us_prices", "us_pipeline.us_prices", settings.ingest_us_prices_cron)

# The US benchmark, as a BRL total-return series under `$SPX`.
#
# Three requests. Kept out of the price job because it must not queue behind an
# eleven-thousand-symbol backfill: a bench with no ruler is what this fixes.
ingest_spx = _ingest_task("spx", "us_pipeline.spx_series", settings.ingest_spx_cron)
