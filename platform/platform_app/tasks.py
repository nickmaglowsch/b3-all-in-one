import asyncio
import json
import math
import shutil
import traceback
import uuid
from pathlib import Path

from procrastinate import App, PsycopgConnector
from procrastinate.exceptions import AlreadyEnqueued
from sqlalchemy import delete, func, insert, select

from platform_app import b3run, enginerun, specstore, subprocs
from platform_app.config import settings
from platform_app.db import async_session_maker, xact_lock
from platform_app.jobstatus import BacktestStatus, ImportStatus, transition
from platform_app.models import (
    BacktestRun, CashEvent, CostBasisOverride, Import, Position,
    RendaFixaMark, TaxSummary, Trade,
)

# Postgres-backed queue (spec 01: jobs live in Postgres).
# Constructing the App does not open a connection — the pool opens on app.open_async().
app = App(connector=PsycopgConnector(conninfo=settings.psycopg_dsn))


@app.task(name="ping")
async def ping(name: str = "world") -> str:
    return f"pong: {name}"


_OVERRIDE_HEADER = ["date", "ticker", "kind", "qty", "price", "amount", "note"]


def _override_rows(rows: list[CostBasisOverride]) -> list[list[object]]:
    return [[o.date, o.ticker, o.kind, o.qty, o.price, o.amount, o.note] for o in rows]


def _trade_dict(tid: uuid.UUID, r: dict[str, str]) -> dict:
    return dict(
        tenant_id=tid, date=b3run.to_date(r["date"]), side=r["side"], market=r["market"],
        maturity=r["maturity"], broker=r["broker"], ticker=r["ticker"],
        qty=b3run.to_float(r["qty"]), price=b3run.to_float(r["price"]),
        amount=b3run.to_float(r["amount"]), source_file=r["__source_file"],
        signed_qty=b3run.to_float(r["signed_qty"]),
    )


def _event_dict(tid: uuid.UUID, r: dict[str, str]) -> dict:
    return dict(
        tenant_id=tid, date=b3run.to_date(r["date"]), ticker=r["ticker"],
        event_type=r["event_type"], event_category=r["event_category"],
        is_amortization=b3run.to_bool(r["is_amortization"]),
        qty=b3run.to_float(r["qty"]), price=b3run.to_float(r["price"]),
        amount=b3run.to_float(r["amount"]), broker=r["broker"],
        source_file=r["__source_file"],
    )


def _rf_mark_dict(tid: uuid.UUID, r: dict[str, str]) -> dict:
    return dict(
        tenant_id=tid, ticker=r["ticker"], date=b3run.to_date(r["date"]),
        price=b3run.to_float(r["price"]),
    )


def _position_dict(tid: uuid.UUID, r: dict[str, str]) -> dict:
    return dict(
        tenant_id=tid, ticker=r["ticker"], qty=b3run.to_float(r["qty"]),
        avg_cost=b3run.to_float(r["avg_cost"]), total_cost=b3run.to_float(r["total_cost"]),
        market=r["market"],
    )


def _finite(obj):
    """``obj`` with every NaN/Infinity replaced by None.

    The engine's summary is parsed by json, which reads its bare `NaN` tokens
    (a Sharpe over a flat curve, a drawdown over a zero start) as floats — and
    Postgres JSONB rejects them, so the commit that stores the metrics would be
    the thing that fails, after the run itself succeeded.
    """
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _finite(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_finite(v) for v in obj]
    return obj


def run_dir(run_id: uuid.UUID | str) -> Path:
    """Where the engine writes a run's artifacts. Absolute: the engine
    subprocess runs with cwd=engine, so a relative artifacts_dir would land
    under engine/, not where the API reads it."""
    return Path(settings.artifacts_dir).resolve() / str(run_id)


