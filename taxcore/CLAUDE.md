# B3 Portfolio — Claude Code project instructions

You are helping the user analyze B3 (Brazilian stock exchange) stocks and manage a personal portfolio. This file is the contract for how the project works. Read it at the start of every session.

## What this project is

A CLI-driven workspace for fundamental analysis of B3 stocks. Data comes from Fundamentus' public site (equity screener at `resultado.php`, FII screener at `fii_resultado.php`, detail pages via the `fundamentus` Python package), with `yfinance` as a fallback for tickers neither page covers (e.g. BDR-ETFs like IVVB11, plus IBOV `^BVSP` for benchmarking).

Portfolio data comes from the user's official B3 reports (Área do Investidor → Extrato), dropped into `b3_imports/`. Two report types are supported, auto-detected by their column structure:

1. **Negociação** — every trade (Compra/Venda). Columns: `Data do Negócio | Tipo de Movimentação | Mercado | Prazo/Vencimento | Instituição | Código de Negociação | Quantidade | Preço | Valor`. By default `load_b3.py` keeps equity-relevant markets only (Mercado à Vista / Mercado Fracionário / Renda Variável); pass `--all-markets` to include Renda Fixa, options, etc.
2. **Movimentação** — every account event: trades + dividendos + JCP + rendimentos (FII distributions) + amortizações + reembolsos + corporate actions (Desdobro, Bonificação em Ativos, Fração em Ativos) + BTC lending entries + custody transfers. Columns: `Entrada/Saída | Data | Movimentação | Produto | Instituição | Quantidade | Preço unitário | Valor da Operação`. **Negociação trades take precedence** when both report types are present (Movimentação's Transferência - Liquidação rows are settlement-date dupes of trades).

`load_b3.py` produces three outputs:
- `data/positions.csv` — current holdings, weighted-avg cost, **split- and amortization-adjusted** (Desdobro / Bonificação em Ativos / Fração em Ativos applied chronologically alongside trades; Amortizações reduce cost basis at the security level)
- `data/trades.csv` — deduped trades from both report types
- `data/cash_events.csv` — every cash event with an `event_category` column: `income` (Dividendo, JCP, Rendimento), `reembolso` (broker/withholding refunds — separate from income), `amortization`, `qty_event` (corporate actions, applied to positions), `other` (Leilão de Fração, etc.)

**Important caveats about cost basis:**
- Reports only cover the date range you select when downloading. If a position has buys older than that range, the parser flags orphan sells in `incomplete_history`. Download a longer-dated report to fix.
- iShares ETFs like IVVB11 are accumulation funds — no Dividendo/Rendimento events exist in B3 reports for them. Their "no income" line is correct, not missing data.
- "Empréstimo" (BTC stock-lending) entries exist for many positions but are NOT classified as income — the schema mixes lending bookkeeping with fee credits.

The user is a **retail investor**. This is for their own decision-making. **You are not a financial advisor. Never frame output as a buy/sell recommendation — frame it as analysis to inform the user's own decision.**

## Investing philosophy (default ranking methodology)

**Equities — mixed value + quality + growth** (`methodologies/mixed.py`):

- **Value (40%)**: low EV/EBIT, low P/L (rejected if negative), reasonable P/VP
- **Quality (35%)**: high ROIC, high ROE, low Dív.Líq/Patrim (or Dív.Bruta/Patrim — accepts whichever Fundamentus exposes; the column was renamed in 2025), healthy margins
- **Growth (25%)**: positive Cresc. Rec. 5a, positive earnings momentum

Hard filters before equity ranking (`methodologies/filters.py`):
- Liq. 2 meses ≥ R$ 1,000,000 (avoid illiquid names)
- P/L > 0 and < 50 (reject losses and obvious bubbles)
- ROE > 0
- Debt/Equity < 2 (constant `MAX_DEBT_EQUITY`; uses divlpatr or divbpatr, whichever present)
- Exclude tickers ending in `11` unless `--allow-units` (those are FIIs/units, ranked separately)

**FIIs — yield + discount + quality** (`methodologies/fii_mixed.py`):

- **Yield (50%)**: high DY + high FFO Yield (FFO Yield close to DY = sustainable distribution)
- **Discount (25%)**: low P/VP (below NAV)
- **Quality (25%)**: low vacância + property count (vacância is masked for paper FIIs where qtd_imoveis == 0 — Fundamentus mis-reports it as 100% for credit/CRI funds)

Hard filters (`methodologies/fii_filters.py`):
- Liquidez ≥ R$ 100k/day, BUT bypassed if `valor_mercado ≥ R$ 100M` (large funds always have real liquidity even when today's snapshot mis-reports zero)
- DY in [2%, 30%] (rejects defective/zombie funds and cota-in-freefall situations)
- P/VP > 0

The user can override weights ad-hoc — e.g., "rank with 60% value 40% quality, drop growth this time" — and you adjust on the fly. The defaults are just defaults.

**Cheapest stocks — Clube do Valor "Nova Seleção Quantitativa"** (`methodologies/cheapest.py`):

A bank-inclusive deep-value screen replicating CdV's "As 20 Ações Mais Baratas da Bolsa". Mechanical, no weights to tune. Steps:
1. **Liquidez** — `liq2m ≥ R$200k/dia` (constant `MIN_LIQ_DAILY`).
1b. **Balanço atualizado** — drop names whose last balance is older than `MAX_BALANCE_AGE_DAYS` (180) vs the snapshot date. **Hard pre-rank filter over the whole liquidity-passing universe** — a stale balance poisons the multiples we rank on (P/L, EV/EBIT, P/VP all derive from it), so a stale name would corrupt the percentiles, not just its own row. The report date isn't in the snapshot; it comes from each ticker's detail page (`Ult_balanco_processado` via `get_papel`), fetched ~5-concurrent and cached in a per-snapshot sidecar `data/balance_dates/<date>.csv`. Missing/unparseable/failed → also dropped, labeled `no-date` (vs `stale>Nd`). `--max-balance-age`, `--refresh-dates`, `--as-of` override.
2. **Saúde** — `P/L > 0`; non-financials also need `Mrg. EBIT > 0`.
3. **Múltiplos combinados** — non-financials ranked by **EBIT/EV** (cheapest = lowest EV/EBIT); banks/EBIT-less names (which report no EBIT, so Fundamentus gives `evebit = 0`) injected by **lowest P/L + P/VP**. Both legs normalized to a cheapness percentile in [0,1] and merged into one ranking — so genuinely cheap banks (BBAS3/BBDC4/...) surface automatically when at low multiples, with no manager bias.
4. **Top N** — slice the integrated list.

**Cadence:** the user rebalances on the **15th of June, September, December, and April** (~6 weeks post quarter-end, after ITRs/DFP are filed). A wave of `stale>180d` exclusions means the run is too early — earnings season isn't in yet; wait. Measure freshness against the snapshot date so historical runs reproduce.

**Cheapest FIIs — Clube do Valor "S-Rank"** (`methodologies/fii_cheapest.py`):

The FII analog of the equity cheapest screen — a **separate asset class**, separate module/runner. Mechanical, no weights. Etapa 1 (eliminação): liquidez ≥ R$200k/dia; drop **desenvolvimento/incorporação** funds; drop **monoativos** (`qtd_imoveis == 1` or manual single-tenant; `qtd_imoveis == 0` = paper funds, kept); drop **prazo determinado**; drop **brick funds with vacância > 20%** (`MAX_VACANCIA`, gated on `qtd_imoveis ≥ 1` — enhancement, not in CdV's spec). Etapa 2 (ranqueamento): rank-sum of **P/VP ascending + DY descending** (50/50), lowest sum wins, **top 15**.

Mandato + prazo + a recent-DY run-rate aren't in the Fundamentus FII snapshot — they come from **CVM Dados Abertos** (`scripts/fetch_fii_metadata.py` → `data/fii_metadata.csv`), bridged ISIN→ticker at **~77%** coverage (best-effort; the rest pass the metadata filters by default — logged). CVM's coarse ANBIMA `Mandato` mis-buckets some dev funds (TGAR11, MFII11) as "Híbrido", so `data/fii_metadata_overrides.csv` injects Investidor10-confirmed dev funds + manual `single_tenant` flags. The fetcher also reads CVM's `complemento` monthly DY and stores `dy_run_rate` (last 6 months annualized). **Refresh metadata semiannually** (Investidor10's dev page resists flat scraping — curate the overrides via WebFetch).

**Yield-trap skip (mirrors the equity screen's pool→clean):** the pure rank-sum loads up on distressed deep-discount paper FIIs, so the runner ranks a candidate pool — **auto-expanded down the ranking until it holds at least `top` sustainable funds** — then drops yield traps, leaving a full clean top 15 from within the visible pool (no opaque backfill). Two trap detectors (both set `is_trap`): `is_unsustainable` (**DY>>FFO** — DY exceeds FFO yield by >3pp, catches negative-FFO like RDLI11) and `is_cut` (**DY-cut** — recent `dy_run_rate` collapsed below `DY_CUT_RATIO`=50% of trailing-12m DY, catching funds whose stale trailing DY hides a distribution cut, e.g. DEVA11/HCTR11 paying ~0 in 2026). A high DY that's *covered and stable* is kept; `DY>20%` is informational only. Caveat to always state: these test distribution coverage/continuity, NOT credit quality. A high DY that's *covered* by FFO is kept (it's the yield the strategy wants) — `DY>20%` is an informational flag only, not a skip. `--no-trap-filter` shows the raw pool. Caveat to always state: the filter tests distribution *coverage*, not credit quality — FFO yield for paper FIIs depends on CRI marks, so funds like DEVA11/HCTR11 can survive the filter yet still carry credit risk. **Cadence: semiannual** (CdV rebalances FIIs every 6 months; sell only on a large ranking drop; direct new money/dividends to the most-underweight holdings).

**Rebalance mode** (`rank_fii_cheapest.py --rebalance`): operationalizes CdV's exit rule against the user's real holdings in `data/positions.csv`. Buffer band (histerese): a held FII is **MANTER** while it stays within the clean top `--exit-rank` (default **22** ≈ 1.5× the 15-fund portfolio), **VENDER** only if it falls below that band OR is eliminated by a filter OR becomes a yield trap (DY-cut / DY>>FFO); **COMPRAR** = best non-held names from the clean top 15; aporte priority = post-rebalance funds furthest below equal weight (new buys first). The band avoids churning funds that merely slip a few spots. It prints a high-turnover warning when ≥60% of the held portfolio would be sold — the S-Rank is deep-value and diverges from a quality/low-discount FII portfolio, so a near-total turnover is a *strategy change* (costs + 20% IR), to analyze not execute blindly.

Why two legs: a pure EV/EBIT screen *structurally* excludes every bank (EV/EBIT undefined for financials). The P/L+P/VP leg is what lets banks appear.

**Value-trap filter** (`cheapest.flag_traps`, applied by default in the runner): flags + drops names whose cheapness signals trouble, not a bargain — `ROE < 8%` (`MIN_ROE_TRAP`), `P/L > 20` (`MAX_PL_TRAP`), `DY > 18%` (`MAX_DY_TRAP`). Default flow is **pool top 30 → drop traps → backfill to a clean 20**. It's a heuristic, not business analysis — always tell the user the surviving list can still hold cyclicals/value traps, and that the screen doesn't dedup share classes (PETR3/PETR4 both appear).

## Data layout

```
data/snapshots/YYYY-MM-DD.csv         ← Fundamentus equity screener (one row per ticker)
data/fii_snapshots/YYYY-MM-DD.csv     ← Fundamentus FII screener (separate page, different schema)
data/external_prices/YYYY-MM-DD.csv   ← yfinance fallback for tickers neither Fundamentus page covers (e.g. IVVB11)
data/balance_dates/YYYY-MM-DD.csv     ← per-snapshot sidecar of each liquid ticker's last balance date (freshness filter for rank_cheapest.py)
data/fii_metadata.csv                 ← FII mandato/prazo/segmento from CVM (ISIN→ticker) for the S-Rank screen; regenerated by fetch_fii_metadata.py
data/fii_metadata_overrides.csv       ← hand-maintained truth applied on top of CVM: Investidor10-confirmed dev funds + manual single_tenant flags
data/sectors_cache.csv                ← per-ticker Setor/Subsetor lookup cache (built by sector_exposure.py)
data/watchlist.txt                    ← tickers actively watched (auto-populated by update_watchlist.py)
data/positions.csv                    ← current holdings (split- and amortization-adjusted)
data/trades.csv                       ← deduped trade history
data/cash_events.csv                  ← dividends, JCP, rendimentos, reembolsos, qty events
b3_imports/                           ← raw B3 Excel reports the user drops in (Negociação and/or Movimentação)
reports/YYYY-MM-DD-<topic>.md         ← markdown reports you write
```

**Snapshots are immutable history.** Never overwrite. A new fetch creates a new dated file. This lets the user see how rankings evolved over time and how their thesis aged.

## Scripts (always prefer these over ad-hoc code)

**Data ingestion:**
| Script | What it does |
|--------|-------------|
| `scripts/fetch_fundamentus.py` | Equity screener → new snapshot in `data/snapshots/` |
| `scripts/fetch_fundamentus_fii.py` | FII screener → new snapshot in `data/fii_snapshots/` |
| `scripts/fetch_external_prices.py` | yfinance fallback for held tickers absent from both Fundamentus pages |
| `scripts/fetch_ticker.py TICKER` | Detailed view for one ticker |
| `scripts/load_b3.py` | Parses Negociação + Movimentação → positions.csv, trades.csv, cash_events.csv |

**Analysis:**
| Script | What it does |
|--------|-------------|
| `scripts/rank.py [--top N] [--watchlist] [--weights ...]` | Equity ranking via mixed methodology |
| `scripts/rank_fii.py [--top N] [--held] [--segment ...] [--weights ...]` | FII ranking |
| `scripts/rank_cheapest.py [--pool 30] [--top 20] [--max-balance-age 180] [--refresh-dates] [--no-trap-filter] [--liq ...]` | Clube do Valor "Nova Seleção Quantitativa" — cheapest stocks (banks included). Drops stale-financials names, then pool top 30 → drop value traps → clean 20 |
| `scripts/balance_dates.py` | Helper for the above: fetches/caches each liquid ticker's last balance date (`Ult_balanco_processado`) into `data/balance_dates/<snapshot-date>.csv` |
| `scripts/rank_fii_cheapest.py [--top 15] [--refresh-metadata] [--no-metadata]` | Clube do Valor "S-Rank" — cheapest FIIs. Eliminates illiquid/desenvolvimento/monoativo/prazo-determinado, then P/VP+DY rank-sum → top 15 |
| `scripts/fetch_fii_metadata.py [--years ...]` | Helper for the above: pulls FII mandato/prazo from CVM Dados Abertos (ISIN→ticker), applies `fii_metadata_overrides.csv`, writes `data/fii_metadata.csv` |
| `scripts/etf_counterfactual.py [--mix "BOVA11=0.4,IVVB11=0.3,CDI=0.3"] [--start ...]` | Replays the portfolio's real cash flows into daily-rebalanced ETF mixes (money-weighted counterfactual in R$); charts actual vs mixes. Gross of taxes on both sides |
| `scripts/rank_history.py TICKER` | How a ticker's score/rank evolved across snapshots |
| `scripts/portfolio_status.py` | Holdings table with unrealized %, YoC, total return on gross invested. Uses all 3 price sources. |
| `scripts/sector_exposure.py [--top N]` | Sector breakdown of priced positions; flags >25% concentration |
| `scripts/portfolio_vs_ibov.py [--start ...]` | Portfolio total return vs IBOV (^BVSP) over the holding period |
| `scripts/portfolio_index.py [--start ...] [--sleeves] [--out ...]` | Daily TWR index chart (base 100): total wealth (listed + bank renda fixa at cost+CDI accrual) vs CDI, IBOV, S&P 500 TR in BRL. `--sleeves` splits Ações BR vs FIIs+FIAgro instead. Saves PNG to `reports/`. Excludes futures/options; validates Yahoo splits against actual trade prices |
| `scripts/update_watchlist.py [--top 30]` | Auto-populates `data/watchlist.txt` from top-ranked names not already held |
| `scripts/suggest_allocation.py --amount N` | Given new capital N (BRL), suggests score-weighted allocation |
| `scripts/irpf_2026.py --year 2025` | Generates a copy-paste-ready IRPF 2026 markdown report for ano-base 2025 |
| `scripts/tlh_plan.py` | Interactive tax-loss-harvesting planner: enter winning sells, get a recommended set of losers to sell today + T+1 rebuy plan to net the month's PnL to zero |

**Run scripts via `uv run` or `python -m scripts.X`** — they assume the project root is on `sys.path` (handled in `pyproject.toml`).

## Workflow patterns

**Fresh ranking session:**
1. Check `data/snapshots/` and `data/fii_snapshots/` for today's files. Run `fetch_fundamentus.py` and/or `fetch_fundamentus_fii.py` if missing.
2. Run `rank.py` for equities, `rank_fii.py` for FIIs.
3. Drop a markdown report in `reports/` summarizing top names with brief commentary.
4. Show the user the top 10-15 inline; link to the full report.

**Cheapest stocks (Clube do Valor method) — run mechanically, quarterly (15th of Jun/Sep/Dec/Apr):**
1. Fetch a fresh snapshot (`fetch_fundamentus.py`) — the run date = the snapshot date for freshness.
2. Run `python -m scripts.rank_cheapest`. First run of a snapshot fetches ~244 balance dates (cached in `data/balance_dates/`); re-runs are instant.
3. Sanity-check the freshness output: a small number of `stale>180d` exclusions is normal (distressed/delisting names); a *wave* of them means you ran before earnings season — wait. Use `--refresh-dates` if `no-date` looks like a fetch glitch.
4. Save a report to `reports/YYYY-MM-DD-cdv-acoes-mais-baratas.md`: method table, names excluded for stale financials, value traps dropped and why, the clean 20 (mark which are banks), and where the big banks landed.
5. Always restate: analysis only (not a recommendation); freshness + trap filters are mechanical; surviving names can still be cyclicals/traps; no share-class dedup.

Ready-to-use prompt:
```text
Run the Clube do Valor "ações mais baratas" screen for this quarter's rebalance. Fetch a fresh snapshot, then run scripts/rank_cheapest.py with defaults (freshness filter → pool-30 → clean-20, value traps removed, banks included). Save a report to reports/<date>-cdv-acoes-mais-baratas.md: method steps, names excluded for stale financials, the value traps you dropped and why, the clean 20 (flag which are banks), and where BBAS3/BBDC4/ITUB4 landed. Flag if a wave of stale exclusions suggests I ran too early. Frame as analysis, not a recommendation.
```

**Cheapest FIIs (Clube do Valor S-Rank) — run mechanically, semiannually:**
1. Fetch a fresh FII snapshot (`fetch_fundamentus_fii.py`).
2. Refresh metadata (`fetch_fii_metadata.py`) — pulls CVM mandato/prazo. Once or twice a year is enough; check `data/fii_metadata_overrides.csv`'s dev list against Investidor10's `/fiis/tipos/desenvolvimento/` (via WebFetch) and update if it drifted.
3. Run `python -m scripts.rank_fii_cheapest`. Note the elimination counts and the ~77% metadata coverage line.
4. Save a report to `reports/YYYY-MM-DD-cdv-fiis-mais-baratos.md`: method, elimination breakdown, top 15 with P/VP-rank / DY-rank / S-Rank, and the yield-sustainability flags.
5. Always restate: analysis only; the rank-sum is biased toward distressed high-yield paper FIIs (flagged); metadata coverage is partial; single-tenant is only partially detectable.

Ready-to-use prompt:
```text
Run the Clube do Valor FII "S-Rank" screen. Fetch a fresh FII snapshot and refresh the CVM metadata, then run scripts/rank_fii_cheapest.py (eliminate illiquid/desenvolvimento/monoativo/prazo-determinado → P/VP+DY rank-sum → top 15). Save a report to reports/<date>-cdv-fiis-mais-baratos.md: method, what got eliminated and why, the top 15 with the S-Rank breakdown, and the yield-trap flags. Call out that the method favors distressed paper FIIs. Frame as analysis, not a recommendation.
```

**Portfolio review:**
1. Run `load_b3.py` to ingest any new Negociação/Movimentação files in `b3_imports/`.
2. Run `fetch_external_prices.py` if any held tickers are unpriced (e.g., new BDR-ETF).
3. Run `portfolio_status.py` to see holdings, total return, YoC.
4. Run `sector_exposure.py` to check concentration; flag any sector >25%.
5. Run `portfolio_vs_ibov.py` to compare to the benchmark.
6. Compare priced positions to `rank.py` / `rank_fii.py --held` — flag holdings that fell out of the top quartile.

**IRPF 2026 report for ano-base 2025:**
1. Run `load_b3.py --all-markets` so tax, renda-fixa, and options-related rows are available when present.
2. Run `irpf_2026.py --year 2025` and save stdout to `reports/2026-irpf-ano-base-2025.md`.
3. Make the output practical for the IRPF program: Bens e Direitos, dividendos, JCP, FII rendimentos, renda fixa where available, realized gains/losses, and a final verification checklist.
4. Do not invent missing CNPJs, tax treatment, or values. Mark uncertain fields as `verificar no informe` and tell the user to check broker informes before submitting.
5. Treat the report as an aid for tax filing, not legal or accounting advice.

Suggested user prompt:

```text
Generate a copy-paste-ready IRPF 2026 report for ano-base 2025.

Use my local B3 data only. First run the portfolio import from the Excel files in b3_imports/, then run the IRPF report generator. Save the final markdown report under reports/ as reports/2026-irpf-ano-base-2025.md.

Make the report practical for filling the IRPF program: Bens e Direitos, dividendos, JCP, rendimentos de FII, renda fixa where available, realized gains/losses, and a final checklist of items I still need to verify against broker informes. Do not invent missing CNPJs or tax values; mark anything uncertain as "verificar no informe".
```

**Deep dive on a name:**
1. Run `fetch_ticker.py TICKER` for the detail page.
2. Cross-reference with the latest snapshot row.
3. Walk through the bull case, bear case, and what would change your view. Be specific about what numbers from the data support each.
4. **Do not predict prices.** Talk about scenarios and what they imply.

**New capital deployment:**
1. Run `portfolio_status.py` for current weights.
2. Run `suggest_allocation.py --amount N`.
3. Cross-check the suggested buys against the current ranking. If the script suggests adding to a name that has fallen out of the ranking, flag that and ask the user.

## Things to do every session

- If the latest snapshot is more than 7 days old, mention it and offer to refresh.
- If the user asks something that requires data you don't have (e.g., real-time intraday prices, options data, FII-specific metrics), say so explicitly. Don't invent.
- If the user asks for a price prediction or "should I buy X" — reframe as analysis: what does the data say, what assumptions would have to hold, what are the risks.

## Things to never do

- Don't write a buy/sell recommendation. Frame as analysis.
- Don't overwrite snapshots. Append-only.
- Don't fabricate numbers. If a value is missing in the snapshot, say so.
- Don't suggest leverage, margin, day-trading strategies, options, or anything outside long-only equities.
- Don't paste B3 Excel raw data into reports — use the parsed CSV from `load_b3.py`.
- Don't present IRPF output as legal/accounting advice; require the user to verify against official broker informes.

## Brazilian market specifics worth remembering

- **JCP** (Juros sobre Capital Próprio) and dividends are different — JCP is taxed at source (15%), dividends are tax-free for individuals. The DY field on Fundamentus mixes both.
- **Tickers ending in 3** are ON (ordinary), **4** are PN (preferred). Some companies have both — pick based on liquidity and corporate governance level.
- **Novo Mercado / Nível 2 / Nível 1** are governance tiers — Novo Mercado is the strictest. Fundamentus doesn't expose this directly; user may want to filter manually.
- **Free float, liquidity, and tag-along rights** matter and are not all in Fundamentus. Flag when the analysis is incomplete because of this.
- **IBOV** is the main benchmark. **IDIV** for dividend strategies, **SMLL** for small caps.
- **Isenção de R$ 20.000/mês em ações** — best-of-both-worlds: em meses com vendas brutas de ações ≤ R$ 20k, **lucros são isentos** (vão em **Rendimentos Isentos e Não Tributáveis código 20**) e NÃO consomem o saldo de prejuízo acumulado. Mas **prejuízos** num mês isento **continuam sendo adicionados ao prejuízo acumulado** e são compensáveis em meses tributáveis futuros normalmente. `scripts/irpf_2026.py` aplica essas regras automaticamente.
