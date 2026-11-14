"""The tools an AI client can call over MCP (spec/09): the bench, as functions.

Every handler is a thin adapter over the route the bench page itself calls —
same validation, same name rules, same backtest quota, same caches — so a
strategy an AI saves is exactly the strategy the form would have saved, and it
shows up in /bench with nothing to sync. The only state of its own is `origin`
on the strategies it creates, which is how /bench can say "IA" next to them.

A handler takes the Ctx and the call's arguments and returns something JSON can
encode; an HTTPException from a route becomes an error result the model reads,
not a protocol failure.
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass

from fastapi import HTTPException
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from platform_app import mcp_results, routes
from platform_app.config import settings
from platform_app.db import async_session_maker
from platform_app.jobstatus import BACKTEST_INFLIGHT
from platform_app.mcp_guide import GUIDE
from platform_app.mcp_results import ToolError
from platform_app.models import BacktestRun, User, UserStrategy
from platform_app.strategy_specs import StrategyIn

_STRATEGY_IN = TypeAdapter(StrategyIn)
_POLL_S = 2.0
_MAX_WAIT_S = 300
ORIGIN = "mcp"


@dataclass
class Ctx:
    user: User
    tid: uuid.UUID
    session: AsyncSession

    @property
    def tenant(self) -> tuple[User, uuid.UUID]:
        return self.user, self.tid


def _parse_spec(spec) -> object:
    if not isinstance(spec, dict):
        raise ToolError("`spec` must be a JSON object — see get_authoring_guide")
    try:
        return _STRATEGY_IN.validate_python(spec)
    except ValidationError as e:
        raise ToolError("invalid spec: " + "; ".join(
            f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors()))


async def _user_rows(ctx: Ctx) -> list[UserStrategy]:
    return list(await ctx.session.scalars(
        select(UserStrategy).where(UserStrategy.tenant_id == ctx.tid).order_by(UserStrategy.name)))


async def _find_user_strategy(ctx: Ctx, ref: str) -> UserStrategy:
    """The tenant's strategy by id or by name — a model remembers names."""
    for r in await _user_rows(ctx):
        if ref in (str(r.id), r.name):
            return r
    raise ToolError(f"no strategy of yours is called or has id {ref!r} (list_my_strategies)")


def _strategy_row(d: dict) -> dict:
    return {k: d.get(k) for k in ("id", "name", "kind", "origin", "spec", "updated_at", "warnings")
            if k in d}


# ── Catalogue ─────────────────────────────────────────────────────────────────

async def get_authoring_guide(ctx: Ctx, args: dict) -> str:
    return GUIDE


async def list_signals(ctx: Ctx, args: dict) -> list:
    return await routes.list_signals()


async def list_strategies(ctx: Ctx, args: dict) -> list:
    source, market = args.get("source", "all"), args.get("market")
    out = []
    for s in await routes._gallery(ctx.session, ctx.tid):
        if source != "all" and s.get("source") != source:
            continue
        if market and s.get("market") != market:
            continue
        row = {k: s.get(k) for k in ("name", "source", "kind", "market",
                                      "needs_fundamentals", "description")}
        if args.get("include_params"):
            row["params"] = [{k: p.get(k) for k in ("name", "type", "default", "min", "max",
                                                    "choices")} for p in s.get("params", [])]
        out.append(row)
    return out


async def get_strategy_spec(ctx: Ctx, args: dict) -> dict:
    name = str(args.get("name", ""))
    for r in await _user_rows(ctx):
        if r.name == name:
            return {"name": r.name, "kind": r.kind, "source": "user", "spec": r.spec}
    return await routes.get_strategy_spec(name, ctx.tenant, ctx.session)


# ── The tenant's strategies ───────────────────────────────────────────────────

async def list_my_strategies(ctx: Ctx, args: dict) -> list:
    return [_strategy_row(routes._user_strategy_dict(r)) for r in await _user_rows(ctx)]


async def validate_strategy(ctx: Ctx, args: dict) -> dict:
    body = _parse_spec(args.get("spec"))
    spec, warnings = await routes._compile_and_validate(body, ctx.session, ctx.tid)
    if any(r.name == body.name.strip() for r in await _user_rows(ctx)):
        warnings.append(f"you already have a strategy named {body.name.strip()!r}: "
                        "create_strategy would fail, update_strategy would replace it")
    return {"ok": True, "compiled_spec": spec, "warnings": warnings}