async def _backtest_outcome(session, run: BacktestRun, run_id: str) -> dict:
    """Run the engine for ``run``; the columns to store, success or not."""
    out_dir = run_dir(run_id)
    # Point the registry at the tenant's user specs (empty for zero-spec tenants,
    # so this is a no-op identical to today for built-in strategies).
    extra_env = await specstore.user_specs_env(session, run.tenant_id)
    # Hand the connection back for the minutes the engine runs.
    await session.commit()
    # The config goes on stdin: `params` is the tenant's free-form dict, and as
    # one argv element anything past 128 KB failed the exec itself.
    proc = await asyncio.to_thread(
        enginerun.run_script, "run_backtest_json.py", [run.strategy, str(out_dir), "-"],
        extra_env, stdin=json.dumps(run.params or {}),
    )
    if proc.returncode != 0:
        return {"status": BacktestStatus.ERROR, "error": (proc.stderr or proc.stdout)[-2000:]}
    try:
        summary = subprocs.last_json(proc.stdout, strict=True)
    except ValueError as e:
        return {"status": BacktestStatus.ERROR,
                "error": f"{e}: {(proc.stderr or proc.stdout)[-1000:]}"}
    return {"status": BacktestStatus.DONE, "error": None, "metrics": _finite(summary),
            "artifacts_path": str(out_dir)}


async def _finish_run(session, rid: uuid.UUID, outcome: dict) -> str:
    """Store ``outcome`` on the row if it is still `running`; the status stored.

    finished_at on the database's clock, the one created_at and the boot sweep
    use. A row that is not `running` any more was deleted under us — the book
    offboarded, the account erased (routes_portfolios.purge_tenant_rows) — while
    the engine ran, or closed by someone else. The purge already swept
    ARTIFACTS_DIR/{id}, and the engine has just written it again: an erased
    tenant's NAV curve on disk with no row pointing at it. So it goes, and the
    outcome is dropped.
    """
    won = await transition(session, BacktestRun, BacktestRun.id == rid,
                           (BacktestStatus.RUNNING,), **outcome, finished_at=func.now())
    await session.commit()
    if won:
        return outcome["status"]
    await asyncio.to_thread(shutil.rmtree, run_dir(rid), True)
    return "abandoned"


@app.task(name="run_backtest")
async def run_backtest(run_id: str) -> dict:
    """Shell into the engine venv to run one backtest. Writes artifacts under
    ARTIFACTS_DIR/{run_id}/ and stores the metrics summary on the row.

    Whatever goes wrong in here ends with the row in `error`. A row left
    `running` is never finished by anything, the retention routes refuse to
    delete it, and it counts against BACKTEST_QUOTA forever — three of them and
    the whole account is locked out of the bench. A dead *process* is the
    startup sweep's to clean up; every failure this process survives is ours.

    Both writes are compare-and-set. Only a `pending` row starts: a second job
    for the same run, or one for a row already failed as lost (worker's boot
    sweep, routes' failed defer), stands down without touching it. Only a
    `running` row finishes (_finish_run).
    """
    rid = uuid.UUID(run_id)
    async with async_session_maker() as session:
        run = await session.get(BacktestRun, rid)
        if run is None:
            return {"run_id": run_id, "status": "missing"}
        if not await transition(session, BacktestRun, BacktestRun.id == rid,
                                (BacktestStatus.PENDING,), status=BacktestStatus.RUNNING):
            return {"run_id": run_id, "status": "skipped"}
        await session.commit()
        try:
            outcome = await _backtest_outcome(session, run, run_id)
            status = await _finish_run(session, rid, outcome)
        except Exception:
            await session.rollback()
            # Logged with a ref, not stored: the message of an arbitrary
            # exception here is SQL and parameters, not anything a tenant reads.
            outcome = {"status": BacktestStatus.ERROR,
                       "error": subprocs.report("backtest", traceback.format_exc())}
            status = await _finish_run(session, rid, outcome)
    if status == BacktestStatus.ERROR:
        return {"run_id": run_id, "status": "error", "error": outcome["error"]}
    return {"run_id": run_id, "status": str(status)}


