---
name: platform-test-baseline
description: platform/ pytest has ~18 pre-existing failures on a dev box without the engine venv/market plane — always baseline against main before blaming a PR
metadata:
  type: project
---

`cd platform && uv run --frozen pytest -q` fails ~18 tests + 1 error on a normal dev
box (test_backtest_e2e, test_platform_e2e, test_portfolio_settings, test_sleeves,
test_user_strategies, test_whatif, test_needs_fundamentals). They 500 because the
routes shell into the `engine/` venv, which is not installed locally, and because the
B3 market plane is empty.

**Why:** these are environment failures, not regressions. CI installs the engine; the
`market` pytest marker auto-skips only the subset that checks `_market_plane_loaded()`.

**How to apply:** before reporting a failing suite on a PR, run the same suite on
`main` in a throwaway worktree with `PLATFORM_TEST_DB=<other_name>` and diff the
failure lists. Only new names are the PR's. `PLATFORM_TEST_DB` exists precisely so
two runs don't race on the same database (see tests/conftest.py).

Also: `tests/conftest.py` builds the schema with `Base.metadata.create_all`, **not**
alembic. Migrations are never exercised by pytest — see
[[platform-tenancy-review-checklist]] for how to test one by hand.
