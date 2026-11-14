"""Procrastinate worker: `python -m platform_app.worker` (docker-compose worker service).

Two roles, selected by WORKER_QUEUES:

  unset / "default"  — imports and backtests (the compose `worker` service)
  "ingest"           — the scheduled market-data pipelines (the `ingest` service)

They are split because an ingestion run is minutes to hours and this worker has
two slots: sharing a queue would let a nightly refresh block every user-facing
import and backtest behind it. Only a worker that listens on a queue defers its
periodic tasks, so the ingest service is also what puts the cron schedule in
motion.

The `tasks_ingest` import is load-bearing rather than cosmetic: procrastinate
registers a periodic when the decorator runs, so a module nobody imports is a
cron that silently never fires.
"""

import asyncio
import os

from procrastinate.jobs import Status

from platform_app import tasks_ingest  # noqa: F401  (registers the periodics)
from platform_app.jobstatus import BacktestStatus, IMPORT_INFLIGHT, IngestStatus
from platform_app.tasks import app, defer_process_imports

# What a row left `running` by a dead worker is closed with. The backtest one is
# shown to the tenant, so it is in their language and says what to do; the
# ingest one is read by whoever operates the box.
INTERRUPTED_BACKTEST = (
    "Interrompido: o worker reiniciou (deploy ou queda) enquanto este backtest "
    "rodava. Envie de novo."
)
INTERRUPTED_INGEST = (
    "interrupted: the ingest worker restarted (a deploy or a crash) while this "
    "run was in progress; the next scheduled run starts from what had landed"
)
LOST_BACKTEST = (
    "Perdido na fila: este backtest nunca chegou a rodar (deploy ou queda logo "
    "após o envio). Envie de novo."
)

# How old a `pending` backtest with no job must be before boot calls it lost.
# create_backtest commits the row and only then defers its job — it must, or
# the job could start before the row is visible — so for a moment every healthy
# submission is a pending row with no job, and no query of the job table can
# tell it from one whose defer never happened: the job does not exist yet in
# either case. That moment is one pool open and one INSERT; five minutes is
# orders of magnitude past it, so a live submission is never failed. The price
# is that a row lost in the five minutes before a boot waits for the next one,
# and inflight_backtests already stops counting it after a day.
LOST_GRACE = "5 minutes"

_RUNNING, _PENDING, _ERROR = BacktestStatus.RUNNING, BacktestStatus.PENDING, BacktestStatus.ERROR

# Which rows each role may close. Only the worker that runs a kind of job knows
# that none of it is in flight: the default worker restarting must not touch an
# ingest the ingest worker is halfway through, and vice versa.
_ORPHANS = {
    "default": (
        (f"UPDATE backtest_runs SET status = '{_ERROR}', error = %(msg)s,"
         " finished_at = now()"
         f" WHERE status = '{_RUNNING}' AND created_at < now() RETURNING id",
         INTERRUPTED_BACKTEST, "interrupted"),
        # A pending row whose job is gone: the API died between the commit and
        # the defer (a defer that raises is failed by routes itself), or a dead
        # worker picked the job up before it could move the row and
        # _fail_stalled_jobs has just failed it. Same subquery as
        # maintenance_backtests._live_job.
        (f"UPDATE backtest_runs AS r SET status = '{_ERROR}', error = %(msg)s,"
         " finished_at = now()"
         f" WHERE r.status = '{_PENDING}' AND r.created_at < now() - interval '{LOST_GRACE}'"
         " AND NOT EXISTS (SELECT 1 FROM procrastinate_jobs j"
         " WHERE j.task_name = 'run_backtest' AND j.args->>'run_id' = r.id::text"
         " AND j.status IN ('todo', 'doing')) RETURNING r.id",
         LOST_BACKTEST, "lost"),
    ),
    "ingest": (
        (f"UPDATE ingest_runs SET status = '{IngestStatus.ERROR}', error = %(msg)s,"
         " finished_at = now()"
         f" WHERE status = '{IngestStatus.RUNNING}' AND started_at < now() RETURNING id",
         INTERRUPTED_INGEST, "interrupted"),
    ),
}


def _say(msg: str) -> None:
    # print, not logging: nothing configures logging in this process, and this
    # is the line an operator reading `docker compose logs` after a deploy is
    # looking for.
    print(f"[worker] {msg}", flush=True)


