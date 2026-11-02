"""
Strategy: TevaIAFD — Índice Teva Ações Fundamentos (IAFD) replica
==================================================================
Source: https://www.tevaindices.com.br/nossos-indices/iafd-indice-teva-acoes-fundamentos
(the official methodology is a DocSend link, https://teva.docsend.com/view/5fmyx4i8meng2az4,
which refuses automated fetches). The rules below come from the index page and
the ETF's own page (AUVP11, https://analitica.auvp.com.br/indices/IAFD).
Rebalance timing is inferred from the published portfolio (one rebalance a
year, 2026-04-01; inception 2015-04-01).

Rules implemented, all point-in-time at the rebalance date t:
  - Universe: B3 stocks/units, with sector exclusions: retail (CVM sector
    "Comércio (Atacado e Varejo)", holding variants included), animal protein and air transport
    (the `excluded_roots` list).
  - Listed for at least 5 years: first price of ANY ticker root of the same
    company (CNPJ, from company_tickers_pit + cvm_companies), so a rename
    (AMBV -> ABEV, TIMP -> TIMS) keeps its history.
  - Market cap >= R$3bn (split-adjusted shares x raw close, sp500_b3 machinery).
  - Traded on every session of t's month, and >= R$100mm traded in each of the
    two months ending at t.
  - Fundamentals from the company's (CNPJ's) latest annual statement (DFP)
    filed on/before t, skipping all-zero placeholder filings:
      positive net income in each of the last 5 fiscal years,
      ROE > 10%, net margin > 8%,
      net debt < 3x EBIT (banks and insurers exempt). fundamentals_pit's
      `ebitda` column IS the EBIT line (DRE 3.05, no D&A added — cvm_parser).
  - No company passing at a rebalance after the first one raises: holding the
    stale book (the simulator's all-zero-row rule) would be a silent wrong answer.
  - Weight = market cap / sum of market caps. No cap (the official 2026
    portfolio has Petrobras at 27%).
  - Annual rebalance at the close of the last trading day of March (data known
    by then), held with drift for the year.

ponytail: rebalances at the March close, not on April 1. The difference is one
session. Change rebalance_month if Teva's calendar turns out to be different.
ponytail: no free-float data. The 15%-free-float filter is skipped, and weights
use full market cap where Teva uses free-float cap, so controlled companies
(BPAC, ABEV, WEGE) are overweight. Add free_float scaling when there is a source.
ponytail: one share class per issuer (the most liquid one) where Teva holds every
eligible class (PETR3+PETR4, ITUB3+ITUB4). The issuer's weight is the same; only
how it splits across classes differs.
ponytail: net_debt nets cash and aplicações financeiras (cvm_metrics) and is
NULL for banks/insurers (pass the leverage test, like financial_roots). RDOR's
1.01.02 includes SulAmérica's reserve portfolio, so it reads nearly unlevered.
ponytail: the yes/no profit-SIGN test over the 4 older years uses a year's first
later filing when no version was filed by t (ITUB's FY2011/12 DFPs exist only as
2020 re-filings). A small, deliberate leak: a 3+-year-old annual result's sign
is public. ROE, margin and leverage stay strictly point-in-time.
ponytail: ROE and margin use the latest fiscal year, because Teva's averaging
window is unknown. Net margin is skipped when revenue is missing or zero
(holdings and insurers, e.g. BBSE, report no revenue line). Leverage passes when
net debt is missing, which is how banks come through the CVM parser.
ponytail: retail and the bank/insurer exemption use the CVM register's CURRENT
sector (cvm_companies.sector via load_sector_pit), not the sector at t: a
company reclassified since then is judged by today's label. Persist FCA's
per-year sector if that matters. An empty register raises SectorRegistryEmpty.
ponytail: CVM's taxonomy files protein under "Alimentos" and airlines under
"Transporte e Logística", with no sub-bucket, so those two exclusions are a root
list. That list is not point-in-time.
ponytail: CVM files some manufacturers under "Comércio (Atacado e Varejo)"
(INTB, Intelbras, which the official index holds), so they are excluded too.
Known data issues outside this module (prod, 2026-11): fixed in code, live
after the next prices + CVM re-ingest — CIEL's 2015 split counted twice (FRE
anchor, pit.share_count_anchor), DIRR's 2025 split dropped (B3 data-com /
percentage factor, split_validation), TIMS's all-zero FY2024/25 DFPs
(cvm_metrics). ABEV's FY2016/17 DFPs exist only as 2018-07 re-filings (stale ->
out 2016-18), and reorganisations that changed the CNPJ (AMBV -> ABEV 2013,
TIMP -> TIMS 2020) are not linked, so their history restarts.
"""
from __future__ import annotations

