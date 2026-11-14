"""The what-if replay as a worker job (POST /whatif queues it; spec/05 Feature 2).

Up to four full backtests of the tenant's real cash flows, on the default
worker beside imports and the bench, instead of inside an API request that held
the page — and the API container's memory — for as long as they took.

The row's life is the backtest's: compare-and-set out of `pending` to start, out
of `running` to finish, and whatever goes wrong in between ends in `error`. A
row that is no longer `running` when the replay ends was deleted (an erasure, an
offboarded book) or closed by the boot sweep; its result is dropped.

worker.py imports this module so the task is registered where it runs.
"""
from __future__ import annotations

import traceback
import uuid

from sqlalchemy import func

from platform_app import routes_whatif, subprocs
from platform_app.db import async_session_maker
from platform_app.jobstatus import WhatIfStatus, transition
from platform_app.tasks import app
from platform_app.whatif_models import WhatIfRun


async def _outcome(session, run: WhatIfRun) -> dict:
    try:
        result = await routes_whatif.replay(session, run.tenant_id, run.params)
    except RuntimeError as e:          # an engine failure, already reported with a ref
        await session.rollback()
        return {"status": WhatIfStatus.ERROR, "error": str(e)}
    except Exception:
        await session.rollback()
        # Logged with a ref, not stored: an arbitrary exception's message here is
        # SQL and parameters, not anything a tenant reads.
        return {"status": WhatIfStatus.ERROR,
                "error": subprocs.report("whatif", traceback.format_exc())}
    return {"status": WhatIfStatus.DONE, "error": None, "result": result}


@app.task(name="run_whatif")
async def run_whatif(run_id: str) -> dict:
    rid = uuid.UUID(run_id)
    async with async_session_maker() as session:
        run = await session.get(WhatIfRun, rid)
        if run is None:
            return {"run_id": run_id, "status": "missing"}
        if not await transition(session, WhatIfRun, WhatIfRun.id == rid,
                                (WhatIfStatus.PENDING,), status=WhatIfStatus.RUNNING):
            return {"run_id": run_id, "status": "skipped"}
        await session.commit()
        outcome = await _outcome(session, run)
        won = await transition(session, WhatIfRun, WhatIfRun.id == rid,
                               (WhatIfStatus.RUNNING,), **outcome, finished_at=func.now())
        await session.commit()
    return {"run_id": run_id, "status": str(outcome["status"]) if won else "abandoned"}
