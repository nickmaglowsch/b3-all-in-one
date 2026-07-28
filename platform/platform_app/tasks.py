import asyncio
import datetime
import json
import shutil
import tempfile
import uuid
from pathlib import Path

from procrastinate import App, PsycopgConnector
from sqlalchemy import delete, func, insert, select

from platform_app import b3run, enginerun, specstore
from platform_app.config import settings
from platform_app.db import async_session_maker
from platform_app.models import (
    BacktestRun, CashEvent, CostBasisOverride, Import, IngestRun, Position,
    TaxSummary, Trade,
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


def _position_dict(tid: uuid.UUID, r: dict[str, str]) -> dict:
    return dict(
        tenant_id=tid, ticker=r["ticker"], qty=b3run.to_float(r["qty"]),
        avg_cost=b3run.to_float(r["avg_cost"]), total_cost=b3run.to_float(r["total_cost"]),
        market=r["market"],
    )


@app.task(name="run_backtest")
async def run_backtest(run_id: str) -> dict:
    """Shell into the engine venv to run one backtest. Writes artifacts under
    ARTIFACTS_DIR/{run_id}/ and stores the metrics summary on the row."""
    rid = uuid.UUID(run_id)
    async with async_session_maker() as session:
        run = await session.get(BacktestRun, rid)
        if run is None:
            return {"run_id": run_id, "status": "missing"}
        run.status = "running"
        await session.commit()

        # Absolute: the engine subprocess runs with cwd=engine, so a relative
        # artifacts_dir would land under engine/, not where the API reads it.
        out_dir = Path(settings.artifacts_dir).resolve() / run_id
        args = [run.strategy, str(out_dir), json.dumps(run.params or {})]
        # Point the registry at the tenant's user specs (empty for zero-spec tenants,
        # so this is a no-op identical to today for built-in strategies).
        extra_env = await specstore.user_specs_env(session, run.tenant_id)
        proc = await asyncio.to_thread(
            enginerun.run_script, "run_backtest_json.py", args, extra_env
        )
        run.finished_at = datetime.datetime.now(datetime.timezone.utc)
        if proc.returncode != 0:
            run.status = "error"
            run.error = (proc.stderr or proc.stdout)[-2000:]
            await session.commit()
            return {"run_id": run_id, "status": "error", "error": run.error}

        try:
            summary = enginerun._last_json(proc.stdout)
        except ValueError as e:
            run.status = "error"
            run.error = f"{e}: {(proc.stderr or proc.stdout)[-1000:]}"
            await session.commit()
            return {"run_id": run_id, "status": "error", "error": run.error}

        run.status = "done"
        run.error = None
        run.metrics = summary
        run.artifacts_path = str(out_dir)
        await session.commit()
        return {"run_id": run_id, "status": "done"}


# ── Market-data ingestion (scheduled) ────────────────────────────────────────
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
            ws = Path(tempfile.mkdtemp(prefix="b3import-", dir=b3run.workspace_root(tid)))
            try:
                b3run.seed_workspace(ws)
                for stored_path, filename in extracts:
                    shutil.copy(stored_path, ws / "b3_imports" / filename)
                b3run.write_csv(
                    ws / "data" / "cost_basis_overrides.csv",
                    _OVERRIDE_HEADER, override_rows,
                )
                proc = b3run.run_module(ws, ["scripts.load_b3"])
                if proc.returncode != 0:
                    return proc, None, None, None
                return (
                    proc,
                    b3run.read_csv_rows(ws / "data" / "trades.csv"),
                    b3run.read_csv_rows(ws / "data" / "cash_events.csv"),
                    b3run.read_csv_rows(ws / "data" / "positions.csv"),
                )
            finally:
                shutil.rmtree(ws, ignore_errors=True)

        proc, trades, events, positions = await asyncio.to_thread(_work)
        if proc.returncode != 0:
            msg = (proc.stderr or proc.stdout)[-2000:]
            for imp in imports:
                imp.status = "error"
                imp.error = msg
            await session.commit()
            return {"tenant_id": tenant_id, "status": "error", "error": msg}

        # load_b3 runs for minutes with the extracts already copied to a temp dir,
        # so the book can be offboarded underneath it. Writing anyway would restore
        # a deleted client's trades under a tenant_id nothing references — invisible
        # to every route, but still their data, after a UI that promised deletion.
        # DELETE /portfolios takes the imports with it, so their absence is the signal.
        still_live = await session.scalar(
            select(func.count()).select_from(Import).where(Import.tenant_id == tid)
        )
        if not still_live:
            return {"tenant_id": tenant_id, "status": "abandoned", "imports": 0}

        # load_b3 prints one JSON object naming every file it read and the kind it
        # sniffed. Best-effort: a parse failure must not fail an import whose CSVs
        # already came back fine, it just leaves the uploaded guess in place.
        sniffed: dict[str, str] = {}
        try:
            for f in (json.loads(proc.stdout).get("files_loaded") or []):
                if f.get("name") and f.get("kind"):
                    sniffed[f["name"]] = f["kind"]
        except (ValueError, AttributeError):
            pass

        # Full replace per tenant, one transaction. Order preserved via ascending ids.
        # TaxSummary goes with them: it is derived from exactly these rows, so
        # leaving it would serve an isenção headroom and an IR devido computed
        # against a book that no longer exists. Dropping it rather than
        # recomputing here keeps this task's cost bounded — the next reader sees
        # "nunca apurado" and asks for it.
        for model in (Trade, CashEvent, Position, TaxSummary):
            await session.execute(delete(model).where(model.tenant_id == tid))
        if trades:
            await session.execute(insert(Trade), [_trade_dict(tid, r) for r in trades])
        if events:
            await session.execute(insert(CashEvent), [_event_dict(tid, r) for r in events])
        if positions:
            await session.execute(
                insert(Position), [_position_dict(tid, r) for r in positions]
            )
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
        }