import pandas as pd

from b3_pipeline import storage
from backtests.core.data import SectorRegistryEmpty
from backtests.core.strategy_base import (
    StrategyBase,
    ParameterSpec,
    COMMON_END_DATE,
    COMMON_INITIAL_CAPITAL,
    COMMON_TAX_RATE,
    COMMON_SLIPPAGE,
)
from backtests.strategies.sp500_b3 import (
    DEFAULT_STALENESS_DAYS,
    LIQUIDITY_WINDOW,
    load_fundamentals_pit_raw,
    load_stock_actions,
    select_constituents,
)

RETAIL_SECTORS = ["comércio (atacado e varejo)"]
FINANCIAL_SECTORS = ["bancos", "seguradoras", "intermediação financeira"]
# Animal protein (JBS, BRF, Marfrig, Minerva, MBRF, Minupar, Excelsior), airlines.
DEFAULT_EXCLUDED_ROOTS = "JBSS,BRFS,MRFG,BEEF,MBRF,MNPR,BAUH,GOLL,AZUL"
DFP_METRICS = ["revenue", "net_income", "ebitda", "equity", "net_debt"]


# ── Pure selection functions (no DB access — unit-tested) ─────────────────────

def annual_snapshot(dfp: pd.DataFrame, t: pd.Timestamp, profit_years: int = 5,
                    staleness_days: int = DEFAULT_STALENESS_DAYS) -> pd.DataFrame:
    """The latest annual statement (DFP) per company, as known at t, indexed by
    the root it was filed under.

    Companies are keyed by `cnpj` when the frame has it (else by root), so a
    rename does not restart the profit history. All-zero filings are dropped
    (placeholders, not results). Each fiscal year is read in its latest version
    filed on/before t: a restatement filed after t never leaks into the metrics.
    The newest fiscal year must have been filed within `staleness_days`.
    `profit_years` counts the fiscal years in the window ending at that newest
    year with positive net income (see _profit_years for late-filed years).
    """
    t = pd.Timestamp(t)
    key = "cnpj" if "cnpj" in dfp.columns else "ticker"
    real = dfp[dfp[DFP_METRICS].fillna(0).ne(0).any(axis=1)]
    srt = real.sort_values(["filing_date", "filing_version"])
    vis = srt[srt["filing_date"] <= t].groupby([key, "fiscal_year"]).tail(1)
    latest = vis.sort_values("fiscal_year").groupby(key).tail(1)
    latest = latest[latest["filing_date"] >= t - pd.Timedelta(days=staleness_days)]
    if latest.empty:
        return pd.DataFrame(columns=DFP_METRICS + ["fiscal_year", "profit_years"])
    pos = _profit_years(srt, vis, latest, key, t, profit_years)
    out = latest.assign(profit_years=latest[key].map(pos).fillna(0).astype(int))
    out = out.drop_duplicates("ticker", keep="last").set_index("ticker")
    return out[["fiscal_year"] + DFP_METRICS + ["profit_years"]]


def _profit_years(srt, vis, latest, key, t, n) -> pd.Series:
    """Positive-net-income years per company in the n-year window ending at its
    newest visible fiscal year. A year with no version filed by t counts with
    its first later filing — only the sign is read, and only for older years."""
    late = srt[srt["filing_date"] > t].groupby([key, "fiscal_year"]).head(1)
    seen = pd.MultiIndex.from_frame(vis[[key, "fiscal_year"]])
    late = late[~pd.MultiIndex.from_frame(late[[key, "fiscal_year"]]).isin(seen)]
    rows = pd.concat([vis, late])
    fy = rows[key].map(latest.set_index(key)["fiscal_year"])
    win = rows[(rows["fiscal_year"] > fy - n) & (rows["fiscal_year"] <= fy)]
    return (win["net_income"] > 0).groupby(win[key]).sum()


def fundamental_pass(snap: pd.DataFrame, financial_roots=(), profit_years: int = 5,
                     min_roe: float = 0.10, min_margin: float = 0.08,
                     max_leverage: float = 3.0) -> pd.Index:
    """Roots that pass the IAFD profitability, margin and leverage screens."""
    ni, eq, rev = snap["net_income"], snap["equity"], snap["revenue"]
    nd, eb = snap["net_debt"], snap["ebitda"]
    profitable = (snap["profit_years"] >= profit_years) & (eq > 0) & (ni > min_roe * eq)
    margin_ok = rev.isna() | (rev <= 0) | (ni > min_margin * rev)
    lev_ok = (nd.isna() | (nd <= 0) | ((eb > 0) & (nd < max_leverage * eb))
              | snap.index.isin(list(financial_roots)))
    return snap.index[profitable & margin_ok & lev_ok]


