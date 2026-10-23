# Feature: user-created strategies (rule + portfolio)

Status: **shipped** — `/strategies/user*` on the `bench` module, migration
`c7e2f4a9b1d8_user_strategies`. This doc is the architecture of record for the
feature, not a plan.

## The core insight

The engine already runs strategies as **data, not code** (`spec/02`,
`engine/docs/config_strategies.md`): a YAML spec of `kind: rank_and_hold` or
`kind: fixed_weight`, over an AST-whitelisted signal DSL
(`engine/backtests/core/signal_dsl.py`) that *cannot execute code*. So this
feature is **persistence + validation + a form**, not a sandbox. Both asks are
one system:

- **Rule strategy** = a user-authored `rank_and_hold` spec (factor, universe
  filters, top-N, weighting).
- **Portfolio strategy** = a `fixed_weight` spec whose sleeves are rule
  strategies, built-ins, listed tickers (`IVVB11.SA`) + `CDI` — the sleeve
  forms the built-in blends use — so it backtests through the *same* path, no
  parallel machinery.

Reuse the spec system and the existing backtest/advise paths. Do not invent
new strategy machinery.

## Engine-boundary mechanism (the one hard part)

The platform never imports the engine — it shells into the engine venv
(`platform/platform_app/enginerun.py:59`, `cwd=ENGINE`). The engine discovers
specs only from the baked-in `SPECS_DIR`
(`engine/backtests/core/strategy_registry.py:20,84-89`). Verified: every
entrypoint resolves names through `get_registry()` — `list_strategies.py:38`,
`advise_weights.py:187`, `run_backtest_json.py:81`, and nested sleeves at
`config_strategy.py:361-362`.

**Decision — extra-specs-dir env var + per-run materialization:**

1. **Engine (~8 lines, `strategy_registry.py` only):** after the built-in spec
   loop in `discover()`, also glob a dir named by `ENGINE_USER_SPECS_DIR` and
   register those specs. Built-ins win on name collision. Same try/except-warn
   wrapper as the built-in loop.
2. **Platform:** `materialize_user_specs(specs) -> Path` writes the tenant's
   specs to `{artifacts_dir}/user_specs/{tenant_id}/{id}.yaml`. **JSON is valid
   YAML** — write `json.dumps(spec)` with a `.yaml` name, no pyyaml dep. Full
   rewrite per call → zero staleness logic. Filenames are the row `id`, never
   the user's `name`.
3. `enginerun.run_script(script, args, extra_env=None)` — merge `extra_env` in
   `_env()`. Every caller that names a strategy sets
   `{"ENGINE_USER_SPECS_DIR": dir}`: `tasks.run_backtest`, `_advise_strategy` /
   `_advise` (routes.py:342-376), the whatif route, and tenant-aware
   `list_strategies`. Zero-spec tenants skip it entirely (identical to today).

## Data model

One new table + one Alembic revision:

```
user_strategies
  id          GUID pk = uuid4        -- also the on-disk {id}.yaml filename
  tenant_id   tenant col, indexed
  name        TEXT   -- registry key; what BacktestRun.strategy / sleeve strategy_ref store
  kind        TEXT   -- 'rank_and_hold' | 'fixed_weight'
  spec        JSONB  -- engine spec schema verbatim; single source of truth
  created_at / updated_at  -- updated_at doubles as the gallery-cache fingerprint
  UNIQUE(tenant_id, name)
```

- Built-in collisions can't be a DB constraint → reject at save (`name` in the
  built-in gallery, or `== CASH_CDI`).
- MVP: name immutable (delete+recreate). **Full: rename with cascade** over the
  tenant's `PortfolioSleeve.strategy_ref` and other `fixed_weight` sleeves.
- Delete → 409 if `name` is referenced by a sleeve or another user
  `fixed_weight` spec. Historical `BacktestRun` rows keep the name string.
- **No change to `PortfolioSleeve` and no data migration** — the existing single
  sleeve set stays the live target.

## Portfolio strategy semantics

