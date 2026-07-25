---
name: platform-tenancy-review-checklist
description: Bug-prone areas and review checklist for this repo's multi-tenant model (tenant_id = user.id OR portfolios.id, X-Portfolio-Id header)
metadata:
  type: project
---

A `tenant_id` is either a `user.id` (own book) or a `portfolios.id` (a managed client
book), chosen per-request from the `X-Portfolio-Id` header in
`platform_app/tenancy.py::_acting_tenant`. The tenant columns carry **no FK** (one
column can't reference two tables), so app code is the only isolation boundary.

**Why:** the enterprise tier sells "dados isolados entre clientes". There is no DB-level
backstop and no RLS yet, so every isolation bug is a product-promise bug.

**How to apply — check these on any PR touching tenancy, routes, or the frontend store:**

1. Every route must take its scope from `get_current_tenant` / `require_plan(...)`, never
   from a body or path id. Grep for `tenant_id ==` and confirm the RHS came from the tuple.
2. Endpoints that never *use* `tenant[1]` (billing/*, /portfolios, /me) still depend on
   `get_current_tenant` and therefore still 402/404 on a stale header. That makes paywall
   recovery reachable only because `frontend/lib/api.ts::ACCOUNT_SCOPED` exempts them by
   string prefix. Any new account-scoped route must be added to both sides.
3. `platform_app/tasks.py` takes `tenant_id` as a plain job argument — a second door to a
   tenant scope that bypasses `_acting_tenant`. Check any new caller.
4. The frontend selection lives in one `localStorage` key with an in-tab listener set.
   It is **not** synced across tabs and is read at fetch time, so any component holding
   dirty local state across a book switch writes into the wrong book. Full-replace
   endpoints (`PUT /portfolio/sleeves`, asset-overrides) are the dangerous ones.
5. `DELETE /portfolios/{id}` sweeps every table with a `tenant_id` column via
   `Base.metadata.sorted_tables` (parent-first — wrong direction for deletes if inter-tenant
   FKs ever appear). It does not cancel queued procrastinate jobs.

**Testing a migration by hand** (pytest never runs alembic):
`uv run --frozen python -c "import psycopg; ..."` to create a scratch DB, then
`DATABASE_URL=postgresql+asyncpg://postgres:devpassword@localhost:5432/<db> ENVIRONMENT=development uv run --frozen alembic upgrade head`
and `alembic downgrade -1`. Seed an orphan `tenant_id` first — restoring the dropped
`tenant_id -> user.id` FK fails on any row whose tenant is neither a user nor a live portfolio.

See [[platform-test-baseline]] for the pre-existing failure list.