def liquidity_pass(fin_vol: pd.DataFrame, td: pd.Timestamp,
                   min_monthly: float = 1e8) -> pd.Index:
    """Tickers that traded on every session of td's month (through td) and
    traded at least `min_monthly` BRL in that month and in the one before."""
    td = pd.Timestamp(td)
    fv = fin_vol.loc[td - pd.Timedelta(days=70): td].fillna(0.0)
    per = fv.index.to_period("M")
    cur, prev = fv[per == td.to_period("M")], fv[per == td.to_period("M") - 1]
    if cur.empty or prev.empty:
        return pd.Index([])
    ok = (cur > 0).all() & (cur.sum() >= min_monthly) & (prev.sum() >= min_monthly)
    return ok.index[ok]


def sector_roots(sector_pit: pd.DataFrame, keywords: list) -> set:
    """Roots whose CVM sector contains any keyword (case-insensitive). Raises
    on an empty register: skipping the exclusions silently is a different index."""
    if sector_pit.empty:
        raise SectorRegistryEmpty("TevaIAFD")
    kw = [k.lower() for k in keywords]
    hit = sector_pit["sector"].astype(str).str.lower().apply(lambda x: any(k in x for k in kw))
    return set(sector_pit.loc[hit, "root"].astype(str))


def company_first_trade(first: pd.Series, companies: pd.DataFrame) -> pd.Series:
    """First trade per root, widened to the earliest of every root its company
    (CNPJ) ever traded under. `companies` is [root, cnpj]; roots without a CNPJ
    keep their own date."""
    m = companies.merge(first.rename("first"), left_on="root", right_index=True)
    by_co = m.groupby("cnpj")["first"].min()
    per_root = companies.assign(first=companies["cnpj"].map(by_co)).groupby("root")["first"].min()
    return per_root.combine_first(first)


def iafd_weights(sel: pd.DataFrame, passed: pd.Index, excluded: set,
                 listed: pd.Index) -> pd.Series:
    """Market-cap weights over the selected tickers whose root passed every
    screen. `sel` is select_constituents output (root, ticker, market_cap)."""
    keep = sel[sel["root"].isin(passed) & sel["root"].isin(listed)
               & ~sel["root"].isin(excluded)]
    if keep.empty:
        return pd.Series(dtype=float)
    mc = keep.set_index("ticker")["market_cap"]
    return mc / mc.sum()


# ── DB loaders (thin) ─────────────────────────────────────────────────────────

def load_dfp(db_path: str) -> pd.DataFrame:
    """Annual statements from fundamentals_pit (thousands of BRL), dates parsed."""
    sql = f"""
        SELECT cnpj, ticker, fiscal_year, filing_date, filing_version,
               {', '.join(DFP_METRICS)}
        FROM fundamentals_pit
        WHERE doc_type = 'DFP' AND ticker IS NOT NULL AND fiscal_year IS NOT NULL
    """
    with storage.get_connection(db_path) as conn:
        df = storage.read_sql(sql, conn)
    df["filing_date"] = pd.to_datetime(df["filing_date"])
    return df


def load_first_trade(db_path: str) -> pd.Series:
    """First traded date per 4-char root, over the whole price history."""
    with storage.get_connection(db_path) as conn:
        df = storage.read_sql("SELECT ticker, MIN(date) AS first FROM prices GROUP BY ticker",
                              conn)
    df["first"] = pd.to_datetime(df["first"])
    return df.groupby(df["ticker"].astype(str).str[:4])["first"].min()


def load_companies(db_path: str) -> pd.DataFrame:
    """[root, cnpj] for every root a company traded under — the same join
    load_sector_pit uses (company_tickers_pit, point-in-time, + cvm_companies)."""
    sql = """
        SELECT DISTINCT cnpj, ticker_root AS root FROM company_tickers_pit
        WHERE ticker_root IS NOT NULL UNION
        SELECT DISTINCT cnpj, ticker_root FROM cvm_companies WHERE ticker_root IS NOT NULL"""
    with storage.get_connection(db_path) as conn:
        return storage.read_sql(sql, conn)[["root", "cnpj"]]