async def _fail_stalled_jobs(queue: str) -> None:
    # Its procrastinate job is stuck the same way, at `doing`, and a `doing`
    # job keeps its `lock`: a process_imports left there would hold
    # imports:<tenant> forever and that tenant's uploads would never run.
    # A heartbeat older than 0 s is every worker but this one, which has
    # not registered yet.
    for job in await app.job_manager.get_stalled_jobs(
            queue=queue, seconds_since_heartbeat=0):
        await app.job_manager.finish_job_by_id_async(
            job.id, Status.FAILED, delete_job=False)


async def _requeue_imports() -> None:
    """Defer process_imports again for every tenant with imports still in flight.

    An import has no `running` row to sweep: its job reads every extract when it
    starts, so the fix for any way of losing one — a defer that never happened,
    a job _fail_stalled_jobs just failed (a crash mid-load_b3), a batch left
    `processing` by a job that stood down as superseded for a newer one that
    then died — is simply another job. Free where one is already waiting:
    procrastinate's queueing_lock index is unique over `todo` jobs, so the
    defer raises AlreadyEnqueued and the waiting one is kept. A `doing` one
    would not block it (the index ignores `doing`; `lock` would then hold the
    new job back until it ends), but at boot there is none: the stalled ones
    were failed just before, and this worker has not taken any yet.

    A book whose extracts are gone from disk fails again, into `error`
    (process_imports' handler), and is not picked up at the next boot.
    """
    rows = await app.connector.execute_query_all_async(
        "SELECT DISTINCT tenant_id FROM imports WHERE status = ANY(%(s)s)",
        s=[str(s) for s in IMPORT_INFLIGHT])
    for r in rows:
        await defer_process_imports(r["tenant_id"])
    if rows:
        _say(f"re-queued imports for {len(rows)} tenant(s)")


async def _close_orphans(queues: list[str]) -> None:
    """Fail the rows a previous incarnation of this worker left `running`.

    A row only reaches `running` inside a worker, and a worker that dies mid-job
    (a deploy outlasting stop_grace_period, an OOM kill, a host reboot) never
    writes `done` or `error`. Nothing else ever would: procrastinate does not
    retry a job whose worker vanished, so the row stays `running` for good. For
    a backtest that is not cosmetic — routes.inflight_backtests counts it against
    the tenant's quota forever and the retention endpoint refuses to delete it,
    so three unlucky deploys lock a bench account out behind a 429 nobody clears.

    Safe because each compose file runs exactly one container per role (fixed
    container_name), and this runs before that container takes its first job:
    anything `running` now belongs to a process that no longer exists. `now()`
    is the transaction's start, so a row this process writes later never
    matches. Scaling a role to several replicas would break that premise.

    The default worker also closes backtests lost while `pending` and re-queues
    imports still in flight — the two ways a row could wait on a job that will
    never come.
    """
    for queue in queues or ["default"]:
        await _fail_stalled_jobs(queue)
        for sql, msg, what in _ORPHANS.get(queue, ()):
            closed = await app.connector.execute_query_all_async(sql, msg=msg)
            if closed:
                ids = ", ".join(str(r["id"]) for r in closed)
                _say(f"closed {len(closed)} {what} {queue} run(s): {ids}")
        if queue == "default":
            await _requeue_imports()


async def _run() -> None:
    queues = [q for q in os.environ.get("WORKER_QUEUES", "").split(",") if q]
    async with app.open_async():
        # procrastinate ships its own tables; create them once on a fresh DB.
        # Its schema SQL isn't idempotent, so guard on an existence check rather
        # than a separate migrate step.
        row = await app.connector.execute_query_one_async(
            "SELECT to_regclass('procrastinate_jobs') AS reg"
        )
        if row["reg"] is None:
            await app.schema_manager.apply_schema_async()
        await _close_orphans(queues)
        # ponytail: concurrency capped low — worker shares CPU on the single VPS
        # (spec 01). This is a throughput setting, not admission control: it says
        # how fast the queue drains, never how much one account may put in it.
        # That job belongs to routes.BACKTEST_QUOTA, which caps a *submitter's*
        # in-flight runs — the ceiling here is the box, and the ceiling there is
        # one customer's share of it. Imports and ingest have no such cap: an
        # import is one job per human upload, and ingest is ours on a schedule.
        #
        # The ingest worker runs one at a time: two concurrent COTAHIST parses on a
        # shared VPS is how you page yourself. queueing_lock already stops the same
        # kind overlapping; this stops prices and fundamentals colliding.
        if queues:
            await app.run_worker_async(queues=queues, concurrency=1)
        else:
            # Default worker: everything EXCEPT ingest, so a multi-hour refresh can
            # never sit in front of a user's import.
            await app.run_worker_async(queues=["default"], concurrency=2)


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