Store as a `user_strategies` row, `kind='fixed_weight'`. A request sleeve is
exactly one of `{strategy_ref, target_pct}` or `{ticker, target_pct}`, and maps
1:1: `{strategy_ref: X, target_pct: 40}` → `{strategy: X, weight: 0.40}`;
`CASH_CDI` → `{asset: CDI, weight: ...}`; `{ticker: IVVB11, target_pct: 25}` →
`{ticker: IVVB11.SA, weight: 0.25}` (normalised to the built-ins' spelling). The
compiled spec always carries `park_in_cdi_until_live: true`: before a ticker's
first price its weight sits in CDI, as in "DIVO11 / CDI / IVVB11 (thirds)".

- Backtests for free via `run_backtest_json` + `run_simulation`. Ticker sleeves
  run through `FixedWeight`'s ticker branch and the market's tax rule exactly as
  the built-ins' do (`engine/tests/test_user_portfolio_parity.py` pins a user
  copy of the thirds to the built-in's curves). The B3 apuração is per class,
  as taxcore's: ações (incl. units) 15% with the R$20k isenção on ações sales;
  ETFs and BDRs 15% with no isenção, sharing the ações loss pool; FIIs 20% in
  their own (`tax.market_ledger`, `asset_classes.py`). Same for built-ins and
  user portfolios.
- A nested strategy and a ticker sleeve on the same instrument add their
  weights (a MomSharpe that picks IVVB11 next to an IVVB11 sleeve), and a
  nested portfolio's ETF columns carry that portfolio's own returns.
- **Live target stays `PortfolioSleeve`.** "Promote to live" compiles the spec's
  sleeves through the existing PUT `/portfolio/sleeves` logic (routes.py:268).
  Two representations on purpose: named spec = saved/backtestable artifact;
  sleeve set = single live target. One-way sync at promote. A live sleeve is a
  strategy (advised per strategy), so promoting a portfolio with ticker sleeves
  is a 422 naming them — never a silent drop of part of the portfolio.
- **Cycle guard:** a user portfolio sleeve may reference built-ins, `CASH_CDI`,
  or the tenant's *rank_and_hold* strategies — never another user
  `fixed_weight` (prevents registry recursion at config_strategy.py:362).

## Tenant-aware gallery & cache

- Keep the global built-ins cache `_STRATEGIES_CACHE` (routes.py:812) as-is.
- Refactor the route into `_gallery(session, tid | None)`: `None` → built-ins;
  else fingerprint the tenant's rows (`count(*), max(updated_at)` — one indexed
  query), 0 rows → built-ins, else consult `_TENANT_GALLERY[tid] =
  (fingerprint, list)`; on miss/mismatch, materialize + shell
  `list_strategies.py` with the env var. Tag user entries `source: "user"`.
- Invalidate by popping `tid` on save/delete; the fingerprint self-heals stale
  replicas — no TTL.
- Internal callers switch to `_gallery`: sleeve-ref validation (routes.py:286),
  `create_backtest` (routes.py:852).
- **Bug to fix regardless:** `_SLEEVE_WEIGHTS_CACHE` (routes.py:316) is keyed
  `(strategy, date, freq)` with NO tenant — two tenants' same-named user
  strategies collide, and a same-day edit serves stale advice. Add
  `tenant_id` + the row's `updated_at` to the key for user refs; built-in refs
  keep the shared key.

## Validation (no data-run on save)

1. **Platform (pydantic):** name rules; numeric ranges (min_adtv ≥ 0,
   0 < top_pct ≤ 1 or top_n ≥ 1, min_names ≥ 1); rebalance ∈ {ME, QE, W-FRI};
   portfolio weights sum to 1 ± 1e-4 (stricter than the engine, matches sleeve
   semantics on promote); sleeve refs resolvable via `_gallery`; cycle guard.
2. **Engine `scripts/validate_spec.py --spec-json '<json>'` (new, ~60 lines):**
   construct `RankAndHold`/`FixedWeight`; run `get_parameter_specs()`; for the
   signal, `ast.parse` + the `_ALLOWED` whitelist walk (expose
   `signal_dsl.check_syntax(expr)`); factor must be a `signals.yaml` name or a
   whitelisted shared-data key; **reject `store:`**; infer/enforce
   `needs_fundamentals: true` when any `f_*` key is referenced. For
   `fixed_weight`: each sleeve is exactly one of ticker / asset CDI / strategy,
   and every ticker must have prices on the B3 plane (`sleeve_tickers.py`, the
   one plane read on save) — unknown is an error naming it, a first price after
   the history starts or a last price before it ends is a warning naming the
   date, an index or a renda-fixa ETF (outside COTAHIST) is an error saying so.
   A fractional code (PETR4F) is normalised to its round lot by the platform. Prints
   `{ok, errors, warnings, needs_fundamentals}`, exit 0/1. Platform shells it on
   save and returns the warnings with the row.
3. No dry-run backtest on save; first real backtest is the dry run. (The ticker
   check reads two index probes off `prices`, not a backtest.)
   Fundamentals gaps → clean 422 via `EngineNeedsFundamentals`
   (enginerun.py:27), reusing the soft-warning-on-save pattern from
   `put_sleeves` (routes.py:298-303).

## API surface

