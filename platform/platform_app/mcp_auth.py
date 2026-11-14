"""Access tokens for the MCP endpoint (spec/09), and resolving one to a book.

The session JWT is the wrong credential to hand an AI client: it lives twelve
hours, renews only through the SPA, and nothing can revoke it. An agent exploring
strategies overnight needs something that does not expire mid-run, and a token
pasted into a config file needs to be killable on its own without logging the
person out. So: a random token, shown once, stored as a sha256, bound to the book
it was created in, revoked by deleting its row.

    GET    /mcp/tokens        the book's tokens (never the secret)
    POST   /mcp/tokens        mint one; the response is the only time it is shown
    DELETE /mcp/tokens/{id}   revoke

Minting is gated on `bench` like everything the token can then do. The gate is
re-checked on every MCP call too (mcp_server), so a lapsed subscription stops the
token without anyone revoking it.
"""
from __future__ import annotations

import datetime
import hashlib
import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from platform_app.billing import EMPRESARIAL, entitled, gate_detail, needs_bench
from platform_app.db import get_async_session
from platform_app.mcp_models import McpToken
from platform_app.models import Portfolio, User
from platform_app.tenancy import get_owned

mcp_tokens_router = APIRouter(prefix="/mcp/tokens", tags=["mcp"])

# Recognisable in a leaked config file or a secret scanner, and lets the endpoint
# refuse a session JWT without a database read.
TOKEN_PREFIX = "qtx_mcp_"
# Per book. A token per client app is the expected use; dozens is a script
# minting them in a loop.
MAX_TOKENS = 10
# last_used_at is for the person deciding which token to revoke, not an audit
# log: once a minute is plenty, and spares a write on every tool call.
_TOUCH_EVERY = datetime.timedelta(minutes=1)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


class TokenIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)


def _token_dict(t: McpToken) -> dict:
    return {
        "id": str(t.id), "name": t.name, "prefix": t.prefix,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "last_used_at": t.last_used_at.isoformat() if t.last_used_at else None,
    }


@mcp_tokens_router.get("")
async def list_tokens(
    tenant=Depends(needs_bench),
    session: AsyncSession = Depends(get_async_session),
) -> list[dict]:
    _user, tid = tenant
    rows = (await session.scalars(
        select(McpToken).where(McpToken.tenant_id == tid).order_by(McpToken.created_at)
    )).all()
    return [_token_dict(t) for t in rows]


@mcp_tokens_router.post("")
async def create_token(
    body: TokenIn,
    tenant=Depends(needs_bench),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    _user, tid = tenant
    have = await session.scalar(
        select(func.count()).select_from(McpToken).where(McpToken.tenant_id == tid))
    if have >= MAX_TOKENS:
        raise HTTPException(409, f"limite de {MAX_TOKENS} tokens — revogue um antes")
    raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
    row = McpToken(tenant_id=tid, name=body.name.strip(), token_hash=hash_token(raw),
                   prefix=raw[:len(TOKEN_PREFIX) + 4])
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return {**_token_dict(row), "token": raw}


@mcp_tokens_router.delete("/{token_id}")
async def revoke_token(
    token_id: str,
    tenant=Depends(needs_bench),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    _user, tid = tenant
    row = await get_owned(session, McpToken, token_id, tid, "token não encontrado")
    await session.delete(row)
    await session.commit()
    return {"deleted": token_id}


def _unauthorized() -> HTTPException:
    return HTTPException(
        401, "token MCP ausente, inválido ou revogado — gere um em /bench",
        headers={"WWW-Authenticate": 'Bearer realm="mcp"'})


async def mcp_principal(request: Request, session: AsyncSession) -> tuple[User, uuid.UUID]:
    """``(user, tenant_id)`` for the request's bearer token, else 401.

    The same pair get_current_tenant yields, so the routes the tools call need no
    second notion of who is asking. A token minted in a client book still answers
    only while the owner holds `empresarial` — the same rule tenancy applies to
    the X-Portfolio-Id header, re-checked here because a token outlives a plan.
    """
    header = request.headers.get("Authorization", "")
    raw = header[7:].strip() if header[:7].lower() == "bearer " else ""
    if not raw.startswith(TOKEN_PREFIX):
        raise _unauthorized()
    row = await session.scalar(select(McpToken).where(McpToken.token_hash == hash_token(raw)))
    if row is None:
        raise _unauthorized()
    owner_id = await session.scalar(
        select(Portfolio.owner_id).where(Portfolio.id == row.tenant_id)) or row.tenant_id
    user = await session.get(User, owner_id)
    if user is None or not user.is_active:
        raise _unauthorized()
    if owner_id != row.tenant_id and EMPRESARIAL not in (have := entitled(user)):
        raise HTTPException(402, detail=gate_detail(EMPRESARIAL, have))
    now = datetime.datetime.now(datetime.timezone.utc)
    if row.last_used_at is None or now - row.last_used_at > _TOUCH_EVERY:
        row.last_used_at = now
        await session.commit()
    return user, row.tenant_id
