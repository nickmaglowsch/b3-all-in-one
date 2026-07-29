"""Procrastinate worker: `python -m platform_app.worker` (docker-compose worker service).

Two roles, selected by WORKER_QUEUES:

  unset / "default"  — imports and backtests (the compose `worker` service)
  "ingest"           — the scheduled market-data pipelines (the `ingest` service)

They are split because an ingestion run is minutes to hours and this worker has
two slots: sharing a queue would let a nightly refresh block every user-facing
import and backtest behind it. Only a worker that listens on a queue defers its
periodic tasks, so the ingest service is also what puts the cron schedule in
motion.
"""

import asyncio
import os

from platform_app.tasks import app


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