| Endpoint | Change |
|---|---|
| `GET /strategies` | Merged gallery (authed → built-ins + tenant's + `source`; anon → built-ins) |
| `GET /strategies/signals` | NEW — named-signal catalog for the factor picker (shell `list_signals.py`, ~15 lines, global cache) |
| `GET /strategies/user` | NEW — tenant rows with full `spec` (for edit form) |
| `POST /strategies/user` | NEW — create; body = form fields → compile → pydantic + `validate_spec.py` → insert → pop cache; returns row + `warnings` |
| `PUT /strategies/user/{id}` | NEW — update spec; full version: allow rename with cascade |
| `DELETE /strategies/user/{id}` | NEW — 409 if referenced |
| `POST /strategies/user/{id}/promote` | NEW (portfolio kind) — compile sleeves → PUT /portfolio/sleeves internals |
| `POST /backtests`, `PUT /portfolio/sleeves`, `GET /portfolio/target`, whatif | Shape unchanged; validation via `_gallery`; engine calls gain the env var |

## UI

- **Rule builder — `/bench`:** "Minhas estratégias" card: list (name, kind,
  backtest shortcut, edit, delete) + "Nova estratégia" form. Form-first: factor
  select fed by `GET /strategies/signals` + lookback; universe (min_adtv,
  min_price, small-cap); selection (top_pct ⇄ top_n, min_names, dedup_roots —
  auto-locked on for fundamentals factors); weighting; rebalance. **Full:**
  "Modo avançado" toggle → raw `expr:` editor with primitive cheat-sheet +
  debounced server-side `validate_spec` feedback; regime-overlay builder;
  "Clonar" a built-in gallery spec into user space prefilled; rename.
- **Portfolio builder — `/estrategia`** (`#sleeves` section): "Salvar como
  estratégia" (names current editor state) + saved-portfolios list with
  **Backtest** (POST /backtests → /bench), **Ativar** (promote → live target;
  target/plan sections refresh), **Excluir**. Sleeve picker includes the
  tenant's rule strategies (arrive via the merged gallery hook).

## Test strategy

- **Engine** (`engine/tests`, patterns from `test_strategy_registry.py` +
  `test_config_strategy.py`): `discover()` with `ENGINE_USER_SPECS_DIR` (tmp
  dir, monkeypatched) registers a user spec and skips a built-in-name collision;
  `validate_spec.py` — valid → ok, bad expr / unknown factor / `store:` /
  `f_*`-without-fundamentals → structured errors.
- **Platform** (`platform/tests`, patterns from `test_sleeves.py`,
  `test_portfolio_settings.py`, `test_backtest_e2e.py`): CRUD + every rejection
  (dup name, built-in collision, bad sleeve ref, weights ≠ 1, cycle); gallery
  merge + cache invalidation; sleeves accepting a user ref; delete-while-
  referenced → 409; **one e2e backtest of a user strategy under
  `ENGINE_BENCH_OFFLINE=1`** (proves materialization + env injection + registry
  pickup + worker path together).
- **Smoke:** one user-strategy create + backtest step (full version).

## Phases (dependency order — build in verified waves)

1. **Engine** (unblocks all): registry env-var hook, `signal_dsl.check_syntax`,
   `validate_spec.py`, `list_signals.py`, engine tests.
2. **Platform:** `user_strategies` table + migration; materialization helper +
   `run_script(extra_env)`; CRUD routes + validation; `_gallery` refactor +
   tenant cache; env var wired into `tasks.run_backtest`, `_advise_strategy`,
   whatif; `_SLEEVE_WEIGHTS_CACHE` key fix; tests.
3. **Frontend — rule builder** on `/bench` (form + list + backtest), incl. the
   advanced expr editor, regime overlay, clone, rename (full version).
4. **Frontend — portfolio builder** on `/estrategia` (save/list/backtest/
   activate/delete), sleeve picker includes user rule strategies.
5. **Smoke** step.

## Risks & sharp edges

- `_SLEEVE_WEIGHTS_CACHE` cross-tenant collision (routes.py:316) — must gain
  tenant_id/updated_at; silently serves another tenant's weights otherwise.
- A missed `run_script` caller = registry `KeyError` for user strategies —
  grep every `enginerun.run_script` caller when wiring.
- Multi-replica gallery staleness — handled by the per-request fingerprint;
  don't optimize it away.
- Fundamentals factors — save-with-warning; the form must auto-set
  `needs_fundamentals` + `dedup_roots` or share classes double-book.
- Name collisions — built-ins win in the registry, but the real guard is
  save-time rejection; reserve `CASH_CDI`.
- Weights-sum — engine tolerates < 1, sleeves require = 100; enforce = 1 at save
  so promote never 422s.
- JSON-as-YAML materialization is valid; keep filenames id-based.