async def create_strategy(ctx: Ctx, args: dict) -> dict:
    created = await routes.create_user_strategy(_parse_spec(args.get("spec")), ctx.tenant,
                                                ctx.session)
    await ctx.session.execute(update(UserStrategy).where(
        UserStrategy.id == uuid.UUID(created["id"])).values(origin=ORIGIN))
    await ctx.session.commit()
    return _strategy_row({**created, "origin": ORIGIN})


async def update_strategy(ctx: Ctx, args: dict) -> dict:
    row = await _find_user_strategy(ctx, str(args.get("strategy", "")))
    return _strategy_row(await routes.update_user_strategy(
        str(row.id), _parse_spec(args.get("spec")), ctx.tenant, ctx.session))


async def delete_strategy(ctx: Ctx, args: dict) -> dict:
    row = await _find_user_strategy(ctx, str(args.get("strategy", "")))
    name = row.name
    await routes.delete_user_strategy(str(row.id), ctx.tenant, ctx.session)
    return {"deleted": name}


# ── Backtests ─────────────────────────────────────────────────────────────────

def _wait_seconds(args: dict, default: int) -> int:
    try:
        return max(0, min(_MAX_WAIT_S, int(args.get("wait_seconds", default))))
    except (TypeError, ValueError):
        raise ToolError("wait_seconds must be an integer")


async def _wait_for(run_id: str, tid: uuid.UUID, seconds: int) -> dict:
    """The run, re-read until it settles or ``seconds`` pass. A fresh session per
    read: the request's own would hand back its cached row forever."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + seconds
    while True:
        async with async_session_maker() as s:
            run = await s.get(BacktestRun, uuid.UUID(run_id))
            d = routes._run_dict(run) if run is not None and run.tenant_id == tid else None
        if d is None or d["status"] not in BACKTEST_INFLIGHT or loop.time() >= deadline:
            return d
        await asyncio.sleep(_POLL_S)


def _run_summary(d: dict) -> dict:
    out = mcp_results.summarize(d)
    out["bench_url"] = f"{settings.frontend_url}/bench/run?id={d['id']}"
    if out["status"] in BACKTEST_INFLIGHT:
        out["next"] = "still running — call get_backtest with wait_seconds to wait for it"
    return out


async def run_backtest(ctx: Ctx, args: dict) -> dict:
    try:
        req = routes.BacktestRequest(**{k: args[k] for k in ("strategy", "params", "start",
                                                              "end", "freq") if k in args})
    except ValidationError as e:
        raise ToolError(f"invalid backtest request: {e.errors()[0]['msg']}")
    run = await routes.create_backtest(req, ctx.tenant, ctx.session)
    wait = _wait_seconds(args, 90)
    # `or run`: deleted from /bench while we waited — report what was queued.
    return _run_summary((await _wait_for(run["id"], ctx.tid, wait) if wait else None) or run)


async def get_backtest(ctx: Ctx, args: dict) -> dict:
    run_id = str(args.get("run_id", ""))
    d = await routes.get_backtest(run_id, ctx.tenant, ctx.session)
    wait = _wait_seconds(args, 0)
    if wait and d["status"] in BACKTEST_INFLIGHT:
        d = await _wait_for(run_id, ctx.tid, wait) or d
    out = _run_summary(d)
    if args.get("full_metrics"):
        out["metrics"] = d.get("metrics")
    return out


async def list_backtests(ctx: Ctx, args: dict) -> list:
    rows = await routes.list_backtests(limit=500, offset=0, tenant=ctx.tenant,
                                       session=ctx.session)
    return mcp_results.leaderboard(
        [_run_summary(d) for d in rows], strategy=args.get("strategy"),
        status=args.get("status"), sort_by=args.get("sort_by", "created_at"),
        basis=args.get("basis", "aftertax"), limit=args.get("limit", 20))


async def get_backtest_curve(ctx: Ctx, args: dict) -> dict:
    run_id = str(args.get("run_id", ""))
    curves = await routes.get_backtest_curves(run_id, ctx.tenant, ctx.session)
    return mcp_results.curve_digest(curves["series"], args.get("max_points", 60))


HANDLERS = {
    f.__name__: f for f in (
        get_authoring_guide, list_signals, list_strategies, get_strategy_spec,
        list_my_strategies, validate_strategy, create_strategy, update_strategy,
        delete_strategy, run_backtest, get_backtest, list_backtests, get_backtest_curve,
    )
}


def error_text(exc: Exception) -> str:
    """What the model is told when a call fails."""
    if isinstance(exc, HTTPException):
        detail = exc.detail
        if isinstance(detail, dict):
            detail = detail.get("message") or detail.get("error") or str(detail)
        return f"{exc.status_code}: {detail}"
    return str(exc)
