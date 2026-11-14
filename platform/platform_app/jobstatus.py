"""The `status` column of each job table, and the one way a worker moves it.

Four tables carry a job's life in a plain string column — backtest_runs,
imports, ingest_runs, whatif_runs — and the strings used to be spelled out wherever a route,
a task, the boot sweep or the MCP schema needed one. Nothing tied them together,
so nothing said which moves were legal either: a task wrote `running` over
whatever the row held, and a final write landed on a row a purge had already
deleted, or that maintenance had put back.

So each table gets a StrEnum (the values ARE the wire format: API JSON, the MCP
contract and the CHECK constraint in the migration all read them verbatim) and
the writes that race go through `transition`, a compare-and-set that only moves
a row out of the statuses the caller expects and says whether it did.
"""
from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession


class BacktestStatus(StrEnum):
    """backtest_runs: pending → running → done | error (routes, tasks.run_backtest)."""
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"


class ImportStatus(StrEnum):
    """imports: pending → processing → done | error (routes, tasks.process_imports)."""
    PENDING = "pending"
    PROCESSING = "processing"
    DONE = "done"
    ERROR = "error"


class WhatIfStatus(StrEnum):
    """whatif_runs: pending → running → done | error (routes_whatif, tasks_whatif)."""
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"


class IngestStatus(StrEnum):
    """ingest_runs: running → done | error (tasks_ingest). Born running."""
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"


# Tuples, not sets: they go into IN (...) lists and error messages, where a
# stable order is the difference between a diff and noise.
BACKTEST_INFLIGHT = (BacktestStatus.PENDING, BacktestStatus.RUNNING)
BACKTEST_FINISHED = (BacktestStatus.DONE, BacktestStatus.ERROR)
IMPORT_INFLIGHT = (ImportStatus.PENDING, ImportStatus.PROCESSING)
IMPORT_FINISHED = (ImportStatus.DONE, ImportStatus.ERROR)
INGEST_FINISHED = (IngestStatus.DONE, IngestStatus.ERROR)
WHATIF_INFLIGHT = (WhatIfStatus.PENDING, WhatIfStatus.RUNNING)


def check_sql(statuses: type[StrEnum]) -> str:
    """The CHECK constraint body for a status column over ``statuses``."""
    return "status IN (" + ", ".join(f"'{s.value}'" for s in statuses) + ")"


async def transition(session: AsyncSession, model, where, from_: Iterable[str],
                     **values) -> bool:
    """``UPDATE model SET values WHERE where AND status IN from_``: True if it
    matched a row. In the caller's transaction; the caller commits.

    False means somebody else got there first — the row was deleted (a purge,
    an LGPD erasure), or moved by another writer — and the caller must not
    assume the state it read is still the state on disk.
    """
    res = await session.execute(
        update(model).where(where, model.status.in_(tuple(from_))).values(**values)
        .returning(model.status).execution_options(synchronize_session=False))
    return res.first() is not None