def _load_book(tid: uuid.UUID, extracts: list[tuple[str, str]], override_rows):
    """Workspace build, load_b3, and reading its CSVs back — all in one thread hop.

    Only the subprocess used to be offloaded. Procrastinate runs async tasks on
    the worker's event loop with two slots, so the copytree, the extract copies,
    the CSV writes, the reads and the rmtree blocked the other slot and the
    worker's own bookkeeping for the duration.
    """
    with b3run.workspace(tid, "b3import-") as ws:
        for stored_path, filename in extracts:
            shutil.copy(stored_path, ws / "b3_imports" / filename)
        b3run.write_csv(ws / "data" / "cost_basis_overrides.csv", _OVERRIDE_HEADER, override_rows)
        proc = b3run.run_module(ws, ["scripts.load_b3"])
        if proc.returncode != 0:
            return proc, None, None, None, None
        # rf_marks.csv is written unconditionally by a load_b3 new enough to know
        # about posição reports, but the workspace is seeded from whatever taxcore
        # is on disk — read defensively so an older one is a book without marks,
        # not a failed import.
        marks_csv = ws / "data" / "rf_marks.csv"
        return (
            proc,
            b3run.read_csv_rows(ws / "data" / "trades.csv"),
            b3run.read_csv_rows(ws / "data" / "cash_events.csv"),
            b3run.read_csv_rows(ws / "data" / "positions.csv"),
            b3run.read_csv_rows(marks_csv) if marks_csv.exists() else [],
        )


@app.task(name="process_imports")
async def process_imports(tenant_id: str) -> dict:
    """Run taxcore's load_b3 over ALL of a tenant's uploaded extracts, then full-replace
    that tenant's trades/cash_events/positions from the produced CSVs. Deterministic
    full rebuild → delete+insert in one transaction is correct and idempotent.

    Whatever goes wrong once the imports are `processing` ends with them in
    `error`, as run_backtest does for its row. Nothing used to: an exception past
    the first commit left every one of the tenant's imports `processing` for
    good — what migration 9d4e2b7c1f05 describes happening in production, behind
    a page that polls while anything is processing. `superseded` and `abandoned`
    leave them `processing` on purpose and return rather than raise, so this
    never touches them; and the next job sets every import back to `processing`,
    so an `error` here is never the last word on a book whose next upload works.
    """
    tid = uuid.UUID(tenant_id)
    async with async_session_maker() as session:
        try:
            return await _process_imports(session, tid)
        except Exception:
            await session.rollback()
            # A ref, not the message: like run_backtest's, it is SQL and paths.
            await transition(
                session, Import, Import.tenant_id == tid, (ImportStatus.PROCESSING,),
                status=ImportStatus.ERROR,
                error=subprocs.report("importação", traceback.format_exc()))
            await session.commit()
            raise


async def _process_imports(session, tid: uuid.UUID) -> dict:
    tenant_id = str(tid)
    imports = (await session.scalars(select(Import).where(Import.tenant_id == tid))).all()
    if not imports:
        return {"tenant_id": tenant_id, "status": "noop", "imports": 0}
    for imp in imports:
        imp.status = ImportStatus.PROCESSING
    overrides = (
        await session.scalars(
            select(CostBasisOverride)
            .where(CostBasisOverride.tenant_id == tid)
            .order_by(CostBasisOverride.id)
        )
    ).all()
    await session.commit()

    proc, trades, events, positions, rf_marks = await asyncio.to_thread(
        _load_book, tid, [(imp.stored_path, imp.filename) for imp in imports],
        _override_rows(overrides))

    # Two uploads in quick succession are two jobs over two snapshots, and
    # the worker's two slots run them in parallel. Unserialised, the older
    # snapshot could finish last and replace the book without the newer
    # extract, or the two delete+inserts could interleave and keep both
    # copies of every trade. So: one writer per tenant at a time, and a
    # writer whose snapshot is no longer the whole set stands down — the job
    # the newer upload deferred covers everything this one read, and more.
    await xact_lock(session, "imports", tid)
    # load_b3 runs for minutes with the extracts already copied to a temp dir,
    # so the book can be offboarded underneath it. Writing anyway would restore
    # a deleted client's trades under a tenant_id nothing references — invisible
    # to every route, but still their data, after a UI that promised deletion.
    # DELETE /portfolios takes the imports with it, so their absence is the signal.
    live = set(await session.scalars(select(Import.id).where(Import.tenant_id == tid)))
    if not live:
        return {"tenant_id": tenant_id, "status": "abandoned", "imports": 0}
    if live - {imp.id for imp in imports}:
        return {"tenant_id": tenant_id, "status": "superseded"}
    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout)[-2000:]
        for imp in imports:
            imp.status = ImportStatus.ERROR
            imp.error = msg
        await session.commit()
        return {"tenant_id": tenant_id, "status": "error", "error": msg}

    sniffed = _sniffed_kinds(proc.stdout)
    await _replace_book(session, tid, trades, events, positions, rf_marks)
    for imp in imports:
        imp.status = ImportStatus.DONE
        imp.error = None
        imp.parser_version = b3run.PARSER_VERSION
        # `kind` was guessed from the filename at upload (routes._detect_kind)
        # while load_b3 decides it by sniffing the columns. Only the sniffed
        # answer is right, and it matters beyond cosmetics: the IRPF route
        # selects `kind == "movimentacao"` to pick which extract to copy for
        # the renda fixa section, so a renamed download silently emptied it
        # while every trade still parsed. load_b3 already reports what it
        # read; this just stops throwing it away.
        imp.kind = sniffed.get(imp.filename, imp.kind)
    await session.commit()
    return {
        "tenant_id": tenant_id, "status": "done",
        "trades": len(trades), "cash_events": len(events), "positions": len(positions),
        "rf_marks": len(rf_marks),
    }