class TevaIAFDStrategy(StrategyBase):
    """Replica of the Índice Teva Ações Fundamentos (IAFD, the AUVP11 ETF)."""

    needs_fundamentals: bool = True

    name = "TevaIAFD"
    description = (
        "Aproximação do Índice Teva Ações Fundamentos (IAFD, ETF AUVP11): "
        "empresas listadas há 5+ anos, valor de mercado >= R$3 bi, 5 anos de "
        "lucro, ROE > 10%, margem líquida > 8%, dívida líquida < 3x EBIT "
        "(bancos e seguradoras isentos); exclui varejo, proteína animal e "
        "aéreas. Rebalanceamento anual inferido (abril); pesos por valor de "
        "mercado total (sem free float). É uma aproximação: nos backtests ficou "
        "~4 p.p./ano abaixo do índice oficial na média, com diferenças grandes "
        "em anos isolados."
    )

    def get_parameter_specs(self) -> list[ParameterSpec]:
        return [
            ParameterSpec("start_date", "Start Date", "date", "2015-03-01",
                          description="Backtest start (index inception: 2015-04-01)"),
            COMMON_END_DATE, COMMON_INITIAL_CAPITAL, COMMON_TAX_RATE, COMMON_SLIPPAGE,
            ParameterSpec(
                "rebalance_freq", "Rebalance Frequency", "choice", "ME",
                description="Grid only; the book trades once a year in rebalance_month",
                choices=["ME", "QE"],
            ),
            ParameterSpec(
                "rebalance_month", "Rebalance Month", "int", 3,
                description="Month whose last close sets the new portfolio (3 = March, "
                            "effective April 1)",
                min_value=1, max_value=12, step=1,
            ),
            ParameterSpec(
                "min_market_cap", "Min Market Cap (BRL)", "float", 3e9,
                description="Methodology: R$3bn", min_value=0.0, step=5e8,
            ),
            ParameterSpec(
                "min_monthly_volume", "Min Monthly Volume (BRL)", "float", 1e8,
                description="Traded in each of the two months before the rebalance "
                            "(methodology: R$100mm)",
                min_value=0.0, step=1e7,
            ),
            ParameterSpec(
                "excluded_roots", "Excluded Issuers", "str", DEFAULT_EXCLUDED_ROOTS,
                description="Comma-separated 4-char roots excluded on top of CVM retail "
                            "(default: animal protein and airlines)",
            ),
            ParameterSpec(
                "db_path", "Database Path", "str", "b3_market_data.sqlite",
                description="B3 database (fundamentals_pit / stock_actions / prices)",
            ),
        ]

    def generate_signals(
        self, shared_data: dict, params: dict
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        ret = shared_data["ret"]
        close_px = shared_data["close_px"]   # daily raw close (ffilled)
        fin_vol = shared_data["fin_vol"]     # daily financial volume
        db_path = str(params.get("db_path", "b3_market_data.sqlite"))
        month = int(params.get("rebalance_month", 3))
        min_mcap = float(params.get("min_market_cap", 3e9))
        min_vol = float(params.get("min_monthly_volume", 1e8))
        roots = {r.strip().upper() for r in
                 str(params.get("excluded_roots", DEFAULT_EXCLUDED_ROOTS)).split(",")}

        fund, dfp = load_fundamentals_pit_raw(db_path), load_dfp(db_path)
        actions = load_stock_actions(db_path)
        first = company_first_trade(load_first_trade(db_path), load_companies(db_path))
        # Only picks the share class and feeds the bad-share-count guard; the
        # screen itself is liquidity_pass. min_periods so a run starting in
        # January still has its first March book (sp500's helper needs 63 days).
        med63 = fin_vol.fillna(0.0).rolling(LIQUIDITY_WINDOW, min_periods=21).median()
        sector_pit = shared_data.get("sector_pit", pd.DataFrame())
        excluded = roots | sector_roots(sector_pit, RETAIL_SECTORS)
        fin_roots = sector_roots(sector_pit, FINANCIAL_SECTORS)

        tw = pd.DataFrame(0.0, index=ret.index, columns=ret.columns)
        started = False
        for t_cal in ret.index[ret.index.month == month]:
            td = close_px.index.asof(t_cal)  # last trading day <= calendar date
            if pd.isna(td):
                continue
            liquid = liquidity_pass(fin_vol, td, min_vol)
            sel = select_constituents(
                td, fund, close_px.loc[td], med63.loc[td].where(med63.columns.isin(liquid), 0.0),
                actions, min_market_cap=min_mcap, min_adtv=0.0, require_earnings=False,
            )
            passed = fundamental_pass(annual_snapshot(dfp, td), fin_roots)
            listed = first.index[first <= td - pd.DateOffset(years=5)]
            w = iafd_weights(sel, passed, excluded, listed)
            cols = w.index.intersection(tw.columns)
            if not len(cols):
                if started:
                    raise RuntimeError(f"TevaIAFD: no company passed the screens at {td.date()}")
                continue  # before inception: no book yet
            started = True
            # trade at the selection close, like SP500B3Strategy
            tw.loc[t_cal, cols] = (w[cols] / w[cols].sum()).values
        return ret, tw
