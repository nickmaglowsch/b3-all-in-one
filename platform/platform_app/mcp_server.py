"""The bench as an MCP server (spec/09): POST /mcp, Streamable HTTP, stateless.

Any MCP client — Claude Code, Claude Desktop, another agent — connects with a
token from /bench and gets the tools in mcp_schemas.TOOLS: read the strategy
language, save strategies, run backtests, rank the results. What it saves and
runs is the user's own, so it appears in /bench like anything they did by hand.

The transport is the spec's Streamable HTTP in its simplest legal form: every
POST carries one JSON-RPC message (or a batch) and gets one JSON answer, no SSE
stream and no session id. Nothing here needs server push — a backtest that is
still running is polled with get_backtest — so GET (the optional stream) and
DELETE (session teardown) answer 405, which the spec allows. Hand-rolled over
FastAPI rather than the SDK because the whole surface is four methods and the
hard part — auth, tenancy, the routes the tools reuse — is already this app's.
"""
from __future__ import annotations

import json
import traceback

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from platform_app import subprocs
from platform_app.billing import entitled
from platform_app.config import settings
from platform_app.db import get_async_session
from platform_app.mcp_auth import mcp_principal
from platform_app.mcp_guide import INSTRUCTIONS
from platform_app.mcp_results import ToolError
from platform_app.mcp_schemas import TOOLS
from platform_app.mcp_tools import HANDLERS, Ctx, error_text

mcp_router = APIRouter(tags=["mcp"])

# Newest first; the first is what we answer a client asking for one we lack.
PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
SERVER_INFO = {"name": "quantax-bench", "title": "Quantax Bench", "version": "1.0.0"}
# A spec is a few KB; a batch of them is still far inside this.
MAX_BODY_BYTES = 1024 * 1024


class _RpcError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def _error(mid, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def _content(payload, is_error: bool) -> dict:
    text = payload if isinstance(payload, str) else json.dumps(
        payload, ensure_ascii=False, default=str)
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


async def _initialize(params: dict, ctx: Ctx) -> dict:
    asked = params.get("protocolVersion")
    return {
        "protocolVersion": asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": SERVER_INFO,
        "instructions": INSTRUCTIONS,
    }


async def _empty(params: dict, ctx: Ctx) -> dict:
    return {}


async def _tools_list(params: dict, ctx: Ctx) -> dict:
    return {"tools": TOOLS}


async def _tools_call(params: dict, ctx: Ctx) -> dict:
    """Run one tool. An unknown tool is a protocol error; everything that goes
    wrong *inside* one is a result with isError, which is what the model sees
    and can correct — a protocol error would only reach the client's logs."""
    handler = HANDLERS.get(params.get("name"))
    if handler is None:
        raise _RpcError(-32602, f"unknown tool: {params.get('name')!r}")
    args = params.get("arguments") or {}
    if not isinstance(args, dict):
        raise _RpcError(-32602, "tool arguments must be an object")
    # Per call, not per token: a lapsed subscription stops the token on its own.
    if "bench" not in entitled(ctx.user):
        return _content("402: this account no longer has the bench module", True)
    try:
        return _content(await handler(ctx, args), False)
    except (ToolError, HTTPException) as e:
        # A route can fail holding a transaction (the quota's advisory lock).
        await ctx.session.rollback()
        return _content(error_text(e), True)
    except Exception:
        await ctx.session.rollback()
        return _content(subprocs.report(f"mcp tool {handler.__name__}",
                                        traceback.format_exc()), True)


_METHODS = {
    "initialize": _initialize,
    "ping": _empty,
    "tools/list": _tools_list,
    "tools/call": _tools_call,
    "notifications/initialized": _empty,
    "notifications/cancelled": _empty,
}


async def _handle(msg, ctx: Ctx) -> dict | None:
    """One JSON-RPC message → its response, or None when none is owed (a
    notification, or a client's response to a request we never send)."""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
        return _error(msg.get("id") if isinstance(msg, dict) else None, -32600,
                      "invalid JSON-RPC 2.0 message")
    method, mid, is_note = msg.get("method"), msg.get("id"), "id" not in msg
    if not isinstance(method, str):
        return None
    fn = _METHODS.get(method)
    params = msg.get("params") or {}
    try:
        if fn is None:
            raise _RpcError(-32601, f"method not found: {method}")
        if not isinstance(params, dict):
            raise _RpcError(-32602, "params must be an object")
        result = await fn(params, ctx)
    except _RpcError as e:
        return None if is_note else _error(mid, e.code, e.message)
    return None if is_note else {"jsonrpc": "2.0", "id": mid, "result": result}


def _check_headers(request: Request) -> None:
    # The spec's DNS-rebinding guard. Desktop and CLI clients send no Origin; a
    # browser page may only call from the app's own origins.
    origin = request.headers.get("origin")
    if origin and origin not in settings.origins:
        raise HTTPException(403, "origin not allowed")
    version = request.headers.get("mcp-protocol-version")
    if version and version not in PROTOCOL_VERSIONS:
        raise HTTPException(400, f"unsupported MCP-Protocol-Version: {version}")


@mcp_router.post("/mcp")
async def mcp_post(
    request: Request, session: AsyncSession = Depends(get_async_session),
) -> Response:
    _check_headers(request)
    user, tid = await mcp_principal(request, session)
    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        raise HTTPException(413, "request too large")
    try:
        payload = json.loads(body)
    except ValueError:
        return JSONResponse(_error(None, -32700, "parse error"), status_code=400)
    batch = isinstance(payload, list)
    if batch and not payload:
        return JSONResponse(_error(None, -32600, "empty batch"), status_code=400)
    ctx = Ctx(user, tid, session)
    replies = [r for m in (payload if batch else [payload]) if (r := await _handle(m, ctx))]
    if not replies:
        return Response(status_code=202)
    return JSONResponse(replies if batch else replies[0])


@mcp_router.api_route("/mcp", methods=["GET", "DELETE"])
async def mcp_no_stream() -> Response:
    """No server-initiated stream and no sessions to end: both are optional in
    Streamable HTTP, and 405 is how a server says it offers neither."""
    return Response(status_code=405, headers={"Allow": "POST"})
