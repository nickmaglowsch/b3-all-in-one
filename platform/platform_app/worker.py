"""Procrastinate worker: `python -m platform_app.worker` (docker-compose worker service)."""

import asyncio

from platform_app.tasks import app


async def _run() -> None:
    async with app.open_async():
        # procrastinate ships its own tables; create them once on a fresh DB.
        # Its schema SQL isn't idempotent, so guard on an existence check rather
        # than a separate migrate step.
        row = await app.connector.execute_query_one_async(
            "SELECT to_regclass('procrastinate_jobs') AS reg"
        )
        if row["reg"] is None:
            await app.schema_manager.apply_schema_async()
        # ponytail: concurrency capped low — worker shares CPU on the single VPS (spec 01);
        # per-tenant quotas are the real admission control, add them when jobs land.
        await app.run_worker_async(concurrency=2)


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
