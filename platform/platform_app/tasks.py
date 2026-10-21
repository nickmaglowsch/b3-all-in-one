import asyncio
import datetime
import json
import math
import shutil
import traceback
import uuid
from pathlib import Path

from procrastinate import App, PsycopgConnector
from sqlalchemy import delete, insert, select, update

from platform_app import b3run, enginerun, specstore, subprocs
from platform_app.config import settings
from platform_app.db import async_session_maker, xact_lock
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


async def _backtest_outcome(session, run: BacktestRun, run_id: str) -> dict:
    """Run the engine for ``run``; the columns to store, success or not."""
    # Absolute: the engine subprocess runs with cwd=engine, so a relative
    # artifacts_dir would land under engine/, not where the API reads it.
    out_dir = Path(settings.artifacts_dir).resolve() / run_id
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
        return {"status": "error", "error": (proc.stderr or proc.stdout)[-2000:]}
    try:
        summary = subprocs.last_json(proc.stdout, strict=True)
    except ValueError as e:
        return {"status": "error", "error": f"{e}: {(proc.stderr or proc.stdout)[-1000:]}"}
    return {"status": "done", "error": None, "metrics": _finite(summary),
            "artifacts_path": str(out_dir)}


@app.task(name="run_backtest")
async def run_backtest(run_id: str) -> dict:
    """Shell into the engine venv to run one backtest. Writes artifacts under
    ARTIFACTS_DIR/{run_id}/ and stores the metrics summary on the row.

    Whatever goes wrong in here ends with the row in `error`. A row left
    `running` is never finished by anything, the retention routes refuse to
    delete it, and it counts against BACKTEST_QUOTA forever — three of them and
    the whole account is locked out of the bench. A dead *process* is the
    startup sweep's to clean up; every failure this process survives is ours.
    """
    rid = uuid.UUID(run_id)
    async with async_session_maker() as session:
        run = await session.get(BacktestRun, rid)
        if run is None:
            return {"run_id": run_id, "status": "missing"}
        run.status = "running"
        await session.commit()
        try:
            outcome = await _backtest_outcome(session, run, run_id)
            for column, value in outcome.items():
                setattr(run, column, value)
            run.finished_at = datetime.datetime.now(datetime.timezone.utc)
            await session.commit()
        except Exception:
            await session.rollback()
            # Logged with a ref, not stored: the message of an arbitrary
            # exception here is SQL and parameters, not anything a tenant reads.
            outcome = {"status": "error",
                       "error": subprocs.report("backtest", traceback.format_exc())}
            # A statement, not the instance: after the rollback `run` is
            # expired, and reloading it is one more thing that can fail here.
            await session.execute(
                update(BacktestRun).where(BacktestRun.id == rid)
                .values(**outcome, finished_at=datetime.datetime.now(datetime.timezone.utc))
                .execution_options(synchronize_session=False)
            )
            await session.commit()
    if outcome["status"] == "error":
        return {"run_id": run_id, "status": "error", "error": outcome["error"]}
    return {"run_id": run_id, "status": "done"}


@app.task(name="process_imports")
async def process_imports(tenant_id: str) -> dict:
    """Run taxcore's load_b3 over ALL of a tenant's uploaded extracts, then full-replace
    that tenant's trades/cash_events/positions from the produced CSVs. Deterministic
    full rebuild → delete+insert in one transaction is correct and idempotent.
    """
    tid = uuid.UUID(tenant_id)
    async with async_session_maker() as session:
        imports = (
            await session.scalars(select(Import).where(Import.tenant_id == tid))
        ).all()
        if not imports:
            return {"tenant_id": tenant_id, "status": "noop", "imports": 0}
        for imp in imports:
            imp.status = "processing"
        overrides = (
            await session.scalars(
                select(CostBasisOverride)
                .where(CostBasisOverride.tenant_id == tid)
                .order_by(CostBasisOverride.id)
            )
        ).all()
        await session.commit()

        extracts = [(imp.stored_path, imp.filename) for imp in imports]
        override_rows = _override_rows(overrides)

        def _work():
            """Workspace build, load_b3, and reading its three CSVs back — all in
            one thread hop.

            Only the subprocess used to be offloaded. Procrastinate runs async
            tasks on the worker's event loop with two slots, so the copytree, the
            extract copies, the CSV writes, the reads and the rmtree blocked the
            other slot and the worker's own bookkeeping for the duration.
            """
            with b3run.workspace(tid, "b3import-") as ws:
                for stored_path, filename in extracts:
                    shutil.copy(stored_path, ws / "b3_imports" / filename)
                b3run.write_csv(
                    ws / "data" / "cost_basis_overrides.csv",
                    _OVERRIDE_HEADER, override_rows,
                )
                proc = b3run.run_module(ws, ["scripts.load_b3"])
                if proc.returncode != 0:
                    return proc, None, None, None, None
                # rf_marks.csv is written unconditionally by a load_b3 new enough
                # to know about posição reports, but the workspace is seeded from
                # whatever taxcore is on disk — read defensively so an older one
                # is a book without marks, not a failed import.
                marks_csv = ws / "data" / "rf_marks.csv"
                return (
                    proc,
                    b3run.read_csv_rows(ws / "data" / "trades.csv"),
                    b3run.read_csv_rows(ws / "data" / "cash_events.csv"),
                    b3run.read_csv_rows(ws / "data" / "positions.csv"),
                    b3run.read_csv_rows(marks_csv) if marks_csv.exists() else [],
                )

        proc, trades, events, positions, rf_marks = await asyncio.to_thread(_work)

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
                imp.status = "error"
                imp.error = msg
            await session.commit()
            return {"tenant_id": tenant_id, "status": "error", "error": msg}

        sniffed = _sniffed_kinds(proc.stdout)
        await _replace_book(session, tid, trades, events, positions, rf_marks)
        for imp in imports:
            imp.status = "done"
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
