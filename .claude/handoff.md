# Handoff — repository-quality-gates

## Goal
Add every quality gate Uncle Bob calls for in an AI-written repo: unit + Gherkin
tests, mutation testing, coverage, cyclomatic complexity, dependency analysis,
module sizing. All six in CI. PR is open: **#26**
(https://github.com/nickmaglowsch/b3-all-in-one/pull/26), branch
`repository-quality-gates`, 2 commits pushed (`756ab03`, `9a7fcd7`).

## Done and verified
- **Gherkin**: `taxcore/tests/features/apuracao_mensal.feature` (pt-BR, `# language: pt`)
  + `taxcore/tests/test_apuracao_bdd.py`, pytest-bdd 8.1. 6 scenarios pass.
- **Coverage floors** in `ci.yml`: platform 70, engine 60, taxcore 20. Measured
  ~77 (sysmon) / 62 / 22. All clear locally and in CI.
- **Complexity + module size + dependency contracts**: `scripts/quality_gates.py`
  vs `scripts/quality_baseline.json` (91 entries: 63 complexity, 28 size).
  Ratchet verified in *both* directions (perturbed a Python entry and a frontend
  entry → FAIL; planted a forbidden import → FAIL). Exit 0 clean.
- **Mutation**: mutmut 3 over `taxcore/scripts/apuracao.py`, 85 mutants @ ~200/s,
  14 survive (some equivalent). Weekly cron + `workflow_dispatch` only.
- **Frontend**: `npm run lint` wired into CI (was already green — the "pre-existing
  react-hooks errors" comment was stale). eslint `complexity` max 10 as a *warning*;
  its 18 offenders live in the ratchet.
- **Latency/coverage fix** (commit 2): `COVERAGE_CORE=sysmon` on the platform job +
  `MAX_ACCEPTABLE_STALL` doubles under coverage.
- Suites green: taxcore 66 + 4 golden, engine 564, frontend 11 + lint, gates 0.

## Next
Only `Platform API tests` is still pending on the re-run of PR #26 — everything
else is green (taxcore, engine, frontend, quality-gates, compose-config pass;
mutation correctly skips on PRs). Check with `gh pr checks 26`.

- If it passes: the PR is complete, nothing further to build.
- If it fails: fetch the real assertion first
  (`gh run view <id> --log-failed`, retry — `gh` intermittently 401s).
  Do **not** assume it is the latency issue again; commit `9a7fcd7` addressed
  that cause and was verified locally under both coverage backends.

## Files
- `scripts/quality_gates.py` — the whole structural gate: ruff C901 + eslint
  complexity, module size, 5 AST import contracts. `--update` re-records.
- `scripts/quality_baseline.json` — the ratchet. Regenerate via `--update` only
  after a genuine improvement, and only with `frontend/node_modules` present.
- `taxcore/scripts/apuracao.py` — **new**, extracted from `irpf_2026.py`. The
  apuração rules (isenção R$20k, alíquotas, compensação). Re-exported from
  `irpf_2026` so existing imports resolve.
- `taxcore/pyproject.toml` — `[tool.mutmut]`, pytest `norecursedirs = ["mutants", …]`,
  pytest-bdd warning filter.
- `platform/tests/test_event_loop_blocking.py` — `_under_coverage()` + doubled bound.
- `.github/workflows/ci.yml` — 7 jobs; `quality-gates` and `mutation` are new.
- `frontend/eslint.config.mjs` — complexity rule (warn).
- `README.md` — "Quality gates" section documents every threshold honestly.

## Decisions (do not relitigate)
- **Ratchet, not wall**, for complexity and module size. The repo has real debt;
  a hard limit would have been disabled on the first PR that hit it. Contracts get
  no baseline — all 5 hold today, so any break is a regression.
- **Coverage floors are today's numbers, not 100.** Reaching a literal 100 gate
  would mean excluding most of `taxcore/scripts`, which is exactly the gap.
  Do not "fix" this by adding omit rules.
- **`apuracao.py` extraction was forced by the mutation gate.** mutmut writes every
  mutant of a module into one file; pointed at the 1100-line `irpf_2026.py` it made a
  42 MB / 745k-line module that never finished importing. Keep mutation targets small;
  widen `source_paths` one small module at a time, never at a directory.
- **mutmut 3, not 2.** mutmut 2 mutates the real file in place (it left a live mutant
  in `irpf_2026.py` once — `EVENTS = None`; restored via git). mutmut 2 is also broken
  on Python 3.13 (pony ORM `Mutant.select()`).
- **Mutation is advisory** (`|| true`). Drop it once the 14 survivors are triaged.
- **eslint complexity is `warn`, not `error`**, so one mechanism (the ratchet) owns
  complexity for both languages.
- **Gherkin lives in taxcore, not platform.** These are pure DataFrame functions; a
  scenario over the async DB-bound routes would be fixture plumbing hiding a spec.
- **Gates job needs `npm ci`** — eslint is half the complexity check. Without
  `node_modules` the script prints SKIPPED and *drops* frontend entries rather than
  reporting them clean; `--update` refuses outright. Deliberate: otherwise a backend
  dev running `--update` would silently erase 18 baselined offenders.
- **`_under_coverage()` uses `coverage.Coverage.current()`, not `sys.gettrace()`** —
  under sysmon the trace hook is empty, so the obvious check reads as "no coverage"
  exactly where CI runs it.
- sysmon reads ~5pp higher than the trace backend (sees `asyncio.to_thread` lines).
  Not a bug; floor 70 clears under both.

## Local-only noise (not caused by this branch)
- `platform`: 24 failures + 1 error locally — need the engine venv and B3 market
  plane. CI builds both; they pass there.
- `engine`: `tests/test_validate_spec.py` and `tests/test_whatif_run.py` fail
  collection locally only. CI green.