def imports_lock(tid: uuid.UUID) -> str:
    """The queueing_lock and lock every process_imports defer must carry."""
    return f"imports:{tid}"


async def defer_process_imports(tid: uuid.UUID) -> None:
    """At most one waiting process_imports per tenant. load_b3 is a full rebuild,
    and a waiting job reads every stored extract when it starts, a later upload's
    included, so a second one is pure waste. Unlocked, one tenant could queue a
    rebuild per request and hold both worker slots every tenant's imports and
    backtests share. `lock` keeps a job deferred while one runs from taking the
    other slot alongside it."""
    key = imports_lock(tid)
    try:
        await process_imports.configure(queueing_lock=key, lock=key).defer_async(
            tenant_id=str(tid))
    except AlreadyEnqueued:
        pass  # the waiting job will read this upload too


async def defer_or_fail(session, rows: list, label: str, defer, pending: str,
                        **values) -> None:
    """Defer the job for ``rows``, which the caller has just committed
    ``pending``; if that raises, fail them (``values``, from ``pending`` only)
    rather than leave them waiting on a job that does not exist, and refresh
    them so the response says so.

    The rows have to be committed first — a job that starts before its row is
    visible finds nothing — so a defer that raises (the queue's pool will not
    open, the INSERT errors) used to leave a row nothing would ever move: a
    spinner on the page and, for a backtest, a slot of the tenant's quota.
    Compare-and-set, because a defer can raise after its INSERT landed, and a
    job that already picked the row up owns it.
    """
    try:
        async with app.open_async():
            await defer()
        return
    except Exception:
        error = subprocs.report(label, traceback.format_exc())
    model = type(rows[0])
    await session.rollback()
    await transition(session, model, model.id.in_([r.id for r in rows]), (pending,),
                     error=error, **values)
    await session.commit()
    for row in rows:
        await session.refresh(row)


def _sniffed_kinds(stdout: str) -> dict[str, str]:
    """``{filename: kind}`` from the JSON object load_b3 prints, naming every
    file it read and the kind it sniffed. Best-effort: a parse failure must not
    fail an import whose CSVs already came back fine, it just leaves the
    uploaded guess in place."""
    return {
        f["name"]: f["kind"]
        for f in (subprocs.last_json(stdout).get("files_loaded") or [])
        if isinstance(f, dict) and f.get("name") and f.get("kind")
    }


async def _replace_book(session, tid: uuid.UUID, trades, events, positions, rf_marks) -> None:
    """Full replace per tenant, in the caller's transaction. Order preserved via
    ascending ids.

    TaxSummary goes with them: it is derived from exactly these rows, so leaving
    it would serve an isenção headroom and an IR devido computed against a book
    that no longer exists. Dropping it rather than recomputing here keeps this
    task's cost bounded — the next reader sees "nunca apurado" and asks for it.
    """
    for model in (Trade, CashEvent, Position, RendaFixaMark, TaxSummary):
        await session.execute(delete(model).where(model.tenant_id == tid))
    for model, rows, to_dict in (
        (Trade, trades, _trade_dict), (CashEvent, events, _event_dict),
        (Position, positions, _position_dict), (RendaFixaMark, rf_marks, _rf_mark_dict),
    ):
        if rows:
            await session.execute(insert(model), [to_dict(tid, r) for r in rows])
