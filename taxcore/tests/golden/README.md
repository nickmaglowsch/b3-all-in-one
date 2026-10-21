# Golden-file regression suite — `taxcore` tax core

Locks in the behavior of the two modules the platform's tax surfaces are built
on, so they can be refactored safely:

- **`scripts/load_b3.py`** — B3 extract parser (trades / cash events / positions).
- **`scripts/irpf_2026.py`** — IRPF ano-base report generator.

The tests run the real scripts, unmodified, over the **vendored synthetic fixtures**
(`b3_imports/*.xlsx`, `data/*.csv`) and compare their output byte-for-byte
(CSV: pandas, float-tolerant, order-independent) against the canonical vendored
files.

## Where the fixtures come from

`make_fixtures.py` in this directory generates every input the suite runs on. It
is deterministic (literal tables, no RNG, no `datetime.now()`): rerunning it
reproduces the same 16 extracts row for row, so `data/*.csv` and the IRPF golden
come back identical. (The `.xlsx` *bytes* differ each run — openpyxl stamps
docProps with the current time — so only re-commit them when the tables changed.)
See **Privacy** below for why they exist at all.

```bash
uv run python tests/golden/make_fixtures.py    # b3_imports/*.xlsx + data/cost_basis_overrides.csv
uv run python -m scripts.load_b3               # data/{trades,cash_events,positions}.csv
uv run python -m scripts.irpf_2026 --year 2025 > tests/golden/expected/irpf-2025.md
```

The tables in `make_fixtures.py` are laid out to keep every branch of the two
scripts under test alive:

| Fixture content | Code path it keeps covered |
|---|---|
| Buys/sells in `Mercado à Vista` + `Mercado Fracionário` (`CMIG4F`, `GOAU4F`, `ABEV3F`) | `consolidate_fractional`, the `*F` strip in `derive_positions` |
| Re-exported (overlapping) Negociação/Movimentação periods | trade dedup + `cash_events` dedup (`b3_rules.drop_reexported`: collapses copies across files, never repeats within one) |
| `Dividendo`, `Juros Sobre Capital Próprio`, `Rendimento`, `Amortização`, `Reembolso`, `Leilão de Fração` | `MOV_CATEGORY` income / reembolso / amort / other |
| `Desdobro` (WEGE3), `Bonificação em Ativos` + `Fração em Ativos` + `Leilão de Fração` (GOAU4) | `qty_event` handling in `derive_positions`; the bonificação's custo atribuído (75 × R$ 8,90) in the cost and in código 18; the fraction + auction pair booked as a R$ 9,87 sale (`b3_rules.fraction_auction_sales`) |
| `Empréstimo` (BTC lending), `Transferência`, unexercised subscription rights, the XPBR31 `Atualização` | the skip branch: `b3_rules.MOV_NOOP` labels skip silently; `Atualização` would land in `movimentacao_review` but its `cost_basis_overrides.csv` line answers it, so the review stays empty |
| TRPL4 trades **and** a TRPL4 dividend | `TICKER_RENAMES` → ISAE4 on trades *and* events |
| Option premiums (`PETRF460`…) + 2 exercises with their Movimentação settlements | `process_option_activity`, `_resolve_exercise_underlying` |
| `Futuro` rows (`WINJ25`) | `--all-markets` genuinely widens `positions.csv` |
| CDB / RDB / LCI / CRI `Produto` strings, one frozen custody | `irpf_2026.renda_fixa_year_end`, issuer-CNPJ lookup + `(verificar no informe)`; `load_b3._rf_code` and the renda fixa lifecycle labels (aplicação → vencimento / resgate antecipado / retirada de custódia), in both of B3's quantity conventions (CDBs as units at PU 0.01, the CRI as 8 units at PU 1035.42) |
| Feb/Mar 2025 sells ≤ R$20k, Apr/Nov > R$20k, Jul loss, Sep isento loss | isenção, taxable months, loss carryforward into 2026 |
| FII sells (Mar/Ago/Dez 2025), IVVB11 sell, LFTB11 sell | FII 20% table, BDR/ETF-RV table, ETF-RF table |
| `cost_basis_overrides.csv` (XPBR31 `buy` + ITUB4 `cost_adjust`) | `read_overrides`, both `kind`s |

## What each test locks in

| Test | Locks in |
|------|----------|
| `test_load_b3_golden::test_default_invocation_reproduces_canonical` | `python -m scripts.load_b3` (default flags) reproduces `data/trades.csv`, `data/cash_events.csv`, `data/positions.csv` exactly. This is the invocation the vendored `data/` was generated with. |
| `test_load_b3_golden::test_all_markets_trades_and_events_are_flag_invariant` | `--all-markets` leaves `trades.csv`/`cash_events.csv` identical and only *widens* `positions.csv` (adds the futures leg — a strict superset of the canonical 25 positions). |
| `test_irpf_2026_golden::test_irpf_2025_matches_golden` | `python -m scripts.irpf_2026 --year 2025` stdout matches `expected/irpf-2025.md`. |

The scripts resolve every path from `ROOT = parent of scripts/`, so each test
copies the minimal tree (`scripts/`, `b3_imports/`, the needed `data/` inputs,
plus an offline-guard `sitecustomize.py`) into a `tmp_path` workspace and runs
the script there as a subprocess — no script or path was modified.

## Offline

Neither script makes network calls with these fixtures (verified: the only
`yfinance`/`http` token in either file is a code comment). Every subprocess also
runs with a `sitecustomize.py` net-guard that raises on any non-local
`socket.connect`, so the suite both **proves** and **enforces** offline
execution — if a future refactor introduces a network call, the tests fail loud.

## Running

```bash
uv sync                       # pyproject.toml + uv.lock are vendored; pytest is in the dev group
uv run pytest tests/golden -v # ~2s
uv run pytest -m golden -v    # same tests, via the marker
```

## Regenerating the goldens (do this deliberately, only when the change is intended)

- **load_b3 CSVs** are the canonical `taxcore/data/*.csv` themselves. To refresh
  after an intended parser change:
  `uv run python -m scripts.load_b3` (from `taxcore/`), which overwrites `data/`.
- **irpf golden:** regenerate `expected/irpf-2025.md`:
  ```bash
  uv run python -m scripts.irpf_2026 --year 2025 > tests/golden/expected/irpf-2025.md
  ```
  The only volatile line is `**Date generated**: <today>`; the test normalizes it
  on both sides, so the committed date does not need updating.

Always review the diff before committing a regenerated golden — an unexpected
change is the whole point of this suite.

## Privacy

**These fixtures are fabricated.** `b3_imports/*.xlsx`,
`data/cost_basis_overrides.csv` and everything derived from them
(`data/{trades,cash_events,positions}.csv`, `expected/irpf-2025.md`) are
generated by `make_fixtures.py` from invented tables — no quantity, price, date,
income amount or holdings mix belongs to anyone's brokerage account.

The only real values are **public market data**: B3 ticker symbols, company
names, broker names and issuer names. Tickers are deliberately real ones present
in `data/snapshots/` and `data/fii_snapshots/` so the synthetic positions can be
priced by the vendored Fundamentus snapshots — which is what the platform's TLH
and rebalance tests need.
