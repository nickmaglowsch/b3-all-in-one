import asyncio
import shutil
import tempfile
import uuid
from pathlib import Path

from procrastinate import App, PsycopgConnector
from sqlalchemy import delete, insert, select

from platform_app import b3run
from platform_app.config import settings
from platform_app.db import async_session_maker
from platform_app.models import CashEvent, CostBasisOverride, Import, Position, Trade

# Postgres-backed queue (spec 01: Redis is cache-only, jobs live in Postgres).
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

        ws = Path(tempfile.mkdtemp(prefix="b3import-"))
        try:
            b3run.seed_workspace(ws)
            for imp in imports:
                shutil.copy(imp.stored_path, ws / "b3_imports" / imp.filename)
            b3run.write_csv(
                ws / "data" / "cost_basis_overrides.csv",
                _OVERRIDE_HEADER, _override_rows(overrides),
            )

            proc = await asyncio.to_thread(b3run.run_module, ws, ["scripts.load_b3"])
            if proc.returncode != 0:
                msg = (proc.stderr or proc.stdout)[-2000:]
                for imp in imports:
                    imp.status = "error"
                    imp.error = msg
                await session.commit()
                return {"tenant_id": tenant_id, "status": "error", "error": msg}

            trades = b3run.read_csv_rows(ws / "data" / "trades.csv")
            events = b3run.read_csv_rows(ws / "data" / "cash_events.csv")
            positions = b3run.read_csv_rows(ws / "data" / "positions.csv")
        finally:
            shutil.rmtree(ws, ignore_errors=True)

        # Full replace per tenant, one transaction. Order preserved via ascending ids.
        for model in (Trade, CashEvent, Position):
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
        await session.commit()
        return {
            "tenant_id": tenant_id, "status": "done",
            "trades": len(trades), "cash_events": len(events), "positions": len(positions),
        }
