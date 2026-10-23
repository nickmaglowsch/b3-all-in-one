# Feature: the bench as an MCP server (AI-authored strategies)

Status: **shipped** — `POST /mcp`, `/mcp/tokens`, the "Conectar uma IA" card on
`/bench`, migration `e8b3d6a41f27_mcp_tokens`.

## Why

spec/06 made strategies data: a JSON spec over a DSL that cannot execute code,
validated by the engine, backtested by the shared worker. That is exactly the
surface an AI needs to *search* for strategies — propose a spec, validate it,
backtest it, compare it with the gallery, iterate — without a single new piece
of strategy machinery. This feature hands that loop to any MCP client (Claude
Code, Claude Desktop, other agents) and puts the results where the user already
looks: their strategies and runs in `/bench`.

## Shape

```
AI client ──POST /mcp (JSON-RPC, Bearer qtx_mcp_…)──▶ mcp_server
                                                        │ mcp_auth.mcp_principal → (user, tenant_id)
                                                        │ mcp_tools.HANDLERS[name](ctx, args)
                                                        ▼
                         routes.create_user_strategy / create_backtest / _gallery / …
                         (the same functions the bench UI's HTTP routes run)
```

- **Transport** — Streamable HTTP in its minimal legal form: every POST carries
  one JSON-RPC message (or a batch) and gets one JSON answer; notifications get
  `202`; `GET`/`DELETE /mcp` answer `405` (no server push, no sessions). Methods:
  `initialize`, `ping`, `tools/list`, `tools/call`. Protocol versions
  `2024-11-05` … `2025-11-25`; an unknown one is answered with the newest.
  `Origin` is checked against `ALLOWED_ORIGINS` (DNS-rebinding guard).
- **Auth** — `mcp_tokens` rows: a random `qtx_mcp_` token shown once, stored as
  sha256, bound to the book (tenant_id) it was minted in (so `X-Portfolio-Id`
  while minting gives a client-book token). No expiry; `DELETE /mcp/tokens/{id}`
  revokes. Every call re-resolves the owner and re-checks `empresarial` for a
  client book and `bench` per tool call, so a lapsed plan stops the token.
  Max 10 tokens per book. The table carries `tenant_id`, so the LGPD export
  (minus `token_hash`) and erasure sweeps cover it.
- **Tools** (`mcp_schemas.TOOLS`, `mcp_tools.HANDLERS`, kept in sync by a test):
  `get_authoring_guide`, `list_signals`, `list_strategies`, `get_strategy_spec`,
  `list_my_strategies`, `validate_strategy`, `create_strategy`,
  `update_strategy`, `delete_strategy`, `run_backtest`, `get_backtest`,
  `list_backtests`, `get_backtest_curve`. A route's `HTTPException` becomes a
  tool result with `isError: true` (the model reads it and corrects itself);
  only an unknown tool or malformed params are JSON-RPC errors.
- **Provenance** — `user_strategies.origin = 'mcp'` on what an AI creates;
  `/bench` shows an `IA` badge. Runs need no flag: they belong to a strategy.
- **Waiting** — `run_backtest`/`get_backtest` poll the run row (fresh session per
  read, 2 s) for up to `wait_seconds` (≤ 300). The worker and the per-account
  backtest quota are unchanged; a 429 reaches the model as an error to wait on.
- **Guide** — `mcp_guide.GUIDE` is the authoring reference written for the
  request shape (which differs from the YAML for portfolios), plus research
  hygiene: judge after tax, confirm on a second window, report variants tried.

## Changes that came with it

- `strategy_specs.RankStrategyIn` accepts `market`, `regime`,
  `selection.min_hold`, `selection.sticky` and `weighting: adtv`. `regime` was
  being dropped silently by pydantic on every save from the bench form.
- `engine/scripts/validate_spec.py` now rejects unknown DSL functions and
  malformed regime overlays (inputs without `flag`, rules naming undeclared
  inputs) at save time instead of at run time.

## Not done (yet)

- OAuth 2.1 authorization (needed for claude.ai's remote connectors, which do
  not take static headers). The token table is the resource-server half.
- Server push (`GET /mcp` SSE) for run completion — polling covers it.
- A per-token rate limit on the engine-shelling tools (gallery/validate); the
  backtest quota already bounds the expensive path.
