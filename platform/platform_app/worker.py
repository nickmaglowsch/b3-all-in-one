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
from platform_app.tasks import app

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

# Which rows each role may close. Only the worker that runs a kind of job knows
# that none of it is in flight: the default worker restarting must not touch an
# ingest the ingest worker is halfway through, and vice versa.
_ORPHANS = {
    "default": (
        ("UPDATE backtest_runs SET status = 'error', error = %(msg)s,"
         " finished_at = now()"
         " WHERE status = 'running' AND created_at < now() RETURNING id"),
        INTERRUPTED_BACKTEST,
    ),
    "ingest": (
        ("UPDATE ingest_runs SET status = 'error', error = %(msg)s,"
         " finished_at = now()"
         " WHERE status = 'running' AND started_at < now() RETURNING id"),
        INTERRUPTED_INGEST,
    ),
}


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
    """
    for queue in queues or ["default"]:
        # Its procrastinate job is stuck the same way, at `doing`, and a `doing`
        # job keeps its `lock`: a process_imports left there would hold
        # imports:<tenant> forever and that tenant's uploads would never run.
        # A heartbeat older than 0 s is every worker but this one, which has
        # not registered yet.
        for job in await app.job_manager.get_stalled_jobs(
                queue=queue, seconds_since_heartbeat=0):
            await app.job_manager.finish_job_by_id_async(
                job.id, Status.FAILED, delete_job=False)
        if queue not in _ORPHANS:
            continue
        sql, msg = _ORPHANS[queue]
        closed = await app.connector.execute_query_all_async(sql, msg=msg)
        if closed:
            # print, not logging: nothing configures logging in this process, and
            # this is the line an operator reading `docker compose logs` after a
            # deploy is looking for.
            ids = ", ".join(str(r["id"]) for r in closed)
            print(f"[worker] closed {len(closed)} interrupted {queue} run(s): {ids}",
                  flush=True)


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
